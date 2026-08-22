# TradingAgents Refactoring Plan
## Building an Autonomous Options Trading Agent for Alpaca

**Project:** lablab.ai x Alpaca Hackathon — AI Trading Agents  
**Timeline:** Aug 22–27, 2026 (6 days)  
**Goal:** Transform TradingAgents into a defined-risk options trading bot with risk gates, Greeks-aware sizing, and autonomous execution  
**Constraint:** No naked shorts. Every position must be a vertical credit spread (bull put or bear call).

---

## Phase 1: Foundation & Analysis (Day 1)

### 1.1 — Clone and understand TradingAgents structure

**Task:** Clone the repo and map the existing graph

```bash
cd ~/alpaca-agent
git clone https://github.com/TauricResearch/TradingAgents.git ta-base
cd ta-base
pip install -e .
```

**Deliverable:** Mental map of:
- Entry point: `main.py` and `cli/main.py`
- Core orchestrator: `tradingagents/graph/trading_graph.py` (the LangGraph)
- Node definitions: Where are analysts, researchers, traders defined?
- Data flow: How do decisions flow from analyst → researcher → trader → portfolio manager?
- Config system: `tradingagents/default_config.py`
- Output: Where does it log decisions?

**Claude Code prompt:**
```
Walk me through the entire TradingAgents graph step by step:
1. How does a run start? (cli/main.py entry point)
2. What are the nodes in tradingagents/graph/trading_graph.py?
3. What does each node do? (analysts, researchers, trader, portfolio manager)
4. How do they connect? (graph edges)
5. What is the final output format of a decision?
6. Where are decisions persisted? (memory/logging)
```

**Success criteria:**
- Can explain the flow in your own words
- Can identify which nodes are opinion-based vs data-based
- Know where the decision log is written

---

### 1.2 — Identify what to delete

**Task:** Flag every component tied to directional trading (bullish/bearish logic)

**Components to hunt down:**
- Researcher nodes (debate rounds)
- Bullish researcher, bearish researcher
- Conviction score logic
- Direction-to-action mapping (bullish → buy long, bearish → sell short)
- Any prompt that says "should we be bullish or bearish"
- Any output that is a direction recommendation

**Deliverable:** A list of file paths + line ranges to delete

**Claude Code prompt:**
```
Find all code that:
1. Implements debate/researcher nodes
2. Computes bullish/bearish conviction scores
3. Maps conviction to buy/sell decisions
4. Generates directional recommendations

List files and line numbers. Explain what each does.
```

**Success criteria:**
- Exact file paths identified
- Can explain why each is directional-specific and thus should be removed

---

### 1.3 — Identify what to keep and extend

**Task:** Flag components that serve options trading

**Components to keep (with extensions):**
- Market analyst (pull sentiment, technicals, macro) → **Extend:** add IV rank calculation
- News analyst → **Extend:** still useful for event detection
- Decision journal → **Extend:** add Greeks, bid/ask, premium collected
- Config system → **Extend:** add options-specific parameters (max delta, min premium, etc.)
- LangGraph orchestration → **Keep:** this is the scaffolding

**Deliverable:** List of components to keep + what needs extending

**Claude Code prompt:**
```
For each of these components, tell me what stays and what needs to change:
1. The analyst team (fundamental, sentiment, technical, news)
2. The decision logging system
3. The config system
4. The LangGraph orchestration itself

What fields/outputs need to be added?
```

---

## Phase 2: Architecture & Design (Day 1, end of day)

### 2.1 — Design the new graph

**Task:** Sketch the new LangGraph nodes and their connections

**New architecture:**

