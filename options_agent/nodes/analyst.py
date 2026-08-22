"""Market-context node: spot, trend, and volatility regime.

This is where TradingAgents put a team of LLM analysts. It is deterministic
here by design — the strategy sells premium on index ETFs and sizes every
position off delta, so a narrative opinion about direction has nothing to
contribute and would only introduce run-to-run variance into an unattended
agent.

What it does produce is the context the rest of the pipeline actually consumes:
where spot is, whether implied volatility is rich or cheap, and a few classic
technicals that go into the journal so a human reviewing a trade after the fact
can see the conditions it was opened in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ..config import AgentConfig
from ..iv import REGIME_LOW, atm_implied_volatility, compute_iv_rank
from ..state import OptionsAgentState

logger = logging.getLogger(__name__)


def _rsi(closes: np.ndarray, period: int = 14) -> float | None:
    """Wilder's RSI on the final bar."""
    if closes.size < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, deltas.size):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float | None:
    """Average true range on the final bar."""
    if close.size < period + 1:
        return None
    prev_close = close[:-1]
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )
    return float(tr[-period:].mean())


def _sma(closes: np.ndarray, period: int) -> float | None:
    if closes.size < period:
        return None
    return float(closes[-period:].mean())


def build_market_context(ticker: str, spot: float, bars, chain: dict, config: AgentConfig) -> dict[str, Any]:
    """Assemble the market-context dict for one ticker."""
    closes = np.asarray(bars["close"].tolist(), dtype=float)
    highs = np.asarray(bars["high"].tolist(), dtype=float)
    lows = np.asarray(bars["low"].tolist(), dtype=float)

    atm_iv = atm_implied_volatility(chain, spot)
    iv_info = compute_iv_rank(
        ticker=ticker,
        atm_iv=atm_iv,
        closes=closes,
        history_path=config.paths["iv_history"],
        iv_config=config.iv,
    )

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    # A coarse trend label, journalled for context only. It never sizes or gates
    # a position — the risk gate is the only thing that can stop a trade.
    if sma20 and sma50:
        trend = "UPTREND" if sma20 > sma50 else "DOWNTREND"
    else:
        trend = "UNKNOWN"

    return {
        "ticker": ticker,
        "spot": round(spot, 4),
        "prev_close": round(float(closes[-1]), 4) if closes.size else None,
        "sma20": round(sma20, 4) if sma20 else None,
        "sma50": round(sma50, 4) if sma50 else None,
        "sma200": round(sma200, 4) if sma200 else None,
        "rsi14": round(_rsi(closes), 2) if _rsi(closes) is not None else None,
        "atr14": round(_atr(highs, lows, closes), 4) if _atr(highs, lows, closes) is not None else None,
        "trend": trend,
        "bars_available": int(closes.size),
        **iv_info,
    }


def make_analyst_node(broker, config: AgentConfig):
    """Build the analyst node bound to a broker and config."""

    def analyst_node(state: OptionsAgentState) -> dict[str, Any]:
        ticker = state["ticker"]
        try:
            spot = broker.get_spot_price(ticker)
            bars = broker.get_daily_bars(ticker)

            # A wide strike band here so the ATM IV sample is well-centred; the
            # options_calculator narrows to the tradeable delta window later.
            today = datetime.now().date()
            chain = broker.get_put_chain(
                ticker,
                strike_low=spot * 0.80,
                strike_high=spot * 1.05,
                expiry_from=today + timedelta(days=config.min_dte),
                expiry_to=today + timedelta(days=config.max_dte),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Analyst node failed for %s: %s", ticker, exc)
            return {
                "halted": True,
                "halt_reason": f"Market data unavailable for {ticker}: {exc}",
                "errors": [*state.get("errors", []), str(exc)],
            }

        context = build_market_context(ticker, spot, bars, chain, config)

        # Record today's ATM IV so the rv_proxy fallback can retire itself once
        # enough real samples exist.
        if context.get("atm_iv"):
            try:
                from ..iv import append_iv_sample

                append_iv_sample(config.paths["iv_history"], ticker, context["atm_iv"], spot)
            except OSError as exc:
                logger.warning("Could not append IV sample for %s: %s", ticker, exc)

        result: dict[str, Any] = {
            "spot": spot,
            "market_context": context,
            "iv_rank": context.get("iv_rank"),
            "iv_rank_source": context.get("iv_rank_source", "unavailable"),
            "regime": context.get("regime", "UNKNOWN"),
            "chain_size": len(chain),
        }

        # Optional regime gate. Off by default for the hackathon run — plan.md's
        # risk table calls for starting permissive and tightening as P&L builds.
        if config.iv.get("skip_when_regime_low") and context.get("regime") == REGIME_LOW:
            result["halted"] = True
            result["halt_reason"] = (
                f"IV rank {context.get('iv_rank')} is in the LOW_IV regime; premium is too "
                f"cheap to sell. Skipping {ticker} this cycle."
            )

        return result

    return analyst_node
