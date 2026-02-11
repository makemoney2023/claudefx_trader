# Order Blocks

## Definition

An Order Block (OB) is the last opposing candle before a significant price move. It represents the footprint of institutional order flow - the zone where smart money placed their orders before driving the market.

## Formation

### Bullish Order Block
The last **bearish (down) candle** before an impulsive bullish move:
- Institutions accumulated long positions during this candle
- Creates a demand zone (support)
- Price often returns here for continuation entries

```
                 │
         ┌───┤  │  Bullish impulse
    OB → │ ▼ │  │
         └───┤  │
             └──┘
         ↑ Last down candle
```

### Bearish Order Block
The last **bullish (up) candle** before an impulsive bearish move:
- Institutions distributed/shorted during this candle
- Creates a supply zone (resistance)
- Price often returns here for continuation entries

```
         ┌───┤
    OB → │ ▲ │  ↑ Last up candle
         └───┤
                 │
                 │  Bearish impulse
                 └──┘
```

## Zone Definition

### Using Candle Body (Recommended)
- **Zone Top**: Open or Close (whichever is higher)
- **Zone Bottom**: Open or Close (whichever is lower)
- More precise, fewer fakeouts
- Institutions likely placed orders within body

### Using Full Candle Range
- **Zone Top**: Candle High
- **Zone Bottom**: Candle Low
- Wider zone, more likely to be reached
- May include wicks that aren't significant

## Quality Criteria

### Strong Order Block Characteristics

1. **Strong impulse move after OB**
   - Multiple candles in same direction
   - Large body candles (>60% body)
   - Creates Fair Value Gap

2. **Clear market structure break**
   - Impulse breaks recent swing high/low
   - Indicates genuine institutional move

3. **Position in trend**
   - Aligns with HTF bias
   - In premium/discount zone

4. **Fresh (unmitigated)**
   - Price hasn't returned to zone
   - First touch highest probability

### Weak Order Block Characteristics
- No structure break after
- Small impulse move
- Against HTF trend
- Already tested multiple times

## Order Block Types

### Standard Order Block
- Basic OB as described above
- Most common type
- Trade on first return

### Breaker Block
An invalidated Order Block that becomes an opposing zone:
- **Bullish OB becomes Bearish Breaker**: Price breaks below bullish OB, then returns to it as resistance
- **Bearish OB becomes Bullish Breaker**: Price breaks above bearish OB, then returns to it as support

### Mitigation Block
- Order Block that has been partially filled
- May still hold but with reduced strength
- Use tighter stops

### Propulsion Block
- OB with extreme imbalance
- Usually contains FVG
- Very high probability zone

## Trading Order Blocks

### Entry Methods

**Aggressive Entry**:
- Limit order at OB zone
- Enter without LTF confirmation
- Tighter stop, potentially stopped out

**Conservative Entry**:
- Wait for price to reach OB
- Look for LTF reversal signal
- Better confirmation, may miss some

### Bullish OB Trade
1. Identify bullish OB (last down candle before up move)
2. Mark the zone (preferably body)
3. Wait for price to return
4. Buy at zone with stop below OB low
5. Target: Recent high or BSL

### Bearish OB Trade
1. Identify bearish OB (last up candle before down move)
2. Mark the zone (preferably body)
3. Wait for price to return
4. Sell at zone with stop above OB high
5. Target: Recent low or SSL

## Order Block Mitigation

### Testing vs Mitigation
- **Test**: Price touches OB but respects it
- **Mitigation**: Price closes through the OB

### Mitigation Rules
- **First test**: Highest probability
- **Second test**: Reduced probability
- **Close through body**: OB invalidated
- **Close through entire zone**: Strong invalidation

### What Happens After Mitigation
- OB is no longer valid as same-direction zone
- May become a breaker block
- Look for new OBs

## Multi-Timeframe OBs

### HTF Order Blocks
- H4, Daily, Weekly
- Define key zones for bias
- Larger zones, higher significance

### LTF Order Blocks
- M5, M15, H1
- Entry refinement
- Within HTF OB = confluence

### Nested Order Blocks
- LTF OB inside HTF OB
- Very high probability
- Best entries

## OB + Other Concepts

### OB + FVG
When OB overlaps with FVG:
- Double confirmation of institutional activity
- Very high probability zone
- Use for aggressive entries

### OB + Liquidity
OB near liquidity pool:
- Price may sweep liquidity first
- Then react at OB
- Wait for sweep before entry

### OB + Structure
OB at key structure level:
- Structure break validates OB
- Higher significance

## Best Practices

1. **Only trade fresh OBs**
   - First touch is best
   - Second touch with caution
   - Skip heavily tested OBs

2. **Require impulse validation**
   - Strong move away from OB
   - Structure break preferred
   - FVG creation is good sign

3. **Proper zone definition**
   - Use body for precision
   - Use range if body is too tight
   - Mark clearly on chart

4. **HTF alignment**
   - Trade bullish OBs in uptrend
   - Trade bearish OBs in downtrend
   - Skip counter-trend OBs

5. **Stop placement**
   - Below/above the entire OB
   - Add buffer for spread
   - Don't place inside zone

## Common Mistakes

1. Marking every candle before a move as OB
2. Trading OBs against the trend
3. Entering before price reaches OB
4. Using stops inside the OB zone
5. Not requiring impulse validation
6. Trading heavily mitigated OBs
