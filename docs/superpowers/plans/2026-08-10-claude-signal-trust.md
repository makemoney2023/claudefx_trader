# Claude Signal Trust (Wide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `TRADING_CLAUDE_SIGNAL_TRUST_MODE=active` and Claude emits long/short with levels, soft-pass all strategy-redundancy gates while keeping data/spread/R:R/FINAL-RISK/daily limits/Judge REJECT hard.

**Architecture:** Add a small helper module + `TradeContext.claude_signal_trust` flag. Entry/permission pipelines check the flag and convert strategy hard-blocks into `claude_trust_bypass:<gate_id>` pass-throughs. Wire Judge DEMOTE ignore like lean. Opt-in via env (default `off`).

**Tech Stack:** Python 3, pytest, existing `gate_pipeline` / `post_claude_gates` / `analyze_and_trade_runner`.

**Spec:** `docs/superpowers/specs/2026-08-10-claude-signal-trust-design.md`

## Global Constraints

- TDD: failing test before production code per task
- Do not bypass: data quality, spread, min R:R, invalid prices, FINAL-RISK, daily loss/trades, blocked symbols, Judge REJECT, flip guard, direction circuit breaker
- Default config `off`; set `.env.local` to `active` for deploy
- Prefer soft-pass over deleting gate functions
- Commits only when the user explicitly asks (skip Step “Commit” until then)

## File map

| File | Role |
|------|------|
| `trading_bot/services/claude_signal_trust.py` | `is_claude_signal_trust_active()`, `should_apply_claude_signal_trust(direction)` |
| `trading_bot/config.py` | `claude_signal_trust_mode` field |
| `trading_bot/services/trade_context.py` | `claude_signal_trust: bool` |
| `trading_bot/services/gate_pipeline.py` | Soft-pass strategy gates when flag set |
| `trading_bot/services/post_claude_gates.py` | Set flag on ctx after price phase |
| `trading_bot/services/analyze_and_trade_runner.py` | Pre-judge + Judge DEMOTE ignore |
| `trading_bot/main.py` | Parity demote path if mirrored |
| `tests/test_claude_signal_trust.py` | New suite |
| `.env.local`, `docs/...`, `risk_management.md` | Opt-in + docs |

---

### Task 1: Config + helper + unit tests

**Files:**
- Create: `Trading/trading_bot/services/claude_signal_trust.py`
- Modify: `Trading/trading_bot/config.py` (after `liquidity_reversal_lean_mode` field)
- Create: `Trading/tests/test_claude_signal_trust.py`

**Interfaces:**
- Produces: `is_claude_signal_trust_active() -> bool`, `should_apply_claude_signal_trust(direction: str) -> bool`
- Config: `TradingSettings.claude_signal_trust_mode: str` default `"off"`, env `TRADING_CLAUDE_SIGNAL_TRUST_MODE`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_signal_trust.py
from trading_bot.config import TradingSettings
from trading_bot.services.claude_signal_trust import (
    is_claude_signal_trust_active,
    should_apply_claude_signal_trust,
)


