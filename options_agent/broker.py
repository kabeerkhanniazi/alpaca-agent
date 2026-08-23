"""Alpaca access via the official CLI, with thin retrying wrappers.

Every network call the agent makes goes through here. Centralising it means the
retry policy, the paper/live switch, and the credential handling each exist in
exactly one place — and it makes the whole pipeline mockable in tests by
swapping a single object.

The transport is Alpaca's official CLI (`alpaca`), not the `alpaca-py` SDK: the
hackathon requires projects to use Alpaca's MCP server or CLI tools. Method
signatures and return shapes are unchanged from the SDK version, so nothing
downstream of this module knows the difference — see
:mod:`options_agent.alpaca_cli` for how JSON is presented with SDK-compatible
attribute names.

plan.md flags network timeouts as the top operational risk for an unattended
5-day run, so reads retry with exponential backoff. Order submission does NOT
retry blindly; the executor handles that path deliberately using a
deterministic client_order_id, because a retried write is a duplicate-trade
risk in a way a retried read never is.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

import pandas as pd

from . import alpaca_cli
from .alpaca_cli import CLIError, wrap
from .config import AgentConfig, Credentials

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.5

# Free-tier data plans: stock bars come from IEX (recent SIP is subscription-only)
# and the options chain from the indicative feed (OPRA needs a signed agreement).
# Both are set here rather than at each call site so upgrading the data plan is a
# two-line change.
#
# The options feed must be passed explicitly on every call: the CLI's own default
# is `opra`, and an account without a signed OPRA agreement gets a 403 rather
# than a silent downgrade to the free feed.
STOCK_FEED = "iex"
OPTIONS_FEED = "indicative"


def _is_permanent(exc: Exception) -> bool:
    """True when retrying this error can never succeed.

    A 403 for an unsubscribed data feed, or a 422 for a malformed request, is
    settled — retrying just burns seconds of a five-minute cycle. Rate limits
    (429) and server errors (5xx) are the ones worth waiting out, and the CLI
    already retries those internally before it returns non-zero.
    """
    if isinstance(exc, CLIError):
        return exc.permanent
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status is None:
        return False
    return 400 <= int(status) < 500 and int(status) != 429


def retrying(fn: Callable) -> Callable:
    """Retry a read-only call with exponential backoff.

    Only safe for idempotent reads. The final failure is re-raised so the caller
    decides whether a cycle can continue without that data.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — transport raises broadly
                last_error = exc
                if _is_permanent(exc):
                    logger.error("%s failed permanently (no retry): %s", fn.__name__, exc)
                    raise
                if attempt == MAX_ATTEMPTS:
                    break
                delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                    fn.__name__, attempt, MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)
        logger.error("%s failed after %d attempts: %s", fn.__name__, MAX_ATTEMPTS, last_error)
        raise last_error  # type: ignore[misc]

    return wrapper