```
Market Analyst Node
  ├─ Input: ticker, date, underlyings
  ├─ Output: IV rank, sentiment, macro regime, technicals
  └─ Extend: fetch IV percentile, classify regime (high/low IV)

IV & Greeks Calculator Node (NEW)
  ├─ Input: ticker, IV rank, spot price
  ├─ Output: options chains filtered by delta 0.15–0.20
  └─ Fetch: Alpaca option snapshots with Greeks

Spread Builder Node (NEW)
  ├─ Input: candidate strikes from above, portfolio delta
  ├─ Output: bull put spreads + bear call spreads with bid/ask/premium/max loss
  └─ Logic: construct vertical spreads, not naked shorts

Risk Gate Node (NEW, DETERMINISTIC, NO LLM)
  ├─ Input: proposed spread, current portfolio, account balance
  ├─ Output: APPROVE / REJECT + reason
  ├─ Hard-coded checks:
  │   ├─ Delta: abs(delta per leg) ≤ 0.20
  │   ├─ Notional: strike width × 100 × contracts ≤ 2% of account
  │   ├─ Portfolio Delta: aggregate delta stays within bounds
  │   ├─ Min premium: credit collected ≥ $25/contract
  │   ├─ Existing positions: don't double-dip on same strike
  │   └─ Drawdown kill-switch: if daily loss > 5%, reject all new trades
  └─ NO LLM INVOLVED

Execution Node (NEW)
  ├─ Input: approved spread
  ├─ Output: order placement confirmation or error
  ├─ Via Alpaca Trading API:
  │   ├─ Submit limit order for the spread
  │   ├─ Track by client order ID (idempotent)
  │   └─ Log to trade journal

Trade Journal Node (EXTENDED)
  ├─ Input: all decisions + outcomes
  ├─ Output: persistent JSON log
  ├─ Fields:
  │   ├─ timestamp, ticker, strategy type
  │   ├─ entry delta, entry IV, entry premium
  │   ├─ spread width, max loss, approval reason
  │   ├─ order ID, fill price, fill time
  │   ├─ exit time, exit delta, realized P&L
  │   └─ notes (why rejected, why exited, etc.)
  └─ File: ~/.tradingagents/memory/options_trading.jsonl

Portfolio Manager Node (EXTENDED)
  ├─ Input: current portfolio state, risk metrics
  ├─ Output: portfolio summary (Greeks, P&L, win rate)
  └─ Extended: compute portfolio delta, total notional, realized P&L
```

**Deliverable:** Graph diagram (text or ascii) + node descriptions

**Claude Code prompt:**
```
Design the new graph for options trading. I'll give you the nodes:

1. Market Analyst (keep, extend for IV)
2. IV & Greeks Calculator (new)
3. Spread Builder (new)
4. Risk Gate (new, deterministic only)
5. Execution (new)
6. Trade Journal (extend from existing logging)
7. Portfolio Manager (extend)

For each node:
- What's the input?
- What's the output?
- Is it LLM-driven or deterministic?
- What file should it live in?
- What tests should verify it?

Draw the graph connections (which node → which node).
```

**Success criteria:**
- All 7 nodes defined clearly
- No LLM anywhere in the risk gate (non-negotiable)
- Graph flow is clear (no cycles)
- Each node has a defined responsibility

---

### 2.2 — Define the risk gate rules (written in English first)

**Task:** Before coding, state every rule the risk gate enforces

**Rules (all must be True to approve a trade):**

1. **Delta Rule:** abs(delta per leg) ≤ 0.20 (roughly 20% ITM probability)
2. **Notional Rule:** (strike width × 100 × contracts) ≤ 2% of account NAV
   - Account NAV = $100,000
   - 2% = $2,000 max loss
   - Enforces: Contracts = floor(2000 / max_loss_per_spread)
3. **Portfolio Delta Rule:** aggregate portfolio delta ≤ 0.10 (in absolute terms per $100k)
   - Prevents concentrated directional bets
4. **Min Premium Rule:** credit collected ≥ $25 per contract
   - Ensures trade is worth the execution risk
5. **Duplicate Strike Rule:** no existing open position at the same strike
   - Prevents over-concentration