class TestClaudeSignalTrustConfig:
    def test_default_is_off(self):
        assert TradingSettings.model_fields["claude_signal_trust_mode"].default == "off"

    def test_active_helper(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        assert is_claude_signal_trust_active() is True
        assert should_apply_claude_signal_trust("long") is True
        assert should_apply_claude_signal_trust("NO_TRADE") is False
        assert should_apply_claude_signal_trust("") is False

    def test_off_helper(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "off")
        assert is_claude_signal_trust_active() is False
        assert should_apply_claude_signal_trust("long") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd Trading && ./venv/bin/python -m pytest tests/test_claude_signal_trust.py::TestClaudeSignalTrustConfig -q --tb=short
```

Expected: FAIL (import / field missing)

- [ ] **Step 3: Write minimal implementation**

```python
# trading_bot/services/claude_signal_trust.py
from __future__ import annotations

from ..config import settings


def is_claude_signal_trust_active() -> bool:
    mode = (getattr(settings.trading, "claude_signal_trust_mode", "off") or "off").lower()
    return mode == "active"


def should_apply_claude_signal_trust(direction: str) -> bool:
    if not is_claude_signal_trust_active():
        return False
    return (direction or "").strip().lower() in ("long", "short")
```

In `config.py` TradingSettings, after lean field:

```python
    claude_signal_trust_mode: str = Field(
        default="off",
        description=(
            "When 'active', Claude long/short emits soft-pass strategy gates "
            "(zone/M15/HTF/volume/ICT/etc.); safety gates stay hard. "
            "Rollback: off + restart."
        ),
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
./venv/bin/python -m pytest tests/test_claude_signal_trust.py::TestClaudeSignalTrustConfig -q
```

---

### Task 2: TradeContext flag + entry-gate soft-pass

**Files:**
- Modify: `Trading/trading_bot/services/trade_context.py`
- Modify: `Trading/trading_bot/services/gate_pipeline.py`
- Modify: `Trading/tests/test_claude_signal_trust.py`

**Interfaces:**
- Consumes: `should_apply_claude_signal_trust`, `ctx.claude_signal_trust`
- Produces: strategy blocks become `GateOutcome.pass_through(f"claude_trust_bypass:{gate_id}")` when `ctx.claude_signal_trust` is True

- [ ] **Step 1: Failing integration test (NY buy_limit case)**

```python
from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.gate_pipeline import evaluate_entry_gates
from trading_bot.services.trade_context import TradeContext


class TestEntryGatesTrustBypass:
    def test_buy_limit_no_displacement_passes_when_trusted(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        monkeypatch.setattr(settings.trading, "ict_confirmation_mode", "active")
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.62,
            actual_rr=2.04,
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bullish",
            order_type="buy_limit",
            regime_type="ranging",
            relative_volume=0.25,
            claude_signal_trust=True,
            analysis_results={"liquidity": {}},
        )
        out = evaluate_entry_gates(
            ctx,
            zone_settings=ZoneGateSettings(gate_mode="active"),
            use_zone_gate=False,
        )
        assert out.blocked is False
        assert any("claude_trust_bypass" in p for p in ctx.gate_path) or any(
            "claude_trust_bypass" in p for p in (out.gate_path or [])
        )
```

- [ ] **Step 2: Run — expect FAIL** (`claude_signal_trust` unknown and/or volume/ICT block)

- [ ] **Step 3: Implement**

1. Add to `TradeContext`: `claude_signal_trust: bool = False`
2. In `gate_pipeline.py`, add helper:

```python
def _trust_soft_pass(ctx: TradeContext, step: GateOutcome) -> GateOutcome:
    if not step.blocked or not getattr(ctx, "claude_signal_trust", False):
        return step
    gid = step.gate_id or (step.gate_path[-1] if step.gate_path else "gate")
    return GateOutcome.pass_through(f"claude_trust_bypass:{gid}")
```

3. After each strategy step that can hard-block in `evaluate_zone_and_regime_gates` and `evaluate_structure_and_quality_gates`, wrap:

```python
step = _trust_soft_pass(ctx, step)
```

Apply to: direction, zone_gate, volatile_regime, tod, m15, htf, amd, off_hours, post_cooldown, volume, confluence, ict.

Do **not** change `evaluate_scaling_gates` / flip / circuit breaker in this task (Task 3).

- [ ] **Step 4: Run — expect PASS**

```bash
./venv/bin/python -m pytest tests/test_claude_signal_trust.py -q --tb=short
```

---

### Task 3: Permission gates (scaling / min-confidence / correlation soft) + set flag in post_claude

**Files:**
- Modify: `Trading/trading_bot/services/gate_pipeline.py` (`evaluate_trade_permission_gates`)
- Modify: `Trading/trading_bot/services/post_claude_gates.py`
- Modify: `Trading/tests/test_claude_signal_trust.py`

- [ ] **Step 1: Failing test — low confidence scaling blocked without trust, passes with trust**

Mirror an existing scaling gate test pattern: build ctx with `claude_signal_trust=True`, `confidence=0.50`, call `evaluate_trade_permission_gates` with a mock scaling_manager that would block; assert not blocked and path contains `claude_trust_bypass`.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

In `evaluate_trade_permission_gates`, wrap `scale_outcome` and correlation blocks with `_trust_soft_pass`.

In `run_post_claude_gates`, after `_build_or_reuse_context` / before `evaluate_entry_gates`:

```python
from .claude_signal_trust import should_apply_claude_signal_trust
pipeline_ctx.claude_signal_trust = should_apply_claude_signal_trust(direction)
if pipeline_ctx.claude_signal_trust:
    path.append("claude_signal_trust")
```

Also set when reusing carried ctx for `start_at="entry"`.

- [ ] **Step 4: Run suite PASS**

```bash
./venv/bin/python -m pytest tests/test_claude_signal_trust.py tests/test_gate_gap_fixes.py tests/test_direction_quality_gates.py -q
```

---

### Task 4: Pre-judge zone + Judge DEMOTE ignore

**Files:**
- Modify: `Trading/trading_bot/services/analyze_and_trade_runner.py` (pre_judge + DEMOTE branches ~1799–2166)
- Modify: `Trading/trading_bot/main.py` if it duplicates DEMOTE lean ignore (~5977–6033)
- Modify: `Trading/tests/test_claude_signal_trust.py`

- [ ] **Step 1: Failing tests**

```python
def test_demote_ignored_under_trust(monkeypatch):
    # Call the same helper lean uses, or extract:
    # apply_judge_demote_policy(verdict, lean_fade, trust) -> {"reason": "claude_trust_demote_ignored"} 
    ...
```

Prefer extracting a tiny shared function in `claude_signal_trust.py`:

```python
def should_ignore_judge_demote(*, direction: str) -> bool:
    return should_apply_claude_signal_trust(direction)
```

Wire runner: if Judge returns DEMOTE and `should_ignore_judge_demote(direction=_dir)`, keep market/limit as Claude emitted (same structure as `lean_demote_ignored`, reason `claude_trust_demote_ignored`).

Pre-judge: if `should_apply_claude_signal_trust(_dir)` and `pre_judge_zone_block_reason(...)`, log and skip block (append path tag).

- [ ] **Step 2: Run FAIL → Step 3 implement → Step 4 PASS**

Also assert: when trust off + lean off, DEMOTE still demotes.

---

### Task 5: Safety gates still hard (regression)

**Files:**
- Modify: `Trading/tests/test_claude_signal_trust.py`

- [ ] **Step 1: Write tests that must keep blocking under trust**

```python
def test_min_rr_still_enforced_in_price_phase(...):
    # actual_rr < min → blocked even if trust active

def test_trust_does_not_skip_flip_guard(...):
    # if flip guard would block, still blocked
```

Use existing `run_post_claude_gates` fixtures / builders from other tests where possible.

- [ ] **Step 2–4:** Implement only if a safety gate was accidentally soft-passed; otherwise tests pass with no prod change.

---

### Task 6: Env + docs

**Files:**
- Modify: `Trading/.env.local` — add `TRADING_CLAUDE_SIGNAL_TRUST_MODE=active`
- Modify: `Trading/docs/WINDOWS_VPS_SETUP.md` — document flag
- Modify: `Trading/trading_bot/docs/risk_management.md` — bullet under direction quality gates
- Spec already written; mark Status: Implemented when done

- [ ] **Step 1:** Update docs/env (no failing test required beyond a docstring/default assertion already in Task 1)
- [ ] **Step 2:** Grep for leftover claims that ICT/volume always hard-block Claude emits; fix wording

---

### Task 7: Full verification

- [ ] **Step 1: Run**

```bash
cd Trading && ./venv/bin/python -m pytest \
  tests/test_claude_signal_trust.py \
  tests/test_liquidity_reversal_lean.py \
  tests/test_lean_kz_and_sweep_age.py \
  tests/test_direction_quality_gates.py \
  tests/test_gate_gap_fixes.py \
  tests/test_continuation_execution_surfaces.py \
  -q --tb=line
```

Expected: all pass

- [ ] **Step 2: Deploy checklist (manual)**

1. Push when user asks
2. VPS `.env.local`: `TRADING_CLAUDE_SIGNAL_TRUST_MODE=active` and keep `TRADING_ICT_CONFIRMATION_MODE=disabled`
3. Restart bot
4. Watch for `claude_signal_trust` / `claude_trust_bypass:` in gate_path; confirm no `Passive retracement missing`
5. Rollback: `TRADING_CLAUDE_SIGNAL_TRUST_MODE=off`

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| Flag + default off | 1 |
| Soft-pass zone/M15/HTF/AMD/volume/ICT/confluence/direction/TOD/regime | 2 |
| Soft-pass scaling/min-conf/correlation soft | 3 |
| Set trust from Claude direction in post_claude | 3 |
| Pre-judge + Judge DEMOTE ignore | 4 |
| Keep R:R / flip / REJECT hard | 5 |
| Env + docs + VPS | 6–7 |
| Lean coexistence | 2–4 (trust independent of sweep) |
