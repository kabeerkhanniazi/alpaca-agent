"""Alpaca client construction and thin, retrying wrappers.

Every network call the agent makes goes through here. Centralising it means the
retry policy, the paper/live switch, and the credential handling each exist in
exactly one place — and it makes the whole pipeline mockable in tests by
swapping a single object.

plan.md flags network timeouts as the top operational risk for an unattended
5-day run, so reads retry with exponential backoff. Order submission does NOT
retry blindly; the executor handles that path deliberately using a
deterministic client_order_id, because a retried write is a duplicate-trade
risk in a way a retried read never is.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType

from .config import AgentConfig, Credentials

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.5

# Free-tier data plans: stock bars come from IEX (recent SIP is subscription-only)
# and the options chain from the indicative feed (OPRA needs a signed agreement).
# Both are set here rather than at each call site so upgrading the data plan is a
# two-line change.
STOCK_FEED = DataFeed.IEX
OPTIONS_FEED = OptionsFeed.INDICATIVE


def _is_permanent(exc: Exception) -> bool:
    """True when retrying this error can never succeed.

    A 403 for an unsubscribed data feed, or a 422 for a malformed request, is
    settled — retrying just burns seconds of a five-minute cycle. Rate limits
    (429) and server errors (5xx) are the ones worth waiting out.
    """
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
            except Exception as exc:  # noqa: BLE001 — vendor SDK raises broadly
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
    """Facade over the three Alpaca clients the agent needs."""

    def __init__(self, credentials: Credentials):
        self._creds = credentials
        self.trading = TradingClient(
            credentials.api_key, credentials.secret_key, paper=credentials.paper
        )
        self.options_data = OptionHistoricalDataClient(
            credentials.api_key, credentials.secret_key
        )
        self.stock_data = StockHistoricalDataClient(
            credentials.api_key, credentials.secret_key
        )

    @classmethod
    def from_config(cls, config: AgentConfig) -> "Broker":
        if config.credentials is None:
            raise ValueError("Config was loaded without credentials; cannot build a Broker.")
        return cls(config.credentials)

    # ---- Account and clock -------------------------------------------------

    @retrying
    def get_account(self) -> Any:
        return self.trading.get_account()

    @retrying
    def get_clock(self) -> Any:
        return self.trading.get_clock()

    def is_market_open(self) -> bool:
        return bool(self.get_clock().is_open)

    @retrying
    def get_positions(self) -> list:
        return list(self.trading.get_all_positions())

    def get_option_positions(self) -> list:
        """Open positions that are options contracts, not shares.

        Alpaca marks these with asset_class ``us_option``; the symbol-length
        check is a fallback in case the SDK's enum representation shifts.
        """
        out = []
        for pos in self.get_positions():
            asset_class = str(getattr(pos, "asset_class", "")).lower()
            if "option" in asset_class or len(pos.symbol) > 10:
                out.append(pos)
        return out

    @retrying
    def get_orders(self, **kwargs) -> list:
        from alpaca.trading.requests import GetOrdersRequest

        return list(self.trading.get_orders(GetOrdersRequest(**kwargs))) if kwargs else list(
            self.trading.get_orders()
        )

    # ---- Market data -------------------------------------------------------

    @retrying
    def get_spot_price(self, ticker: str) -> float:
        """Latest traded price for the underlying."""
        result = self.stock_data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=ticker)
        )
        return float(result[ticker].price)

    @retrying
    def get_daily_bars(self, ticker: str, lookback_days: int = 400):
        """Daily OHLCV bars, returned as a pandas DataFrame.

        The lookback defaults past 252 trading days so realized-volatility
        percentiles have a full year of calendar data to draw on.
        """
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        bars = self.stock_data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=STOCK_FEED,
            )
        )
        return bars.df

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

        Returns a dict of ``{contract_symbol: OptionsSnapshot}``, where each
        snapshot carries ``.greeks``, ``.implied_volatility`` and
        ``.latest_quote``.
        """
        request = OptionChainRequest(
            underlying_symbol=ticker,
            type=ContractType.PUT,
            strike_price_gte=strike_low,
            strike_price_lte=strike_high,
            expiration_date_gte=expiry_from,
            expiration_date_lte=expiry_to,
            feed=OPTIONS_FEED,
        )
        return self.options_data.get_option_chain(request)

    @retrying
    def get_option_snapshots(self, symbols: list[str]) -> dict:
        """Snapshots for specific contract symbols (used when marking positions)."""
        from alpaca.data.requests import OptionSnapshotRequest

        if not symbols:
            return {}
        return self.options_data.get_option_snapshot(
            OptionSnapshotRequest(symbol_or_symbols=symbols, feed=OPTIONS_FEED)
        )

    # ---- Orders ------------------------------------------------------------

    def submit_order(self, order_request: Any) -> Any:
        """Submit an order. Deliberately not wrapped in ``retrying``.

        A failed write may or may not have reached the exchange. The executor
        handles this by pre-computing a deterministic client_order_id and
        checking for an existing order with that id before resubmitting.
        """
        return self.trading.submit_order(order_request)

    @retrying
    def get_order_by_client_id(self, client_order_id: str) -> Any | None:
        try:
            return self.trading.get_order_by_client_id(client_order_id)
        except Exception:  # noqa: BLE001 — a 404 here means "no such order", not a failure
            return None
