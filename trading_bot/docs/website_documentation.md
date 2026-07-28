# ICT Trading Bot - Comprehensive Documentation

> **AI-Powered Forex & Crypto Trading with Institutional Intelligence**

An advanced algorithmic trading system that combines Claude AI's analytical capabilities with Inner Circle Trading (ICT) methodology and real-time market intelligence to execute institutional-grade trading strategies.

---

## 🎯 Overview

The ICT Trading Bot is a sophisticated automated trading system designed to capture 100+ pip expansion moves by identifying and trading with institutional order flow. It leverages:

- **Claude AI (Opus 5)** for intelligent chart analysis and decision-making
- **ICT Methodology** including AMD cycles, liquidity sweeps, and Smart Money concepts
- **Real-Time Intelligence** via Firecrawl integration for sentiment, positioning, and macro data
- **MetaTrader 5** for reliable trade execution across forex, crypto, and precious metals

### Release hardening notes

- Set `BOT_API_KEY` for production API mutations and expensive LLM routes.
- Set `STRICT_ICT_SESSIONS=true` to enforce ICT kill-zone timing fail-closed.
- Judge infrastructure failures (`UNAVAILABLE`) block execution entirely; only explicit `DEMOTE` permits reduced execution.
- A+ exit behavior, decision telemetry (`decision_records` + outcome worker), and replay policy parity are documented in `risk_management.md`.
- Windows MT5 verification (`python test_mt5_connection.py`) is required before paper promotion; macOS/Linux use simulation only.

---

## ✨ Key Features

### 🤖 AI-Powered Analysis

- **Vision-Based Chart Analysis**: Claude analyzes actual chart images to identify patterns, structure, and setups
- **Contextual Learning**: Learns from past trades to improve future recommendations
- **Confidence Scoring**: Every trade signal includes a confidence level based on multiple factors
- **Natural Language Reasoning**: Detailed explanations for every trade decision
- **Claude Opus 5 everywhere**: chart analysis, trade judge, position/pending re-evals, trade reviews, weekly insights, and sizing all run on Opus 5 with adaptive thinking (medium effort for analysis/judge/reviews with a 64k analysis output budget, low for sizing/re-evals)
- **Guaranteed-valid outputs**: the analysis tool uses strict tool use and the trade judge uses structured outputs (JSON schema), so signals and verdicts are always schema-valid
- **Prompt caching**: the static ICT ruleset (`ANALYSIS_RULES`), the trade-judge rubric (`JUDGE_RUBRIC`), the re-eval rules, and strategy docs all live in cached system blocks, cutting repeat token cost per scan cycle
- **Cost telemetry**: every API call logs a `[USAGE]` line and writes a row to the `api_usage` table (tokens, cache hits, estimated USD cost per task type)
- **Prompt telemetry**: `scripts/prompt_baseline_report.py` compares signal distributions before/after the prompt-v3 (Opus 5) cutover to measure behavioural drift

### 📊 ICT Strategy Implementation

- **AMD Cycle Detection**: Identifies Accumulation, Manipulation, and Distribution phases
- **Fair Value Gaps (FVG)**: Detects and tracks unfilled price imbalances
- **Order Blocks**: Identifies institutional supply and demand zones
- **Breaker Blocks**: Detects failed order blocks that become reversal zones
- **Liquidity Mapping**: Tracks buy-side and sell-side liquidity pools
- **Market Structure Analysis**: BOS, CHoCH, and MSS detection
- **Kill Zone Timing**: Optimal trading windows (London, NY, overlaps)
- **Silver Bullet Windows**: High-probability time-based entries

### 🎯 100-Pip Expansion System

- **Displacement Detection**: Confirms distribution phase with impulsive candles
- **IPDA Level Targeting**: Uses PDH/PDL/PWH/PWL as take profit targets
- **Premium/Discount Zones**: Ensures optimal entry locations
- **NWOG Tracking**: New Week Opening Gap targets for confluence
- **Multi-Tier Take Profits**: 30%/30%/40% scaling out strategy

