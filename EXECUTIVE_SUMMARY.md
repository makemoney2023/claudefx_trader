# Executive Summary: ICT Trading Bot & Dashboard

## Overview

This project is a fully autonomous, AI-powered trading system built on ICT (Inner Circle Trading) methodology. It combines real-time market execution via MetaTrader 5, institutional-grade technical analysis through 22 purpose-built modules, and AI-driven decision-making powered by Anthropic's Claude. A 17-page Next.js dashboard provides complete operational control, performance analytics, and market intelligence.

The system is designed around a single goal: compound a trading account from $1,000 to $100,000 through disciplined, risk-managed, ICT-aligned execution -- with every trade reviewed, scored, and fed back into a learning loop that improves over time.

---

## Architecture at a Glance

```
MetaTrader 5 (execution)
       |
  Python Backend (FastAPI, async)
  ├── 22 Analysis Modules (ICT concepts)
  ├── Claude AI (chart vision + structured signals)
  ├── Staged Trade Pipeline (analysis → Claude → shared gates → execution → fill)
  ├── 27 Services (pipeline stages, risk, learning, scaling, news, intelligence)
  ├── Execution Engine (orders, positions, trailing, multi-TP)
  ├── Backtesting Suite (standard, AI replay, walk-forward) — shares live gate/judge policy
  ├── SQLite Database (9 tables, WAL mode)
  └── WebSocket Server (5 channels, real-time push)
       |
  Next.js 14 Dashboard (TypeScript, Tailwind)
  ├── 17 Pages (monitoring, analytics, config)
  ├── 12 Components (charts, signals, intelligence)
  ├── 30+ API Endpoints across 18 route groups
  └── WebSocket Client (auto-reconnect, adaptive polling)
```

---

## Core Value Propositions

### 1. AI-Augmented Trade Analysis

Every potential trade passes through Claude's vision model, which analyzes chart screenshots across multiple timeframes (H4, H1, M15). The AI receives 21 ICT strategy documents as context, ensuring its analysis is grounded in a specific, proven methodology rather than generic pattern recognition.

The output is a structured trade signal: direction, confidence score, entry price, stop loss, take profit, R:R ratio, market structure context, AMD phase, and detailed reasoning. A **Judge system** then evaluates each signal (APPROVE / DEMOTE / REJECT), with the accuracy of these judgments tracked over time to calibrate the system's decision quality.

**Fail-closed judge policy:** infrastructure failures (missing client, timeout, exception, malformed verdict) map to `UNAVAILABLE` and block execution entirely. Only an explicit `DEMOTE` allows reduced-size or pending-order execution. `REJECT` and `UNAVAILABLE` always release reservations.

**API security:** set `BOT_API_KEY` in production to protect mutating routes and expensive LLM endpoints. Set `STRICT_ICT_SESSIONS=true` to enforce ICT kill-zone timing fail-closed.

This is not a black-box AI. The bot provides full transparency into why every trade was taken or rejected, with all reasoning logged and queryable.

### 2. Institutional-Grade ICT Strategy Engine

The analysis layer implements the complete ICT/SMC framework across **22 dedicated Python modules**:

| Category | Modules |
|----------|---------|
| **Structure** | Market structure (BOS, CHoCH, MSS), swing validation, regime classification |
| **Entry** | Fair value gaps, order blocks, optimal trade entry (Fibonacci/OTE), Silver Bullet, bar extreme zones |
| **Context** | Liquidity mapping, premium/discount zones, IPDA, NWOG, displacement, volume analysis, volume profile |
| **Timing** | Kill zones, Power of 3, AMD cycle |
| **Assets** | Silver analysis, precious metals, crypto analysis |
| **Multi-TF** | MTF analyzer (H4 bias, H1 confirmation, M15 execution) |

An **execution model hierarchy** ranks setups by quality: Unicorn (OB + FVG overlap) > Silver Bullet > 2022 Model > Standard OB/FVG. Confluence scoring starts at 0.5 and adds 0.15 per factor (FVG, order block, liquidity sweep, kill zone), producing a quantified confidence level for every setup.

A **zone gate** enforces premium/discount alignment. Trades at or above **60% confidence** are eligible in replay and live; zone-misaligned trades still require higher R:R (3:1), preventing the most common ICT mistake: buying in premium or selling in discount without enough reward.

### 3. Multi-Layer Risk Management

Risk is controlled at five independent levels:

1. **Per-trade**: Fixed 1% risk with equity-tier-based position sizing (6 tiers from <$500 to $100K+)
2. **Per-day**: 3% daily drawdown cap and configurable max daily trades
3. **Per-week**: 6% weekly drawdown limit
4. **Per-symbol**: Historical win rate below 40% blocks the symbol entirely; 40-50% reduces size
5. **Per-session**: Session-specific multipliers based on tracked performance (Asian, London, NY, overlap)

