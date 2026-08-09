# Liquidity Reversal Lean — Design

**Date:** 2026-08-09  
**Status:** Implemented (incl. residual killers)  
**Flag:** `TRADING_LIQUIDITY_REVERSAL_LEAN_MODE` (`off` | `active`)

## Goal

Unblock ICT Silver Bullet–style sweep-fade (`liquidity_reversal`) setups from Claude emit through Judge and `prepare_order`, without loosening continuation.

## Behavior when `active`

Eligible when `is_lean_sweep_fade(direction, ar)`: lean on + directional SSL/BSL/EQL/EQH sweep within `LEAN_SWEEP_MAX_AGE_BARS` (12) of the chart’s last bar (`_raw_bar_count` / `reference_bar_index`) + not HTF+displacement continuation.

Live sweep types match `LiquidityType` (`sell_side_liquidity`, `buy_side_liquidity`, `equal_lows`, `equal_highs`).

| Surface | Lean effect |
|---------|-------------|
| M15 / HTF dual-oppose / direction alignment | Pass |
| ICT confirm | Sweep-only when `lean_sweep_fade` (same aged predicate); bare flag alone does not skip MSS/disp |
| Fingerprint / `sb_lean` | Uses aged directional sweep under lean (parity with gates) |
| Zone gate / zone conversion / disp parity / pre-judge | Keep market / bypass extremes |
| AMD distribution / confluence | Soft-pass |
| Judge DEMOTE / auto_convert | Keep market (`lean_demote_ignored`) |
| `validate_limit_zone` | Safety net pass if somehow still a limit |
| Claude KZ + main cycle | Analyze any session; outside KZ keeps full symbol set and clears `off_hours_mode` (`resolve_outside_kz_cycle_symbols`) |
| Sweep age | Lean ignores sweeps older than 12 bars (last 3 candidates) |
| Prompts/tools | CORE MANDATE LEAN block, LEAN FADE OVERRIDE, LEAN SWING EXEMPTION, per-request tool description |
| Fingerprint | Family `liquidity_reversal`, tag `sb_lean` |

## Rollback

Set `TRADING_LIQUIDITY_REVERSAL_LEAN_MODE=off` and restart. No DB migration.

## Non-goals

1m execution TF, re-entry loops, Judge UNAVAILABLE→APPROVE, volume&lt;0.3x dead-market loosen.
