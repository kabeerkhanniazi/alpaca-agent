# 3-Minute Demo Video — Script & Shot List

**Format:** screen recording with voiceover. No talking head needed.
**Total:** 3:00. Timings are cumulative.

Before recording:
```bash
cd ~/alpaca-agent
./venv/bin/python cron_runner.py --dry-run --force    # seed the journal
./venv/bin/streamlit run streamlit_app.py             # leave running in a tab
```

---

## 0:00–0:25 — The problem

**Screen:** README, scrolled to "The thesis".

> "Most trading agents ask a language model whether a trade is a good idea. That
> answer can't be reproduced, can't be tested, and can't be explained afterwards.
>
> This agent doesn't ask. It sells defined-risk options spreads, and every single
> position has to clear nine hard rules first. No model in the decision path."

---

## 0:25–0:50 — Live market read

**Screen:** terminal, run:
```bash
./venv/bin/python cron_runner.py --dry-run --force --ticker SPY
```

Let the output scroll. Point at the candidate/rejection counts line.

> "Every five minutes it pulls the live option chain. Here: three hundred and
> eighty-one SPY put contracts come back, and twenty-one survive the filters —
> the right delta, the right time to expiry, and a quote tight enough that the
> price is real rather than theoretical."

---

## 0:50–1:20 — Greeks and spread construction

**Screen:** the spread table. Have this ready to paste:
```bash
./venv/bin/python -c "
from datetime import datetime, timedelta
from options_agent.config import load_config
from options_agent.broker import Broker
from options_agent.nodes.options_calculator import calculate_options_opportunities, index_chain
from options_agent.nodes.spread_builder import build_spreads
cfg=load_config(); b=Broker.from_config(cfg); spot=b.get_spot_price('SPY'); today=datetime.now().date()
ch=b.get_put_chain('SPY', spot*0.85, spot*1.00, today+timedelta(days=7), today+timedelta(days=14))
sp=build_spreads(calculate_options_opportunities(ch,'SPY',spot,cfg), index_chain(ch,today), 100000.0, cfg)
print(f\"{'SELL':>6}{'BUY':>6}{'CREDIT':>9}{'MAXLOSS':>9}{'POP%':>7}{'CTR':>5}\")
for s in sp[:6]: print(f\"{s['sell_strike']:>6.0f}{s['buy_strike']:>6.0f}{s['net_credit']:>9.2f}{s['max_loss']:>9.2f}{s['prob_profit']:>7.1f}{s['max_contracts']:>5}\")
"
```

> "It pairs each candidate with a protective long strike — that's what makes the
> risk defined. Sixty-three dollars of credit, four hundred and thirty-seven of
> capped loss, an eighty percent chance of expiring worthless. And the credit is
> calculated at the *bid*, not the mid — the worst realistic fill, not the
> optimistic one."

---

## 1:20–2:05 — The risk gate (the centrepiece — do not rush this)

**Screen:** dashboard → Trade journal → **Rejections** tab. Expand one entry so
all nine checks are visible with their ✅/❌ marks.

> "This is the part that matters. Nine rules, every one of them a pure function.
> Here's a trade being refused, and it tells you exactly why — the rule, the
> observed value, the limit.
>
> No model was asked. The same inputs give the same answer every time."

**Screen:** cut to terminal:
```bash
./venv/bin/python -m pytest tests/test_risk_gate.py -q
```

> "Fifty tests just on the gate. Every rule gets a pass case, a fail case, and a
> boundary case sitting exactly on the limit. One of them monkeypatches the
> socket layer to raise on any network call, and asserts the gate still returns a
> decision — a hard guarantee it's pure."

---

## 2:05–2:30 — Execution

**Screen:** the dry-run output line showing the order.

> "When a spread does pass, both legs go to Alpaca as a single multi-leg order —
> never separately, because a partial fill would leave a naked short put open,
> which is exactly what this whole strategy exists to avoid.
>
> The order ID is derived from the trade itself, so if a cycle dies mid-submit,
> the retry collides with the existing order instead of opening a second position."

---

## 2:30–2:55 — Dashboard and autonomy

**Screen:** dashboard, scroll top to bottom slowly.

> "Live portfolio with aggregate Greeks. Headroom bars showing how close the book
> is to the limits that would stop it trading. Win rate on closed positions only.
> And a breakdown of which rule is doing the most rejecting.
>
> It runs itself — every five minutes during market hours, gated on Alpaca's own
> clock so holidays and half-days are handled."

---

## 2:55–3:00 — Close

**Screen:** the nine-rule table in the README.

> "Deterministic risk beats model conviction. That's the whole idea."

---

## Notes

- The **Rejections** tab is the money shot. Anyone can show winners; showing
  refusals with the arithmetic behind them is what proves the gate is real.
- If a live cycle finds no rejection to show, force one by temporarily setting
  `premium.min_credit_usd` to `500` in `config/risk_config.json`, running a
  cycle, then reverting. Mention on camera that you raised the threshold.
- Record with the market **open** if possible — Monday 09:30–16:00 ET — so the
  quotes are live rather than Friday's close.
