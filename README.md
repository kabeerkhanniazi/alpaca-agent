# Autonomous Options Agent

An options trading agent for Alpaca that sells **defined-risk vertical credit
spreads** and refuses to do anything else. Every position it opens is bounded:
the long leg caps the loss before the trade is ever placed. There are no naked
shorts, no directional bets, and **no language model anywhere in the decision
path**.

Built for the lablab.ai × Alpaca *AI Trading Agents* hackathon.

---

## The thesis

Most trading agents ask a language model whether a trade is a good idea. That
answer cannot be reproduced, cannot be unit-tested, and cannot be explained to
anyone afterwards in terms of numbers.

This agent replaces that judgement with **nine hard rules**. The rules are pure
functions of the proposed trade and the current book. The same inputs always
produce the same decision, every decision reports the values that produced it,
and all nine rules are covered by tests — including the boundary cases where
risk logic actually breaks.

The result is an agent whose risk behaviour you can *audit*, not just trust.

---

## Architecture

```
START
  │
  ├─ analyst              spot, trend, RSI, ATR, IV rank + regime
  ├─ position_manager     portfolio Greeks, daily P&L, open positions
  ├─ options_calculator   option chain → candidate short strikes
  ├─ spread_builder       candidates → ranked vertical spreads
  ├─ risk_gate            nine rules → APPROVE or REJECT
  │      │
  │      ├─ approved ──→ executor ──┐   multi-leg order via Alpaca
  │      └─ otherwise ──────────────┤
  │                                 │
  └─────────────────── journal ─────┘   JSONL: decisions, fills, rejections
                          │
                         END
```

Orchestrated with LangGraph, but every node is a deterministic function.
LangGraph is doing state merging and routing, not agent reasoning.

The position manager runs **before** the gate, not after execution: Rules 3, 8
and 9 need current portfolio delta, daily P&L and buying power, and evaluating
them against last cycle's numbers would be evaluating them against stale facts.

---

## The nine rules

All nine must pass. Thresholds live in [`config/risk_config.json`](config/risk_config.json) —
there are no magic numbers in the checking logic.

| # | Rule | Limit | What it prevents |
|---|---|---|---|
| 1 | Short-leg delta | ≤ 0.20 | Strikes too close to the money. ~20% chance of finishing ITM, so ~80% expire worthless. |
| 2 | Max loss per trade | ≤ 2% of NAV | One bad trade wiping out a week. Also *sets* the position size. |
| 3 | Portfolio delta | ≤ 50% of NAV in delta-dollars | Many small spreads quietly summing into one large directional bet. |
| 4 | Minimum credit | ≥ $25 / contract | Trades too thin to survive bid/ask slippage. Filters illiquid strikes. |
| 5 | Duplicate strike | none | Stacking the same bet across cycles. |
| 6 | Minimum DTE | ≥ 7 days | Gamma risk — inside a week, small moves swing P&L violently. |
| 7 | Maximum DTE | ≤ 14 days | Capital tied up earning theta too slowly. |
| 8 | Daily drawdown | > 5% loss → reject all | Compounding a bad day. A circuit breaker with no override. |
| 9 | Buying-power reserve | ≥ 20% of starting BP | Margin calls when positions move against the book. |

A rejection names the rule and the numbers:

```
REJECTED — R4_min_premium: Credit of $12.00 per contract against a $25.00
floor. Thinner credits do not survive bid/ask slippage.
```

### Two places this deviates from the original plan

Both are documented in the code and config rather than applied silently.

**Rule 3's limit was recalibrated from 0.10 to 0.50.** The original spec says
"aggregate delta ≤ 0.10 per $100k", which is unit-ambiguous. Read as
delta-dollars — the reading that actually constrains risk — a single 15-delta
spread sized to the 2% budget carries roughly $32k of delta on a $100k account.
A 0.10 cap ($10k) would reject *every* trade the rest of the strategy is built
to produce. 0.50 still binds at roughly two concurrent max-size positions.

**The gate sizes down instead of rejecting outright** when a spread is sound but
too large for the remaining delta headroom. A trade that is merely oversized is a
sizing problem, not a risk violation. The rules still reject when even a single
contract will not fit, so no limit is ever exceeded.

---

## IV rank, honestly

Alpaca serves current implied volatility but keeps no history, so IV rank cannot
be read off the API. The agent computes it two ways and **always records which
one it used**:

- `iv_history` — today's ATM IV as a percentile of its own recorded history.
  One sample per ticker per day accumulates in `data/iv_history.jsonl`.
