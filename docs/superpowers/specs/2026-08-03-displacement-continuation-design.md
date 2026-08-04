# Displacement Continuation (Metals) — Design

**Date:** 2026-08-03  
**Status:** Approved for implementation  
**Problem:** Asian-session XAUUSD impulses (e.g. 4067→4040) are missed because (1) M5 displacement wakeup is kill-zone-only and (2) `pre_claude_viability` skips Claude when M15 still opposes the HTF-aligned short and AMD ≠ manipulation.

## Goal

Catch precious-metal displacement impulses outside kill zones without FOMO chasing exhausted moves.

## Behavior

1. **Wakeup outside KZ (XAU/XAG):** While on analysis cooldown, run M5 displacement wakeup for metals regardless of kill zone. Keep 60s minimum elapsed.
2. **Pre-Claude bypass (directional):** Fresh strong **M5** displacement (≥1.5× ATR, age ≤3 bars) clears the M15-oppose block for the **aligned** direction only — both in `pre_claude_viability` and `evaluate_m15_gate` (stamped as `fresh_displacement_direction`). Opposite side stays blocked. D1+H4 both-opposing still blocks that direction. Metals must use M5 for this check (execution TF is often M15 and lags the impulse).
3. **Bar index:** Age checks use `last_closed_bar_index = len(raw_df) - 2` to match `exclude_forming_candle`.
4. **Entry preference:**
   - Primary: limit/stop into displacement-origin zone (displacement's own FVG) when available; repair SL/TP so geometry stays valid after retargeting entry.
   - Secondary: market/stop only if still expanding and excursion from origin ≤ 1.0× ATR.
   - Skip if excursion > 1.5× ATR with no structural hold (missed — do not chase).
5. **Tag:** `setup=displacement_continuation` for expectancy.

## Non-goals

- Always-on Asian Claude
- Removing HTF gates for the counter-displacement side
- Separate loose-RR scalp mode

## Files

- `trading_bot/services/analysis_cooldown.py` — wakeup eligibility + direction helper
- `trading_bot/main.py` — remove KZ-only guard on metal wakeup
- `trading_bot/utils/win_optimization.py` — pre-Claude + entry plan helpers
- `trading_bot/services/analyze_and_trade_runner.py` — pass displacement into viability / apply entry plan
- Tests under `tests/`
