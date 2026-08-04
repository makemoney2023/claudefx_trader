# Continuation Execution Surfaces — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** After zone-gate continuation bypass, stop the remaining mechanical surfaces from converting/killing HTF+displacement Claude continuations before MT5.

**Architecture:** Shared predicate `htf_aligned and has_displacement` (same as zone-gate continuation). Apply it at zone conversion, ICT confirmation, pre-judge zone, displacement parity, and session haircut.

## Files

- `trading_bot/services/setup_fingerprint.py` — add `is_htf_displacement_continuation()`
- `trading_bot/services/parity_gates.py` — skip OTE convert / allow market on continuation
- `trading_bot/services/post_claude_gates.py` — wire HTF+disp into parity calls
- `trading_bot/services/entry_gates.py` — ICT + session penalty continuation exemptions
- `trading_bot/services/gate_pipeline.py` — pass continuation into session penalty
- `trading_bot/services/opportunity_scanner.py` — pre_judge continuation exemption
- `trading_bot/services/analyze_and_trade_runner.py` — pass HTF+disp into pre_judge
- Docs: `risk_management.md`, design spec update
- Tests under `tests/`

## Policy

When `htf_aligned and has_displacement`:

1. **Zone conversion:** leave market order unchanged (do not force OTE limit)
2. **Displacement parity:** `allow_market` (do not reject/convert on `distribution_confirmed=False`)
3. **ICT continuation:** MSS optional (HTF+disp enough)
4. **ICT passive limit:** `valid_zone` optional (HTF+disp enough)
5. **pre_judge market extremes:** do not hard-block ≤38.2% short / ≥61.8% long
6. **Session haircut:** skip Asian/non-KZ confidence penalty

Non-continuation paths unchanged. Extreme limit checks (`buy_limit` >70% / `sell_limit` <30%) unchanged.

## Tasks

1. TDD: failing tests for each surface
2. Implement helper + wire all call sites
3. Update docs
4. Run related pytest suite
