# Risk Management

## Core Principles

Risk management is the foundation of successful trading. No strategy can succeed without proper risk controls. The goal is capital preservation first, profits second.

## Position Sizing

### Fixed Percentage Risk Model
Risk a fixed percentage of account on each trade:

**Recommended**: 1% per trade
**Maximum**: 2% per trade
**Aggressive**: 3% (high confidence only)

### Position Size Calculation

```
Position Size (lots) = Risk Amount / (SL pips × Pip Value)

Where:
- Risk Amount = Account Balance × Risk Percentage
- SL pips = Entry Price - Stop Loss (in pips)
- Pip Value = Value per pip per lot (~$10 for standard lot on majors)
```

**Example**:
- Account: $10,000
- Risk: 1% = $100
- SL: 20 pips
- Pip value: $10/lot

Position Size = $100 / (20 × $10) = 0.5 lots

On micro-priced symbols (e.g. DOGEUSD) a tight stop can produce a huge
risk-based lot size before limits apply. `RiskManager` caps to
`max_position_size` **before** broker `normalize_lots` so the intermediate
value is never clamped up to broker `volume_max` (often 100) and logged as a
false oversize warning. Final dollar risk is still enforced by FINAL-RISK
immediately before order send.

### Direction quality gates

- **ICT confirmation** defaults to `active`: incomplete passive retracements
  (limit without displacement-origin / invalid PD zone), reversals without
  sweep+MSS+displacement, and continuations without HTF+MSS+displacement are
  hard rejects. Fingerprint `zone_valid` is derived from PD retrace
  (short ≥50% / long ≤50%), not left always-true.
- **Zone gate**: shorts below 50% retrace / longs above 50% hard-block unless
  either (1) **HTF-aligned continuation** (D1+H4 agree with direction) with
  directional displacement (sweep optional), or (2) **both** directional
  sweep and displacement. Confidence and R:R alone never bypass wrong-zone
  location. Displacement includes directional impulse candles, not only
  `distribution_confirmed`.
- **Continuation surfaces** (same HTF+displacement predicate): keep market
  orders through zone→OTE conversion and displacement parity; ICT continuation
  treats MSS as optional and passive limits skip `valid_zone`; pre-judge market
  extreme blocks (short ≤38.2% / long ≥61.8%) are skipped. Extreme limit checks
  (`buy_limit` >70% / `sell_limit` <30%) stay hard. Metals stamp M5 displacement
  (and `fresh_displacement_direction`) into analysis results so post-Claude
  gates see the same impulse as the continuation planner.
- **TOD / volatile regime**: weak hours and `volatile_ranging` require **70%**
  confidence (above the 60% execution floor so the gate is not a no-op).
- **M15 pullback**: HTF-aligned pending limits against opposing M15 are allowed
  when confidence ≥68% and RR ≥2.0 (soft-capped to 68%); otherwise reject as
  `m15_pullback_quality`.
- **Analysis cadence**: default Claude throttle ~270s per symbol. Precious
  metals (XAU/XAG) use **90s in kill zone** / **180s outside**. A fresh M5
  displacement can wake metals early **in any session** (London/NY/Asian/
  off-hours — not kill-zone-only; never faster than 60s). A live M5 impulse
  also overrides the Claude kill-zone hard-skip for that metal. Fresh M5
  displacement clears the M15-oppose skip for the **aligned** direction in
  both pre-Claude viability and `evaluate_m15_gate` (HTF dual-oppose still
  blocks). Metals stamp `fresh_displacement_direction` from an M5 scan (not
  execution-TF M15) so impulses are not re-skipped while the M15 bar is still
  forming. Entry prefers displacement-origin limits with SL/TP repair after
  retarget. Hybrid late-chase: market within ~1.0× ATR; Claude market + HTF
  allows market to ~2.5× ATR at 0.5× size; otherwise limit at displacement
  open; hard-skip only beyond ~3.0× ATR (`setup=displacement_continuation`).
  Post-loss entry cooldown is **15 minutes** for metals and crypto (30m forex).
- **Pyramid adds** (default **off**, `TRADING_PYRAMID_ENABLED`): after a
  primary fill reaches **+1R** with `a_plus` or confidence ≥70%, the bot may
  place **one** same-direction market add sized ≤ primary volume, SL at the
  primary’s current stop, TP at runner/TP3. Children are not re-pyramided.
  FINAL-RISK and daily risk still apply; undersized adds are skipped.

### Lot Size Reference

| Lot Type | Units | Pip Value (USD pairs) |
|----------|-------|----------------------|
| Standard | 100,000 | $10 |
| Mini | 10,000 | $1 |
| Micro | 1,000 | $0.10 |

## Stop Loss Placement

### Rules for Stop Loss

1. **Beyond structure**
   - Below swing low for longs
   - Above swing high for shorts

2. **Beyond entry zone**
   - Below Order Block for longs
   - Above Order Block for shorts

3. **Include buffer**
   - Add 5-10 pips for spread/volatility
   - Avoid exact level stops

4. **Never inside zone**
   - Stop must be beyond the entry area
   - Gives trade room to work

### Stop Loss Guidelines by Asset

| Asset | Minimum SL | Typical SL |
|-------|-----------|-----------|
| EUR/USD | 15 pips | 20-30 pips |
| GBP/USD | 20 pips | 25-40 pips |
| USD/JPY | 15 pips | 20-30 pips |
| XAU/USD | 50 pips | 100-200 pips |