6. **Min DTE Rule:** days to expiration ≥ 7
   - Avoids gamma explosion (last 3 days)
7. **Max DTE Rule:** days to expiration ≤ 14
   - Avoids slow theta decay
8. **Drawdown Kill-Switch:** if today's portfolio loss > 5%, reject ALL new trades
   - Circuit breaker to prevent compounding losses
9. **Buying Power Rule:** remaining buying power (after this trade) > 20%
   - Never use more than 80% of buying power

**If ANY rule fails → REJECT with logged reason**

**Deliverable:** The 9 rules written in pseudocode

**Claude Code prompt:**
```
I'm building a risk gate — a hard-coded deterministic check before any trade is approved.

Here are the 9 rules (all must pass):
1. Delta rule: abs(delta) ≤ 0.20
2. Notional rule: max loss ≤ 2% of $100k account
3. Portfolio delta: agg delta ≤ 0.10
4. Min premium: credit ≥ $25/contract
5. Duplicate strike: no existing position at this strike
6. Min DTE: ≥ 7 days
7. Max DTE: ≤ 14 days
8. Drawdown kill-switch: daily loss > 5% → reject all
9. Buying power: remaining > 20%

For each rule:
- Write it in pseudocode
- Explain what it prevents
- Write a test case (pass + fail example)

Then design the RiskGate class interface.
```

**Success criteria:**
- All 9 rules clear and testable
- Pseudocode is implementation-ready
- Test cases show edge cases

---

## Phase 3: Implementation (Days 2–5)

### 3.1 — Delete directional nodes (Day 2, morning)

**Task:** Remove researcher nodes and directional logic

**Steps:**
1. Delete researcher node definitions from `trading_graph.py`
2. Remove debate rounds from graph edges
3. Delete conviction scoring logic
4. Remove bullish/bearish output formatting
5. Update graph to flow: analyst → risk gate (NOT through debate)

**Deliverable:** `trading_graph.py` compiles and runs but without researchers

**Claude Code prompt:**
```
In tradingagents/graph/trading_graph.py:
1. Find and delete the researcher node definitions
2. Remove edges from analysts → researchers
3. Remove conviction score calculation
4. Remove debate round logic
5. Update the graph so analysts feed directly into the risk gate

After deletion, the graph should still construct without errors.
```

**Success criteria:**
- Graph compiles
- No reference to "bullish/bearish" in output
- No researcher nodes in graph

---

### 3.2 — Build the IV & Greeks calculator (Day 2–3)

**Task:** Create a new node that fetches options data and computes Greeks

**File:** `tradingagents/nodes/options_calculator.py`

**Responsibilities:**
1. Take ticker + IV rank from analyst
2. Call Alpaca OptionHistoricalDataClient to get option snapshots
3. Filter for puts with delta −0.15 to −0.20 (roughly), 7–14 DTE
4. Extract bid, ask, mid, IV, delta, gamma from snapshots
5. Return candidate strikes with all Greeks

**Interface:**

```python
def calculate_options_opportunities(
    ticker: str,
    iv_rank: float,
    current_price: float,
    analyst_output: dict
) -> dict:
    """
    Returns:
    {
        "ticker": "SPY",
        "iv_rank": 65.0,
        "candidates": [
            {
                "strike": 750,
                "bid": 0.45,
                "ask": 0.47,
                "mid": 0.46,
                "delta": -0.16,
                "gamma": 0.012,
                "theta": 0.03,
                "vega": -0.02,
                "iv": 0.18,
                "dte": 10,
                "credit": 46  # mid × 100
            },
            ...
        ]
    }
    """
```

**Deliverable:** Node integrated into graph, tested against live Alpaca data

