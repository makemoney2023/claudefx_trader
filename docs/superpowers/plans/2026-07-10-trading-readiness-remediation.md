# Trading Readiness Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live trading flow accounting-correct, fail-closed, restart-safe, measurable, replay-aligned, and fully verifiable before paper promotion.

**Architecture:** Four sequential waves isolate accounting, execution safety, measurement/parity, and release hardening. Shared policy modules hold behavior used by live and replay; `main.py` orchestrates but does not duplicate policy. Tests exercise production entry points and real persistence boundaries, with MT5 and Claude mocked only at external boundaries.

**Tech Stack:** Python 3.12+, asyncio, FastAPI, SQLAlchemy/aiosqlite, SQLite, pytest/pytest-asyncio, MetaTrader 5, Next.js 14/TypeScript.

---

## File map

- `trading_bot/services/trade_reservations.py`: exactly-once slot/risk ownership and release.
- `trading_bot/services/gate_funnel.py`: unified decision records and MFE/MAE outcome updates.
- `trading_bot/services/pending_order_manager.py`: pending ownership and fast fill-close events.
- `trading_bot/execution/position_manager.py`: awaited persistence and explicit A+ exit behavior.
- `trading_bot/execution/scaling_position_sizer.py`: final broker-bound dollar-risk invariant.
- `trading_bot/services/news_service.py`: successful-fetch freshness and injected intelligence source.
- `trading_bot/services/trade_learning_service.py`: decision outcome worker and DEMOTE/gate analytics.
- `trading_bot/llm/claude_client.py`: reject incoherent model output; no direction/SL/TP auto-repair.
- `trading_bot/mt5/client.py`: actual position identity and aware UTC conversions.
- `trading_bot/api/auth.py`, `trading_bot/api/main.py`, route modules: default-protect mutations/expensive actions.
- `trading_bot/api/database.py`: migrations and decision/reservation persistence.
- `trading_bot/backtesting/replay.py`, `simulator.py`, `optimizer.py`: shared execution policy parity.
- `trading_bot/main.py`: wire shared services and remove duplicated/bypassing paths.
- `dashboard/src/app/backtest/page.tsx`: production TypeScript build repair.
- `tests/test_readiness_*.py`: behavioral and integration regression suites.

## Task 1: Exactly-once reservation accounting

**Files:**
- Create: `trading_bot/services/trade_reservations.py`
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/services/pending_order_manager.py`
- Modify: `trading_bot/api/database.py`
- Test: `tests/test_readiness_reservations.py`

- [x] Write failing tests proving one reservation increments slot/risk once, release is idempotent, transfer to pending/position retains ownership, imported broker orders own no reservation, and every post-reservation rejection restores the original totals.
- [x] Run `/tmp/trading-test-venv/bin/python -m pytest tests/test_readiness_reservations.py -q`; confirm failures are caused by missing reservation behavior.
- [x] Implement a `TradeReservation` state machine with `reserve()`, `transfer()`, and idempotent `release()`. Persist reservation identity with bot-created pending orders/positions.
- [x] Replace direct post-reservation counter decrements in `main.py` with reservation release in a `try/finally` ownership boundary.
- [x] Run focused reservation and pending-order tests; require zero failures and no resource warnings.

## Task 2: Unified fast-close lifecycle

**Files:**
- Modify: `trading_bot/services/pending_order_manager.py`
- Modify: `trading_bot/main.py`
- Test: `tests/test_readiness_fast_pending_close.py`

- [x] Write a failing integration test for pending order fill→TP/SL between syncs, asserting exactly one update to P/L, risk, slot, streak/cooldown, analytics, notification, and learning.
- [x] Run the focused test and confirm the existing DB-only update misses lifecycle callbacks.
- [x] Return a normalized `ClosedTradeEvent` from pending synchronization and route it through the same close service used by tracked positions.
- [x] Add idempotency by broker deal/position identity.
- [x] Run focused tests plus `tests/test_pending_order_manager.py` and close/learning integration tests.

## Task 3: Durable position state and migrations

**Files:**
- Modify: `trading_bot/api/database.py`
- Modify: `trading_bot/execution/position_manager.py`
- Modify: `trading_bot/utils/state_persistence.py`
- Test: `tests/test_readiness_position_restart.py`

- [x] Write failing migration tests for every ORM `position_states` column and a real save→reload→manage roundtrip proving TP1/TP2 cannot fire twice.
- [x] Write a failing shutdown test proving queued persistence is drained.
- [x] Add idempotent SQLite migrations for peak R/P&L, near-TP, TP flags, break-even, volume, close reason, A+ state, and reservation identity.
- [x] Replace untracked `create_task` writes with an owned task set/queue and `flush_persistence()` on shutdown; await durability before destructive partial/close state transitions complete.
- [x] Run migration, position manager, persistence, and restart tests without aiosqlite closed-loop warnings.

## Task 4: Shared fail-closed judge policy

**Files:**
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/llm/claude_client.py`
- Create: `trading_bot/services/trade_judge.py`
- Test: `tests/test_readiness_judge_paths.py`

