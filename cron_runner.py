#!/usr/bin/env python
"""Entry point for one scheduled cycle of the options agent.

Invoked by cron every five minutes during market hours. Each run manages exits
on the existing book, then scans each configured underlying for a new spread.

Market-hours gating happens here in Python, against Alpaca's own clock endpoint,
rather than being left to the crontab's hour range. This machine runs on PKT
while the market runs on Eastern, and a timezone assumption baked into a crontab
line is the kind of thing that silently trades at the wrong hour. The clock
endpoint is authoritative and also knows about holidays and half-days, which no
crontab does.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

from options_agent.broker import Broker
from options_agent.config import ConfigError, load_config
from options_agent.graph import OptionsAgentGraph
from options_agent.nodes.trade_journal import TradeJournal

REPO_ROOT = Path(__file__).resolve().parent


def setup_logging(verbose: bool = False) -> None:
    """Log to stdout (captured by cron into cron.log) and to errors.log."""
    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(stream)

    errors = logging.FileHandler(log_dir / "errors.log", encoding="utf-8")
    errors.setLevel(logging.WARNING)
    errors.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(errors)


def run_cycle(
    tickers: list[str] | None = None,
    dry_run: bool = True,
    force: bool = False,
) -> int:
    """Run one full cycle. Returns a process exit code."""
    logger = logging.getLogger("cron_runner")
    run_id = uuid.uuid4().hex[:12]

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    journal = TradeJournal(config.paths["journal"])

    try:
        broker = Broker.from_config(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not reach Alpaca: %s", exc)
        journal.log_error(run_id, "", f"broker init failed: {exc}")
        return 2

    # --- Market-hours gate ---------------------------------------------------
    if not force:
        try:
            clock = broker.get_clock()
        except Exception as exc:  # noqa: BLE001
            logger.error("Clock check failed: %s", exc)
            return 2
        if not clock.is_open:
            logger.info(
                "Market closed. Next open %s. Skipping cycle %s.",
                getattr(clock, "next_open", "unknown"), run_id,
            )
            return 0

    mode = "DRY RUN" if dry_run else "LIVE"
    tickers = tickers or config.underlyings
    logger.info("=== Cycle %s (%s) — %s ===", run_id, mode, ", ".join(tickers))

    agent = OptionsAgentGraph(broker, config, journal)

    # --- Manage the existing book before opening anything new ----------------
    # Closing a position frees buying power and reduces portfolio delta, both of
    # which are gate inputs. Doing this first means new trades are evaluated
    # against the book as it will actually be, not as it was.
    try:
        exits = agent.manage_exits(dry_run=dry_run, run_id=run_id)
        if exits:
            logger.info("Closed %d position(s) this cycle.", len(exits))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Exit management failed: %s", exc)
        journal.log_error(run_id, "", f"exit management failed: {exc}")
        exits = []

    # --- Scan each underlying ------------------------------------------------
    approved = 0
    submitted = 0
    rejected = 0
    skipped = 0
    max_new = int(config.execution.get("max_new_positions_per_cycle", 1))

    for ticker in tickers:
        # Every underlying is analysed every cycle even after the new-position
        # cap is reached: the IV history keeps accumulating for all three, and
        # the dashboard's market panel needs a current IV rank for each. Only
        # *execution* is capped — once the cap is hit the remaining tickers run
        # as dry runs regardless of the mode.
        capped = approved >= max_new
        ticker_dry_run = dry_run or capped
        if capped:
            logger.info(
                "New-position cap (%d) reached; analysing %s without executing.", max_new, ticker
            )

        try:
            state = agent.run(ticker, dry_run=ticker_dry_run, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not end the cycle
            logger.exception("Cycle failed for %s: %s", ticker, exc)
            journal.log_error(run_id, ticker, str(exc))
            continue

        if state.get("halted"):
            skipped += 1
            logger.info("%s skipped: %s", ticker, state.get("halt_reason"))
        elif state.get("approved"):
            execution = state.get("execution") or {}
            # Only a genuine (non-capped) approval counts toward the cap.
            if not capped:
                approved += 1
                if execution.get("success") and not execution.get("dry_run"):
                    submitted += 1
            logger.info("%s: %s", ticker, execution.get("message", "approved but not executed"))
        else:
            rejected += 1
            logger.info("%s rejected: %s", ticker, state.get("gate_reason", "")[:200])

    summary = {
        "mode": mode,
        "tickers": tickers,
        "approved": approved,
        "submitted": submitted,
        "rejected": rejected,
        "skipped": skipped,
        "exits": len(exits),
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    journal.log_cycle(run_id, summary)
    logger.info(
        "=== Cycle %s complete — %d approved, %d submitted, %d rejected, %d skipped, %d exits ===",
        run_id, approved, submitted, rejected, skipped, len(exits),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one cycle of the Alpaca options agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  cron_runner.py --dry-run --force --ticker SPY   # offline test, no orders\n"
            "  cron_runner.py --live                           # real orders, market hours only\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Run the full pipeline but never submit an order. This is the default.",
    )
    mode.add_argument(
        "--live", action="store_true",
        help="Submit real orders to the configured Alpaca account.",
    )
    parser.add_argument(
        "--ticker", action="append", dest="tickers", metavar="SYMBOL",
        help="Scan only this underlying. Repeatable. Defaults to the configured list.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the market-hours check. For testing outside trading hours.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-level logging.")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Dry run is the default: --live must be asked for explicitly, so a
    # mistyped command can never place an order by accident.
    dry_run = not args.live
    return run_cycle(tickers=args.tickers, dry_run=dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
