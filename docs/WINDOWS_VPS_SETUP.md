# Windows VPS Installation & Deployment Guide

This guide walks through installing and running the ICT Trading Bot on a **Windows VPS** for live MetaTrader 5 trading. A Windows VPS is required because the MetaTrader 5 Python API only runs on Windows.

For local Windows desktop setup, see the [main README](../README.md#windows-setup-recommended-for-live-trading).

---

## Table of Contents

1. [Overview](#overview)
2. [VPS Requirements](#vps-requirements)
3. [Initial VPS Setup](#initial-vps-setup)
4. [Install Prerequisites](#install-prerequisites)
5. [Deploy the Application](#deploy-the-application)
6. [Configure Environment Variables](#configure-environment-variables)
7. [MetaTrader 5 Setup](#metatrader-5-setup)
8. [Verify MT5 Connection](#verify-mt5-connection)
9. [Telegram Notifications](#telegram-notifications)
10. [Run the Application](#run-the-application)
11. [Production Deployment](#production-deployment)
12. [Remote Access & Firewall](#remote-access--firewall)
13. [Auto-Start on Reboot](#auto-start-on-reboot)
14. [Monitoring & Logs](#monitoring--logs)
15. [Troubleshooting](#troubleshooting)

---

## Overview

The application consists of three components that must run on the VPS:

| Component | Port | Purpose |
|-----------|------|---------|
| **MetaTrader 5** | — | Broker connectivity and order execution |
| **Backend API** (FastAPI) | 8000 | Trading bot, analysis, WebSocket updates |
| **Dashboard** (Next.js) | 3000 | Web UI for monitoring and control |

```
┌─────────────────────────────────────────────────────────┐
│                    Windows VPS                          │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ MetaTrader 5 │◄──►│ Backend API  │◄──►│ Dashboard │ │
│  │  (terminal)  │    │  :8000       │    │  :3000    │ │
│  └──────────────┘    └──────────────┘    └───────────┘ │
│         ▲                    ▲                ▲        │
└─────────┼────────────────────┼────────────────┼────────┘
          │                    │                │
     Broker server         Your browser / API clients
```

---

## VPS Requirements

### Operating System

- **Windows Server 2019 or 2022** with Desktop Experience (GUI)
- A VPS with a full Windows desktop session — MT5 does **not** run reliably on Windows Server Core

### Recommended Specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Storage | 40 GB SSD | 80 GB SSD |
| Network | Stable, low-latency to broker | Same region as broker if possible |

### Software Prerequisites

| Software | Version | Download |
|----------|---------|----------|
| Python | 3.10+ | https://www.python.org/downloads/ |
| Node.js | 18+ LTS | https://nodejs.org/ |
| Git | Latest | https://git-scm.com/download/win |
| MetaTrader 5 | Latest | Your broker's website |

> **Important:** During Python installation, check **"Add Python to PATH"**.

---

## Initial VPS Setup

Connect to the VPS via **Remote Desktop (RDP)** and complete these steps first.

### 1. Connect via RDP

```
Host: <your-vps-ip>
User: Administrator (or your assigned user)
```

On macOS, use Microsoft Remote Desktop or any RDP client.

### 2. Install Windows Updates

Open **Settings → Windows Update** and install all pending updates. Reboot if required.

### 3. Set Power Options (Prevent Sleep)

MT5 and the bot must stay running 24/7:

1. Open **Control Panel → Power Options**
2. Select **High performance**
3. Click **Change plan settings → Change advanced power settings**
4. Set **Sleep → Sleep after** to **Never**
5. Set **Display → Turn off display after** to **Never** (or a long interval)

### 4. Configure Time Zone

Set the VPS time zone to **UTC** or your preferred trading reference zone:

**Settings → Time & Language → Date & time → Time zone**

---

## Install Prerequisites

Open **PowerShell as Administrator** and run:

### Python

Download and install Python 3.10+ from python.org. Verify:

```powershell
python --version
pip --version
```

### Node.js

Download and install Node.js 18+ LTS. Verify:

```powershell
node --version
npm --version
```

### Git

Download and install Git for Windows. Verify:

```powershell
git --version
```

### MetaTrader 5

1. Download MT5 from your broker's website
2. Install to the default path: `C:\Program Files\MetaTrader 5\`
3. Launch MT5, log in to your trading account, and leave it running

---

## Deploy the Application

### Clone the Repository

Choose an install directory (example: `C:\Trading`):

```powershell
cd C:\
git clone https://github.com/makemoney2023/claudefx_trader.git Trading
cd Trading
```

### Run Automated Setup

**Option A — Batch script (recommended):**

```batch
setup_windows.bat
```

**Option B — PowerShell script:**

If you see an execution policy error, allow local scripts once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\setup_windows.ps1
```

The setup script will:

1. Verify Python and Node.js
2. Create a Python virtual environment (`venv\`)
3. Install Python dependencies from `requirements.txt`
4. Install the `MetaTrader5` package
5. Install dashboard npm packages
6. Copy `.env.example` → `.env.local` if missing
7. Create `logs/` and `data/` directories

---

## Configure Environment Variables

### Backend — `.env.local`

Edit `C:\Trading\.env.local` (copy from `.env.example` if needed):

```env
# MetaTrader 5 (required for live trading)
MT5_LOGIN=12345678
MT5_PASSWORD=your_password_here
MT5_SERVER=YOUR-BROKER-SERVER
MT5_PATH=C:/Program Files/MetaTrader 5/terminal64.exe

# Claude API (required)
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here

# API authentication (REQUIRED on VPS — generate a long random secret)
BOT_API_KEY=your-long-random-secret-here

# Trading settings
TRADING_SYMBOLS=["EURUSD", "GBPUSD", "XAUUSD"]
TRADING_RISK_PER_TRADE=0.01
TRADING_MAX_DAILY_TRADES=3
TRADING_MIN_RISK_REWARD=2.0
TRADING_ALLOWED_SESSIONS=["london", "new_york", "london_close"]
TRADING_AUTO_START_BOT=false

# Claude API time gate (default true): hard-skip Claude/judge/sizing outside
# ICT kill zones — London 02:00–05:00, NY 07:00–10:00, London Close 10:00–12:00
# America/New_York (~7 hrs/day). MT5 sync and position management stay on.
# Set false only for debugging off-hours analysis.
TRADING_CLAUDE_KILL_ZONE_ONLY=true

# Mechanical opportunity scanner (Market Watch → hot list → Claude cycle).
# Default false. When true, scans MW + TRADING_SYMBOLS with ICTStrategy
# (no Claude), promotes top setups into a temporary hot list merged into
# each trading cycle. Dashboard: /opportunities
# TRADING_OPPORTUNITY_SCANNER_ENABLED=true
# TRADING_OPPORTUNITY_SCANNER_INTERVAL_SECONDS=150
# TRADING_OPPORTUNITY_SCANNER_MAX_UNIVERSE=40
# TRADING_OPPORTUNITY_SCANNER_HOT_LIST_SIZE=3
# TRADING_OPPORTUNITY_SCANNER_HOT_TTL_MINUTES=60
# TRADING_OPPORTUNITY_SCANNER_MIN_RR=1.5
# TRADING_OPPORTUNITY_SCANNER_MIN_CONFIDENCE=0.65
# (hot-list also requires HTF trend aligned with setup direction)

# News gates (blackout windows around red-folder events + stale-calendar
# fail-closed). The strategy-review changes assume these are ON in live
# trading — set this in the VPS .env.local:
TRADING_NEWS_GATES_ENABLED=true

# After pulling strategy-review changes (pre-Claude viability filter,
# entry-based limit-zone checks for anticipatory premium/discount limits,
# truthful gate rejects, direction-gate consolidation, counterfactual
# journal) or fill-path / Friday-gate fixes, redeploy and restart the
# backend on the VPS so live MT5 uses the new code:
#   git pull
#   Restart-Service / nssm restart / or re-run start script
#
# After a day or two of live running, review what the decision gates saved
# vs cost via the counterfactual journal:
#   GET http://YOUR_VPS_IP:8000/api/analysis/counterfactuals
# (per-gate saved_r / missed_r / net_saved_r tallies plus recent records)
#
# --- Net expectancy / promotion controls (shadow-first) ---
# ICT setup confirmation (default shadow — would-block only):
# TRADING_ICT_CONFIRMATION_MODE=shadow
# Correlated group risk cap (default shadow — logs only until activated):
# TRADING_CORRELATION_GROUP_MODE=shadow
# TRADING_CORRELATION_MAX_GROUP_RISK_PCT=0.10
#
# Analytics (advisory until promotion evaluator passes):
#   GET /api/learning/hierarchical-expectancy
#   GET /api/learning/calibration
#   GET /api/learning/exit-policy-comparison
#   GET /api/analysis/counterfactuals
#
# Promotion criteria (replay → paper → live), enforced by
# trading_bot.backtesting.promotion.evaluate_promotion:
#   - zero live/replay parity mismatches
#   - >= 100 paper trades; positive paired net-expectancy bootstrap CI
#   - profit factor + fill success not worse than baseline
#   - max DD not materially worse; no single-symbol dependence
#   - no unresolved data-quality gaps
# Activate ONE independent policy at a time. Rollback: restore the previous
# config version keyed by promotion.rollback_config_key and restart the bot.
# Data-quality terminal gate id: market_data_quality (OHLCV validator).
# The validator accepts bounded metals maintenance gaps (up to 3 hours) when:
#   - they overlap the DST-aware 5–7 PM New York rollover window, OR
#   - at least two stable gaps establish the broker's repeated daily closure
#     (M15 windows often only hold ~2 sessions), OR
#   - a single provisional metals gap (45m–3h) is the only mid-session hole
#     (Monday/short windows after weekend reopen — LHFX XAU ≈ 1h15).
# Multiple irregular daytime gaps, non-metals gaps, and longer outages still
# fail closed.

# CORS — set to your VPS IP or domain (see Remote Access section)
CORS_ORIGINS=http://YOUR_VPS_IP:3000,http://localhost:3000

# Optional: Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional: Firecrawl market intelligence
FIRECRAWL_API_KEY=
FIRECRAWL_ENABLED=true

# Logging
LOG_LEVEL=INFO
```

Generate a secure `BOT_API_KEY`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Dashboard — `dashboard\.env.local`

Create or edit `dashboard\.env.local`:

```env
# Replace YOUR_VPS_IP with the VPS public IP or domain
NEXT_PUBLIC_API_URL=http://YOUR_VPS_IP:8000
NEXT_PUBLIC_WS_URL=ws://YOUR_VPS_IP:8000/ws
NEXT_PUBLIC_BOT_API_KEY=your-long-random-secret-here
```

> The `NEXT_PUBLIC_BOT_API_KEY` must match `BOT_API_KEY` in the root `.env.local`.

---

## MetaTrader 5 Setup

Before starting the bot, configure MT5 on the VPS:

### 1. Enable Algorithmic Trading

1. Open MT5 → **Tools → Options → Expert Advisors**
2. Enable:
   - ✅ Allow automated trading
   - ✅ Allow DLL imports

### 2. Enable AutoTrading

Click the **AutoTrading** button in the MT5 toolbar until it turns **green**.

### 3. Keep MT5 Running

MT5 must remain open and logged in while the bot operates. If MT5 is not running, the bot falls back to **simulation mode** and will not place live orders.

### 4. RDP Session Note (Critical)

When you disconnect from RDP, Windows may suspend the desktop session and MT5 can stop receiving ticks. To avoid this:

- Use **Start → Sign out** instead of closing the RDP window abruptly, **or**
- Configure the VPS provider's "always-on desktop" option if available, **or**
- Use a session keep-alive utility (search for "RDP session keep alive MT5")

Test that MT5 stays connected after disconnecting from RDP before going live.

---

## Verify MT5 Connection

With MT5 running and `.env.local` configured:

```powershell
cd C:\Trading
.\venv\Scripts\activate
python test_mt5_connection.py
```

Expected output includes:

- `[OK] MetaTrader5 package imported successfully`
- `[OK] MT5 initialized`
- `[OK] Already logged in` or `[OK] Login successful!`
- Account balance, equity, and symbol bid/ask prices

Fix any errors before proceeding. Common issues are covered in [Troubleshooting](#troubleshooting).

---

## Telegram Notifications

Telegram provides trade alerts and remote bot control via slash commands (`/status`, `/positions`, `/close`, etc.). It runs inside the **backend API process** and uses outbound HTTPS only — no extra firewall ports are required.

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (format: `123456789:ABCdef...`)

### 2. Get Your Chat ID

1. Message [@userinfobot](https://t.me/userinfobot)
2. Copy your numeric **Id** (e.g. `716001905`)

### 3. Start a Conversation With Your Bot

Open your new bot in Telegram and send **`/start`**. Telegram requires this before the bot can message you.

### 4. Add Credentials to `.env.local`

In the **root** `.env.local` (not `dashboard/.env.local`):

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef_your_token_here
TELEGRAM_CHAT_ID=716001905
```

Do not wrap the token in quotes unless your editor adds them automatically.

### 5. Verify Telegram Connection

```powershell
cd C:\Trading
.\venv\Scripts\activate
python test_telegram_connection.py
```

Expected output:

- `[OK] Environment variables loaded`
- `[OK] Bot token valid (@your_bot_username)`
- `[OK] Test message delivered — check your Telegram app`

You should receive a test message on your phone. If the test passes, notifications will work when the backend API is running.

### What Telegram Sends

| Event | Notification |
|-------|----------------|
| Bot startup | Startup summary with equity and symbols |
| Trade opened | Entry, SL, TP, size, confidence |
| Trade closed | P/L, pips, win/loss |
| Errors | Error alerts with context |
| Daily summary | End-of-day stats (when bot is running) |
| Signals | **Not sent** — only executed trades notify |

### Telegram Commands

Once `start_api.bat` or `start_bot_production.bat` is running, send these to your bot:

| Command | Action |
|---------|--------|
| `/help` | List all commands |
| `/status` | Bot running state, positions, P/L |
| `/account` | Balance and equity |
| `/positions` | Open positions |
| `/close <ticket>` | Close a specific position |
| `/closeall` | Emergency close all |
| `/stop` | Pause the bot |
| `/start` | Resume the bot |

Commands only work from the configured `TELEGRAM_CHAT_ID` for security.

### Telegram on VPS Notes

- Notifications require the **backend API** to be running (`stop_bot.bat` stops Telegram too)
- Trade alerts require the **trading bot** to be started (dashboard or `TRADING_AUTO_START_BOT=true`)
- The VPS must allow outbound HTTPS to `api.telegram.org`
- Command polling starts automatically when the API starts — no webhook or inbound port setup needed
- If Telegram worked on localhost but fails on the VPS with an SSL/certificate error, set `TELEGRAM_SSL_VERIFY=false` in `.env.local` (see troubleshooting below)

---

## Run the Application

### Quick Start (Development Mode)

For initial testing on the VPS:

```batch
start_bot.bat
```

This opens two processes:

- **Backend API** at `http://localhost:8000` (bound to `0.0.0.0` for remote access, with hot-reload)
- **Dashboard** at `http://localhost:3000` (Next.js dev server)

### Production Start (Recommended for VPS)

For 24/7 operation without hot-reload:

```batch
start_bot_production.bat
```

This script:

1. Builds the dashboard if `.next` is missing
2. Starts the dashboard in a new window (`start_dashboard.bat`)
3. Starts the backend in the current window (`start_api.bat`)

You can also run components individually:

```batch
start_api.bat
start_dashboard.bat
```

### Stop Services

```batch
stop_bot.bat
```

### Manual Start (Two Terminals)

**Terminal 1 — Backend:**

```powershell
cd C:\Trading
.\start_api.bat
```

**Terminal 2 — Dashboard (production):**

```powershell
cd C:\Trading
.\start_dashboard.bat
```

**Terminal 2 — Dashboard (development):**

```powershell
cd C:\Trading\dashboard
npm run dev
```

### Access Points

| Service | Local (on VPS) | Remote (from your machine) |
|---------|----------------|----------------------------|
| Dashboard | http://localhost:3000 | http://YOUR_VPS_IP:3000 |
| API | http://localhost:8000 | http://YOUR_VPS_IP:8000 |
| API Docs | http://localhost:8000/docs | http://YOUR_VPS_IP:8000/docs |

### Start the Trading Bot

The bot does **not** auto-start by default (`TRADING_AUTO_START_BOT=false`). After the API is running:

1. Open the dashboard
2. Navigate to the bot controls
3. Click **Start Bot**

Or set `TRADING_AUTO_START_BOT=true` in `.env.local` if you want the bot to start when the API launches.

---

## Production Deployment

For 24/7 operation, use the production batch scripts (no hot-reload):

```batch
start_bot_production.bat
```

Or run components separately:

| Script | Purpose |
|--------|---------|
| `start_bot_production.bat` | Build (if needed) + start both services |
| `start_api.bat` | Backend only (production) |
| `start_dashboard.bat` | Dashboard only (requires prior `npm run build`) |
| `stop_bot.bat` | Stop both services |

### Manual Production Steps

If you prefer to run commands yourself:

```powershell
cd C:\Trading\dashboard
npm run build
cd ..
.\start_api.bat
```

In a second terminal:

```powershell
cd C:\Trading
.\start_dashboard.bat
```

> The backend runs a single uvicorn worker because the trading bot runs as an in-process background task.

### Production Checklist

- [ ] `BOT_API_KEY` set to a strong, persistent secret
- [ ] `CORS_ORIGINS` restricted to your IP/domain (not `*`)
- [ ] Dashboard built with `npm run build`
- [ ] Running without `--reload` flag
- [ ] MT5 running with AutoTrading enabled
- [ ] Firewall rules configured (see below)
- [ ] Tested on demo account before live trading

---

## Remote Access & Firewall

### Open Windows Firewall Ports

Run in **PowerShell as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "ICT Trading API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallRule -DisplayName "ICT Trading Dashboard" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

Also open these ports in your **VPS provider's cloud firewall** (AWS Security Group, Azure NSG, etc.) if one is enabled.

### Restrict Access by IP (Recommended)

Instead of allowing all IPs, restrict to your home/office IP:

```powershell
New-NetFirewallRule -DisplayName "ICT Trading API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -RemoteAddress YOUR.HOME.IP.ADDRESS
New-NetFirewallRule -DisplayName "ICT Trading Dashboard" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -RemoteAddress YOUR.HOME.IP.ADDRESS
```

### HTTPS / Reverse Proxy (Optional)

For production, put **IIS**, **Caddy**, or **nginx for Windows** in front of the dashboard and API to terminate TLS. Update `CORS_ORIGINS` and dashboard `NEXT_PUBLIC_*` URLs to use `https://your-domain.com`.

---

## Auto-Start on Reboot

Use **Task Scheduler** to start services automatically after a VPS reboot.

### Task 1 — MetaTrader 5

1. Open **Task Scheduler → Create Task**
2. **General:** Run whether user is logged on or not; Run with highest privileges
3. **Triggers:** At startup; Delay 30 seconds
4. **Actions:** Start program
   - Program: `C:\Program Files\MetaTrader 5\terminal64.exe`
5. Save and test by rebooting the VPS

### Task 2 — Backend API

1. Open **Task Scheduler → Create Task**
2. **General:** Run whether user is logged on or not; Run with highest privileges
3. **Triggers:** At log on; Delay 60 seconds (after MT5 starts)
4. **Actions:** Start program
   - Program: `C:\Trading\start_api.bat`
   - Start in: `C:\Trading`

### Task 3 — Dashboard

1. Create another task with the same general settings
2. **Triggers:** At log on; Delay 90 seconds
3. **Actions:** Start program
   - Program: `C:\Trading\start_dashboard.bat`
   - Start in: `C:\Trading`

> Run `start_bot_production.bat` once manually before scheduling Task 3 so the dashboard is built (`.next` folder exists).

> **Alternative:** [NSSM (Non-Sucking Service Manager)](https://nssm.cc/) can wrap the backend and dashboard as Windows Services for more reliable restarts.

---

## Monitoring & Logs

### Application Logs

Logs are written to `C:\Trading\logs\`. Check here first when diagnosing issues.

### Health Check

```powershell
curl http://localhost:8000/api/health
```

### Verify Services Are Running

```powershell
# Backend on port 8000
netstat -ano | findstr :8000

# Dashboard on port 3000
netstat -ano | findstr :3000

# MT5 process
tasklist | findstr terminal64
```

### Kill Stuck Processes

If ports are occupied after a crash:

```powershell
# Kill process on port 8000
.\kill_port.ps1

# Or use stop_bot.bat
stop_bot.bat
```

---

## Troubleshooting

### Bot starts in simulation mode

**Cause:** MT5 is not running or not detected.

**Fix:**
1. Launch MT5 and log in
2. Enable AutoTrading (green button)
3. Run `python test_mt5_connection.py`
4. Restart the backend

### MT5 stops updating after RDP disconnect

**Cause:** Windows suspends the desktop session.

**Fix:** See [RDP Session Note](#4-rdp-session-note-critical). Verify ticks still flow after disconnecting from RDP.

### `MetaTrader5` import error

**Fix:**

```powershell
.\venv\Scripts\activate
pip install MetaTrader5
```

### Dashboard cannot reach API

**Fix:**
1. Confirm backend is running: `curl http://localhost:8000/api/health`
2. Check `dashboard\.env.local` — `NEXT_PUBLIC_API_URL` must use the correct IP/host
3. Verify firewall allows port 8000
4. Confirm `CORS_ORIGINS` in `.env.local` includes your dashboard URL

### 401 / 403 on dashboard actions

**Cause:** `BOT_API_KEY` mismatch.

**Fix:** Ensure `BOT_API_KEY` (backend) and `NEXT_PUBLIC_BOT_API_KEY` (dashboard) are identical.

### Python or Node not found

**Fix:** Reinstall and ensure **"Add to PATH"** was checked. Restart PowerShell after installation.

### Port already in use

**Fix:**

```batch
stop_bot.bat
```

Or manually:

```powershell
netstat -ano | findstr :8000
taskkill /F /PID <pid>
```

### Login failed — MT5 credentials

**Fix:**
1. Verify `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` in `.env.local`
2. Confirm the account works in the MT5 terminal manually
3. Check that the server name matches exactly (case-sensitive)

### Telegram test fails or no messages received

**Fix:**
1. Run `python test_telegram_connection.py` and read the specific error
2. Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in root `.env.local`
3. Open your bot in Telegram and send `/start` first
4. Confirm `TELEGRAM_CHAT_ID` matches your numeric ID from @userinfobot
5. Ensure the VPS can reach `https://api.telegram.org` (outbound HTTPS)
6. Check backend logs for `Telegram notifications enabled` on startup

### Telegram SSL / certificate verify failed on VPS

**Symptom in logs:**
`SSLCertVerificationError: self-signed certificate in certificate chain`

**Cause:** Common on Windows VPS when antivirus/firewall HTTPS scanning injects its own certificate. Works on localhost, fails after moving to the VPS.

**Fix:**
1. Add this to root `.env.local`:
   ```
   TELEGRAM_SSL_VERIFY=false
   ```
2. Restart the backend API (`stop_bot.bat` then `start_bot_production.bat`)
3. Re-run `python test_telegram_connection.py`
4. Confirm startup log shows `Telegram notifications enabled (SSL verify OFF...)`

The bot will also auto-retry once with verify disabled if an SSL cert error is detected, but setting the env var makes it permanent and quieter.

### Telegram commands not responding

**Cause:** Backend API not running, or bot instance not started.

**Fix:**
1. Confirm backend is running (`netstat -ano | findstr :8000`)
2. Start the trading bot from the dashboard (commands need a linked bot instance)
3. Send `/help` only from the configured chat ID
4. Look for `[TELEGRAM] Command handler started` in backend console output

---

## Security Reminders

- Never commit `.env.local` or API keys to git
- Always set a strong `BOT_API_KEY` on a VPS
- Restrict firewall rules to known IPs when possible
- Test thoroughly on a **demo account** before live trading
- Keep Windows, Python packages, and Node dependencies updated

---

## Quick Reference

| Script | Purpose |
|--------|---------|
| `setup_windows.bat` | First-time install (venv, deps, env files) |
| `start_bot.bat` | Dev mode — hot-reload backend + dev dashboard |
| `start_bot_production.bat` | Production — build if needed, start both |
| `start_api.bat` | Backend only (production) |
| `start_dashboard.bat` | Dashboard only (production, requires build) |
| `stop_bot.bat` | Stop backend and dashboard |

```batch
REM Setup (first time)
setup_windows.bat

REM Test MT5
venv\Scripts\activate
python test_mt5_connection.py

REM Test Telegram
python test_telegram_connection.py

REM Start (development)
start_bot.bat

REM Start (production / VPS)
start_bot_production.bat

REM Stop
stop_bot.bat
```

---

## Disclaimer

This software is for educational purposes only. Trading forex and other instruments involves substantial risk of loss. Past performance does not guarantee future results. Always test on demo accounts before live trading.
