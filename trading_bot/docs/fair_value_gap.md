# Fair Value Gap (FVG)

## Definition

A Fair Value Gap (FVG) is an imbalance in price created when price moves so aggressively that it leaves a gap between three consecutive candles. This gap represents an area where one side (buyers or sellers) completely dominated, leaving unfilled orders.

## Formation Rules

### Three-Candle Pattern
An FVG requires exactly three candles:
1. **Candle 1**: The starting candle
2. **Candle 2**: The impulse candle (large displacement)
3. **Candle 3**: The confirmation candle

### Bullish FVG
Forms during an upward impulse move:
- Gap exists between Candle 1's HIGH and Candle 3's LOW
- **Condition**: Candle 1 High < Candle 3 Low
- Zone: From Candle 1 High to Candle 3 Low
- Acts as potential **support** when price returns

```
        │   Candle 3
        ├───┤
   GAP  │   │ ←── FVG Zone (support)
        │   │
    ┌───┤   
    │   │ Candle 2 (impulse)
    │   │
    ├───┘
    │   │ Candle 1
    └───┤
```

### Bearish FVG
Forms during a downward impulse move:
- Gap exists between Candle 1's LOW and Candle 3's HIGH
- **Condition**: Candle 1 Low > Candle 3 High
- Zone: From Candle 3 High to Candle 1 Low
- Acts as potential **resistance** when price returns

```
    ┌───┤
    │   │ Candle 1
    │   │
    ├───┘
   GAP  │   │ ←── FVG Zone (resistance)
        │   │
        ├───┤
        │   │ Candle 2 (impulse)
        │   │
        └───┤ Candle 3
```

## Quality Criteria

### Strong FVG Characteristics
1. **Large impulse candle** (Candle 2)
   - Body covers >60% of candle range
   - Strong momentum in direction of gap
   
2. **Significant gap size**
   - At least 5-10 pips for forex majors
   - Larger gaps = stronger imbalance
   
3. **Context alignment**
   - Forms in direction of HTF trend
   - Located at premium/discount zone
   
4. **Fresh (untested)**
   - Price hasn't returned to the zone yet
   - Higher probability on first touch

### Weak FVG Characteristics
- Small gap size
- Weak impulse candle
- Against HTF trend
- Multiple tests already

## Trading FVGs

### Entry Method
1. Identify valid FVG
2. Wait for price to return to the zone
3. Enter at FVG (limit order) or on reaction
4. Stop loss beyond the FVG zone

### Bullish FVG Trade
- Entry: Buy limit at FVG zone (or top of FVG)
- Stop Loss: Below FVG zone (with buffer)
- Take Profit: Recent high or BSL

### Bearish FVG Trade
- Entry: Sell limit at FVG zone (or bottom of FVG)
- Stop Loss: Above FVG zone (with buffer)
- Take Profit: Recent low or SSL

## FVG Mitigation

### Unfilled FVG
- Price hasn't returned to the zone
- Higher probability zone
- Best for limit orders

### Partially Filled (Mitigated)
- Price wicked into FVG but didn't close through
- Still valid but reduced probability
- Can still trade but with tighter stop

### Fully Filled (Invalidated)
- Price closed through the entire FVG
- No longer valid as support/resistance
- Look for new FVGs

## Special FVG Types

### Consequent Encroachment (CE)
- The 50% midpoint of an FVG
- Often acts as the reaction point
- More precise entry than full zone

### Inverse FVG
- FVG that forms against the immediate move direction
- Can signal exhaustion
- Trade with caution

### Balanced Price Range (BPR)
- Overlapping bullish and bearish FVGs
- Creates high-probability reaction zone
- Strong confluence area

## FVG + Other Concepts

### FVG + Order Block
When an FVG overlaps with an Order Block:
- Very high probability zone
- Strong institutional presence
- Use for aggressive entries

### FVG + Liquidity
FVG pointing toward liquidity:
- Price likely to fill FVG en route to liquidity
- Good entry for liquidity target trade

### FVG + Structure
FVG at key structure level:
- Structure provides additional confluence
- Higher probability entry zone

## Best Practices

1. **Trade fresh FVGs only**
   - First touch has highest probability
   - Avoid over-mitigated zones

2. **Align with HTF trend**
   - Bullish FVGs in uptrend
   - Bearish FVGs in downtrend

3. **Use appropriate timeframe**
   - HTF FVGs for bias
   - LTF FVGs for entry

4. **Combine with confluence**
   - FVG alone is not enough
   - Add OB, liquidity, structure

5. **Proper risk management**
   - Stop beyond FVG zone
   - Don't widen stops

## Common Mistakes

1. Trading every FVG regardless of context
2. Entering before price reaches FVG
3. Using stops inside the FVG zone
4. Ignoring FVG mitigation status
5. Trading FVGs against HTF trend
