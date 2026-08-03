# Mechanical Opportunity Scanner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or implement directly with TDD). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Mechanical MT5 opportunity scanner with hot-list promotion into the trading cycle, API + thin dashboard, and pre-judge zone hard-blocks.

**Architecture:** Pure scoring/hot-list helpers in `opportunity_scanner.py`; `ICTStrategy.analyze(..., require_tradeable_session=)`; background loop in API main; cycle merge in `main.py`; FastAPI router; dashboard page.

**Tech Stack:** Python/FastAPI, existing ICTStrategy + DataFetcher, Next.js dashboard, pytest.

---

### Task 1: ICTStrategy scan-mode flag

**Files:**
- Modify: `trading_bot/strategy/ict_strategy.py`
- Test: `tests/test_opportunity_scanner.py`

- [ ] Test: default `require_tradeable_session=True` returns None when session not tradeable
- [ ] Test: `False` continues analysis when session not tradeable
- [ ] Implement kwarg on `analyze`

### Task 2: Scanner core (universe, score, hot list)

**Files:**
- Create: `trading_bot/services/opportunity_scanner.py`
- Test: `tests/test_opportunity_scanner.py`

- [ ] Test blocked-pair / BTC-quote exclusion
- [ ] Test zone hard-filter and score formula
- [ ] Test hot-list promote, TTL refresh, evict
- [ ] Implement dataclasses + pure helpers + `OpportunityScanner` class

### Task 3: Config flags

**Files:**
- Modify: `trading_bot/config.py`
- Modify: `docs/WINDOWS_VPS_SETUP.md`

### Task 4: Cycle merge + background loop

**Files:**
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/api/main.py`

### Task 5: Pre-judge zone hard-block

**Files:**
- Modify: `trading_bot/services/analyze_and_trade_runner.py`
- Test: `tests/test_opportunity_scanner.py` (or existing gate tests)

### Task 6: API + dashboard

**Files:**
- Create: `trading_bot/api/routes/opportunities.py`
- Modify: `trading_bot/api/main.py`
- Modify: `dashboard/src/lib/api.ts`
- Modify: `dashboard/src/components/Sidebar.tsx`
- Create: `dashboard/src/app/opportunities/page.tsx`

### Task 7: Verify

- [ ] Run `pytest tests/test_opportunity_scanner.py` and related gate tests
