# Technical Stack & Architecture
## TradingAgents → Options Trading Agent

---

## Part 1: Technology Stack

### Core Dependencies

| Component | Package | Version | Purpose | Notes |
|---|---|---|---|---|
| **LLM Orchestration** | langgraph | 0.1+ | Multi-agent graph | Kept from TradingAgents; no changes needed |
| **LLM Client** | anthropic | 0.25+ | Claude API | For analyst layer only; NO LLM in risk gate |
| **Broker API** | alpaca-py | 0.44+ | Trading + market data | OptionHistoricalDataClient, TradingClient, OptionDataStream |
| **Data Handling** | pandas | 3.0+ | DataFrame operations | For position tracking, Greeks calculations |
| **HTTP** | requests | 2.34+ | REST calls | Fallback if MCP unavailable |
| **Config** | python-dotenv | 1.0+ | `.env` file loading | API keys, account config |
| **Async** | asyncio | stdlib | Non-blocking I/O | For streaming market data |
| **Logging** | python-logging | stdlib | Event logging | Structured logs to cron.log |
| **Testing** | pytest | 7.4+ | Unit tests | Especially for risk gate |
| **Web UI** | streamlit | 1.30+ | Dashboard | Live portfolio + trade journal view |
| **Deployment** | streamlit-cloud | free tier | Hosting | Public dashboard for judges |

### Optional / Conditional

| Component | Package | When |
|---|---|---|
| MCP Server | alpaca-mcp-server | If interactive analysis needed (not required for automation) |
| Real-time WebSocket | websockets | For live quote streaming (lower latency than REST polling) |
| Email alerts | smtplib | For critical error notifications (kill-switch triggered, etc.) |

---

## Part 2: File Structure

```
~/alpaca-agent/
├── .env                                 # API keys (not in git)
├── .env.example                         # Template (in git)
├── .gitignore                           # Includes .env, venv/, logs/
├── requirements.txt                     # pip dependencies
├── pyproject.toml                       # Project metadata
│
├── ta-base/                             # Cloned TradingAgents (base)
│   ├── tradingagents/
│   │   ├── graph/
│   │   │   └── trading_graph.py         # (MODIFIED) Remove researchers, wire new nodes
│   │   ├── nodes/
│   │   │   ├── analysts.py              # (KEPT) Sentiment, technicals, macro
│   │   │   ├── options_calculator.py    # (NEW) Greeks, IV, options chain
│   │   │   ├── spread_builder.py        # (NEW) Vertical spreads with bid/ask
│   │   │   ├── risk_gate.py             # (NEW) Deterministic 9-rule checker
│   │   │   ├── executor.py              # (NEW) Alpaca order placement
│   │   │   └── trade_journal.py         # (EXTENDED) JSONL logging
│   │   └── default_config.py            # (EXTENDED) Add options-specific config
│   └── ... (other TA files)
│
├── cron_runner.py                       # Entry point for scheduled cycles
├── streamlit_app.py                     # Dashboard UI
│
├── config/
│   ├── risk_config.json                 # 9 risk gate thresholds (loaded by risk_gate.py)
│   └── options_config.json              # Spread builder filters (min premium, etc.)
│
├── logs/
│   ├── cron.log                         # Cycle execution log
│   └── errors.log                       # Error log
│
├── tests/
│   ├── test_risk_gate.py                # Unit tests for 9 rules
│   ├── test_options_calculator.py       # Test Greeks calculations
│   ├── test_spread_builder.py           # Test spread construction
│   └── test_executor.py                 # Mock Alpaca order placement
│
└── docs/
    ├── plan.md                          # This file (high-level plan)
    ├── stack.md                         # This file (tech stack)
    └── README.md                        # For GitHub submission
```

---

## Part 3: Component Comparison Table (DETAILED)