The **Scaling Manager** automatically selects one of four trading modes based on real-time performance:

| Mode | Risk Multiplier | Setup Filter | Min Confidence | Trigger |
|------|----------------|--------------|----------------|---------|
| Aggressive | 1.15x | All grades | 65% | Strong WR, low drawdown |
| Normal | 1.0x | A and B | 70% | Default |
| Conservative | 0.5x | A and B | 65% | Losing streak or weak edge |
| Defensive | 0.25x | A only | 90% | Drawdown breach or edge collapse |

A **correlation service** prevents concentrated exposure: pairs with >0.8 correlation are blocked from simultaneous positions; 0.6-0.8 correlation triggers position size reduction.

### 4. Adaptive Learning System

The bot gets smarter with every trade. After each loss and each significant winner (>2R), Claude reviews the trade and extracts lessons. These reviews are stored in a structured database and consolidated weekly into a **knowledge base** with confidence-scored insights.

The learning pipeline includes:

- **Judge accuracy tracking**: Measures how often APPROVE vs DEMOTE decisions led to winners, enabling the system to calibrate its own filtering
- **False rejection analysis**: Identifies rejected signals that would have been winners, preventing the system from becoming overly conservative
- **Confidence calibration**: Compares stated confidence levels against actual win rates to detect and correct systematic over/under-confidence
- **Setup playbook**: A performance matrix of setup type x symbol x session x direction, revealing which specific combinations produce the best results
- **Reactive levels**: Historical price levels with tracked win rates, building an evolving map of significant prices

This learning context is fed back into Claude's analysis prompt, creating a feedback loop where past mistakes actively inform future decisions.

### 5. Sophisticated Position Management

Once a trade is open, the **Position Manager** handles it through a multi-stage exit framework:

- **TP1 (1R)**: Close 40% of position, move stop to breakeven
- **TP2 (2R)**: Close 30% of position, activate trailing stop
- **Runner (remaining 30%)**: Trail with dynamic stops that lock 50% of profit above 1R

Two protective mechanisms prevent profit giveback:

- **Peak profit protection**: If unrealized profit reaches 1R+ and then gives back more than 55% (65% for crypto), the position is closed
- **Near-TP reversal protection**: If price reaches 85%+ of TP distance and then reverses 60-70%, the position is closed to capture the move
- **A+ exits**: Only positions explicitly classified as A+ at creation (high setup grade + confluence) skip TP1 and use accelerated protection; ordinary trades keep standard multi-TP staging

Pending orders receive kill-zone-based expiration (auto-cancel when the active session ends) and are synced with MT5 to handle fills, cancellations, and fast SL/TP hits.

**Decision telemetry schema:** `decision_records` captures every terminal gate outcome (approve, demote, reject, mechanical block, expired, cancelled). The outcome worker attaches MFE/MAE and hypothetical TP/SL results to blocked and unfilled decisions for expectancy analytics.

**Replay limitations:** Backtest/replay shares live judge, pending, final-risk, confidence, post-Claude gates, and exit policy through the `post_claude_gates` and `trade_judge` modules — verified by dedicated parity tests. Optional `scaling_manager`, `correlation_service`, and `news_service` can be injected into `ClaudeReplayBacktester` to match live gating. macOS/Linux still cannot verify Windows MT5 ticket identity, broker tick values, or live news calendar freshness; run `python test_mt5_connection.py` on Windows before paper promotion.

### 6. Market Intelligence Pipeline

The system ingests external market intelligence through **Firecrawl** web scraping:

- **DXY (Dollar Index)** trend and strength
- **VIX** volatility regime
- **COT (Commitment of Traders)** positioning data
- **Central bank** rate expectations and stance
- **Retail sentiment** (contrarian signal)
- **Intermarket correlations**

An **economic calendar** with high-impact event detection (NFP, FOMC, CPI, GDP) creates automatic **blackout windows** that halt trading before and during major releases. Geopolitical risk filtering adds another layer of event awareness.

Data refreshes on a tiered schedule: news every 5 minutes, extraction every 15 minutes, agent analysis every 30 minutes -- balancing freshness with API cost.

### 7. Production-Ready Infrastructure

This is not a prototype. The system includes:

