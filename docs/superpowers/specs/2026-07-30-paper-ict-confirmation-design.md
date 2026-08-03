# Paper ICT Confirmation Activation

## Objective

Activate the existing setup-family ICT confirmation gate for the configured
demo/paper environment without changing the safe global default used by future
live deployments.

## Configuration

- `TradingSettings.ict_confirmation_mode` defaults to `active`.
- Set `TRADING_ICT_CONFIRMATION_MODE=active` explicitly on the VPS `.env.local`
  (required if an older env still pins `shadow`).
- Keep correlation group sizing in shadow mode.

In active mode, incomplete continuation and liquidity-reversal confirmations,
and invalid passive retracement prerequisites, become hard mechanical rejects.
No confirmation thresholds or setup-family rules change.

Wrong-zone entries (short below 50% retrace / long above 50%) are also
hard-blocked by the zone gate unless both directional sweep and displacement
are present — conf/RR alone is no longer a bypass.

## Data flow

`PostClaudeGateInput` builds a `TradeContext`. The shared entry pipeline creates
the setup fingerprint and calls `evaluate_ict_confirmation_gate`. Active-mode
failures return `gate_id=ict_confirmation` through the existing terminal
decision and counterfactual telemetry paths. Passing setups continue through
the unchanged risk, judge, and execution stages.

In `shadow` mode, would-block events are recorded as outcome_type
`shadow_would_block` (a recognized `TERMINAL_OUTCOMES` value in `gate_funnel`)
for telemetry only — the trade path continues.

## Safety and verification

- Verify runtime settings load `ict_confirmation_mode=active`.
- Run focused active-mode tests for continuation, reversal, and passive limits.
- Update the stale zone-conversion wiring test to inspect the shared
  `parity_gates` implementation.
- Run the complete test suite.
- Do not start automatically; `TRADING_AUTO_START_BOT=false` remains unchanged.

## Rollback

Change `TRADING_ICT_CONFIRMATION_MODE` back to `shadow` and restart the backend.
No database or strategy migration is required.