### 📡 Real-Time Market Intelligence

Powered by Firecrawl API integration:

| Intelligence Source | Description | Usage |
|---------------------|-------------|-------|
| **DXY Analysis** | Dollar Index trend and bias | EUR/GBP correlation |
| **Retail Sentiment** | Crowd positioning (contrarian) | Trade against extremes |
| **VIX Sentiment** | Fear/greed gauge | Risk-on/risk-off mode |
| **Currency Strength** | Relative strength rankings | Pair selection |
| **TradingView Technical** | Community consensus | Signal confirmation |
| **Rate Expectations** | Fed/ECB/BOE outlook | Currency bias |
| **Options Flow** | Institutional positioning | Magnet levels |
| **Bond Yields** | US-DE spread | EUR/USD bias |
| **Social Sentiment** | Twitter/X forex chatter | Contrarian signals |
| **Seasonal Patterns** | Historical monthly bias | Additional confirmation |
| **Intermarket Analysis** | SPX/VIX/Gold correlations | Risk environment |
| **Economic Calendar** | High-impact events | Blackout periods |
| **BTC Dominance** | Crypto market structure | Altcoin sentiment |

### 💰 Risk Management

- **Dynamic Position Sizing**: Scales with account equity ($1K → $100K tiers)
- **Margin Validation**: Pre-trade checks prevent overleveraging
- **Correlation Protection**: Prevents excessive exposure to correlated pairs
- **Max Exposure Limits**: Configurable total account exposure caps
- **Emergency Controls**: One-click close all positions
- **News Blackouts**: Automatic trade blocking during high-impact events
- **Trailing Stops**: Dynamic stop loss management
- **Break-Even Logic**: Automatic stop movement to entry

### 📈 Performance Tracking

- **Goal Tracking**: Progress toward $100K target with milestones
- **Session Analytics**: Performance breakdown by trading session
- **Win/Loss Streaks**: Real-time streak monitoring
- **R-Multiple Tracking**: Risk-adjusted performance metrics
- **Equity Curve**: Visual progress tracking
- **Trade Journal**: Automatic logging with AI reviews
- **Weekly Reviews**: Claude-generated performance analysis

---

## 🏗️ Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                      ICT Trading Bot                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   MT5       │  │   Claude    │  │   Firecrawl             │ │
│  │   Client    │  │   AI        │  │   Intelligence          │ │
│  │             │  │             │  │                         │ │
│  │ • Data      │  │ • Vision    │  │ • DXY/COT               │ │
│  │ • Orders    │  │ • Analysis  │  │ • Sentiment             │ │
│  │ • Positions │  │ • Learning  │  │ • Options Flow          │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│         │                │                    │                 │
│         └────────────────┼────────────────────┘                 │
│                          │                                      │
│                    ┌─────▼─────┐                                │
│                    │   Main    │                                │
│                    │   Loop    │                                │
│                    └─────┬─────┘                                │
│                          │                                      │
│  ┌───────────────────────┼───────────────────────────────────┐ │
│  │                 Analysis Layer                             │ │
│  │                                                            │ │
│  │  • Market Structure  • AMD Cycle    • Displacement        │ │
│  │  • FVG Detection     • IPDA Levels  • Premium/Discount    │ │
│  │  • Order Blocks      • NWOG         • MTF Analysis        │ │
│  │  • Liquidity         • Kill Zones   • Silver Bullet       │ │
│  └────────────────────────────────────────────────────────────┘ │
│                          │                                      │
│  ┌───────────────────────┼───────────────────────────────────┐ │
│  │                Execution Layer                             │ │
│  │                                                            │ │
│  │  • Risk Manager      • Order Manager    • Position Mgr    │ │
│  │  • Pending Orders    • Trade Manager    • Scaling Sizer   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                          │                                      │
│                    ┌─────▼─────┐                                │
│                    │ Dashboard │                                │
│                    │   API     │                                │
│                    └───────────┘                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology |
|-------|------------|
| **Trading Engine** | Python 3.12+, AsyncIO |
| **AI Integration** | Anthropic Claude API (Opus 5 for ALL tasks — analysis, judge, re-evals, reviews, sizing) |
| **Market Data** | MetaTrader 5 MCP Server |
| **Intelligence** | Firecrawl API |
| **Backend API** | FastAPI, SQLAlchemy |
| **Database** | SQLite (local), PostgreSQL (production) |
| **Dashboard** | Next.js 14, React, TailwindCSS |
| **Notifications** | Telegram Bot API |

