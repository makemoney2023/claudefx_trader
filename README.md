# ICT Trading Bot

An AI-powered forex trading bot combining ICT (Inner Circle Trading), Market Maker, and Fair Value Gap strategies with MetaTrader 5 via MCP, using Claude Opus 4.5 for intelligent chart analysis and trade execution.

## Features

- **ICT Strategy Implementation**: Full Inner Circle Trading methodology including:
  - Market Structure Analysis (BOS, CHoCH, MSS)
  - Fair Value Gap Detection
  - Order Block Identification
  - Liquidity Pool Mapping
  - Kill Zone/Session Timing

- **AI-Powered Analysis**: Claude Opus 4.5 vision capabilities for chart analysis
- **MT5 Integration**: MetaTrader 5 connectivity through MCP server
- **Risk Management**: Professional position sizing and trade validation
- **Multi-Timeframe Analysis**: HTF bias with LTF entry refinement

## Project Structure

```
trading_bot/
├── __init__.py              # Package initialization
├── config.py                # Configuration + per-symbol specs
├── main.py                  # TradingBot orchestrator (cycles, state, telemetry)
├── analysis/                # 22 technical analysis modules
│   ├── market_structure.py  # BOS, CHoCH, MSS detection
│   ├── fair_value_gap.py    # FVG detection
│   ├── order_blocks.py      # Order block detection
│   ├── liquidity.py         # Liquidity pool mapping
│   ├── kill_zones.py        # Session timing
│   ├── mtf_analyzer.py      # Multi-timeframe bias
│   ├── regime_classifier.py # Trend/range regime
│   ├── amd_cycle.py         # Accumulation/Manipulation/Distribution
│   ├── silver_bullet.py     # Silver Bullet windows
│   ├── volume_profile.py    # POC/VAH/VAL
│   └── ...                  # fibonacci, ipda, nwog, displacement, etc.
├── llm/                     # Claude integration
│   ├── claude_client.py     # Claude API client (chart vision + judge)
│   ├── context_builder.py   # Strategy context loader
│   └── prompts.py           # Prompt templates
├── services/                # Pipeline stages + supporting services
│   ├── trade_pipeline.py            # TradePipeline orchestrator
│   ├── analyze_and_trade_runner.py  # run_analyze_and_trade (live flow)
│   ├── analysis_orchestrator.py     # MTF/chart package building
│   ├── claude_analysis_stage.py     # ClaudeAnalysisStage (run_stage)
│   ├── expanded_analysis.py         # Extended ICT analysis
│   ├── post_claude_gates.py         # Shared post-Claude gate chain (live/replay)
│   ├── entry_gates.py / gate_pipeline.py / scaling_gates.py
│   ├── signal_normalizer.py         # Price normalization + flip detection
│   ├── trade_judge.py               # Live + replay judge adapter
│   ├── trade_context.py             # TradeContext model
│   ├── scaling_manager.py           # Trading modes + drawdown control
│   ├── correlation_service.py       # Correlated exposure control
│   ├── news_service.py              # Economic calendar + blackouts
│   ├── session_analytics.py         # Per-session performance
│   ├── goal_tracker.py              # $1K→$100K progress
│   ├── pending_order_manager.py     # Pending order lifecycle
│   ├── trade_reservations.py        # Trade slot reservations
│   ├── trade_learning_service.py    # Post-trade review + knowledge base
│   └── firecrawl_intelligence.py    # Market intelligence pipeline
├── execution/               # Trade execution
│   ├── risk_manager.py              # Base risk model
│   ├── scaling_position_sizer.py    # Tier-based position sizing
│   ├── order_manager.py             # Order placement
│   ├── position_manager.py          # Position tracking + multi-TP exits
│   ├── exit_policy.py               # A+ / peak-profit / reversal protection
│   ├── trade_execution.py           # ExecutionCoordinator
│   └── trade_fill_handler.py        # Post-fill handling + reservation release
├── backtesting/             # Validation suite
│   ├── engine.py                    # Standard ICT backtest
│   ├── replay.py                    # ClaudeReplayBacktester (AI replay)
│   ├── replay_simulation.py         # Raw trade simulation
│   ├── execution_policy.py          # Shared judge/pending/exit policy
│   ├── optimizer.py                 # Walk-forward optimizer
│   ├── simulator.py / costs.py / metrics.py / report.py
├── mt5/                     # MT5 integration
│   ├── client.py            # MT5 MCP client
│   └── data_fetcher.py      # Market data retrieval
├── strategy/                # Trading strategies
│   └── ict_strategy.py      # ICT strategy implementation
├── api/                     # FastAPI backend
│   └── routes/              # 18 route groups + WebSocket server
├── utils/                   # Utility functions (logging, candles, charts)
└── docs/                    # 21 ICT strategy documents
    ├── ict_strategy.md
    ├── market_structure.md
    ├── fair_value_gap.md
    ├── order_blocks.md
    ├── liquidity_concepts.md
    ├── kill_zones.md
    ├── market_maker.md
    └── risk_management.md
```