- [ ] Write failing tests that call real regular and reversal orchestrator entry methods for absent API client, timeout, exception, malformed verdict, APPROVE, explicit DEMOTE, and REJECT.
- [ ] Confirm infrastructure failures currently allow or demote into execution on at least one path.
- [ ] Implement one adapter returning typed outcomes: `APPROVE`, `DEMOTE`, `REJECT`, `UNAVAILABLE`. Map `UNAVAILABLE` to no execution and reservation release.
- [ ] Route regular and reversal paths through the adapter. Permit reduced/pending execution only for a valid explicit DEMOTE response.
- [ ] Run judge path, gap-fix, and orchestrator tests.

## Task 5: Final broker-bound risk invariant

**Files:**
- Modify: `trading_bot/execution/scaling_position_sizer.py`
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/config.py`
- Test: `tests/test_readiness_final_risk.py`

- [ ] Write failing parameterized tests for forex, JPY, gold, silver, indices, and crypto using final entry/SL/lots and broker tick value/size.
- [ ] Write failing integration tests proving spread widening, tick-refined entry, DEMOTE, and reversal paths revalidate after their final mutation.
- [ ] Implement `calculate_broker_loss_at_stop()` and `enforce_final_risk_cap(account_equity, risk_fraction, final_entry, final_sl, final_lots, symbol_spec)`.
- [ ] Invoke it immediately before every MT5 order request; shrink normalized lots or reject when broker minimum still exceeds the cap.
- [ ] Run sizing, risk manager, orchestrator, and MT5 config tests.

## Task 6: News, identity, UTC, auth, and signal coherence

**Files:**
- Modify: `trading_bot/services/news_service.py`
- Modify: `trading_bot/mt5/client.py`
- Modify: `trading_bot/execution/position_manager.py`
- Modify: `trading_bot/api/auth.py`
- Modify: `trading_bot/api/main.py`
- Modify: mutating route modules
- Modify: `trading_bot/llm/claude_client.py`
- Test: `tests/test_readiness_execution_edges.py`
- Test: `tests/test_readiness_auth_matrix.py`

- [ ] Write failing tests for stale populated news cache after failed refresh, injected calendar service, hedging/netting ticket identity, remaining UTC conversions, every POST/PUT/PATCH/DELETE route without auth, and incoherent Claude output before auto-repair.
- [ ] Implement freshness timestamps only on successful fetch and inject the configured intelligence service.
- [ ] Resolve actual MT5 position identity from broker result/history or constrained post-fill query.
- [ ] Convert remaining broker timestamps to aware UTC.
- [ ] Default-protect mutations and expensive LLM actions with an explicit public allowlist; never log/write generated keys.
- [ ] Reject inconsistent Claude levels/direction before any swap/flip.
- [ ] Run execution-edge, auth, MT5, news, and API suites.

## Task 7: Deterministic confidence and explicit A+ exits

**Files:**
- Modify: `trading_bot/utils/win_optimization.py`
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/execution/position_manager.py`
- Modify: `trading_bot/api/database.py`
- Test: `tests/test_readiness_confidence_exits.py`