| Component | TradingAgents (Original) | Your Hackathon (Options) | Status | Rationale |
|---|---|---|---|---|
| **Multi-agent framework** | ✅ LangGraph with nodes: analysts, researchers, trader, portfolio manager | ✅ LangGraph with nodes: analyst, options_calc, spread_builder, risk_gate, executor, portfolio_manager | **KEEP** | LangGraph orchestration is solid and proven. Removes coupling to task specifics. |
| **Node structure** | Sequential: analysts → researchers → trader | Optimized: analysts → options_calc → spread_builder → risk_gate → executor | **REFACTOR** | Options workflow is shorter (no debate rounds). Direct flow from risk gate to execution. |
| **Decision logging** | ✅ Persists to `~/.tradingagents/memory/trading_memory.md` | ✅ Extended to `~/.tradingagents/memory/options_trades.jsonl` (JSONL for structured queries) | **EXTEND** | JSON lines format allows programmatic reading for dashboard and analytics. Includes Greeks, fill prices, rejection reasons. |
| **Analyst team — Fundamental** | ✅ Analyzes earnings, balance sheet, P/E ratio | ⚠️ Kept but de-emphasized | **KEEP** | Still useful for macro outlook, but not primary signal for options sizing. |
| **Analyst team — Sentiment** | ✅ Aggregates StockTwits, Reddit, news headlines | ✅ Kept as IV regime input | **KEEP** | Feeds into "are options expensive or cheap" decision; informs whether to sell premium. |
| **Analyst team — Technical** | ✅ MACD, RSI, moving averages, breakout detection | ✅ Kept as directional hint (support/resistance) | **KEEP** | Helps identify candidate underlyings, but NOT used for position sizing. |
| **Analyst team — News/Macro** | ✅ Monitors global news, central bank decisions, earnings dates | ✅ Kept; critical for event risk detection | **KEEP** | Earnings dates + macro calendar → avoid high-gap risk or exploit IV crush. |
| **Debate / Researcher rounds** | ✅ Bullish researcher vs bearish researcher; argumentative debate | ❌ DELETED | **DELETE** | Directional debate is irrelevant for defined-risk spreads. Risk gate replaces conviction logic. |
| **Conviction scoring** | ✅ Computes "bullish conviction 0.0–1.0" | ❌ DELETED | **DELETE** | Replaced by quantitative rules (delta, premium, notional). No LLM scoring. |
| **Directional trade mapping** | ✅ bullish → buy calls, bearish → sell puts (long premium) | ❌ DELETED | **DELETE** | Options strategy is always sell premium (short volatility). No directional betting. |
| **Portfolio optimizer** | ✅ Computes portfolio Greeks, Sharpe ratio, drawdown | ✅ Kept; extended to track realized P&L | **EXTEND** | Now tracks: portfolio delta, theta, vega, daily P&L, win rate. |
| **Options Greeks calculation** | ❌ Does not exist | ✅ **NEW: options_calculator.py** Fetches from Alpaca OptionHistoricalDataClient; extracts delta, gamma, theta, vega | **ADD** | Non-negotiable. Every position sized off delta. Greeks inform risk gate checks. |
| **Implied Volatility (IV) rank** | ❌ Does not exist | ✅ **NEW: options_calculator.py** Computes IV rank (current IV as % of 252-day range); classifies regime (high/low) | **ADD** | Core signal: high IV (>60%) = sell premium; low IV (<30%) = don't sell. |
| **Options chain retrieval** | ❌ Does not exist | ✅ **NEW: options_calculator.py** Calls Alpaca OptionHistoricalDataClient; filters for delta 0.15–0.20, DTE 7–14 | **ADD** | Fetches live bid/ask for all candidate strikes. Critical dependency on Alpaca API. |
| **Spread construction** | ❌ Does not exist (TA trades individual calls/puts) | ✅ **NEW: spread_builder.py** Builds bull put spreads and bear call spreads; computes max loss, POP, net credit; ranks by quality | **ADD** | No naked shorts. Every position must be defined-risk (long wing capped loss). |
| **Risk gate — LLM-based** | ✅ Trader agent uses LLM to decide "should we trade this?" | ❌ DELETED | **DELETE** | LLM decisions are not repeatable or interpretable. Trades must be deterministic. |
| **Risk gate — Deterministic rules** | ❌ Does not exist | ✅ **NEW: risk_gate.py** 9 hard-coded checks: delta, notional, portfolio delta, premium, duplicates, DTE, daily loss, buying power | **ADD** | **Non-negotiable**. Prevents margin calls, account ruin, and over-leverage. Replaces LLM conviction. |
| **Rule: Delta cap** | ❌ | ✅ abs(delta) ≤ 0.20 | **ADD** | ~20% ITM probability = 80% win rate. Keeps distance from ATM. |
| **Rule: Notional cap** | ❌ | ✅ max loss per trade ≤ 2% of $100k account | **ADD** | Prevents single trade from wiping out a week's profit. Enforces position sizing. |
| **Rule: Portfolio delta** | ❌ | ✅ aggregate portfolio delta ≤ 0.10 per $100k | **ADD** | Prevents concentrated directional bets even if individual spreads are balanced. |
| **Rule: Min premium** | ❌ | ✅ credit collected ≥ $25/contract | **ADD** | Low premiums aren't worth execution risk. Filters out illiquid strikes. |
| **Rule: Duplicate strike prevention** | ❌ | ✅ No two open positions at same strike | **ADD** | Prevents over-concentration and simplifies management. |
| **Rule: DTE range** | ❌ | ✅ 7 ≤ DTE ≤ 14 | **ADD** | Avoids gamma explosion (<7 days) and slow theta decay (>14 days). |
| **Rule: Drawdown kill-switch** | ❌ | ✅ Daily loss > 5% → reject all new trades | **ADD** | Circuit breaker. Prevents cascading losses from compounding. |
| **Rule: Buying power reserve** | ❌ | ✅ Remaining buying power ≥ 20% after trade | **ADD** | Avoids margin call if positions go deep ITM. Always have cushion. |
| **Order placement — via API** | ✅ Places orders (but via stock API) | ✅ **NEW: executor.py** Places multi-leg options orders via Alpaca TradingClient | **ADD** | Alpaca's multi-leg orders ensure both legs fill together (no orphans). |
| **Order execution error handling** | ⚠️ Basic try/catch | ✅ **NEW: executor.py** Retries, logs error + reason, updates journal | **EXTEND** | Network timeouts common. Retry logic + detailed logging essential. |
| **Idempotent order placement** | ❌ | ✅ **NEW: executor.py** Uses client_order_id to prevent duplicates | **ADD** | If cron cycle crashes mid-order, retry doesn't double-place. |
| **Trade journal — storage format** | ✅ Markdown file (semi-structured) | ✅ **NEW: trade_journal.py** JSONL (JSON Lines, structured) | **EXTEND** | Markdown is human-readable but hard to parse. JSONL is machine-queryable. |
| **Trade journal — logged fields** | ✅ Decision + reasoning | ✅ EXTENDED: decision + Greeks + bid/ask + fill price + rejection reason + portfolio state | **EXTEND** | Richer context for post-trade analysis and auditing. Critical for judges. |
| **Trade exit logic** | ❌ Does not exist (TA does not close positions) | ✅ **NEW: executor.py** Close position if: (a) time decay > 80%, (b) max loss hit, (c) manual override | **ADD** | Spreads need lifecycle management. Can't hold to expiration without pin risk. |
| **Position tracking** | ✅ Reads from portfolio manager | ✅ **NEW: executor.py + trade_journal.py** Tracks open positions, Greeks, and realized P&L | **EXTEND** | Risk gate needs to know existing positions (for duplicate strike check). |
| **Portfolio Greeks aggregation** | ⚠️ Basic | ✅ **NEW: portfolio_manager.py** Sums delta, theta, vega across all positions | **EXTEND** | Portfolio-level Greeks inform risk gate checks. Per-position Greeks inform exit timing. |
| **Daily P&L calculation** | ⚠️ Estimated from prices | ✅ **NEW: portfolio_manager.py** Computes realized P&L (closed trades) + mark-to-market (open positions) | **EXTEND** | Daily P&L feeds the 5% kill-switch. Crucial for circuit breaker. |
| **Win rate tracking** | ❌ | ✅ **NEW: trade_journal.py** % of closed trades that were profitable | **ADD** | Key metric for judges. Target: ≥75% (most spreads expire worthless). |
| **Config system** | ✅ YAML/dict-based, loaded from default_config.py | ✅ **EXTENDED** Add options-specific knobs: max_delta, min_credit, max_notional, dte_range, kill_switch_pct | **EXTEND** | Makes thresholds tunable without code changes. |
| **Scheduled execution — cron** | ❌ | ✅ **NEW: cron_runner.py** Wraps graph; runs via `crontab` every 5 min during market hours | **ADD** | Agent must run unattended overnight. Cron is the simplest scheduler in WSL. |
| **Scheduled execution — monitoring** | ❌ | ✅ **NEW: cron_runner.py** Logs every cycle to cron.log; errors logged separately | **ADD** | Know when cycles fail, why, and for how long. Essential for debugging. |
| **Dashboard — real-time positions** | ❌ | ✅ **NEW: streamlit_app.py** Shows open positions, Greeks, delta, theta, vega | **ADD** | Judges need to see live portfolio state. Proves agent is running. |
| **Dashboard — trade history** | ❌ | ✅ **NEW: streamlit_app.py** Recent trades from journal (last 20), with fills + rejections | **ADD** | Judges audit decision quality. Rejections show risk gate working. |
| **Dashboard — performance metrics** | ❌ | ✅ **NEW: streamlit_app.py** Win rate, avg premium, total notional, realized P&L, cycles run | **ADD** | Single-screen summary of agent performance. Judges don't dig through logs. |
| **Dashboard — IV rank** | ❌ | ✅ **NEW: streamlit_app.py** Current IV rank (%) for SPY, QQQ, IWM | **ADD** | Shows market regime. Justifies why agent is/isn't trading. |
| **Dashboard — hosting** | ❌ | ✅ **NEW: streamlit_app.py** Deployed on Streamlit Cloud (free tier) | **ADD** | Must be publicly accessible for judges. Not localhost. |
| **MCP server integration** | ❌ | ⚠️ **OPTIONAL** If building interactive demo | **OPTIONAL** | Allows judges to query agent state via Claude. Not required for automation. |
| **Documentation — README** | ✅ Exists | ✅ **EXTENDED** Explain options strategy, risk rules, how to run | **EXTEND** | GitHub judges read README first. Must sell the story. |
| **Documentation — code comments** | ⚠️ Some | ✅ **NEW** Every risk rule commented with threshold + rationale | **EXTEND** | Code should be self-documenting. Especially risk gate. |
| **Testing — unit tests** | ⚠️ Some TA tests | ✅ **NEW: tests/** Full coverage for risk_gate.py (9 rules × 2 scenarios each), options_calculator, spread_builder | **ADD** | Risk gate is mission-critical. Test every rule + edge cases. |
| **Testing — integration tests** | ⚠️ | ✅ **NEW: tests/** End-to-end cycle: analyst → options_calc → spread_builder → risk_gate → executor | **ADD** | Catch integration bugs before live trading. |
| **Video demo** | ❌ | ✅ **NEW: 3 min video** Show analyst output, Greeks, risk gate approval/rejection, execution, dashboard | **ADD** | Judges won't read code. Video sells the vision. |
| **Social posts** | ❌ | ✅ **NEW: 5 posts on X/LinkedIn** Day-by-day building journey, risk gate concept, live trades | **ADD** | Extra credit component. Shows enthusiasm + communication. |
| **GitHub repo** | ✅ Original TA is public | ✅ **NEW fork** Your refactored version, public, `.env` in `.gitignore` | **ADD** | Required submission. Shows all code + decisions. |
| **Alpaca account** | ❌ | ✅ **NEW: Fresh paper account** Created Aug 28, judged account ID submitted Sep 4 | **ADD** | Reused accounts disqualify. Fresh account proves integrity. |

---

## Part 4: Execution Flow (Simplified)

### Cycle 1: Analyst & Options Calc

```
Input: Ticker = SPY, Date = 2026-08-28
  ↓
[Analyst Node] → Fetches:
  • Sentiment (StockTwits, Reddit, news)
  • Technicals (MACD, RSI, support/resistance)
  • Macro (upcoming earnings, Fed decisions)
  • Current spot price: $766.62
  ↓
Output: IV_rank = 72%, regime = HIGH_IV, sentiment = NEUTRAL
  ↓
[Options Calculator] → Fetches from Alpaca:
  • Implied Volatility at each strike
  • Option snapshots (bid, ask, delta, theta, vega, gamma)
  • Filters: puts, delta in [-0.20, -0.15], DTE in [7, 14]
  ↓
Output: 40 candidate strikes with full Greeks
```

### Cycle 2: Spread Builder & Risk Gate

```
Input: 40 candidate strikes
  ↓
[Spread Builder] → Constructs spreads:
  • For each pair of strikes: computes net credit, max loss, POP
  • Ranks by: high credit + high POP
  • Filters: max loss ≤ 2% of $100k ($2,000)
  ↓
Output: Top 10 spreads (bull put ranked by quality)
  ↓
[Risk Gate] → Checks EACH spread against 9 rules:
  • Rule 1 (delta ≤ 0.20): ✅ PASS
  • Rule 2 (max loss ≤ 2%): ✅ PASS
  • Rule 3 (portfolio delta ≤ 0.10): ✅ PASS
  • ... (other rules)
  ↓
Output: APPROVED (spread 1) / REJECTED (spread 2: max loss $2,100 > 2%)
```

### Cycle 3: Execution & Journaling

```
Input: Approved spread
  ↓
[Executor] → Places Alpaca order:
  • Constructs multi-leg order (sell 750 put @ $0.45, buy 745 put @ $0.05)
  • Limit price: mid-market ($0.40 net credit)
  • Submits via TradingClient
  ↓
Output: Order ID = xyz789, fill price = $0.465, filled time = 14:31:15
  ↓
[Trade Journal] → Appends to JSONL:
{
  "timestamp": "2026-08-28T14:31:15Z",
  "ticker": "SPY",
  "event": "spread_filled",
  "spread": {"type": "bull_put", "sell": 750, "buy": 745},
  "fill_price": 0.465,
  "credit": 46,
  "max_loss": 458,
  "order_id": "xyz789",
  "status": "filled"
}
```

### Cycle 4: Dashboard & Monitoring

```
Streamlit continuously reads:
  • Trade journal JSONL (latest 20 trades)
  • Alpaca API (current positions + Greeks)
  • Portfolio state (realized P&L, win rate)
  ↓
Dashboard displays:
  • Open positions: SPY 750/745 bull put (1 contract, delta -0.16, theta +0.02/day)
  • Recent trades: 8 entries (6 filled, 2 rejected)
  • Performance: 75% win rate, +$143 realized P&L, +$28 unrealized
  • Market regime: IV rank 72% (HIGH IV, good for selling)
```

---

## Part 5: Dependency Graph

```
alpaca-py                          ← Core broker integration
  ├── TradingClient (orders)
  ├── OptionHistoricalDataClient (Greeks)
  └── StockHistoricalDataClient (spot prices)

langgraph                          ← Graph orchestration
  ├── Graph (node + edge definitions)
  ├── StateGraph (shared state across nodes)
  └── BaseModel (typed inputs/outputs)

pandas                             ← Data wrangling
  ├── DataFrame (position tracking)
  └── Series (Greeks aggregation)

streamlit                          ← Dashboard
  ├── st.metric (portfolio KPIs)
  ├── st.dataframe (trade history)
  └── st.plotly_express (charts)

python-dotenv                      ← Config
  └── load_dotenv() (.env file)

pytest                             ← Testing
  ├── fixtures (mock Alpaca client)
  ├── parametrize (test all 9 risk rules)
  └── assert (expected outcomes)
```

---

## Part 6: Configuration

### Risk Gate Thresholds (in `config/risk_config.json`)

```json
{
  "delta": {
    "max_abs": 0.20,
    "rationale": "~20% ITM probability, keeps distance from ATM"
  },
  "notional": {
    "max_loss_pct": 0.02,
    "account_balance": 100000,
    "max_loss_usd": 2000
  },
  "portfolio": {
    "max_delta": 0.10,
    "max_theta": -0.30,
    "max_vega": -0.50
  },
  "premium": {
    "min_credit_usd": 25,
    "rationale": "Below $25 not worth execution slippage"
  },
  "dte": {
    "min_days": 7,
    "max_days": 14,
    "rationale": "Avoid gamma (<7) and slow decay (>14)"
  },
  "daily_loss": {
    "kill_switch_pct": 0.05,
    "rationale": "Stop loss at 5% daily decline"
  },
  "buying_power": {
    "min_reserve_pct": 0.20,
    "rationale": "Never use >80% of buying power"
  }
}
```

### Agent Config (in `config/options_config.json`)

```json
{
  "underlyings": ["SPY", "QQQ", "IWM"],
  "cycle_interval_seconds": 300,
  "market_hours": {
    "open_time": "09:30",
    "close_time": "16:00",
    "timezone": "US/Eastern"
  },
  "spread_builder": {
    "target_delta": -0.16,
    "delta_range": [-0.20, -0.15],
    "min_spread_width": 5,
    "max_spread_width": 10
  },
  "position_management": {
    "exit_if_profit_pct": 50,
    "exit_if_loss_pct": -50,
    "max_hold_days": 10
  }
}
```

---

## Part 7: Deployment Checklist

- [ ] Clone and refactor TradingAgents
- [ ] Install all dependencies: `pip install -r requirements.txt`
- [ ] Set API keys in `.env` (not committed to git)
- [ ] Create fresh paper trading account
- [ ] Test risk_gate.py with unit tests: `pytest tests/test_risk_gate.py`
- [ ] Test full cycle with single ticker: `python cron_runner.py --dry-run --ticker SPY`
- [ ] Set up cron: `crontab -e` (add every-5-min job)
- [ ] Verify cron log grows: `tail -f logs/cron.log`
- [ ] Deploy Streamlit: `streamlit run streamlit_app.py` + push to GitHub
- [ ] Record 3-minute video
- [ ] Create slide deck
- [ ] Draft 5 social posts
- [ ] Test with live trades for 1–2 hours (monitor closely)
- [ ] If all green, let run for 5.5 trading days (Aug 28 – Sep 4)
- [ ] Finalize GitHub repo: README, code comments, .env in .gitignore
- [ ] Submit: account ID + GitHub link + Streamlit URL + video + slides

---

## Part 8: Key Design Decisions Ratified

| Decision | Rationale |
|---|---|
| **Deterministic risk gate, no LLM** | Repeatable, auditable, fast. LLM convictions are not defensible. |
| **Vertical spreads, no naked shorts** | Margin is bounded; no margin calls on gap moves. Survivable during flash crashes. |
| **Sell premium (short volatility)** | Positive theta in 5-day window. Win rate built-in if sizing is correct. |
| **Delta 0.15–0.20** | ~15–20% ITM probability = 80–85% win rate. Conservative enough to survive edge moves. |
| **Max 2% risk per trade** | Recoverable if 1–2 trades blow up. Prevents single trade from wrecking week. |
| **JSONL journal** | Machine-queryable. Powers dashboard. Human-readable one line at a time. |
| **Cron + Python** | Simple, reliable, runs unattended. No need for Docker or cloud orchestration. |
| **Streamlit dashboard** | Free tier. Judges get live view. No complex frontend dev needed. |
| **Kill-switch at 5%** | Psychological and mathematical threshold. Beyond that, compounding loss is severe. |
| **Scan 3 underlyings** | SPY (large cap), QQQ (tech), IWM (small cap). Diversifies wins without overwhelming complexity. |

---

End of Stack