**Claude Code prompt:**
```
Build tradingagents/nodes/options_calculator.py:

1. Create calculate_options_opportunities() that:
   - Takes ticker, IV rank, current price
   - Calls Alpaca OptionHistoricalDataClient
   - Filters for puts: delta in [-0.20, -0.15], DTE in [7, 14]
   - Returns dict with candidate strikes + Greeks

2. Integration:
   - Add this node to the LangGraph
   - Input: analyst output (IV rank, regime)
   - Output: options chain with Greeks

3. Testing:
   - Test with SPY (should return ~20–40 candidate strikes)
   - Verify delta values are in expected range
   - Verify bid/ask are present

Use alpaca-py SDK (already installed).
```

**Success criteria:**
- Returns real options data from Alpaca
- Filters work (delta, DTE correct)
- Greeks are populated

---

### 3.3 — Build the spread builder (Day 3)

**Task:** Create spreads from candidate strikes

**File:** `tradingagents/nodes/spread_builder.py`

**Responsibilities:**
1. Take candidate strikes from options calculator
2. For each strike pair (sell strike, buy strike), compute:
   - Net credit = (sell bid − buy ask) × 100
   - Max loss = (spread width − net credit) × 100
   - Probability of profit = 100% − (sell delta magnitude × 100)
3. Return ranked spreads (sorted by credit or POP)
4. Filter out spreads with max loss > 2% of account ($2,000)

**Interface:**

```python
def build_spreads(
    options_data: dict,
    account_balance: float = 100000
) -> dict:
    """
    Returns:
    {
        "ticker": "SPY",
        "spreads": [
            {
                "type": "bull_put",
                "sell_strike": 750,
                "buy_strike": 745,
                "sell_delta": -0.16,
                "buy_delta": -0.06,
                "net_credit": 42,
                "spread_width": 5,
                "max_loss": 458,  # width × 100 − credit
                "prob_of_profit": 84,
                "max_contracts": 4,  # floor(2000 / 458)
                "rating": "A"  # high credit, high POP
            },
            ...
        ]
    }
    """
```

**Deliverable:** Spreads ranked by quality, filtered by risk

**Claude Code prompt:**
```
Build tradingagents/nodes/spread_builder.py:

1. Create build_spreads() that:
   - Takes options chain from options_calculator
   - For each pair of strikes (wider spread width = safer):
     - Computes net credit (sell bid − buy ask)
     - Computes max loss (width − credit) × 100
     - Computes POP = 100% − abs(sell_delta × 100)
   - Filters: max loss ≤ 2% of account ($2,000)
   - Ranks by: high credit + high POP
   - Returns top 10 spreads

2. Integration:
   - Add to graph: options_calculator → spread_builder
   - Output: list of viable spreads sorted by rating

3. Testing:
   - Test with sample options chain
   - Verify spreads are sorted correctly
   - Verify max loss calculations are accurate
```

**Success criteria:**
- Spreads are mathematically correct
- Max loss ≤ $2,000 per spread
- Top spreads have credit ≥ $35–50

---

### 3.4 — Build the risk gate (Day 4, all day)

**Task:** Implement all 9 rules as hard-coded logic

**File:** `tradingagents/nodes/risk_gate.py`

**Interface:**

```python
def risk_gate_check(
    proposed_spread: dict,
    portfolio_state: dict,
    account_balance: float,
    daily_loss: float
) -> tuple:
    """
    Returns: (approved: bool, reason: str, metadata: dict)
    
    Example:
    (True, "All checks passed. Approved for execution.", {...})
    (False, "Daily loss > 5%. Kill-switch active.", {...})
    """
```

**Implementation checklist:**

- [ ] Rule 1: Delta check
- [ ] Rule 2: Notional check (max loss ≤ 2%)
- [ ] Rule 3: Portfolio delta check
- [ ] Rule 4: Min premium check
- [ ] Rule 5: Duplicate strike check (query existing positions)
- [ ] Rule 6: Min DTE check
- [ ] Rule 7: Max DTE check
- [ ] Rule 8: Daily drawdown kill-switch
- [ ] Rule 9: Buying power reserve check
- [ ] Logging: every rejection must log the failing rule + values
- [ ] Unit tests: test each rule in isolation + integration