- [ ] Write failing tests proving caps cannot be undone by later boosts and ordinary intraday/swing trades do not inherit A+ behavior.
- [ ] Implement a confidence decision object collecting base, boosts, penalties, and caps, with final cap application after all modifiers.
- [ ] Define explicit A+ criteria from setup grade/confluence, assign it at position creation, persist/reload it, and apply TP1 skipping only when true.
- [ ] Run confidence, exit, persistence, and profit-protection tests.

## Task 8: Complete decision telemetry and outcome worker

**Files:**
- Modify: `trading_bot/services/gate_funnel.py`
- Modify: `trading_bot/services/trade_learning_service.py`
- Modify: `trading_bot/main.py`
- Modify: `trading_bot/api/database.py`
- Add API read route in existing analytics/learning router
- Test: `tests/test_readiness_decision_outcomes.py`

- [ ] Write failing tests proving every terminal gate outcome creates a decision row and blocked/DEMOTE/unfilled decisions receive MFE/MAE and deterministic hypothetical TP/SL outcomes.
- [ ] Replace raw cwd-relative SQLite access with the application database/session and migration model.
- [ ] Capture decision IDs and implement an idempotent periodic outcome worker using `MT5Client`, including same-bar resolution and data-completeness status.
- [ ] Extend false-rejection analytics to Judge REJECT, DEMOTE, mechanical gates, expired, and cancelled orders.
- [ ] Expose aggregate gate expectancy and MFE coverage through a read-only API.
- [ ] Run telemetry, learning, DB, and orchestrator tests.

## Task 9: Replay/live policy parity

**Files:**
- Modify: `trading_bot/backtesting/replay.py`
- Modify: `trading_bot/backtesting/simulator.py`
- Modify: `trading_bot/backtesting/optimizer.py`
- Reuse policy services from Tasks 4, 5, 7, and 8
- Test: `tests/test_readiness_replay_parity.py`

- [ ] Build frozen fixtures for APPROVE, DEMOTE pending fill/expiry, reject, partial exits, trailing, giveback, and realistic symbol costs.
- [ ] Confirm current replay decisions/exits differ from the live policy fixtures.
- [ ] Route replay through shared judge policy inputs, pending simulator, final risk math, confidence result, and position exit policy.
- [ ] Filter optimizer data to bot-owned trades, preserve chronological holdouts, and report execution-policy metrics separately from raw strategy metrics.
- [ ] Run replay, optimizer, backtest, and parity tests.

## Task 10: Release hardening

**Files:**
- Modify: `dashboard/src/app/backtest/page.tsx`
- Modify: tests whose assertions contradict approved behavior
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `README.md`, `EXECUTIVE_SUMMARY.md`, relevant `trading_bot/docs/*.md`
- Test: full repository

- [ ] Add a TypeScript regression/type-safe formatter for optimizer metric values and fix the dashboard production build.
- [ ] Repair the five full-suite failures without weakening approved behavior; replace source-text tests with behavioral tests where touched.
- [ ] Remove AppleDouble files from the working tree, ignore `._*`, and avoid unrelated line-ending rewrites.
- [ ] Document `BOT_API_KEY`, `STRICT_ICT_SESSIONS`, judge failure policy, A+ exits, telemetry, schema, replay limitations, and Windows MT5 verification.
- [ ] Run:
  - `/tmp/trading-test-venv/bin/python -m compileall -q trading_bot tests`
  - `/tmp/trading-test-venv/bin/python -m pytest -q`
  - `npm run build` in `dashboard`
  - IDE diagnostics on changed files
- [ ] Require zero failures, zero async DB/thread warnings introduced by these changes, and a successful dashboard build.

## Task 11: Independent final review

**Files:** all changes from Tasks 1–10.

- [ ] Run a Composer 2.5 spec-compliance review against the approved design and this plan.
- [ ] Fix every confirmed P0/P1 review issue with a failing regression test first.
- [ ] Run a separate Composer 2.5 code-quality review.
- [ ] Re-run the complete verification commands from Task 10.
- [ ] Update the post-fix readiness canvas with measured results and unresolved Windows-only checks.

## Plan self-review

- Every approved design requirement maps to Tasks 1–10.
- Task ordering prevents later parity work from duplicating policies that earlier tasks centralize.
- New tests are behavioral; source inspection is not accepted as proof.
- No placeholder implementation steps remain.
- No commits or pushes are authorized by this plan.
