"""Order placement and position closing via Alpaca's multi-leg options API.

Both legs of a vertical spread go to the exchange as one ``mleg`` order. That
matters: submitting the legs separately risks one filling and the other not,
which would leave a naked short put open — precisely the exposure this whole
strategy exists to avoid.

Two details in here are easy to get wrong and are covered by dedicated tests:

**Sign convention.** For an ``mleg`` order Alpaca reads a positive limit price
as a debit and a negative one as a credit. A credit spread must therefore be
submitted with a *negative* limit price. Inverting this would send an order
willing to *pay* to open a position that should collect.

**Idempotency.** ``client_order_id`` is derived deterministically from the trade
itself, so if a cycle dies between submitting and recording, the next cycle's
attempt collides with the existing order instead of opening a second position.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from ..config import AgentConfig
from ..state import OptionsAgentState

logger = logging.getLogger(__name__)


def build_client_order_id(spread: dict[str, Any], trade_date: str, contracts: int) -> str:
    """A deterministic id for this exact trade.

    Same spread, same day, same size produces the same id, so a retry after a
    crash is recognised by the broker as a duplicate rather than opening a
    second position. Alpaca caps this field at 128 characters; the hash keeps it
    well inside that.
    """
    payload = "|".join([
        str(spread.get("ticker", "")),
        str(spread.get("expiry", "")),
        str(spread.get("sell_strike", "")),
        str(spread.get("buy_strike", "")),
        str(trade_date),
        str(contracts),
    ])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
    return f"oa-{spread.get('ticker', 'X')}-{digest}"


def compute_limit_price(spread: dict[str, Any], config: AgentConfig) -> float:
    """The limit price for the spread, signed for Alpaca's mleg convention.

    Returns a **negative** number: for multi-leg orders Alpaca treats a negative
    limit as a credit to be received and a positive one as a debit to be paid.

    The price starts from the mid of the two legs and gives back a configured
    amount of slippage. Sending the mid exactly tends not to fill; sending the
    conservative bid/ask credit gives away the whole edge. Splitting the
    difference is what actually gets filled without trading badly.
    """
    execution = config.execution
    mode = str(execution.get("limit_price_mode", "mid")).lower()
    slippage = float(execution.get("limit_price_slippage", 0.0))

    if mode == "conservative":
        credit_per_share = float(spread["net_credit"]) / 100.0
    else:
        credit_per_share = float(spread.get("mid_credit", spread["net_credit"])) / 100.0

    credit_per_share = max(0.01, credit_per_share - slippage)
    return -round(credit_per_share, 2)


def build_spread_order(
    spread: dict[str, Any],
    contracts: int,
    client_order_id: str,
    config: AgentConfig,
) -> dict[str, Any]:
    """Construct the two-leg limit order for a bull put spread.

    Leg one sells the near strike (``sell_to_open``), leg two buys the far one
    (``buy_to_open``). Explicit position intents keep Alpaca from interpreting
    the legs as closing something else.

    Every scalar is rendered as a string because that is what the CLI's flags
    and the multi-leg ``legs`` payload expect; passing numbers gets them
    stringified inconsistently at the boundary.
    """
    tif = str(config.execution.get("time_in_force", "day")).lower()
    time_in_force = "day" if tif == "day" else "gtc"

    return {
        "qty": str(contracts),
        "order_class": "mleg",
        "type": "limit",
        "time_in_force": time_in_force,
        "client_order_id": client_order_id,
        "limit_price": str(compute_limit_price(spread, config)),
        "legs": [
            {
                "symbol": spread["sell_symbol"],
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
            {
                "symbol": spread["buy_symbol"],
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
        ],
    }


def execute_spread(
    broker,
    spread: dict[str, Any],
    contracts: int,
    trade_date: str,
    config: AgentConfig,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Submit the spread, or describe what would be submitted in a dry run.

    Returns a result dict in both cases so the journal records dry runs in the
    same shape as live trades and the dashboard needs no special handling.
    """
    client_order_id = build_client_order_id(spread, trade_date, contracts)
    limit_price = compute_limit_price(spread, config)

    base = {
        "client_order_id": client_order_id,
        "contracts": contracts,
        "limit_price": limit_price,
        "credit_target": round(abs(limit_price) * 100.0 * contracts, 2),
        "max_loss_total": round(float(spread["max_loss"]) * contracts, 2),
        "sell_symbol": spread["sell_symbol"],
        "buy_symbol": spread["buy_symbol"],
        "submitted_at": datetime.now().astimezone().isoformat(),
    }

    if dry_run:
        return {
            **base,
            "success": True,
            "dry_run": True,
            "status": "dry_run",
            "order_id": None,
            "message": (
                f"DRY RUN — would sell {contracts}x {spread['ticker']} "
                f"{spread['sell_strike']:.0f}/{spread['buy_strike']:.0f} put spread "
                f"for a {abs(limit_price):.2f} credit per contract."
            ),
        }

    # Idempotency guard: if a previous cycle already placed this exact trade,
    # adopt that order instead of opening a second position.
    existing = broker.get_order_by_client_id(client_order_id)
    if existing is not None:
        logger.warning("Order %s already exists; not resubmitting.", client_order_id)
        return {
            **base,
            "success": True,
            "dry_run": False,
            "duplicate": True,
            "order_id": str(existing.id),
            "status": str(getattr(existing, "status", "unknown")),
            "filled_avg_price": float(getattr(existing, "filled_avg_price", 0) or 0),
            "message": "Order with this client_order_id already existed; adopted it.",
        }

    try:
        order = broker.submit_order(build_spread_order(spread, contracts, client_order_id, config))
    except Exception as exc:  # noqa: BLE001 — surface any broker rejection into the journal
        logger.error("Order submission failed for %s: %s", spread.get("ticker"), exc)
        return {
            **base,
            "success": False,
            "dry_run": False,
            "status": "failed",
            "order_id": None,
            "error": str(exc),
            "message": f"Alpaca rejected the order: {exc}",
        }

    filled_price = float(getattr(order, "filled_avg_price", 0) or 0)
    return {
        **base,
        "success": True,
        "dry_run": False,
        "order_id": str(order.id),
        "status": str(getattr(order, "status", "unknown")),
        "filled_avg_price": filled_price,
        "filled_qty": float(getattr(order, "filled_qty", 0) or 0),
        "message": (
            f"Submitted {contracts}x {spread['ticker']} "
            f"{spread['sell_strike']:.0f}/{spread['buy_strike']:.0f} put spread."
        ),
    }


