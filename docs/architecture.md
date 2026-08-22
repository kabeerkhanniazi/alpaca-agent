# Architecture Notes

Design decisions and the reasoning behind them. The README covers *what* the
agent does; this covers *why it is shaped this way*.

---

## Why the position manager runs before the risk gate

plan.md's original sequence put portfolio management after execution. That
cannot work: Rules 3, 8 and 9 read portfolio delta, daily P&L and buying power
respectively. Running the position manager afterwards would have the gate
evaluating this cycle's trade against last cycle's book.

The exit logic also runs before new positions are opened, in `cron_runner.py`.
Closing a position frees buying power and reduces portfolio delta — both gate
inputs — so a new trade is evaluated against the book as it will actually be.

---

## Why LangGraph, with no LLM

LangGraph earns its place on state merging and conditional routing, which is
what the pipeline actually needs. It also preserves the node-and-edge shape the
project was specified against, which makes the architecture legible in a demo.

What it is *not* doing here is agent reasoning. Every node is an ordinary
deterministic function. The framework is scaffolding, not intelligence.

---

## Why the risk gate is a pure function

`risk_gate_check()` takes a spread, a portfolio, and a config, and returns a
decision. It performs no I/O, makes no network calls, reads no clock, and uses
no randomness.

This buys three things:

1. **Testability.** All 50 gate tests run in 0.3 seconds with no fixtures beyond
   plain dicts.
2. **Reproducibility.** A journalled decision can be replayed exactly, months
   later, from the values in the log.
3. **A guarantee that can be asserted.** `test_gate_makes_no_network_calls`
   monkeypatches `socket.socket` to raise, then asserts the gate still returns a
   decision. Purity is enforced, not just intended.

Every rule returns a `RuleCheck` carrying its observed value and its limit, even
when it passes — so a decision is reconstructable from the journal alone.

---

## Why rules size down rather than reject

A spread that is sound but slightly too large for the remaining delta headroom
is a sizing problem, not a risk violation. The gate computes two independent
budgets — capital at risk (Rule 2) and directional exposure (Rule 3) — and takes
the smaller. If even one contract will not fit, *then* it rejects.

The alternative, rejecting outright, would make the agent stop trading the
moment its book was anywhere near a limit, which is not what a limit is for.

`size_limited_by` is recorded on the Rule 3 check so the journal explains why a
position came out smaller than the loss budget alone would allow.

---

## Why credits are computed at the bid, not the mid

`spread_builder` computes net credit as `short_bid − long_ask`: the worst
realistic fill. Using the mid on both legs would overstate every credit by the
half-spread on two legs at once, and a spread that only clears the $25 minimum
at mid prices does not clear it in reality.

The *limit price* sent to the broker is a separate calculation — mid, less a
configured slippage allowance. Sending the conservative bid/ask credit as a
limit would give away the entire edge; sending the exact mid tends not to fill.

---

## Why exits are market orders while entries are limit orders

Entering is optional. If a limit order does not fill, nothing has been lost —
there will be another cycle in five minutes.

Exiting is not optional. The exit triggers are all risk decisions: a profit
target reached, a stop breached, expiry approaching. A limit order that fails to
fill leaves the position open and the risk unmanaged. Certainty of exit is worth
more than a few cents of spread.

---

## Why the buying-power reserve is measured against a persisted baseline

If Rule 9's floor were computed from *current* buying power, it would shrink as
the account drew down — permitting new trades exactly when the account could
least afford them. The baseline is snapshotted on first run into
`data/account_baseline.json` and the floor is measured against that.

`test_r9_measures_reserve_against_the_starting_baseline` pins this: an account
at $81k buying power against a $400k baseline fails, where a current-BP floor
would have passed it trivially.

---

## Why reads retry and writes do not

`broker.py` wraps read calls in exponential backoff — plan.md correctly flags
network timeouts as the top operational risk for an unattended multi-day run.

Order submission is deliberately *not* wrapped. A failed write may or may not
have reached the exchange, and a blind retry is a duplicate-position risk in a
way a retried read never is. The executor handles that path explicitly, by
checking for an existing order with the same deterministic `client_order_id`
before resubmitting.

The retry decorator also refuses to retry permanent 4xx errors. An unsubscribed
data feed returning 403 will return 403 on every attempt; retrying just burns
seconds out of a five-minute cycle.

---

## Why the journal self-heals torn lines

If a process is killed mid-write, the file can end without a newline. Appending
directly onto that partial line would merge two records and destroy the intact
one as well as the torn one. `TradeJournal.write()` checks the final byte and
starts a fresh line when needed.

Reads skip unparseable lines rather than raising, so one bad line costs one
record instead of the whole file.

---

## Known limitations

- **IV rank is a proxy at first.** Until 20 sessions accumulate, it ranks ATM IV
  against realized volatility. That is a different statistic, and it is labelled
  `rv_proxy` everywhere it appears rather than being quietly presented as an IV
  percentile.
- **Free-tier data.** Stock bars come from IEX rather than full SIP, and option
  quotes from the indicative feed rather than OPRA. Quotes are good enough for
  strike selection; a paid plan would tighten fills.
- **Bull put spreads only.** The plan allows for bear call spreads. The builder
  is structured to accommodate them — `type` is already a field — but only the
  put side is implemented, so the book is structurally long delta. Rule 3 is
  what keeps that bounded.
- **No earnings-date awareness.** The underlyings are index ETFs, which do not
  report earnings, so this matters less than it would on single names. It would
  need adding before pointing the agent at individual stocks.
