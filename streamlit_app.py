"""Live dashboard for the options agent.

Reads two sources: the Alpaca account for current positions and Greeks, and the
JSONL trade journal for history. Deliberately read-only — nothing here can place
or cancel an order, so a dashboard left open in a browser tab can never move the
book.

The rejection panel is the one worth looking at. Anyone can build a bot that
shows its winners; showing the trades the risk gate refused, and the exact rule
and numbers behind each refusal, is what demonstrates the gate is real.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_agent.config import ConfigError, load_config  # noqa: E402
from options_agent.nodes.trade_journal import TradeJournal  # noqa: E402

st.set_page_config(
    page_title="Alpaca Options Agent",
    page_icon="📉",
    layout="wide",
)

REFRESH_SECONDS = 60


# --------------------------------------------------------------- helpers

@st.cache_resource
def get_config():
    return load_config()


@st.cache_data(ttl=30)
def get_account_snapshot():
    """Account and positions, refreshed at most every 30 seconds."""
    from options_agent.broker import Broker
    from options_agent.nodes.position_manager import build_portfolio_state

    config = get_config()
    broker = Broker.from_config(config)

    spots = {}
    for ticker in config.underlyings:
        try:
            spots[ticker] = broker.get_spot_price(ticker)
        except Exception:  # noqa: BLE001 — a missing quote must not blank the page
            spots[ticker] = None

    portfolio = build_portfolio_state(broker, config, {k: v for k, v in spots.items() if v})
    return portfolio, spots


@st.cache_data(ttl=30)
def get_market_state():
    """Current IV rank and regime per underlying, for the market panel."""
    from datetime import timedelta

    from options_agent.broker import Broker
    from options_agent.iv import atm_implied_volatility, compute_iv_rank

    config = get_config()
    broker = Broker.from_config(config)
    rows = []

    for ticker in config.underlyings:
        try:
            spot = broker.get_spot_price(ticker)
            today = datetime.now().date()
            chain = broker.get_put_chain(
                ticker,
                strike_low=spot * 0.80,
                strike_high=spot * 1.05,
                expiry_from=today + timedelta(days=config.min_dte),
                expiry_to=today + timedelta(days=config.max_dte),
            )
            atm_iv = atm_implied_volatility(chain, spot)
            closes = broker.get_daily_bars(ticker)["close"].tolist()
            info = compute_iv_rank(ticker, atm_iv, closes, config.paths["iv_history"], config.iv)
            rows.append({
                "Ticker": ticker,
                "Spot": spot,
                "ATM IV": info["atm_iv"],
                "IV Rank": info["iv_rank"],
                "Regime": info["regime"],
                "Source": info["iv_rank_source"],
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "Ticker": ticker, "Spot": None, "ATM IV": None,
                "IV Rank": None, "Regime": "ERROR", "Source": str(exc)[:60],
            })
    return rows


def money(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def render_error(exc: Exception) -> None:
    st.error(f"Could not reach Alpaca: {exc}")
    st.caption(
        "Check that `.env` holds a valid ALPACA_API_KEY and ALPACA_SECRET_KEY, "
        "and that the account has options trading enabled."
    )


# ----------------------------------------------------------------- header

st.title("📉 Autonomous Options Agent")
st.caption(
    "Defined-risk credit spreads on SPY, QQQ and IWM. Every position passes a "
    "nine-rule deterministic risk gate — no language model anywhere in the decision path."
)

try:
    config = get_config()
except ConfigError as exc:
    st.error(f"Configuration problem: {exc}")
    st.stop()

journal = TradeJournal(config.paths["journal"])
stats = journal.compute_stats()

with st.sidebar:
    st.header("Agent")
    st.metric("Underlyings", ", ".join(config.underlyings))
    st.metric("Delta window", f"{config.delta_range[0]} to {config.delta_range[1]}")
    st.metric("DTE window", f"{config.min_dte}–{config.max_dte} days")
    st.metric("Max risk / trade", f"{config.max_loss_pct:.0%} of NAV")
    st.metric("Kill-switch", f"−{config.kill_switch_pct:.0%} daily")
    st.divider()
    st.caption(f"Journal: `{config.paths['journal']}`")
    st.caption(f"Refreshed {datetime.now().strftime('%H:%M:%S')}")
    if st.button("Refresh now", width="stretch"):
        st.cache_data.clear()
        st.rerun()

# ------------------------------------------------------------- portfolio

st.subheader("Portfolio")

try:
    portfolio, spots = get_account_snapshot()
except Exception as exc:  # noqa: BLE001
    render_error(exc)
    portfolio, spots = None, {}

if portfolio:
    cols = st.columns(5)
    cols[0].metric("Net liquidation", money(portfolio["nav"]))
    cols[1].metric(
        "Daily P&L",
        money(portfolio["daily_pnl"]),
        delta=f"{portfolio['daily_pnl_pct']:+.2%}",
    )
    cols[2].metric("Unrealized", money(portfolio["unrealized_pnl"]))
    cols[3].metric("Open positions", portfolio["position_count"])
    cols[4].metric("Buying power", money(portfolio["buying_power"], 0))

    greeks = st.columns(4)
    delta_pct = portfolio["net_delta_dollars"] / portfolio["nav"] if portfolio["nav"] else 0
    greeks[0].metric(
        "Net delta",
        money(portfolio["net_delta_dollars"], 0),
        delta=f"{delta_pct:.1%} of NAV",
        delta_color="off",
    )
    greeks[1].metric("Theta / day", money(portfolio["portfolio_theta"]))
    greeks[2].metric("Vega", money(portfolio["portfolio_vega"]))
    greeks[3].metric("Gamma", f"{portfolio['portfolio_gamma']:.4f}")

    # How close is the book to the limits that would stop it trading?
    st.caption("Headroom against the gate's portfolio-level limits")
    bars = st.columns(2)
    delta_limit = portfolio["nav"] * config.max_portfolio_delta_pct
    bars[0].progress(
        min(1.0, abs(portfolio["net_delta_dollars"]) / delta_limit) if delta_limit else 0.0,
        text=f"Portfolio delta: {money(abs(portfolio['net_delta_dollars']), 0)} of {money(delta_limit, 0)} (Rule 3)",
    )
    loss_used = -min(0.0, portfolio["daily_pnl_pct"])
    bars[1].progress(
        min(1.0, loss_used / config.kill_switch_pct) if config.kill_switch_pct else 0.0,
        text=f"Daily drawdown: {loss_used:.2%} of {config.kill_switch_pct:.0%} kill-switch (Rule 8)",
    )

    if portfolio["open_positions"]:
        frame = pd.DataFrame(portfolio["open_positions"])
        display = frame[[
            "symbol", "underlying", "strike", "expiry", "dte", "contracts",
            "delta", "theta", "vega", "avg_entry_price", "current_price", "unrealized_pl",
        ]].rename(columns={
            "symbol": "Contract", "underlying": "Underlying", "strike": "Strike",
            "expiry": "Expiry", "dte": "DTE", "contracts": "Qty", "delta": "Delta",
            "theta": "Theta", "vega": "Vega", "avg_entry_price": "Entry",
            "current_price": "Mark", "unrealized_pl": "Unrealized",
        })
        st.dataframe(display, width="stretch", hide_index=True)
    else:
        st.info("No open positions. The agent opens at most one new spread per cycle.")

st.divider()

# ------------------------------------------------------------ performance

st.subheader("Performance")

perf = st.columns(6)
perf[0].metric(
    "Win rate",
    f"{stats['win_rate']:.0f}%" if stats["win_rate"] is not None else "—",
    help="Closed positions only. Open positions have no realized outcome.",
)
perf[1].metric("Realized P&L", money(stats["realized_pnl"]))
perf[2].metric("Positions closed", stats["positions_closed"])
perf[3].metric("Orders filled", stats["orders_filled"])
perf[4].metric("Avg credit", money(stats["avg_credit"]))
perf[5].metric(
    "Gate approval rate",
    f"{stats['approval_rate']:.0f}%" if stats["approval_rate"] is not None else "—",
    help=f"{stats['approvals']} approved / {stats['rejections']} rejected.",
)

if stats["rejections_by_rule"]:
    st.caption("Which rule is doing the work — rejections by rule")
    rejects = pd.DataFrame(
        sorted(stats["rejections_by_rule"].items(), key=lambda kv: -kv[1]),
        columns=["Rule", "Rejections"],
    )
    st.bar_chart(rejects.set_index("Rule"), height=200)

st.divider()

# ---------------------------------------------------------- market state

st.subheader("Market")

try:
    market = get_market_state()
    market_frame = pd.DataFrame(market)
    st.dataframe(
        market_frame.style.format({"Spot": "{:.2f}", "ATM IV": "{:.2%}", "IV Rank": "{:.1f}"}, na_rep="—"),
        width="stretch",
        hide_index=True,
    )
    if any(row["Source"] == "rv_proxy" for row in market):
        st.caption(
            "IV rank marked `rv_proxy` is a cold-start estimate: today's ATM implied "
            "volatility ranked against the past year of realized volatility. It switches "
            "to a true IV percentile once 20 sessions of history have accumulated."
        )
except Exception as exc:  # noqa: BLE001
    st.warning(f"Market data unavailable: {exc}")

st.divider()

# --------------------------------------------------------------- journal

st.subheader("Trade journal")

tabs = st.tabs(["Recent activity", "Rejections", "Fills & exits", "Raw"])
events = journal.load_recent(limit=200)

with tabs[0]:
    if not events:
        st.info("Nothing journalled yet. Run `python cron_runner.py --dry-run --force`.")
    else:
        rows = []
        for event in events[:40]:
            rows.append({
                "Time": event.get("timestamp", "")[:19].replace("T", " "),
                "Type": event.get("event_type", ""),
                "Ticker": event.get("ticker", ""),
                "Detail": (
                    event.get("reason")
                    or event.get("exit_reason")
                    or (event.get("execution") or {}).get("message")
                    or event.get("error", "")
                )[:160],
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

with tabs[1]:
    rejections = [e for e in events if e.get("event_type") == "trade_rejected"]
    if not rejections:
        st.info("No rejections recorded yet.")
    else:
        st.caption(
            "Every refusal, with the rule that caused it and the numbers behind it. "
            "This is the risk gate working."
        )
        for event in rejections[:15]:
            spread = event.get("spread") or {}
            label = (
                f"{event.get('ticker', '?')} "
                f"{spread.get('sell_strike', '?')}/{spread.get('buy_strike', '?')} — "
                f"{', '.join(event.get('failing_rules', [])) or 'no spread'}"
            )
            with st.expander(f"{event.get('timestamp', '')[:19].replace('T', ' ')} · {label}"):
                for check in event.get("checks", []):
                    icon = "✅" if check.get("passed") else "❌"
                    st.markdown(f"{icon} **{check.get('rule')}** — {check.get('detail')}")

with tabs[2]:
    trades = [
        e for e in events
        if e.get("event_type") in ("order_filled", "order_submitted", "position_exit", "order_failed")
    ]
    if not trades:
        st.info("No orders or exits recorded yet.")
    else:
        rows = []
        for event in trades:
            spread = event.get("spread") or {}
            execution = event.get("execution") or {}
            rows.append({
                "Time": event.get("timestamp", "")[:19].replace("T", " "),
                "Type": event.get("event_type"),
                "Ticker": event.get("ticker"),
                "Strikes": (
                    f"{spread.get('sell_strike')}/{spread.get('buy_strike')}"
                    if spread.get("sell_strike") else execution.get("symbol", "")
                ),
                "Qty": execution.get("contracts"),
                "Limit": execution.get("limit_price"),
                "Credit": spread.get("net_credit"),
                "Max loss": spread.get("max_loss"),
                "Status": execution.get("status"),
                "Realized": execution.get("realized_pnl"),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

with tabs[3]:
    st.caption(f"Last 25 raw journal entries from `{config.paths['journal'].name}`")
    st.json(events[:25], expanded=False)

st.caption(
    f"Auto-refreshes every {REFRESH_SECONDS}s · "
    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
)