def close_position(broker, position: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    """Close a single option position at market.

    Exits go out as market orders on purpose. The exit triggers are risk
    decisions — a profit target reached, a stop breached, expiry approaching —
    and a limit order that fails to fill leaves the position open and the risk
    unmanaged. Certainty of exit is worth more than a few cents of spread.
    """
    symbol = position["symbol"]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "status": "dry_run",
            "symbol": symbol,
            "realized_pnl": round(position.get("unrealized_pl") or 0.0, 2),
            "message": f"DRY RUN — would close {symbol}.",
        }

    try:
        order = broker.close_position(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to close %s: %s", symbol, exc)
        return {
            "success": False,
            "dry_run": False,
            "status": "failed",
            "symbol": symbol,
            "error": str(exc),
            "message": f"Could not close {symbol}: {exc}",
        }

    return {
        "success": True,
        "dry_run": False,
        "status": str(getattr(order, "status", "unknown")),
        "symbol": symbol,
        "order_id": str(getattr(order, "id", "")),
        "realized_pnl": round(position.get("unrealized_pl") or 0.0, 2),
        "message": f"Closing order submitted for {symbol}.",
    }


def make_executor_node(broker, config: AgentConfig):
    """Build the executor node. Only ever reached on the approved branch."""

    def executor_node(state: OptionsAgentState) -> dict[str, Any]:
        if state.get("halted") or not state.get("approved"):
            return {}

        spread = state.get("selected_spread")
        contracts = int(state.get("contracts") or 0)
        if not spread or contracts < 1:
            return {"execution": None}

        result = execute_spread(
            broker=broker,
            spread=spread,
            contracts=contracts,
            trade_date=state.get("trade_date", ""),
            config=config,
            dry_run=bool(state.get("dry_run", True)),
        )
        return {"execution": result}

    return executor_node