## Take Profit Strategy

### Minimum Risk-Reward
- **Minimum**: 1:2 (risk $1 to make $2)
- **Preferred**: 1:3 or better
- **Never below 1:1.5**

### TP Placement

1. **At opposing liquidity**
   - BSL for long trades
   - SSL for short trades

2. **At key structure**
   - Recent swing high/low
   - Previous day high/low

3. **At Order Block**
   - Opposing OB can act as TP

### Multiple Take Profits

**TP1** (50% position):
- Conservative target
- Secures profit
- Move SL to break-even

**TP2** (remaining 50%):
- Extended target
- Ride the trend
- Trail stop

## Break-Even Management

### When to Move to BE
- After price moves 1R in your favor
- After TP1 is hit
- After clear structure forms

### BE Rules
- Add small buffer past entry
- Account for spread
- Don't move too quickly

## Trailing Stop Strategy

### Methods

**1. Structure-Based Trailing**
- Move stop below/above new swing points
- Best for trending markets

**2. R-Multiple Trailing**
- Move stop as profit increases
- At 2R profit, trail at 1R
- At 3R profit, trail at 2R

**3. ATR-Based Trailing**
- Trail at 2× ATR below/above price
- Adapts to volatility

## Daily Risk Limits

### Maximum Daily Loss
- **Limit**: 3% of account
- **Action**: Stop trading for day if hit
- **Review**: Analyze what went wrong

### Maximum Daily Trades
- **Limit**: 3-5 quality trades
- **Quality over quantity**
- **Avoid overtrading**

### Maximum Concurrent Exposure
- **Limit**: 5% total risk across all trades
- **Correlation awareness**: Don't double up on correlated pairs

## Risk Rules

### The 1% Rule
Never risk more than 1% on a single trade:
- Allows 100 consecutive losses before ruin
- Removes emotion from decisions
- Enables recovery from drawdowns

### The 3% Daily Rule
Maximum 3% daily risk exposure:
- 3 trades at 1% each
- Or 2 trades at 1.5% each
- Prevents revenge trading

### The 6% Weekly Rule
Maximum 6% weekly drawdown:
- If reached, stop trading
- Review and reset next week
- Protects from tilt

## Drawdown Management

### Drawdown Stages

| Drawdown | Action |
|----------|--------|
| 0-3% | Normal trading |
| 3-6% | Reduce position size by 50% |
| 6-10% | Reduce to minimum size |
| 10%+ | Stop trading, review strategy |

### Recovery Math

| Drawdown | Required Gain |
|----------|--------------|
| 10% | 11.1% |
| 20% | 25% |
| 30% | 42.9% |
| 50% | 100% |

*Preventing drawdowns is easier than recovering*

## Correlation Risk

### Correlated Pairs
Pairs that move together:
- EUR/USD & GBP/USD (positive)
- EUR/USD & USD/CHF (negative)
- AUD/USD & NZD/USD (positive)

### Managing Correlation
- Don't take same direction on correlated pairs
- Count correlated positions as one for risk
- Diversify across uncorrelated assets

## Trade Journaling

### Record Every Trade
- Entry reason
- Entry price, SL, TP
- Position size
- Screenshots
- Result
- Lessons learned

### Weekly Review
- Win rate
- Average R:R
- Total R profit/loss
- Best/worst trades
- Patterns in losses

## Psychological Risk

### Avoid These Behaviors

1. **Revenge Trading**
   - Taking trades to recover losses
   - Usually leads to more losses

2. **FOMO Trading**
   - Entering without setup
   - Chasing price

3. **Moving Stops**
   - Widening stop to avoid loss
   - Kills the edge

4. **Overleveraging**
   - Too large position size
   - Single trade can ruin account

### Healthy Trading Habits

1. Take breaks after losses
2. Stick to the plan
3. Accept losses as cost of business
4. Focus on process, not outcome

## Emergency Rules

### Stop Trading If:
- 3% daily loss
- 3 consecutive losses
- Emotional/tilted
- Not following rules

### Before Returning:
- Review what went wrong
- Reset mentally
- Paper trade if needed
- Start with reduced size

## Checklist Before Each Trade

1. ☐ Risk is 1% or less
2. ☐ SL is beyond structure
3. ☐ R:R is 1:2 or better
4. ☐ Not exceeding daily limits
5. ☐ Not correlated with open positions
6. ☐ Trading during kill zone
7. ☐ Clear mind, no emotions

## Bot enforcement (live system)

These runtime policies complement discretionary rules above:

| Policy | Behavior |
|--------|----------|
| `BOT_API_KEY` | Protects mutating API routes and expensive LLM actions; required in production |
| `STRICT_ICT_SESSIONS` | When enabled, blocks entries outside configured ICT kill zones |
| Trade Judge `UNAVAILABLE` | Timeout, API error, malformed verdict, or missing client → **no execution**, reservation released |
| Trade Judge `REJECT` | Hard block; reservation released |
| Trade Judge `DEMOTE` | Only path allowing reduced/pending execution |
| A+ exits | TP1 skip applies only to persisted A+ positions at creation |
| Decision telemetry | Every terminal gate writes `decision_records`; outcome worker fills MFE/MAE for blocked/unfilled rows |
| Replay | Shares policy modules with live bot; Windows MT5 fills and broker identity must be verified separately on the execution host |