- **Database**: SQLite with WAL mode for concurrent reads, automatic pre-migration backups, schema migrations for forward compatibility
- **Reliability**: Single-instance file locking, graceful shutdown handlers (SIGINT/SIGTERM), async event loop with separate position management and trading cycles
- **Security**: API key authentication (REST + WebSocket), CORS configuration, rate limiting (slowapi)
- **Real-time communication**: WebSocket server with 5 channels (trades, prices, analysis, activity, all), broadcasting 30+ event types from trade lifecycle, signal generation, and bot state changes. Frontend hooks with auto-reconnect, exponential backoff, and ping/pong keepalive
- **Notifications**: Telegram bot integration for trade alerts, errors, and status updates
- **State persistence**: Full bot state serialized to JSON for crash recovery (streaks, scaling mode, signal hashes, pending orders, goal tracker snapshots)
- **Testing**: 67 test modules (1,180+ tests) covering critical paths, the staged trade pipeline, post-Claude gate/replay parity, judge policy, edge protection, live readiness, learning system integration, WebSocket infrastructure, and individual analysis modules
- **Duplicate prevention**: Signal hashing prevents the same setup from being traded twice
- **Cooldowns**: Post-loss cooldown (15-30 min) and per-symbol analysis cooldown (5 min) prevent revenge trading and API waste

---

## Dashboard UI

The dashboard is a 17-page, dark-themed trading control center built with Next.js 14, TypeScript, and Tailwind CSS. It provides complete visibility into and control over every aspect of the trading operation.

### Pages

| Page | Purpose |
|------|---------|
| **Dashboard** | Overview: equity curve, session status, recent signals, pending orders, market intelligence, open positions |
| **Bot Activity** | Real-time bot logs (3s polling), start/stop control, MTF analysis output, Claude reasoning |
| **Positions** | Active position management, emergency close, SL/TP modification, margin health |
| **Trades** | Full trade history with filtering, pagination, CSV/JSON export |
| **Performance** | Win rate, expectancy, ICT concept performance, per-symbol analytics, Edge Tracker |
| **Analysis** | Live ICT analysis: market structure, FVGs, order blocks, OTE, AMD phase |
| **Intelligence** | Firecrawl market data: DXY, VIX, retail sentiment, intermarket analysis, deep research |
| **Learning** | Claude trade reviews, mistake patterns, winning patterns, knowledge base, weekly reports |
| **Scaling** | Current tier, risk multiplier, drawdown status, position sizing breakdown |
| **Goal** | $1K to $100K progress tracker, milestones, compound growth calculator |
| **Sessions** | Per-session performance (Asian, London, NY, overlap), session schedule |
| **Calendar** | Economic calendar, blackout status, geopolitical risk indicators |
| **Precious Metals** | Gold/silver analysis, gold-silver ratio, safe-haven demand indicators |
| **Silver** | Silver 1979 pattern analysis, key levels, trade setup |
| **Crypto** | XRP/ADA/BTC/ETH/SOL analysis, key levels, regulatory risk tracking |
| **Backtesting** | Three modes: standard ICT backtest, Claude Replay, walk-forward optimizer |
| **Settings** | Full configuration: MT5 connection, API keys, symbols, risk params, sessions, alerts |

### Key Components

| Component | Function |
|-----------|----------|
| **EquityChart** | Canvas-rendered 90-day equity curve with gradient fill |
| **EdgeHealthCard** | Real-time edge score (0-100), per-symbol health, win rate sparkline, alerts |
| **TradeMonitor** | Split view of open positions vs recent closed trades |
| **MarketIntelligence** | Expandable market data panel (DXY, VIX, retail, intermarket) |
| **RecentSignals** | Last 10 trade signals with direction, confidence, and reasoning |
| **SessionStatus** | Kill zone schedule with active session indicator |
| **AMDPhaseIndicator** | Current Accumulation/Manipulation/Distribution phase |
| **PendingOrdersTable** | Active pending orders with expiration countdown and cancel action |
| **NotificationDropdown** | Activity feed of trades, signals, and system events |

### Real-Time Communication

The dashboard receives real-time updates through a **hybrid WebSocket + adaptive polling** architecture:

**WebSocket Layer (5 channels):**

| Channel | Events Pushed |
|---------|--------------|
| **trades** | Trade opened/closed, pending order placed/filled/cancelled, position management actions (TP hits, trailing stop moves, protection closes) |
| **prices** | Live bid/ask streaming every 2 seconds for symbols with open positions |
| **analysis** | New signal generated, AI analysis complete |
| **activity** | 30+ bot events: mode changes, news blackouts, cooldowns, edge health shifts, tier promotions/demotions, symbol blocks |
| **all** | Aggregated feed of all channels |

**Frontend Hooks:**