---

## 📱 Dashboard Features

### Main Dashboard
- Real-time equity and P&L display
- Current positions with live updates
- Pending orders management
- Market intelligence overview
- AMD phase indicator
- Session status

### Intelligence Page
- Comprehensive market intelligence display
- DXY analysis with EUR/GBP impact
- Retail sentiment (contrarian indicator)
- VIX risk mode indicator
- Currency strength rankings
- Options flow and magnet levels
- Bond yield spreads
- Seasonal patterns

### Performance Page
- Equity curve visualization
- Win rate and R-multiple stats
- Session performance breakdown
- Symbol performance analysis
- Daily/weekly summaries

### Positions Page
- Open positions with P&L
- Pending orders table
- Position modification controls
- Emergency close all button
- Margin health indicator

### Analysis Page
- Symbol-specific ICT analysis
- Market structure visualization
- FVG and order block display
- Liquidity level mapping

### Settings Page
- Trading configuration
- Risk parameters
- Symbol management
- Alert thresholds
- API key management

---

## 🔧 Trading Modes

### Scaling Tiers

The bot automatically adjusts position sizing based on account equity:

| Tier | Equity Range | Base Lots | Max Lots | Risk % |
|------|--------------|-----------|----------|--------|
| **Micro** | $0 - $2,500 | 0.01 | 0.05 | 1% |
| **Mini** | $2,500 - $10,000 | 0.02 | 0.10 | 1% |
| **Standard** | $10,000 - $25,000 | 0.05 | 0.25 | 1% |
| **Pro** | $25,000 - $50,000 | 0.10 | 0.50 | 1% |
| **Elite** | $50,000 - $100,000 | 0.20 | 1.00 | 1% |
| **Master** | $100,000+ | 0.50 | 2.00 | 0.5% |

### Trading Modes

| Mode | Description | Trigger |
|------|-------------|---------|
| **Aggressive** | Higher risk, more trades | Win streak 5+, high performance |
| **Normal** | Standard parameters | Default state |
| **Conservative** | Reduced risk, fewer trades | Loss streak 3+, drawdown |
| **Defensive** | Minimum risk, A+ setups only | Significant drawdown |

---

## 📋 Order Types

The bot intelligently selects order types based on market conditions:

| Order Type | When Used |
|------------|-----------|
| **Market** | Distribution phase confirmed, displacement present |
| **Buy Limit** | Bullish setup, waiting for price to retrace to OB/FVG |
| **Sell Limit** | Bearish setup, waiting for price to retrace to OB/FVG |
| **Buy Stop** | Bullish, waiting for breakout confirmation |
| **Sell Stop** | Bearish, waiting for breakdown confirmation |

---

## 🛡️ Risk Controls

### Pre-Trade Validation

1. **Margin Check**: Ensures sufficient free margin
2. **Exposure Check**: Limits total account exposure
3. **Position Count**: Caps concurrent positions
4. **Correlation Check**: Prevents correlated position stacking
5. **News Blackout**: Blocks trades near high-impact events
6. **Session Check**: Validates kill zone timing

### During Trade

1. **Break-Even Logic**: Moves SL to entry at 1R profit
2. **Trailing Stop**: Dynamic SL based on price movement
3. **Partial Close**: Takes profit at multiple levels
4. **Emergency Close**: Manual override capability

### Post-Trade

1. **Automatic Journaling**: All trades logged
2. **Claude Review**: AI analysis of each closed trade
3. **Performance Update**: Stats and analytics refresh
4. **Learning Integration**: Insights fed back to system

---

## 🌍 Supported Markets