**Deliverable:** Fully tested risk gate, no LLM involved

**Claude Code prompt:**
```
Build tradingagents/nodes/risk_gate.py with all 9 rules:

1. Rule 1 — Delta: abs(delta) ≤ 0.20
2. Rule 2 — Notional: max loss ≤ 2% of $100k
3. Rule 3 — Portfolio delta: agg ≤ 0.10
4. Rule 4 — Min premium: ≥ $25/contract
5. Rule 5 — No duplicate strikes: check existing positions
6. Rule 6 — Min DTE: ≥ 7
7. Rule 7 — Max DTE: ≤ 14
8. Rule 8 — Kill-switch: daily loss > 5% → reject all
9. Rule 9 — Buying power: remaining > 20%

For each rule:
- Implement the check
- If it fails, reject with clear reason
- Log the decision (pass/fail + values)

Also write unit tests for each rule (pass + fail cases).

Critical: NO LLM in this node. Pure deterministic logic.
```

**Success criteria:**
- All 9 checks functional
- Rejections include reason + values
- Unit tests pass
- No dependencies on LLM

---

### 3.5 — Build the execution layer (Day 4, afternoon)

**Task:** Place actual orders via Alpaca API

**File:** `tradingagents/nodes/executor.py`

**Responsibilities:**
1. Take approved spread from risk gate
2. Construct multi-leg order for Alpaca
3. Submit with limit prices (mid-market or slightly better)
4. Track by client order ID (idempotent)
5. Log fill confirmation to trade journal

**Interface:**

```python
def execute_spread(
    spread: dict,
    client_order_id: str
) -> tuple:
    """
    Returns: (success: bool, order_id: str, fill_price: float, error: str)
    
    Raises: alpaca_py exceptions (handled gracefully)
    """
```

**Deliverable:** Orders placed and confirmed on paper trading account

**Claude Code prompt:**
```
Build tradingagents/nodes/executor.py:

1. Create execute_spread() that:
   - Constructs a multi-leg limit order for bull put spread
   - Submits via Alpaca TradingClient
   - Uses client order ID for idempotency
   - Logs success/failure to trade journal
   - Handles errors (insufficient buying power, invalid symbols, etc.)

2. Use alpaca-py's PlaceOrderRequest with OrderLeg[]

3. Testing:
   - Place a test spread order on the paper account
   - Verify order appears in Alpaca dashboard
   - Verify journal logs the trade

Do NOT place real orders. Use paper trading only.
```

**Success criteria:**
- Order placed on paper account
- Confirmation logged to journal
- Can be verified in Alpaca dashboard

---

### 3.6 — Build the trade journal (Day 4, evening)

**Task:** Persistent structured logging of all decisions + outcomes

**File:** `tradingagents/nodes/trade_journal.py`

**Format:** JSONL (one JSON object per line, appended)

**Schema:**

```json
{
  "timestamp": "2026-08-28T14:30:00Z",
  "run_id": "abc123",
  "ticker": "SPY",
  "event_type": "trade_approved",
  "spread": {
    "type": "bull_put",
    "sell_strike": 750,
    "buy_strike": 745,
    "sell_delta": -0.16
  },
  "entry": {
    "bid": 0.45,
    "ask": 0.47,
    "mid": 0.46,
    "credit": 46,
    "timestamp": "2026-08-28T14:30:00Z"
  },
  "risk_metrics": {
    "max_loss": 458,
    "po_notional": 750,
    "account_impact_pct": 0.458
  },
  "execution": {
    "order_id": "xyz789",
    "filled_price": 0.465,
    "filled_time": "2026-08-28T14:31:15Z",
    "status": "filled"
  },
  "reasoning": "IV rank 72%, analyst bullish on support level"
}
```