- `rv_proxy` — the cold-start fallback until 20 sessions exist: today's ATM IV
  ranked against the past year of 20-day realized volatility. A reasonable
  stand-in, but a different statistic, and labelled as such everywhere it appears.

---

## Setup

All Alpaca access goes through **Alpaca's official CLI**, not the `alpaca-py`
SDK — the hackathon requires projects to use Alpaca's MCP server or its CLI
tools. Install the CLI first:

```bash
go install github.com/alpacahq/cli/cmd/alpaca@latest
```

That puts the binary in `~/go/bin`. Then:

```bash
git clone <your-repo-url> && cd alpaca-agent
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env-example .env      # then fill in your Alpaca paper keys
```

The CLI reads `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from the environment and
defaults to paper trading, so no `alpaca profile login` is needed and no
credentials are written to disk — which is what makes the cron path work.

The account needs **options trading level 3** for spreads. Verify the CLI, the
credentials, and the account level in one step:

```bash
./venv/bin/python check_credentials.py
```

> The CLI is in Alpha Preview: commands, flags, and output formats can change
> between releases. This project was built and verified against **v0.0.13**.

### Data feed note

On Alpaca's free data plan this project uses the `iex` feed for stock bars and
the `indicative` feed for option chains. Recent SIP data and the OPRA options
feed both require a paid subscription (OPRA additionally requires a signed
agreement). Both feed choices are set in one place —
[`options_agent/broker.py`](options_agent/broker.py) — so upgrading is a two-line change.

The options feed is passed explicitly on every call rather than left to default:
the CLI's own default is `opra`, and an account without a signed OPRA agreement
gets a `403 OPRA agreement is not signed` rather than a silent downgrade to the
free feed.

---

## Running

```bash
# Full pipeline, real market data, no orders. Safe any time.
./venv/bin/python cron_runner.py --dry-run --force

# One ticker only
./venv/bin/python cron_runner.py --dry-run --force --ticker SPY

