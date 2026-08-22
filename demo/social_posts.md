# Social Posts — 5 Drafts

Written for X; the LinkedIn variants just need the hashtags trimmed and a
sentence of context added. Fill in the bracketed numbers from real runs before
posting — don't post a number the agent hasn't actually produced.

---

## Post 1 — The premise (post first, sets up everything else)

> Building an options trading agent for the @AlpacaHQ × @lablabai hackathon.
>
> One rule I set before writing any code: **no LLM in the risk gate.**
>
> The model can read the news. It doesn't get to decide whether a trade is
> within risk limits. That's arithmetic, and arithmetic should be testable.
>
> 🧵

---

## Post 2 — The nine rules

> The risk gate is nine hard rules. All must pass:
>
> 1. Short delta ≤ 0.20
> 2. Max loss ≤ 2% of NAV
> 3. Portfolio delta ≤ 50% of NAV
> 4. Credit ≥ $25/contract
> 5. No duplicate strikes
> 6-7. 7 ≤ DTE ≤ 14
> 8. Down 5% on the day → reject everything
> 9. Keep 20% buying power
>
> Every rejection tells you which rule and what the numbers were.

---

## Post 3 — Why determinism is the feature

> "Is this a good trade?" asked of an LLM gives an answer you can't reproduce,
> can't test, and can't explain later.
>
> Asked of nine pure functions, it gives an answer you can do all three to.
>
> 50 tests on the gate alone. Every rule: a pass case, a fail case, and a
> boundary case exactly on the limit.
>
> One test monkeypatches socket.socket to raise, then asserts the gate still
> returns a decision. If it ever reaches for the network, that test fails.

---

## Post 4 — A bug the tests caught

> Found a good one today.
>
> A bull put spread is net **long** delta — you're short the near strike, so its
> delta enters the sum with a flipped sign.
>
> I had the subtraction the other way round. The agent was reporting every
> bullish position as bearish and feeding the portfolio-delta rule a number
> pointing the wrong way.
>
> Caught it writing the test, not in production. Write the test.

---

## Post 5 — Shipped

> The options agent is live on Alpaca paper.
>
> • Defined-risk credit spreads only — no naked shorts, ever
> • 9-rule deterministic gate, no LLM in the decision path
> • Scans SPY/QQQ/IWM every 5 min during market hours
> • [N] trades placed, [X]% win rate, [$Y] realized over [Z] days
>
> Dashboard: [streamlit url]
> Code: [github url]
>
> Deterministic risk beats model conviction.

---

## Notes on posting

- Post 1 the day you start, 5 the day you finish. 2–4 spread across the build.
- Post 4 lands best of the middle three — a specific caught bug reads as real
  work in a way "made progress today" never does.
- Attach the dashboard rejection panel to post 2 or 3. The screenshot of a trade
  being *refused*, with the arithmetic, is more persuasive than any winner.
- Don't post the [N]/[X]/[$Y] placeholders. If the numbers aren't in yet, post
  the rest and add results as a reply later.
