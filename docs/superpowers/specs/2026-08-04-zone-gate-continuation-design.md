# Zone Gate Continuation Bypass — Design

**Date:** 2026-08-04  
**Status:** Implemented  
**Problem:** Post-Claude `evaluate_zone_gate` hard-blocked wrong-zone entries unless **both** sweep and displacement were present. HTF-aligned continuation impulses (e.g. XAUUSD SHORT at 46% retrace with displacement but no sweep) were rejected after Claude already agreed with mechanical SHORT — missing large dump/rip moves.

## Goal

Let Claude continuation signals through when price has already left the "correct" half of the PD range, without reopening conf/RR location bypasses.

## Behavior

When not in the correct zone (short retrace < 50% / long retrace > 50%):

1. **HTF-aligned continuation:** `htf_aligned` (D1+H4 agree with trade direction) **and** directional displacement → **allow** (`allowed_wrong_zone_continuation`). Sweep optional.
2. **Reversal override:** directional sweep **and** displacement → **allow** (`allowed_wrong_zone_confirmed`). Unchanged.
3. Otherwise → **block** (`blocked_wrong_zone`).

Correct-zone entries remain unrestricted. Counter-HTF wrong-zone still needs sweep+displacement.

## Alignment with ICT confirmation

ICT confirmation already treats continuation as HTF + MSS + displacement (sweep optional). Zone gate now matches that family for location overrides.

## Files

- `trading_bot/services/entry_gates.py` — continuation path in `evaluate_zone_gate`
- `trading_bot/services/gate_pipeline.py` — wire `htf_aligned()` into zone gate call
- Tests: `tests/test_entry_gates.py`, `tests/test_direction_quality_gates.py`, `tests/test_gate_pipeline.py`, `tests/test_pipeline_characterization.py`
- Docs: `trading_bot/docs/risk_management.md`, `docs/WINDOWS_VPS_SETUP.md`

## Non-goals

- Removing the zone gate
- Softening extreme limit-zone checks (`buy_limit` deep premium / `sell_limit` deep discount)
- Changing ICT confirmation, TOD, regime, or M15 gates
