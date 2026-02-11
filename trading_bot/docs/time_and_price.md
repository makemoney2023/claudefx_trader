# Time and Price Theory

The foundation of ICT methodology is understanding how TIME and PRICE work together. Institutional traders don't just trade price levels - they trade specific price levels at specific times.

## Core Principle

**"Time meets Price"** - The best trading opportunities occur when a key price level aligns with a key time window.

## Key Time Windows (Kill Zones)

### Session Times (EST)

| Session | Time (EST) | Characteristics |
|---------|-----------|-----------------|
| Asian Session | 7:00 PM - 12:00 AM | Range-bound, accumulation phase |
| London Open | 2:00 AM - 5:00 AM | High volatility, trend initiation |
| London Close | 10:00 AM - 12:00 PM | Reversals common, profit taking |
| New York Open | 7:00 AM - 10:00 AM | Highest volatility, continuation or reversal |
| New York Lunch | 12:00 PM - 1:00 PM | Low volume, avoid trading |

### Macro Time Windows

Critical 15-20 minute windows where algorithmic activity peaks:

- **XX:50 - XX:10** (around each hour)
- **8:30 AM EST** - Major economic releases
- **9:30 AM EST** - Equity market open
- **3:00 PM EST** - Bond market close
- **4:00 PM EST** - Forex daily close

### New York Midnight (00:00 EST)

The **most important time reference** in ICT:
- Marks the start of the new trading day
- Opening price often acts as equilibrium
- Price deviations from midnight open indicate daily bias
- Above midnight = bullish bias for the day
- Below midnight = bearish bias for the day

## Time-Based Entry Rules

### Rule 1: Trade Only During Kill Zones
- Best setups occur during London (2-5 AM) or NY (7-10 AM)
- Avoid Asian session for directional trades
- Never trade during NY lunch (12-1 PM)

### Rule 2: Wait for Time Alignment
Before entering, confirm:
1. Price is at a key level (OB, FVG, liquidity pool)
2. Time is within a kill zone
3. Session bias aligns with trade direction

### Rule 3: Macro Time Entries
- Enter positions during macro windows (XX:50 - XX:10)
- These are when algorithms execute large orders
- Provides better fills and cleaner moves

## Daily Bias Determination

### Using New York Midnight

1. **Mark the midnight opening price**
2. **Observe Asian session range relative to midnight:**
   - If Asian session trades above midnight → bullish bias
   - If Asian session trades below midnight → bearish bias
   - If Asian session straddles midnight → wait for London

3. **London session should confirm bias:**
   - Bullish: London makes low, then rallies above midnight
   - Bearish: London makes high, then drops below midnight

### Using Previous Day's Range

- **Previous Day High (PDH)** = Sell-side target / resistance
- **Previous Day Low (PDL)** = Buy-side target / support
- **Previous Day Close** = Equilibrium reference

## Time and Price Confluence Checklist

Before every trade, confirm:

- [ ] Currently in a kill zone (London or NY)?
- [ ] Price at a significant level (OB, FVG, liquidity)?
- [ ] Time aligns with macro window?
- [ ] Daily bias determined from midnight?
- [ ] No major news within 30 minutes?

## Example: Bullish Time & Price Setup

1. **6:00 PM EST**: Mark NY midnight price
2. **Asian session**: Price consolidates above midnight (bullish bias)
3. **2:30 AM EST (London open)**: Price sweeps below Asian low (Judas swing)
4. **2:50 AM EST (Macro time)**: Price returns to bullish OB inside Asian range
5. **Entry**: Long at OB with stop below liquidity sweep
6. **Target**: Previous day high or higher timeframe liquidity

## Common Mistakes

1. **Trading outside kill zones** - Lower probability setups
2. **Ignoring midnight reference** - Missing daily bias
3. **Not waiting for macro times** - Poor entries, wide stops
4. **Fighting the time bias** - Shorting when bias is bullish

## Integration with Other ICT Concepts

Time and Price should combine with:
- **AMD Cycle**: Know which phase the market is in
- **Liquidity**: Time when stops will be hunted
- **Order Blocks**: Valid only during correct time windows
- **FVGs**: Best entries when time aligns
