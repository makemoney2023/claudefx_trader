# Displacement Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch XAU/XAG M5 displacement impulses outside kill zones by waking early, unblocking pre-Claude for the displacement direction, and preferring origin-zone limits over late market chase.

**Architecture:** Pure helpers in `analysis_cooldown` + `win_optimization`; wire wakeup in `main.py` and viability/entry plan in `analyze_and_trade_runner.py`. Existing gate stack and risk sizing unchanged.

**Tech Stack:** Python 3, pytest, existing DisplacementDetector / pre_claude_viability.

## Global Constraints

- TDD: failing tests before production code
- Metals only for off-KZ wakeup (XAUUSD/XAGUSD)
- Displacement M15 bypass is directional; HTF dual-oppose still blocks
- No new dependencies

---

### Task 1: Recent displacement direction helper

- [x] Add failing tests for `recent_m5_displacement_direction`
- [x] Implement in `analysis_cooldown.py`
- [x] Run tests green

### Task 2: Pre-Claude displacement M15 bypass

- [x] Add failing tests on `TestPreClaudeViability` for bearish-disp unlocking short when M15 bullish
- [x] Extend `pre_claude_viability` / `_direction_structurally_blocked`
- [x] Run tests green

### Task 3: Metal wakeup outside KZ

- [x] Add characterization/unit coverage for wakeup eligibility (or document via main wiring test if pure helper extracted)
- [x] Remove `_in_kill_zone` guard in `main.py` metal wakeup path
- [x] Run cooldown tests green

### Task 4: Entry plan helper

- [x] Add failing tests for `plan_displacement_continuation_entry` (limit / market / skip)
- [x] Implement helper in `win_optimization.py`
- [x] Wire into analyze path when displacement continuation applies
- [x] Run tests green

### Task 5: Docs + verification

- [x] Update website/risk docs briefly
- [x] Run focused pytest suite