### Forex Majors
- EUR/USD, GBP/USD, USD/JPY
- USD/CHF, AUD/USD, USD/CAD
- NZD/USD

### Forex Crosses
- EUR/GBP, EUR/JPY, GBP/JPY
- AUD/JPY, CAD/JPY, CHF/JPY

### Precious Metals
- XAU/USD (Gold)
- XAG/USD (Silver)

### Cryptocurrencies (USD pairs only)
- BTC/USD, ETH/USD
- XRP/USD, ADA/USD
- SOL/USD, DOT/USD
- LTC/USD, DOGE/USD

⚠️ **Warning**: BTC-quoted pairs (ETHBTC, XRPBTC, etc.) are blocked due to incorrect position sizing calculations.

---

## 📊 API Endpoints

### Trading
- `GET /api/trades` - List all trades
- `GET /api/trades/positions/open` - Open positions
- `POST /api/trades/sync-from-mt5` - Sync from MT5

### Analysis
- `GET /api/analysis/{symbol}` - Full ICT analysis
- `GET /api/analysis/session` - Current session info
- `GET /api/analysis/signals` - Recent signals

### Intelligence
- `GET /api/intelligence/status` - Service status
- `GET /api/intelligence/dxy` - DXY analysis
- `GET /api/intelligence/complete/{symbol}` - Full analysis
- `POST /api/intelligence/refresh` - Refresh all data

### Bot Control
- `GET /api/bot/status` - Bot status
- `POST /api/bot/start` - Start bot
- `POST /api/bot/stop` - Stop bot
- `POST /api/bot/emergency-close` - Close all positions

### Performance
- `GET /api/performance` - Performance stats
- `GET /api/performance/equity-curve` - Equity history
- `GET /api/goal/progress` - Goal tracking

---

## 🚀 Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- MetaTrader 5 Terminal
- Anthropic API Key
- Firecrawl API Key (optional)
- Telegram Bot Token (optional)

### Quick Start

```bash
# Clone repository
git clone https://github.com/your-repo/ict-trading-bot.git
cd ict-trading-bot

# Install Python dependencies
pip install -r requirements.txt

# Install dashboard dependencies
cd dashboard && npm install && cd ..

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start the bot
python -m trading_bot.main

# Start dashboard (separate terminal)
cd dashboard && npm run dev
```

### Configuration

Key settings in `.env`:

```env
# MT5 Connection
MT5_SERVER=your_broker_server
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_password

# Claude AI
ANTHROPIC_API_KEY=sk-ant-...

# Firecrawl (optional)
FIRECRAWL_API_KEY=fc-...

# Telegram (optional)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Trading Parameters
RISK_PER_TRADE=0.01
MAX_DAILY_TRADES=10
MIN_RISK_REWARD=2.0
```

---

## 📈 Performance Expectations

### Target Metrics

| Metric | Target |
|--------|--------|
| Win Rate | 55-65% |
| Average R | 1.5-2.5R |
| Profit Factor | >1.5 |
| Max Drawdown | <10% |
| Monthly Return | 5-15% |

### Growth Projection

Starting from $1,000 with 10% monthly returns:

| Month | Equity |
|-------|--------|
| 6 | $1,772 |
| 12 | $3,138 |
| 18 | $5,560 |
| 24 | $9,850 |
| 30 | $17,449 |
| 36 | $30,913 |

---

## ⚠️ Disclaimers

- **Risk Warning**: Forex and crypto trading involves substantial risk of loss
- **Past Performance**: Historical results do not guarantee future performance
- **Not Financial Advice**: This software is for educational purposes
- **Use at Own Risk**: Users are responsible for their trading decisions
- **Capital at Risk**: Only trade with money you can afford to lose

---

## 📞 Support

- **Documentation**: `/docs` folder
- **Issues**: GitHub Issues
- **Discord**: [Community Server]
- **Email**: support@example.com

---

## 📄 License

MIT License - See LICENSE file for details.

---

*Built with ❤️ using Claude AI, ICT Methodology, and Firecrawl Intelligence*