**Also log rejections:**

```json
{
  "timestamp": "2026-08-28T14:35:00Z",
  "event_type": "trade_rejected",
  "spread": {...},
  "rejection_reason": "Rule 8: Daily loss 5.2% exceeds kill-switch threshold",
  "failing_rules": ["daily_drawdown_check"]
}
```

**Deliverable:** All trades and rejections logged to `~/.tradingagents/options_trades.jsonl`

**Claude Code prompt:**
```
Build tradingagents/nodes/trade_journal.py:

1. Create TradeJournal class that:
   - Appends to ~/.tradingagents/options_trades.jsonl
   - Logs: analyst output, spread builder output, risk gate decision, execution result
   - Each entry: timestamp, ticker, event type, full context
   - Handles: approvals, rejections, fills, exits

2. Methods:
   - log_analysis(analyst_output)
   - log_spread_candidate(spreads)
   - log_gate_decision(approved/rejected, reason)
   - log_execution(order_id, fill)

3. Reading:
   - load_recent_trades(ticker, limit=50) → list of recent trades
   - compute_win_rate(ticker) → % of profitable exits
   - compute_total_pnl(ticker) → realized P&L

Ensure JSONL format (one JSON per line, newline-delimited).
```

**Success criteria:**
- Trades logged to file
- Rejections logged with reason
- Can read and analyze later

---

### 3.7 — Wire it all together in the graph (Day 5, morning)

**Task:** Connect all nodes into a complete LangGraph

**Sequence:**
1. Analyst Node → outputs IV rank, sentiment, regime
2. Options Calculator → outputs candidate strikes + Greeks
3. Spread Builder → outputs ranked spreads
4. Risk Gate → APPROVES or REJECTS
5. [IF APPROVED] Executor → places order
6. Trade Journal → logs everything

**File:** `tradingagents/graph/trading_graph.py` (refactored)

**Deliverable:** Graph compiles, nodes connect correctly, full dry-run works

**Claude Code prompt:**
```
Refactor tradingagents/graph/trading_graph.py to wire the new nodes:

Current flow:
- analysts → researchers → trader → portfolio manager

New flow:
- analyst → options_calculator → spread_builder → risk_gate
- [if approved] → executor → portfolio_manager
- [always] → trade_journal

1. Remove researcher nodes + edges
2. Add new nodes to the graph
3. Update edges
4. Wire decision logic (if approved → execute, else → skip executor)
5. Ensure portfolio_manager receives updated state (new position, Greeks)

Test: run end-to-end on a single ticker for a single cycle.
```

**Success criteria:**
- Graph has no cycles
- New nodes all connected
- No "dangling" nodes

---

## Phase 4: Frontend & Demo (Day 5, evening - Day 6)

### 4.1 — Build Streamlit dashboard (Day 5, evening)

**Task:** Web UI for judges to see live state

**File:** `streamlit_app.py` (root of alpaca-agent)

**Panels:**

1. **Live Portfolio**
   - Current positions (ticker, strike, delta, theta, vega)
   - Total portfolio Greeks (delta, theta, vega)
   - P&L (realized + unrealized)

2. **Trade Journal**
   - Last 20 trades (reverse chronological)
   - For each: entry delta, premium, fill price, status
   - Rejections with reasons

3. **Statistics**
   - Win rate (% of profitable exits)
   - Avg credit per trade
   - Total notional deployed
   - Avg max loss per trade

4. **Live IV & Market**
   - Current IV rank (SPY, QQQ, IWM)
   - Current spot prices
   - Market regime (high IV / low IV)

**Deployment:** Streamlit Cloud (free tier)

**Deliverable:** Hosted link judges can visit

