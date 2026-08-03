# Mechanical Opportunity Scanner — Design

**Date:** 2026-08-03  
**Status:** Approved for implementation

## Goal

Continuously scan a curated MT5 universe with cheap mechanical ICT rules (no Claude), promote the best candidates into a temporary hot list, and merge that list into the existing Claude → gates → judge → execute pipeline.

## Approach

Temporary hot-list promotion:

- Scanner ranks opportunities every N seconds.
- Top K symbols merge into trading cycles for a TTL.
- `TRADING_SYMBOLS` remains the durable base list.
- Hard filters still apply before Claude (spread, market open, blocked pairs, data quality, zone).

Claude is never used in the scan phase.

## Universe

1. MT5 Market Watch (primary)
2. Plus current `settings.trading.symbols`

Fail-closed filters:

- Drop `BLOCKED_PAIRS` and `*BTC` / `*BIT` quote pairs
- Keep metals, USD crypto in `CRYPTO_SYMBOLS`, and USD forex
- `is_market_open(symbol)` must pass
- Soft OHLCV freshness; skip stale symbols
- Cap at `opportunity_scanner_max_universe` (default 40)

## Scoring

Reuse `ICTStrategy.analyze` with `require_tradeable_session=False` for scan mode (default `True` preserves legacy behavior).

Hard reject before hot-list:

- Zone misaligned (buy premium / sell discount)
- Spread blocked
- No valid mechanical setup
- R:R below scan floor (default 1.5)

Rank:

```
score = confluence_count * 0.25
      + confidence * 0.35
      + min(rr, 4) / 4 * 0.25
      + (0.15 if in_kill_zone or crypto else 0)
```

## Hot list

- Promote top K (default 3) not already in base symbols
- TTL default 60 minutes; refresh on re-rank
- Evict expired / market-closed / stale
- Optional persist next to bot state for restart
- Do not rewrite `.env` `TRADING_SYMBOLS`

## Cycle merge

```
cycle_symbols = filter(base_symbols ∪ hot_list.symbols)
```

Friday / news / market-hour filters apply after merge.

## Background loop

- Feature flag `TRADING_OPPORTUNITY_SCANNER_ENABLED` (default false)
- Interval default 150s
- Launched with the bot background task

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/opportunities` | Latest ranked results |
| `GET /api/opportunities/hot` | Hot list + TTLs |
| `POST /api/opportunities/scan` | Force scan |
| `POST /api/opportunities/promote/{symbol}` | Manual promote |
| `DELETE /api/opportunities/hot/{symbol}` | Remove |

## Dashboard

Thin Opportunities page: rank, symbol, direction, score, confluence, zone, KZ, TTL, promote/remove.

## Related fix

Hard zone / limit-zone checks run **before** the trade judge so approved setups cannot be killed post-judge by zone gates.

## Non-goals (v1)

- Full-broker catalog scan
- Claude in the screener
- Permanent auto-mutation of `TRADING_SYMBOLS`
- Judge overriding zone gates
