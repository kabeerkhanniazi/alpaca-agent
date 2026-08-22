# Slide Deck — 5 Slides

---

## Slide 1 — The problem with AI trading agents

**Headline:** An answer you can't test isn't a risk control.

- The usual design: an LLM reads the news and decides whether to trade.
- That answer is not reproducible, not auditable, not testable.
- You cannot explain it to anyone afterwards in terms of numbers.
- And on a five-day unattended run, unexplainable is the same as uncontrolled.

**Bottom line:** *Use the model where judgement helps. Not where arithmetic does.*

---

## Slide 2 — What this agent does instead

**Headline:** Defined-risk credit spreads, gated by nine hard rules.

- Sells vertical put spreads on SPY, QQQ, IWM — 15–20 delta, 7–14 days out.
- Every position has a long leg. Loss is capped before the order is placed.
- No naked shorts. No directional bets. No model in the decision path.

```
analyst → position_manager → options_calculator → spread_builder → risk_gate
                                                       ├─ approved → executor
                                                       └─ rejected → journal
```

*LangGraph orchestrates. Every node is a deterministic function.*

---

## Slide 3 — The risk gate

**Headline:** Nine rules. All must pass. All are tested.

| # | Rule | Limit |
|---|---|---|
| 1 | Short-leg delta | ≤ 0.20 |
| 2 | Max loss per trade | ≤ 2% of NAV |
| 3 | Portfolio delta | ≤ 50% of NAV |
| 4 | Minimum credit | ≥ $25 / contract |
| 5 | Duplicate strike | none |
| 6–7 | Days to expiry | 7 ≤ DTE ≤ 14 |
| 8 | Daily drawdown | > 5% → reject all |
| 9 | Buying-power reserve | ≥ 20% |

**50 tests on the gate alone** — every rule gets pass, fail, and a boundary case
sitting exactly on the limit. One test breaks the socket layer and asserts the
gate still answers.

> `REJECTED — R4_min_premium: Credit of $12.00 per contract against a $25.00 floor.`

---

## Slide 4 — What it looks like running

**Headline:** Autonomous, and legible while it runs.

- Every 5 minutes during market hours, gated on Alpaca's own clock.
- Both legs submitted as one multi-leg order — a partial fill would leave a
  naked short.
- Deterministic order IDs: a crash mid-submit cannot double-place.
- Everything journalled as JSONL — approvals, **rejections with the failing
  rule**, fills, exits.

*[Screenshot: dashboard — portfolio Greeks, headroom bars, rejection breakdown]*

---

## Slide 5 — The insight

**Headline:** Deterministic risk beats model conviction.

- The gate is a pure function. Same inputs, same answer, every time.
- Every refusal reports the rule and the arithmetic behind it.
- That is what makes it *auditable* rather than merely trusted.
- The LLM-shaped hole in this design is deliberate — and it is the point.

**Also honest about its limits:**
- IV rank starts as a realized-vol proxy and says so, until 20 sessions of real
  history accumulate.
- One spec threshold was recalibrated to a workable value, documented in the
  config rather than changed silently.