# Live: submits real orders. Only runs when the market is actually open.
./venv/bin/python cron_runner.py --live
```

`--dry-run` is the default. `--live` must be asked for explicitly, so a mistyped
command can never place an order.

### Dashboard

```bash
./venv/bin/streamlit run streamlit_app.py
```

A market-status banner at the top shows **OPEN**/**CLOSED** with a live countdown
to the next bell, then five panels:

- **Portfolio** — metric cards, the full Greeks row, and headroom bars showing how
  close the book is to the limits that would stop it trading.
- **Risk Gate** — all nine rules with their live thresholds from
  `config/risk_config.json`, each marked pass / fail / not-yet-evaluated from the
  most recent spread the gate saw.
- **Performance** — win rate, realized P&L, positions closed, orders filled, avg
  credit, gate approval rate, plus a cumulative realized-P&L curve.
- **Market** — spot, ATM IV, IV rank and a colour-coded regime pill per underlying.
- **Trade journal** — a terminal block with four tabs (Recent activity, Rejections,
  Fills & exits, Raw). The **Rejections** tab leads each entry with the failing rule
  number, its threshold, and the value that breached it.

The P&L curve plots **realized** P&L only. Marking open positions to market would
make the line jump on quote noise and would flatter a premium-selling strategy,
where an open spread looks like a winner right up until it isn't. Dry-run cycles
are excluded from every count and labelled separately.

### The dashboard is read-only

It is deployed publicly, so nothing on the page can reach the broker. The design
mockup's `EXECUTE LIVE` and `KILL SWITCH` controls are rendered as **status
spans, not buttons** — `MODE: DRY-RUN`/`MODE: LIVE` read from the agent's last
logged cycle, and `KILL-SWITCH: ARMED` with the live drawdown against the 5%
threshold. The only interactive controls are Refresh, the journal tabs, and the
chart. `test_no_control_can_reach_the_broker` fails if any other button appears.

### Where the deployed dashboard gets its data

`data/` is gitignored and Streamlit Cloud has an ephemeral filesystem, so a
deployed instance has no journal of its own. It resolves one in order — local
file, then the public `data` branch, then a committed snapshot — and always
reports which source it used and how stale it is. See
[journal_source.py](options_agent/journal_source.py).

`scripts/push_journal.sh` publishes the journal to that branch each cycle. The
branch is orphan and every push **amends a single commit and force-pushes**, so a
week at five-minute cadence leaves one commit rather than ~2000, and `main`'s
history stays readable. Pushing there does not trigger a redeploy, so the
dashboard refreshes without an app restart.

If Alpaca is unreachable or credentials are absent, the live panels show a
neutral "unavailable" card and every journal-derived panel renders normally.

### Design

The visual language is ported from [docs/design/alpaca.html](docs/design/alpaca.html).
Tailwind could not be used — it ships as a `<script>` tag and Streamlit strips
those — so every utility is hand-written CSS in
[dashboard_theme.py](options_agent/dashboard_theme.py), injected once, with fonts
loaded via `@import` inside the style block. Icons are inline SVG rather than an
icon font, to avoid a CDN dependency that leaves ligature text visible when it
fails.

Two accessibility fixes against the mockup: `outline` (#5a5068) scores **2.46:1**
on the card background and fails WCAG AA, so it is a **border-only token** — no
text anywhere uses it, enforced by `test_outline_is_never_a_text_colour`. And
figures use `clamp()` sizing with `min-width: 0`, which is the fix for the
buying-power value overflowing its card in the mockup.

The dashboard's pure helpers live in `options_agent/dashboard_utils.py` and
`options_agent/dashboard_theme.py`, so they are unit-tested without a Streamlit
runtime.

### Scheduling

```bash
./install_cron.sh            # every 5 min during market hours, dry-run
./install_cron.sh --live     # same, but placing real orders (asks to confirm)
./install_cron.sh --uninstall
```

The schedule pins `CRON_TZ=America/New_York`. `cron_runner.py` independently
checks Alpaca's clock endpoint, so a misfiring schedule costs an API call rather
than a badly-timed trade — and holidays and half-days are handled for free.

---

## Tests

```bash
./venv/bin/python -m pytest tests/ -q                        # 270 tests
./venv/bin/python -m pytest tests/ -q -m "not integration"   # fully offline
```

The risk gate has 50 tests of its own: every rule gets a pass case, a fail case,
and a boundary case sitting exactly on the limit. Two of them are worth calling
out:

- `test_gate_is_deterministic` — the same inputs produce the same answer 20 times
  running.
- `test_gate_makes_no_network_calls` — monkeypatches `socket.socket` to raise, and
  asserts the gate still returns a decision. A hard guarantee of purity.

---

## Layout

```
options_agent/
  config.py          validated config; fails loudly at startup
  broker.py          the only module that talks to Alpaca; retry policy lives here
  iv.py              IV rank, regime, OCC symbol parsing
  state.py           the state dict passed between nodes
  dashboard_utils.py pure helpers for the dashboard (testable without Streamlit)
  dashboard_theme.py tokens, stylesheet, and HTML component builders
  journal_source.py  resolves the journal: local -> data branch -> snapshot
  graph.py           LangGraph wiring
  nodes/
    analyst.py             spot, technicals, IV regime
    position_manager.py    portfolio Greeks, daily P&L, exit decisions
    options_calculator.py  chain → candidate strikes
    spread_builder.py      candidates → ranked spreads
    risk_gate.py           the nine rules
    executor.py            multi-leg orders, idempotent
    trade_journal.py       JSONL logging + analytics
config/              risk_config.json, options_config.json
tests/               270 tests
cron_runner.py       one scheduled cycle
streamlit_app.py     dashboard
ta-base/             upstream TradingAgents, local reference only (not committed)
```

---

## Two implementation details worth knowing

**Multi-leg limit prices are signed.** For an `mleg` order Alpaca reads a
positive limit price as a *debit* and a negative one as a *credit*. A credit
spread must be submitted with a negative limit price. Inverting this would send
an order willing to pay to open a position that should be collecting.
Covered by `test_credit_spread_submits_a_negative_limit_price`.

**Exits are evaluated per spread, not per leg.** Alpaca reports each leg as its
own position. Judged individually, a short leg decaying into profit would hit
the 50% target and close alone, orphaning its long wing. Legs are grouped by
`(underlying, expiry)` and closed together.

**Order IDs are deterministic.** `client_order_id` is derived from the trade
itself — ticker, expiry, both strikes, date, size. If a cycle dies between
submitting and recording, the next attempt collides with the existing order
instead of opening a second position.

---

## Relationship to TradingAgents

The project started from [TradingAgents](https://github.com/TauricResearch/TradingAgents).
Its graph shape is the inspiration; **none of its code is imported** — the agent
is standalone, and `grep -r tradingagents options_agent/` returns nothing.

The bull/bear researcher debate, the conviction scoring and the LLM trader are
all irrelevant to selling premium on index ETFs: the strategy has no directional
opinion to debate, and position sizing comes from delta rather than conviction.
What replaced them is this repository.

The upstream clone lives in `ta-base/` locally for reference and is **not
committed** (it carries its own `.git`). To read along:

```bash
git clone https://github.com/TauricResearch/TradingAgents.git ta-base
```