**Claude Code prompt:**
```
Build streamlit_app.py:

1. Read from:
   - Alpaca API (positions, account state, Greeks)
   - Trade journal JSONL file (history)

2. Display:
   - Portfolio: positions table with Greeks
   - Journal: recent trades + rejections
   - Stats: win rate, avg credit, total notional, realized P&L
   - Market: current IV rank + spot prices

3. Deployment:
   - Push to GitHub
   - Deploy on Streamlit Cloud (free)
   - Provide judges with public link

Use st.metric, st.dataframe, st.plotly_express for charts.
```

**Success criteria:**
- Dashboard loads without errors
- Shows real account state
- Shows recent trades from journal
- Hosted and accessible

---

### 4.2 — Overnight autonomous loop (Day 5, evening)

**Task:** Set up cron job to run agent every 5 minutes during market hours

**File:** `cron_runner.py` (wraps the graph)

**Pseudocode:**

```python
def cron_cycle():
    """Runs once per cycle (every 5 min during market hours)"""
    
    # Load config
    config = load_config()
    
    # Initialize graph
    graph = TradingAgentsGraph(config=config)
    
    # Run one cycle
    underlyings = ["SPY", "QQQ", "IWM"]  # Scan 3 underlyings
    for ticker in underlyings:
        _, decision = graph.propagate(ticker, today_date)
        # Decision includes: analyst output, approved spreads, execution results
    
    # Log cycle completion
    log(f"Cycle complete. Checked {len(underlyings)} tickers.")
```

**Cron setup (WSL):**

```bash
# Edit crontab
crontab -e

# Add line:
*/5 9-16 * * 1-5 cd /home/niazi/alpaca-agent && /home/niazi/alpaca-agent/venv/bin/python cron_runner.py >> /home/niazi/alpaca-agent/logs/cron.log 2>&1
```

This runs every 5 minutes during 9 AM–4 PM EST, Monday–Friday.

**Deliverable:** Cron job running, logs show cycles completing

**Claude Code prompt:**
```
Build cron_runner.py:

1. Create a single-cycle function:
   - Load config
   - Initialize graph
   - Iterate through 3 tickers: SPY, QQQ, IWM
   - For each, run graph.propagate(ticker, today)
   - Log all decisions to a cron.log file

2. Set up WSL cron job:
   - crontab -e
   - Add line to run cron_runner.py every 5 min during market hours
   - Log to /home/niazi/alpaca-agent/logs/cron.log

3. Monitoring:
   - Verify log file grows
   - Show sample log entries
   - Confirm trades appear in Alpaca account
```

**Success criteria:**
- Cron job running
- Log file exists and grows
- Trades appear in Alpaca dashboard

---

### 4.3 — Video and slide deck (Day 6)

**Deliverable:** 3-minute video showing the agent in action

**Script outline:**
1. **Intro (30 sec)** — "This is an autonomous options trading bot built on Alpaca."
2. **Analyst layer (30 sec)** — Show live IV rank, sentiment, macro input
3. **Greeks & spreads (30 sec)** — Show candidate spreads with bid/ask and delta
4. **Risk gate (30 sec)** — Show a trade being APPROVED and a trade being REJECTED (with reasons)
5. **Execution (30 sec)** — Show order placed on Alpaca, filled, logged to journal
6. **Dashboard (30 sec)** — Show Streamlit: portfolio Greeks, trade history, win rate
7. **Closing (30 sec)** — "The agent runs autonomously, scans 3 underlyings every 5 minutes, and respects hard risk limits. No LLM in the risk gate."

**Slide deck:** 5 slides
1. Concept: define-risk options, premium-selling
2. Architecture: analyst → Greeks → risk gate → execution
3. The risk gate: 9 hard rules, deterministic
4. Results: trades placed, P&L, win rate
5. Key insight: deterministic risk beats LLM convictions

---

## Phase 5: Submission (Aug 28 – Sep 4)

### 5.1 — Create fresh Alpaca account (Aug 28, morning)

**Task:** Before trading begins, create a brand-new paper account for judging

**Why:** Hackathon rules require a fresh account. Reused accounts are disqualified.