- `useWebSocket`: Core hook with auto-reconnect (exponential backoff up to 30s), ping/pong keepalive, API key authentication, and typed message parsing
- `useWebSocketWithPolling`: Hybrid hook that combines WebSocket events with adaptive polling -- slow intervals (60-120s) when connected, fast fallback (3-30s) when disconnected, with immediate debounced re-fetches on relevant WebSocket messages
- Connection status indicator in the Header shows live WebSocket connectivity

**Resilience:** If the WebSocket connection drops, components automatically fall back to fast polling and re-synchronize state on reconnect. No data is lost -- WebSocket accelerates delivery, polling guarantees eventual consistency.

---

## Backtesting and Optimization

Three distinct backtesting modes serve different validation needs:

1. **Standard ICT Backtest**: Runs the strategy against historical OHLCV data with simulated order execution (spread, slippage, commission). Produces win rate, Sharpe ratio, profit factor, max drawdown, and R-multiple statistics.

2. **Claude Replay**: Replays historical data through the full AI pipeline -- chart generation, Claude analysis, signal filtering, and position management. This validates the complete decision chain, not just the technical strategy, and tracks MFE (Maximum Favorable Excursion) and MAE (Maximum Adverse Excursion) per trade.

3. **Walk-Forward Optimizer**: Optimizes gate parameters (minimum confidence, R:R floor, session penalties, cooldown periods) using in-sample/out-of-sample walk-forward methodology. This prevents overfitting by validating that optimized parameters generalize to unseen data.

---

## Asset Coverage

The system trades across five asset classes with symbol-specific configuration:

| Asset Class | Symbols | Special Handling |
|-------------|---------|------------------|
| **Forex** | Major and minor pairs | Session-based timing, pip-based sizing |
| **Precious Metals** | XAUUSD, XAGUSD | Wider stops, gold-silver ratio analysis, safe-haven context |
| **Crypto** | BTCUSD, ETHUSD, XRPUSD, ADAUSD, SOLUSD | 24/7 trading, higher giveback threshold (65%), weak-hour filtering |
| **Indices** | US30, NAS100, SPX500 | Daily break handling, index-specific hours |
| **Energy** | XTIUSD (Oil) | Daily break handling, commodity-specific analysis |

Every symbol has defined contract size, pip size, pip value, minimum SL distance, tick value, volume constraints, and swap rates. These specs are synced from MT5 at runtime to stay current with broker conditions.

---

## Technical Metrics

| Metric | Count |
|--------|-------|
| Analysis modules | 22 |
| ICT strategy documents | 21 |
| Backend services | 27 |
| API route groups | 18 |
| WebSocket channels | 5 |
| Database tables | 9 |
| Dashboard pages | 17 |
| Dashboard components | 12 |
| Test modules | 67 (1,180+ tests) |
| Python dependencies | 20+ |

---

## Growth Architecture

The system is designed around a structured path from $1,000 to $100,000:

| Tier | Equity Range | Risk/Trade | Max Lots | Max Daily Trades |
|------|-------------|------------|----------|------------------|
| 1 | $0 - $500 | 1.0% | 0.01 | 2 |
| 2 | $500 - $2,500 | 1.0% | 0.05 | 3 |
| 3 | $2,500 - $10,000 | 1.5% | 0.20 | 4 |
| 4 | $10,000 - $25,000 | 1.5% | 0.50 | 4 |
| 5 | $25,000 - $100,000 | 2.0% | 2.00 | 5 |
| 6 | $100,000+ | 2.0% | 5.00 | 5 |

As equity grows, the system automatically adjusts position sizing, risk tolerance, and trade frequency. The Goal Tracker visualizes progress through milestones, and a compound growth calculator projects timelines based on current performance metrics.

---

## What Makes This System Unique

1. **Not a black box**: Every decision is logged with full reasoning, from Claude's analysis to the gate logic that filtered it, to the judge that approved it. Complete auditability.

2. **Self-improving**: The learning loop means the system's performance should improve over time as it accumulates knowledge about which setups work, which symbols to avoid, and how its own confidence maps to reality.

3. **Defensive by default**: Five independent risk layers ensure that no single failure mode -- a bad streak, a volatile session, a correlated exposure -- can cause catastrophic loss.

4. **Methodology-specific**: This is not a generic "AI trading bot." Every module, every document, every prompt is built around ICT/SMC concepts. The AI is constrained to think and trade like an ICT practitioner.

5. **Full-stack operational**: From market data ingestion to trade execution to post-trade review to performance visualization -- the entire trading workflow is automated and monitored through a single system.

---

*Document generated from codebase review -- March 2026; updated July 2026 (staged pipeline extraction, shared post-Claude gates, live/replay parity).*