## Installation

### Windows Setup (Recommended for Live Trading)

> **Deploying on a Windows VPS?** See the dedicated guide: [docs/WINDOWS_VPS_SETUP.md](docs/WINDOWS_VPS_SETUP.md)

**Prerequisites:**
- Python 3.10+ ([Download](https://www.python.org/downloads/))
- Node.js 18+ ([Download](https://nodejs.org/))
- MetaTrader 5 installed and logged in

**Quick Setup:**
```batch
# Run the automated setup script
setup_windows.bat
```

**Or use PowerShell:**
```powershell
.\setup_windows.ps1
```

**Manual Setup:**
```batch
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install Python dependencies
pip install -r requirements.txt
pip install MetaTrader5

# 3. Install dashboard dependencies
cd dashboard
npm install
cd ..

# 4. Copy and edit configuration
copy .env.example .env.local
notepad .env.local
```

### macOS/Linux Setup (Simulation Only)

Note: MT5 only runs on Windows. macOS/Linux will run in simulation mode.

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install dashboard
cd dashboard && npm install && cd ..

# 4. Configure
cp .env.example .env.local
```

## Configuration

Edit `.env.local` file with your settings:

```env
# MetaTrader 5 Configuration
MT5_LOGIN=your_mt5_login
MT5_PASSWORD=your_mt5_password
MT5_SERVER=your_broker_server

# Claude API Configuration
ANTHROPIC_API_KEY=your_anthropic_api_key

# API authentication for dashboard mutations and expensive LLM actions
BOT_API_KEY=generate-a-long-random-secret

# ICT session enforcement (block entries outside configured kill zones when true)
STRICT_ICT_SESSIONS=false

# Trading Configuration
TRADING_SYMBOLS=EURUSD,GBPUSD,XAUUSD
RISK_PER_TRADE=0.01
MAX_DAILY_TRADES=3
MIN_RISK_REWARD=2.0
ALLOWED_SESSIONS=london,new_york
```

## Usage

### Starting the Bot (Windows)

**Development (local testing):**
```batch
start_bot.bat
```

**Production (VPS / 24-7):**
```batch
start_bot_production.bat
```

**Individual components:**
```batch
start_api.bat
start_dashboard.bat
stop_bot.bat
```

> See [docs/WINDOWS_VPS_SETUP.md](docs/WINDOWS_VPS_SETUP.md) for full VPS deployment instructions.

**Test MT5 Connection First:**
```batch
python test_mt5_connection.py
```

**Test Telegram Notifications:**
```batch
python test_telegram_connection.py
```

> See [docs/WINDOWS_VPS_SETUP.md#telegram-notifications](docs/WINDOWS_VPS_SETUP.md#telegram-notifications) for full Telegram setup.

**Manual Start:**
```batch
# Terminal 1: Start API backend
start_api.bat

# Terminal 2: Start dashboard (production)
start_dashboard.bat

# Or for development:
cd dashboard
npm run dev
```

### Starting the Bot (macOS/Linux - Simulation Mode)

```bash
# Terminal 1: Start API backend
source venv/bin/activate
python -m uvicorn trading_bot.api.main:app --reload

# Terminal 2: Start dashboard
cd dashboard
npm run dev
```

### Access Points

- **Dashboard**: http://localhost:3000
- **API Backend**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

### Running Tests

The suite has 67 test modules (1,180+ tests) covering analysis modules, the staged
trade pipeline, post-Claude gate parity, judge policy, replay integration, and live
readiness. New work follows test-driven development.

```bash
# Full suite
pytest tests/ -v

# Pipeline + replay parity focus
pytest tests/test_pipeline_integration.py tests/test_replay_integration.py \
       tests/test_post_claude_gates.py -v
```

### Running with Coverage

```bash
pytest tests/ --cov=trading_bot --cov-report=html
```

### Running Backtests

Three validation modes are available (see [Backtesting](#backtesting-and-validation)):

```bash
# Standard ICT backtest against historical OHLCV
python -m trading_bot.backtesting.engine

# Claude AI replay / walk-forward optimizer are invoked via the
# backtesting runner and the dashboard Backtesting page
python -m trading_bot.backtesting.run
```

## Trade Pipeline Architecture

The live analyze-and-trade flow is a staged pipeline. `main.py` orchestrates but
delegates policy to dedicated modules so live and replay share the same logic.

```
TradePipeline.run(symbol)
        │
        ├── AnalysisOrchestrator      → MTF analysis, ICT modules, chart package
        ├── ClaudeAnalysisStage       → context build, Claude vision call, no_trade handling
        ├── signal_normalizer         → price normalization + direction-flip detection
        ├── post_claude_gates         → shared gate chain (phased in live, one-shot in replay)
        │     • ATR-SL adjustment, R:R hard floor, counter-trend scalp
        │     • entry gates (zone / M15 / HTF alignment)
        │     • permission gates (scaling, correlation, min-confidence)
        │     • flip guard
        ├── ExecutionCoordinator      → position sizing + broker order placement
        └── TradeFillHandler          → fills, DB, notifications, reservation release on failure
```

Key parity guarantees:

- **Shared gates**: `post_claude_gates.run_post_claude_gates()` is the single source of
  truth. Live runs it phased (`price` → `entry` → `permission`) so scaling mode can refresh
  between entry and permission; replay runs it one-shot. Both produce identical `gate_path`.
- **Shared judge**: `trade_judge.py` exposes the same fail-closed judge to live and replay.
- **Reservation safety**: execution failures release the trade reservation unless the fill is
  reconciled from MT5.

## Trading Strategy

The bot implements ICT (Inner Circle Trading) methodology:

1. **Higher Timeframe Bias**: Determine trend direction on H4/Daily
2. **Kill Zone Timing**: Trade only during London (2-5 AM EST) or NY (7-10 AM EST)
3. **Liquidity Sweep**: Wait for price to sweep obvious liquidity
4. **Entry at POI**: Enter at Order Block or Fair Value Gap
5. **Risk Management**: 1% risk per trade, minimum 1:2 R:R

## Key Components

### Market Structure Analyzer
Identifies trend direction and structure breaks (BOS, CHoCH, MSS) by analyzing swing highs and lows.

### Fair Value Gap Detector
Finds imbalance zones using the three-candle pattern where gaps exist between candle 1 and candle 3.

### Order Block Detector
Locates institutional entry zones - the last opposing candle before a significant move.

### Liquidity Mapper
Maps buy-side and sell-side liquidity pools, including equal highs/lows.

### Claude Integration
Uses Claude Opus 4.5 vision API to analyze chart screenshots and generate trade signals.

## Risk Management

### Operational policies (release hardening)

- **BOT_API_KEY**: Required for POST/PUT/PATCH/DELETE API routes and expensive LLM actions in production. Set in `.env.local`; never commit generated keys.
- **STRICT_ICT_SESSIONS**: When `true`, entries outside configured ICT kill zones are blocked fail-closed.
- **Trade Judge failure policy**: Judge timeouts, API errors, malformed verdicts, and missing client all map to `UNAVAILABLE` and **block execution entirely** (reservation released). Only an explicit `DEMOTE` permits reduced/pending execution.
- **A+ exits**: TP1 skip and accelerated profit protection apply only to positions explicitly classified and persisted as A+ at creation; ordinary intraday/swing trades keep standard multi-TP behavior.
- **Decision telemetry**: Every terminal gate outcome writes a `decision_records` row; blocked/DEMOTE/unfilled decisions receive MFE/MAE and hypothetical TP/SL outcomes via the outcome worker.
- **Replay limitations**: Backtest/replay routes shared judge, pending, final-risk, confidence, post-Claude gates, and exit policy. Optional `scaling_manager`, `correlation_service`, and `news_service` can be injected into `ClaudeReplayBacktester` for live parity. Windows MT5 broker fills, hedging ticket identity, and live news calendar freshness are not fully simulated on macOS/Linux.

### Windows MT5 verification

Live execution requires Windows with MetaTrader 5 installed. Run `python test_mt5_connection.py` on the Windows host before paper promotion. macOS/Linux development uses simulation mode only.

Optional Telegram alerts: run `python test_telegram_connection.py` after setting `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env.local`.

## Backtesting and Validation

Three modes validate different layers of the system:

1. **Standard ICT backtest** (`backtesting/engine.py`) — strategy against historical
   OHLCV with simulated spread/slippage/commission. Reports win rate, Sharpe, profit
   factor, max drawdown, R-multiples.
2. **Claude Replay** (`backtesting/replay.py`) — replays historical charts through the
   full AI pipeline (chart → Claude → shared post-Claude gates → judge → exit policy).
   Optional `scaling_manager`, `correlation_service`, and `news_service` can be injected
   into `ClaudeReplayBacktester` for live parity. Tracks MFE/MAE and separates raw-strategy
   R from execution-policy R.
3. **Walk-forward optimizer** (`backtesting/optimizer.py`) — optimizes gate parameters with
   in-sample/out-of-sample folds to avoid overfitting.

Live/replay parity is enforced by the shared `post_claude_gates` and `trade_judge` modules
and verified by `tests/test_replay_integration.py` and `tests/test_readiness_replay_parity.py`.

## Risk Management (strategy)

- **Fixed percentage risk**: 1% per trade (configurable)
- **Maximum daily risk**: 6% total
- **Minimum R:R**: 1:2 required for trade entry
- **Position management**: Break-even and trailing stops

## MetaTrader 5 Setup

Before the bot can trade, configure MT5:

1. **Enable Algo Trading:**
   - Open MT5 → Tools → Options → Expert Advisors
   - Check: ✅ Allow automated trading
   - Check: ✅ Allow DLL imports

2. **Enable AutoTrading:**
   - Click the "AutoTrading" button in the toolbar (should turn green)

3. **Keep MT5 Running:**
   - MT5 must be running and logged in while the bot operates

4. **Test Connection:**
   ```batch
   python test_mt5_connection.py
   ```

## Requirements

- **Windows** (required for live trading - MT5 only runs on Windows)
- Python 3.10+
- Node.js 18+
- MetaTrader 5 terminal
- Anthropic API key (for Claude)
- MT5 broker account

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## Disclaimer

This software is for educational purposes only. Trading forex involves substantial risk of loss and is not suitable for all investors. Past performance is not indicative of future results. Always test strategies on demo accounts before live trading.

## License

MIT License - see LICENSE file for details.