**Steps:**
1. Go to alpaca.markets
2. Sign up with a new email (or same email, new account)
3. Generate new API keys
4. Update `.env` with new keys
5. Verify account is paper trading
6. Verify options Level 3 is enabled

**Deliverable:** Fresh account ID noted for submission

### 5.2 — Agent runs live (Aug 28, evening – Sep 4)

**Task:** Let the cron job run for 5.5 trading days

**Monitoring:**
- Check Streamlit dashboard daily
- Confirm trades appearing
- Monitor P&L
- Verify no errors in cron.log

**Potential issues:**
- Network timeouts → add retry logic to executor
- Invalid spreads → tighten spread_builder filters
- Orders not filling → adjust limit prices (move closer to mid)

### 5.3 — Final submission (Sep 4, morning)

**Deliverable checklist:**
- [ ] Public GitHub repo (with `.env` in `.gitignore`)
- [ ] README explaining the agent
- [ ] Alpaca account ID
- [ ] Streamlit dashboard URL
- [ ] Video (3 min)
- [ ] Slide deck
- [ ] Social media posts (up to 5)

---

## Success Metrics

### Technical
- ✅ Agent places real trades autonomously
- ✅ Risk gate enforces all 9 rules
- ✅ Zero manual intervention needed
- ✅ Cron job runs 5+ trading days without error

### Trading Performance
- ✅ Positive P&L (target: +0.5% to +2% over 5 days)
- ✅ Win rate ≥ 75% (most spreads expire worthless)
- ✅ No blown account (always stay within risk limits)
- ✅ Defined-risk only (no naked shorts)

### Presentation
- ✅ Judges understand the strategy in 3 minutes
- ✅ Dashboard shows live state + history
- ✅ Code is clean and commented
- ✅ Social media shows building journey

---

## Day-by-Day Summary

| Day | Focus | Deliverable |
|---|---|---|
| **Aug 22** | Clone TA, analyze structure, delete directional logic | Modified graph, no researchers |
| **Aug 23** | Build options calculator + spread builder | Options chain with Greeks, ranked spreads |
| **Aug 24** | Build risk gate + executor | Tested risk gate, first trade placed |
| **Aug 25** | Build journal, wire graph together, test end-to-end | Full cycle working: analyst → execution → journal |
| **Aug 26** | Build Streamlit dashboard, set up cron | Dashboard hosted, cron running |
| **Aug 27** | Dry runs, video, slides, social post #1 | Rehearsed demo, all submission materials ready |
| **Aug 28** | Create fresh account, agent goes live | Judged account created, first trades placed |
| **Aug 29–Sep 3** | Monitor, adjust, fix bugs | Agent running 5+ trading days |
| **Sep 4** | Final submission | GitHub repo + account ID + dashboard + video + slides |

---

## Risk Mitigations

| Risk | Mitigation |
|---|---|
| Spread builder creates unsellable spreads | Test bid/ask availability; filter by min credit ≥ $25 |
| Risk gate is too strict, no trades placed | Start permissive, tighten as P&L grows |
| Cron job crashes mid-hackathon | Add error handling + email alerts; restart manually if needed |
| Account loses >10% in one day | Kill-switch fires; all new trades rejected until next day |
| Judges can't access dashboard | Host on Streamlit Cloud; have GitHub backup |
| Forgot to make GitHub public | Double-check repo visibility day before submission |

---

## Questions for Claude Code

When refactoring, ask Claude:

1. **Architecture:** "Does this graph structure make sense? Any missing nodes?"
2. **Risk gate:** "Do these 9 rules cover all edge cases? What am I missing?"
3. **Performance:** "Can the agent scale to scan 5+ underlyings per cycle?"
4. **Error handling:** "What errors can Alpaca throw? How should I handle them?"
5. **Testing:** "What tests should I write to verify Greeks calculations?"

---

End of Plan
