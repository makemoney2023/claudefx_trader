# Paper ICT Confirmation Activation

## Objective

Activate the existing setup-family ICT confirmation gate for the configured
demo/paper environment without changing the safe global default used by future
live deployments.

## Configuration

- Set `TRADING_ICT_CONFIRMATION_MODE=active` in `.env.local`.
- Keep `TradingSettings.ict_confirmation_mode` defaulting to `shadow`.
- Keep correlation group sizing in shadow mode.

In active mode, incomplete continuation and liquidity-reversal confirmations,
and invalid passive retracement prerequisites, become hard mechanical rejects.
No confirmation thresholds or setup-family rules change.

## Data flow

`PostClaudeGateInput` builds a `TradeContext`. The shared entry pipeline creates
the setup fingerprint and calls `evaluate_ict_confirmation_gate`. Active-mode
failures return `gate_id=ict_confirmation` through the existing terminal
decision and counterfactual telemetry paths. Passing setups continue through
the unchanged risk, judge, and execution stages.

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
