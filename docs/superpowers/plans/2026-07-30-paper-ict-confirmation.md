# Paper ICT Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the existing ICT confirmation gate for the demo/paper environment while preserving the global shadow default.

**Architecture:** Use the existing Pydantic environment override to select `active` mode locally. Keep gate logic and global defaults unchanged; repair the stale source-location regression test after the gate extraction.

**Tech Stack:** Python 3.14, Pydantic Settings, pytest, `.env.local`

## Global Constraints

- `TradingSettings.ict_confirmation_mode` remains `shadow`.
- `.env.local` sets `TRADING_ICT_CONFIRMATION_MODE=active`.
- `TRADING_AUTO_START_BOT=false` remains unchanged.
- No strategy thresholds change.

---

### Task 1: Repair zone-conversion source-location regression

**Files:**
- Modify: `tests/test_zone_conversion_fix.py:83-92`
- Test: `tests/test_zone_conversion_fix.py`

**Interfaces:**
- Consumes: `trading_bot.services.parity_gates.evaluate_zone_conversion`
- Produces: Regression coverage proving OTE conversion uses `ote_pullback_entry` and not inverted `ote_low`/`ote_high` fields.

- [ ] **Step 1: Update the stale test**

```python
def test_shared_gate_no_longer_uses_inverted_ote_fields(self):
    import inspect
    from trading_bot.services import parity_gates

    src = inspect.getsource(parity_gates)
    assert "pd_analysis.ote_low" not in src
    assert "pd_analysis.ote_high" not in src
    assert "ote_pullback_entry" in src
```

- [ ] **Step 2: Run focused tests**

Run: `./venv/bin/python -m pytest tests/test_zone_conversion_fix.py tests/test_expectancy_setup_shadow.py -q`

Expected: all tests pass.

### Task 2: Activate ICT confirmation in paper configuration

**Files:**
- Modify: `.env.local`

**Interfaces:**
- Consumes: `TradingSettings.ict_confirmation_mode`
- Produces: Runtime value `active` for the configured demo account.

- [ ] **Step 1: Add the environment override**

```env
TRADING_ICT_CONFIRMATION_MODE=active
```

- [ ] **Step 2: Verify loaded settings**

Run:

```bash
./venv/bin/python - <<'PY'
from trading_bot.config import settings
assert settings.trading.ict_confirmation_mode == "active"
assert settings.trading.auto_start_bot is False
print("ict_confirmation_mode=active")
print("auto_start_bot=False")
PY
```

Expected: both lines print and exit status is zero.

### Task 3: Full readiness verification

**Files:**
- Verify: `tests/`

**Interfaces:**
- Consumes: complete repository test suite
- Produces: fresh go/no-go evidence for paper startup.

- [ ] **Step 1: Run the complete suite**

Run: `./venv/bin/python -m pytest tests/ -q`

Expected: 1,492 tests pass with zero failures.

- [ ] **Step 2: Verify repository state**

Run: `git status -sb`

Expected: only intended test/document changes plus pre-existing unrelated backup deletions and `run16.json`; `.env.local` may be ignored.

- [ ] **Step 3: Report restart requirement**

Restart the backend before starting the bot so Pydantic reloads `.env.local`.
