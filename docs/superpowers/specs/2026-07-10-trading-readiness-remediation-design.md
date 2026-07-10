# Trading Readiness Remediation Design

## Goal

Make the live trading pipeline internally consistent, restart-safe, measurable, and behaviorally aligned with replay before any unattended live-capital use. This design improves execution correctness and the ability to measure expectancy; it does not promise profitable trades.

## Scope

The program is split into four independently testable waves:

1. Accounting and persistence integrity
2. Execution and safety consistency
3. Win measurement and replay parity
4. Release hardening and documentation

Each wave must leave the repository in a runnable state and must pass its focused tests before the next wave begins.

## Decisions

- Judge infrastructure failure, timeout, absent client, or malformed output blocks the trade entirely.
- Only a valid, explicit Judge `DEMOTE` response may produce reduced-size or pending execution.
- Risk is validated from the final broker-bound entry, stop, and normalized filled/requested volume.
- Slot and risk reservations use exactly-once ownership; imported/manual orders cannot reclaim bot reservations they do not own.
- Position state mutations are durably persisted before the management action is considered complete.
- A+ exit behavior requires an explicit classification. Intraday and swing labels alone do not qualify.
- Every rejected, demoted, expired, and unfilled signal is measurable through a common decision/outcome model.
- Replay must model the production policies used to claim strategy expectancy.

## Wave 1: Accounting and Persistence Integrity

### Reservation lifecycle

Introduce a reservation object owned by one trade attempt. It records:

- reservation identifier
- symbol and signal identifier
- daily-trade slot ownership
- reserved risk percentage/dollars
- pending order ticket or position ticket
- state: `reserved`, `transferred`, `released`, or `closed`

All post-reservation exits call one idempotent release operation. Market fills transfer ownership to the position. Pending orders transfer ownership to the tracked pending order. Imported broker orders default to no reservation ownership unless restored from persisted bot state.

### Fast pending close

Pending synchronization returns a normalized close event when an order fills and closes between sync cycles. The event enters the same close handler used by tracked positions, exactly once, updating:

- authoritative MT5 P/L
- daily P/L
- daily risk
- trade slot/reservation
- streaks and cooldowns
- session/scaling analytics
- notifications
- learning review

### Position persistence

Add migrations for all ORM position-state fields, including peak R, peak unrealized P/L, near-TP state, TP flags, break-even state, remaining volume, close reason, and explicit A+ classification.

Replace untracked fire-and-forget writes with an owned persistence queue or awaited mutation writes. Shutdown drains outstanding persistence work. Add a real database save → process restart/load → manage test proving a TP stage cannot fire twice.

## Wave 2: Execution and Safety Consistency

### Judge semantics

Regular and reversal entries share one judge adapter:

- `APPROVE`: execute unchanged subject to final mechanical checks
- `DEMOTE`: apply only when the response is valid and explicit
- `REJECT`: release reservation and stop
- unavailable/timeout/malformed/exception: release reservation and stop

No absent-key or exception branch may continue to sizing or execution.

### Final risk invariant

Calculate maximum loss using broker tick size/value when available, with a tested symbol-spec fallback. The invariant is:

`actual_loss_at_stop <= configured_account_risk_dollars * tolerance`

Run it after all entry refinement, spread allowance, stop widening, lot normalization, scaling, news, correlation, and DEMOTE changes. Run the same invariant for market, pending, reversal, forex, JPY, metals, indices, and crypto paths.

### Broker identity and time

Resolve a market fill to the actual position ticket using broker-provided position identity or a constrained post-fill position query; do not assume order ticket equals position ticket. Standardize remaining MT5 timestamps on aware UTC datetimes.

### News freshness

A failed refresh never changes the successful-fetch timestamp. Stale events remain stale. Inject the configured Firecrawl service/client rather than creating an unconfigured instance. Empty/stale policy remains explicit by asset class.

### Auth and signal coherence

Protect all mutating and expensive endpoints by method policy, with an explicit public allowlist for health/status reads. Never log or append generated API keys.

Claude response validation must reject incoherent direction/SL/TP output rather than auto-flipping or swapping it before the main validator.

### Confidence and exits