class Broker:
    """Facade over the Alpaca CLI for every call the agent needs."""

    def __init__(self, credentials: Credentials):
        self._creds = credentials
        # The CLI authenticates from the environment, which keeps secrets off
        # disk and out of any profile file. Setting them per-call rather than
        # relying on the ambient environment means a caller that loaded a .env
        # into an AgentConfig behaves the same as one that exported them.
        self._env = {
            "ALPACA_API_KEY": credentials.api_key,
            "ALPACA_SECRET_KEY": credentials.secret_key,
        }
        if not credentials.paper:
            self._env["ALPACA_LIVE_TRADE"] = "true"

    @classmethod
    def from_config(cls, config: AgentConfig) -> "Broker":
        if config.credentials is None:
            raise ValueError("Config was loaded without credentials; cannot build a Broker.")
        return cls(config.credentials)

    def _run(self, args: list[str], timeout: int = alpaca_cli.DEFAULT_TIMEOUT) -> Any:
        previous = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        try:
            return alpaca_cli.run(args, timeout=timeout)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    # ---- Account and clock -------------------------------------------------

    @retrying
    def get_account(self) -> Any:
        return wrap(self._run(["account", "get"]))

    @retrying
    def get_clock(self) -> Any:
        return wrap(self._run(["clock"]))

    def is_market_open(self) -> bool:
        return bool(self.get_clock().is_open)

    @retrying
    def get_positions(self) -> list:
        return wrap(self._run(["position", "list"]) or [])

    def get_option_positions(self) -> list:
        """Open positions that are options contracts, not shares.

        Alpaca marks these with asset_class ``us_option``; the symbol-length
        check is a fallback in case the field's representation shifts.
        """
        out = []
        for pos in self.get_positions():
            asset_class = str(getattr(pos, "asset_class", "") or "").lower()
            if "option" in asset_class or len(pos.symbol) > 10:
                out.append(pos)
        return out

    @retrying
    def get_orders(self, **kwargs) -> list:
        return wrap(self._run(["order", "list", *alpaca_cli.flags(**kwargs)]) or [])

    # ---- Market data -------------------------------------------------------

    @retrying
    def get_spot_price(self, ticker: str) -> float:
        """Latest traded price for the underlying."""
        result = self._run(
            ["data", "latest-trade", *alpaca_cli.flags(symbol=ticker, feed=STOCK_FEED)]
        )
        return float(result["trade"]["p"])

    @retrying
    def get_daily_bars(self, ticker: str, lookback_days: int = 400):
        """Daily OHLCV bars, returned as a pandas DataFrame.

        The lookback defaults past 252 trading days so realized-volatility
        percentiles have a full year of calendar data to draw on. Columns are
        renamed to the long forms the analyst reads (``close``/``high``/``low``).
        """
        end = datetime.now()
        start = end - timedelta(days=lookback_days)

        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            payload = self._run([
                "data", "bars",
                *alpaca_cli.flags(
                    symbol=ticker,
                    timeframe="1Day",
                    start=start.date().isoformat(),
                    end=end.date().isoformat(),
                    feed=STOCK_FEED,
                    limit=10000,
                    page_token=page_token,
                ),
            ])
            rows.extend((payload or {}).get("bars") or [])
            page_token = (payload or {}).get("next_page_token") or None
            if not page_token:
                break

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"])
        return pd.DataFrame(rows).rename(columns={
            "o": "open", "h": "high", "l": "low", "c": "close",
            "v": "volume", "n": "trade_count", "vw": "vwap", "t": "timestamp",
        })

    @retrying
    def get_put_chain(
        self,
        ticker: str,
        strike_low: float,
        strike_high: float,
        expiry_from: Any,
        expiry_to: Any,
    ) -> dict:
        """Fetch put snapshots with Greeks and quotes in a single call.

        Uses the option-chain endpoint rather than listing contracts and then
        requesting snapshots for each: one round trip instead of two, and the
        strike/expiry/type filters are applied server-side.

        Returns a dict of ``{contract_symbol: snapshot}``, where each snapshot
        answers ``.greeks``, ``.implied_volatility`` and ``.latest_quote``.
        """
        payload = self._run([
            "data", "option", "chain",
            *alpaca_cli.flags(
                underlying_symbol=ticker,
                type="put",
                strike_price_gte=round(float(strike_low), 2),
                strike_price_lte=round(float(strike_high), 2),
                expiration_date_gte=_as_date(expiry_from),
                expiration_date_lte=_as_date(expiry_to),
                feed=OPTIONS_FEED,
                limit=1000,
            ),
        ])
        return {sym: wrap(snap) for sym, snap in ((payload or {}).get("snapshots") or {}).items()}

    @retrying
    def get_option_snapshots(self, symbols: list[str]) -> dict:
        """Snapshots for specific contract symbols (used when marking positions)."""
        if not symbols:
            return {}
        payload = self._run([
            "data", "option", "snapshot",
            *alpaca_cli.flags(symbols=",".join(symbols), feed=OPTIONS_FEED, limit=100),
        ])
        return {sym: wrap(snap) for sym, snap in ((payload or {}).get("snapshots") or {}).items()}

    # ---- Orders ------------------------------------------------------------

    def submit_order(self, order_request: dict[str, Any]) -> Any:
        """Submit an order. Deliberately not wrapped in ``retrying``.

        A failed write may or may not have reached the exchange. The executor
        handles this by pre-computing a deterministic client_order_id and
        checking for an existing order with that id before resubmitting.

        ``order_request`` is the plain dict built by
        :func:`options_agent.nodes.executor.build_spread_order`. The legs go
        over as a JSON string, which is what the CLI's ``--legs`` flag expects.
        """
        request = dict(order_request)
        legs = request.pop("legs", None)
        args = ["order", "submit", *alpaca_cli.flags(**request)]
        if legs:
            args.extend(["--legs", json.dumps(legs)])
        return wrap(self._run(args))

    def close_position(self, symbol: str) -> Any:
        """Close an open position at market."""
        return wrap(self._run(["position", "close", *alpaca_cli.flags(symbol_or_asset_id=symbol)]))

    @retrying
    def get_order_by_client_id(self, client_order_id: str) -> Any | None:
        try:
            return wrap(self._run([
                "order", "get-by-client-id",
                *alpaca_cli.flags(client_order_id=client_order_id),
            ]))
        except Exception:  # noqa: BLE001 — a 404 here means "no such order", not a failure
            return None


def _as_date(value: Any) -> str | None:
    """Render a date/datetime/string as the YYYY-MM-DD the CLI expects."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
