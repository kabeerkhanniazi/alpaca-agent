"""Options chain retrieval and candidate-strike filtering.

Turns a raw option chain into the short list of put strikes worth building a
spread around: the right distance from the money (by delta, not by percentage),
the right time to expiry, and liquid enough that the quoted credit is
achievable rather than theoretical.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..config import AgentConfig
from ..iv import expiry_from_symbol, strike_from_symbol
from ..state import OptionsAgentState

logger = logging.getLogger(__name__)


def _quote(snapshot) -> tuple[float, float]:
    """Bid and ask from a snapshot, defaulting to zero when absent."""
    quote = getattr(snapshot, "latest_quote", None)
    bid = float(getattr(quote, "bid_price", 0.0) or 0.0)
    ask = float(getattr(quote, "ask_price", 0.0) or 0.0)
    return bid, ask


def _greek(snapshot, name: str) -> float | None:
    greeks = getattr(snapshot, "greeks", None)
    value = getattr(greeks, name, None)
    return float(value) if value is not None else None


def calculate_options_opportunities(
    chain: dict,
    ticker: str,
    spot: float,
    config: AgentConfig,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Filter a chain down to tradeable short-put candidates.

    Filters applied, in order of how much they remove:

    1. **Delta window** — the configured range (default -0.20 to -0.15). This is
       the position-sizing decision; everything else is hygiene.
    2. **Days to expiry** — inside the configured 7-14 day band.
    3. **Two-sided quote** — a strike with no bid cannot be sold at any price.
    4. **Bid/ask width** — a spread wider than the configured fraction of the
       mid means the mid is fiction and the fill will be much worse.

    Rejections are counted rather than discarded silently, so a cycle that finds
    nothing can say *why* it found nothing.
    """
    as_of = as_of or datetime.now().date()
    delta_low, delta_high = config.delta_range
    max_width_pct = float(config.spread_builder.get("max_bid_ask_spread_pct", 0.35))

    candidates: list[dict[str, Any]] = []
    rejected = {"delta": 0, "dte": 0, "no_quote": 0, "wide_quote": 0, "no_greeks": 0}

    for symbol, snapshot in chain.items():
        delta = _greek(snapshot, "delta")
        if delta is None:
            rejected["no_greeks"] += 1
            continue

        # Short puts have negative delta; the configured range is [-0.20, -0.15].
        if not (delta_low <= delta <= delta_high):
            rejected["delta"] += 1
            continue

        expiry = expiry_from_symbol(symbol)
        strike = strike_from_symbol(symbol)
        if expiry is None or strike is None:
            rejected["no_greeks"] += 1
            continue

        dte = (expiry - as_of).days
        if not (config.min_dte <= dte <= config.max_dte):
            rejected["dte"] += 1
            continue

        bid, ask = _quote(snapshot)
        if bid <= 0 or ask <= 0:
            rejected["no_quote"] += 1
            continue

        mid = (bid + ask) / 2.0
        if mid <= 0 or (ask - bid) / mid > max_width_pct:
            rejected["wide_quote"] += 1
            continue

        candidates.append(
            {
                "symbol": symbol,
                "ticker": ticker,
                "strike": strike,
                "expiry": expiry.isoformat(),
                "dte": dte,
                "bid": round(bid, 4),
                "ask": round(ask, 4),
                "mid": round(mid, 4),
                "spread_pct": round((ask - bid) / mid, 4),
                "delta": round(delta, 4),
                "gamma": _greek(snapshot, "gamma"),
                "theta": _greek(snapshot, "theta"),
                "vega": _greek(snapshot, "vega"),
                "rho": _greek(snapshot, "rho"),
                "iv": getattr(snapshot, "implied_volatility", None),
                "credit": round(mid * 100.0, 2),
                "pct_otm": round((spot - strike) / spot * 100.0, 2),
            }
        )

    candidates.sort(key=lambda c: (c["expiry"], -c["strike"]))
    logger.info(
        "%s: %d candidates from %d contracts (rejected: %s)",
        ticker, len(candidates), len(chain), rejected,
    )
    return candidates


def index_chain(chain: dict, as_of: date | None = None) -> dict[str, dict[str, Any]]:
    """Index every put in the chain by ``"expiry|strike"``.

    The spread builder needs quotes for the *long* leg, which by construction
    sits below the delta window that produced the short-leg candidates and so
    never appears in that list. Indexing the whole chain once here saves a
    second network round trip per spread.
    """
    as_of = as_of or datetime.now().date()
    index: dict[str, dict[str, Any]] = {}
    for symbol, snapshot in chain.items():
        expiry = expiry_from_symbol(symbol)
        strike = strike_from_symbol(symbol)
        if expiry is None or strike is None:
            continue
        bid, ask = _quote(snapshot)
        index[f"{expiry.isoformat()}|{strike}"] = {
            "symbol": symbol,
            "strike": strike,
            "expiry": expiry.isoformat(),
            "dte": (expiry - as_of).days,
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "delta": _greek(snapshot, "delta"),
            "gamma": _greek(snapshot, "gamma"),
            "theta": _greek(snapshot, "theta"),
            "vega": _greek(snapshot, "vega"),
            "iv": getattr(snapshot, "implied_volatility", None),
        }
    return index


def make_options_calculator_node(broker, config: AgentConfig):
    """Build the options-calculator node bound to a broker and config."""

    def options_calculator_node(state: OptionsAgentState) -> dict[str, Any]:
        if state.get("halted"):
            return {}

        ticker = state["ticker"]
        spot = state["spot"]
        today = datetime.now().date()

        try:
            # Narrow strike band: at 15-20 delta on a 7-14 day put, the strike
            # sits a few percent below spot. The band is deliberately generous
            # on the downside so a high-volatility day still finds strikes.
            chain = broker.get_put_chain(
                ticker,
                strike_low=spot * 0.85,
                strike_high=spot * 1.00,
                expiry_from=today + timedelta(days=config.min_dte),
                expiry_to=today + timedelta(days=config.max_dte),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Chain fetch failed for %s: %s", ticker, exc)
            return {
                "halted": True,
                "halt_reason": f"Option chain unavailable for {ticker}: {exc}",
                "errors": [*state.get("errors", []), str(exc)],
            }

        candidates = calculate_options_opportunities(chain, ticker, spot, config, as_of=today)
        chain_index = index_chain(chain, as_of=today)

        if not candidates:
            return {
                "candidates": [],
                "chain_index": chain_index,
                "chain_size": len(chain),
                "halted": True,
                "halt_reason": (
                    f"No {config.delta_range[0]}..{config.delta_range[1]} delta puts with "
                    f"{config.min_dte}-{config.max_dte} DTE and a usable two-sided quote "
                    f"in {len(chain)} {ticker} contracts."
                ),
            }

        return {
            "candidates": candidates,
            "chain_index": chain_index,
            "chain_size": len(chain),
        }

    return options_calculator_node