Apply confidence modifiers in one immutable decision pass: collect boosts, penalties, and caps, then compute the final value once. Caps are applied after boosts and cannot be undone.

Set `a_plus` explicitly from setup grade/confluence criteria. Only A+ trades skip TP1 by policy; ordinary intraday/swing trades retain configured partial behavior. Persist the classification.

## Wave 3: Win Measurement and Replay Parity

### Unified decision telemetry

Replace scattered best-effort funnel calls with one decision recorder covering every terminal signal outcome:

- no trade
- rejected by each mechanical gate
- Judge reject/demote/failure
- pending placed, filled, expired, cancelled
- market filled
- execution failure

Store decision ID, market snapshot reference, entry/SL/TP, confidence components, session, mode, and reason.

### MFE/MAE outcome worker

A periodic worker evaluates blocked/demoted/unfilled decisions after configured horizons using MT5Client data, not direct platform imports. It records:

- MFE and MAE in R
- whether TP or SL would have hit first using deterministic same-bar rules
- spread/cost-adjusted hypothetical result
- data completeness

The worker is idempotent and available on Windows production and mocked cross-platform tests.

### Replay parity

Create shared policy components used by live and replay for:

- signal coherence
- confidence computation
- judge failure/DEMOTE semantics
- pending placement, fill, and expiry
- symbol-specific spread/slippage
- partial exits, break-even, trailing, and giveback
- reservation/risk accounting

Replay reports both strategy result and execution-policy result. Walk-forward optimization uses bot-only data, preserves chronological holdouts, and reports multiple-testing limitations.

### Promotion standard

A configuration is eligible for paper promotion only when out-of-sample results meet documented thresholds for:

- expectancy after costs
- profit factor
- maximum drawdown
- minimum sample size
- fill rate
- false-rejection rate
- stability across symbols/sessions

Live capital remains blocked until at least 100 paper trades complete without accounting drift or untracked positions.

## Wave 4: Release Hardening

### Verification

- Repair all existing Python failures without weakening intentional behavior.
- Remove async database/thread warnings by closing sessions and draining tasks.
- Fix the dashboard TypeScript build.
- Remove or ignore macOS AppleDouble files and normalize accidental line-ending churn.
- Run Python compilation, full pytest, dashboard production build, and lint/diagnostics.
- Add Windows MT5 smoke tests for symbol specs, fill identity, pending lifecycle, and history-based P/L.

### Documentation and configuration

Update README, executive summary, environment example, risk documentation, and canvas trackers to match:

- judge failure policy
- A+ exit policy
- authentication requirements
- strict-session behavior
- schema/table count
- model names
- test and platform limitations

Taskmaster MCP is currently unavailable; implementation tracking will use the repository plans and Cursor task list until the server is restored.

## Error Handling

- Accounting release is idempotent and logs duplicate attempts without mutating totals.
- Database persistence failures block subsequent destructive position actions when state durability is required.
- Unknown broker identity keeps the fill in a reconciliation state rather than pretending the position is absent.
- Missing market data produces an explicit unknown outcome, not a win/loss guess.
- Infrastructure failures never become implicit trading approvals.

## Testing Strategy

Every behavior follows red-green-refactor:

1. Add a behavioral test against the real production entry point or shared policy.
2. Run it and confirm the expected failure.
3. Add the smallest production change.
4. Run focused tests and confirm they pass without warnings.
5. Run the wave regression suite.

Source-text assertions do not count as proof for new behavior. Mocks are limited to external boundaries such as MT5 and Claude.

## Acceptance Criteria

- No post-reservation return leaks a slot or risk amount.
- Every fill/close path invokes exactly one close lifecycle.
- Position state survives a forced restart without duplicate partials.
- Judge infrastructure failure causes zero orders.
- Final broker-bound risk never exceeds the configured cap within tolerance.
- News failure cannot refresh stale data timestamps.
- Every terminal gate decision is queryable and at least 95% of eligible historical decisions receive MFE/MAE outcomes.
- Replay and paper execution produce matching decisions for a frozen fixture set.
- Full Python suite, dashboard production build, compile checks, and lint checks pass without async resource warnings.
- Documentation matches runtime behavior.
