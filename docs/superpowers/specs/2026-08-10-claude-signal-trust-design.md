# Claude Signal Trust (Wide) — Design

**Date:** 2026-08-10  
**Status:** Implemented  
**Flag:** `TRADING_CLAUDE_SIGNAL_TRUST_MODE` (`off` | `active`)

## Goal

When Claude emits a tradeable long/short signal, do **not** let strategy-redundancy gates hard-block execution. Keep only account-survival / broker-reality gates. Trust Claude on the trade idea; do not trust Claude with the account.

## Trigger

All of:

1. `TRADING_CLAUDE_SIGNAL_TRUST_MODE=active`
2. Normalized direction is `long` or `short` (not `no_trade`)
3. Entry / SL / TP present after price normalization

Telemetry tag on every bypassed hard-block: `claude_trust_bypass:<gate_id>`.

## Soft-pass when triggered (log only, never hard-block)

| Surface | Notes |
|---------|--------|
| Direction alignment / counter-D1 floors | Includes scalp RR floors |
| Zone gate | Wrong-zone / premium-discount |
| Zone conversion / demote-to-OTE / disp parity | Keep Claude `order_type` |
| Pre-judge zone extremes | Market and limits |
| Volatile regime + TOD conf floors | |
| M15 bias | |
| HTF dual-oppose | |
| AMD distribution RR | |
| Confluence min-count | |
| Off-hours / post-cooldown strategy caps | |
| Volume floor (incl. &lt;0.3x) | Wide scope — intentional |
| ICT confirmation | Already default `disabled`; keep skip |
| Scaling / min-confidence setup-grade | |
| Correlation strategy soft-blocks | Soft-pass; keep hard group risk dollar cap if active |
| Playbook hard gate | Soft-pass `playbook_block` (`claude_trust_bypass:playbook_block`) |
| Prepare-order limit zone / auto-convert | Keep Claude market; skip extreme-limit zone hard-block (`claude_trust_bypass:limit_zone`) |
| Judge **DEMOTE** | Ignore demote → keep Claude’s emitted order type + prices (`claude_trust_demote_ignored`) |

## Still hard (never bypassed by this flag)

| Surface | Why |
|---------|-----|
| Data quality / stale OHLCV | Blind Friday bars |
| Spread block | Broker reality |
| Min R:R | Risk geometry |
| Invalid prices / SL wrong side | Sanity |
| FINAL-RISK / max lot / normalize | Account survival |
| Daily loss / max daily trades / drawdown stops | Account survival |
| BTC / blocked symbols | Known blow-up |
| Symbol not tradeable | Broker |
| Judge **REJECT** | Explicit veto stays |
| Flip guard / direction circuit breaker | Anti-revenge / streak safety |

## Default / rollback

- Config default: `off` (opt-in).
- Local + VPS `.env.local`: set `TRADING_CLAUDE_SIGNAL_TRUST_MODE=active` when deploying this.
- Rollback: `off` + restart. No DB migration.

## Non-goals

- Rewriting Claude prompts/tools
- 1m execution TF / re-entry loops
- Changing risk % / position sizing formula
- Auto-APPROVE on Judge UNAVAILABLE
- Removing gates from code permanently (shadow/bypass only under flag)

## Success criteria

1. Replayed NY-style case: Claude LONG `buy_limit`, HTF aligned, no displacement → reaches order prep / Judge with `claude_trust_bypass` tags; no `[BLOCKED] Passive retracement missing: displacement_origin`.
2. With trust `off`, prior active ICT / M15 / volume blocks still behave as before.
3. Stale M15 / spread / FINAL-RISK still hard-block under trust `active`.

## Relationship to lean

Lean (`TRADING_LIQUIDITY_REVERSAL_LEAN_MODE`) remains for sweep-fade-specific prompt/KZ behavior. Trust is broader and **does not require** a fresh sweep. When both active, trust supersedes strategy hard-blocks; lean tags may still appear for analytics.
