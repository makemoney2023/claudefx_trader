# Continuation Execution Surfaces — Design

**Date:** 2026-08-04  
**Status:** Implemented  
**Problem:** After zone-gate continuation bypass, HTF+displacement Claude signals still died at zone→OTE conversion, ICT `valid_zone` / MSS requirements, displacement parity (`distribution_confirmed`), or pre-judge market extremes.

## Goal

One predicate — `htf_aligned and has_displacement` — clears the remaining mechanical surfaces so impulse continuations reach the Trade Judge / MT5.

## Behavior

When continuation structure is present:

1. **Zone conversion:** leave market order unchanged (`zone_continuation_market`)
2. **Displacement parity:** `allow_market` even if `distribution_confirmed` is false
3. **ICT continuation:** MSS optional (HTF+displacement enough)
4. **ICT passive limit:** `valid_zone` optional when HTF+displacement
5. **pre_judge market extremes:** do not block short ≤38.2% / long ≥61.8%

Unchanged:

- Extreme limit zone checks (`buy_limit` >70% / `sell_limit` <30%)
- Non-continuation wrong-zone / ICT / pre-judge paths
- Session haircut already floors at 60% execution confidence

## Files

- `setup_fingerprint.py` — `is_htf_displacement_continuation`
- `parity_gates.py`, `post_claude_gates.py` — conversion + disp parity wiring
- `entry_gates.py` — ICT exemptions
- `opportunity_scanner.py`, `analyze_and_trade_runner.py` — pre_judge wiring
- Tests: `tests/test_continuation_execution_surfaces.py`
