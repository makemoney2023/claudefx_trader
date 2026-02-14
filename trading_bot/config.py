"""
Configuration settings for the ICT Trading Bot.

Uses pydantic-settings for environment variable loading and validation.
"""

import os
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env files manually to ensure they're available for nested settings
from dotenv import load_dotenv

# Load .env first, then .env.local (which overrides)
load_dotenv(".env")
load_dotenv(".env.local", override=True)


# =============================================================================
# Symbol Specifications - contract sizes, pip sizes, and pip values
# =============================================================================
@dataclass
class SymbolSpec:
    """Specification for a trading symbol's contract parameters."""
    contract_size: float   # Units per standard lot (e.g., 100000 for forex)
    pip_size: float        # Minimum price movement in pips
    pip_value: float       # USD value per pip per standard lot
    min_sl_pips: float     # Minimum recommended SL in pips
    category: str          # Symbol category: forex, metal, crypto, index
    # Broker-provided fields (populated from MT5 at runtime, safe defaults otherwise)
    tick_value: float = 0.0       # trade_tick_value from MT5 (0 = use fallback formula)
    volume_min: float = 0.01      # Minimum lot size allowed by broker
    volume_max: float = 100.0     # Maximum lot size allowed by broker
    volume_step: float = 0.01     # Lot size increment
    swap_long: float = 0.0        # Overnight swap cost for long positions (per lot per night)
    swap_short: float = 0.0       # Overnight swap cost for short positions (per lot per night)


# Default symbol specifications
# These can be overridden at runtime with actual broker values from MT5
SYMBOL_SPECS: Dict[str, SymbolSpec] = {
    # Major Forex Pairs (standard lot = 100,000 units)
    'EURUSD': SymbolSpec(100000, 0.0001, 10.0, 10, 'forex'),
    'GBPUSD': SymbolSpec(100000, 0.0001, 10.0, 10, 'forex'),
    'AUDUSD': SymbolSpec(100000, 0.0001, 10.0, 10, 'forex'),
    'NZDUSD': SymbolSpec(100000, 0.0001, 10.0, 10, 'forex'),
    'USDCHF': SymbolSpec(100000, 0.0001, 10.0, 10, 'forex'),
    'USDCAD': SymbolSpec(100000, 0.0001, 7.5, 10, 'forex'),
    'USDJPY': SymbolSpec(100000, 0.01, 9.0, 15, 'forex'),
    # Cross Pairs — pip_value approximations by quote currency:
    # GBP-quoted: ~$12.60/pip, AUD-quoted: ~$6.50/pip, NZD-quoted: ~$5.80/pip
    # CAD-quoted: ~$7.40/pip, CHF-quoted: ~$11.20/pip, JPY-quoted: ~$6.60/pip
    'EURGBP': SymbolSpec(100000, 0.0001, 12.60, 10, 'forex'),
    'EURJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    'GBPJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    'AUDJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    'AUDCAD': SymbolSpec(100000, 0.0001, 7.40, 10, 'forex'),
    'AUDCHF': SymbolSpec(100000, 0.0001, 11.20, 10, 'forex'),
    'EURAUD': SymbolSpec(100000, 0.0001, 6.50, 10, 'forex'),
    'EURCHF': SymbolSpec(100000, 0.0001, 11.20, 10, 'forex'),
    'EURCAD': SymbolSpec(100000, 0.0001, 7.40, 10, 'forex'),
    'EURNZD': SymbolSpec(100000, 0.0001, 5.80, 10, 'forex'),
    'GBPAUD': SymbolSpec(100000, 0.0001, 6.50, 10, 'forex'),
    'GBPCAD': SymbolSpec(100000, 0.0001, 7.40, 10, 'forex'),
    'GBPCHF': SymbolSpec(100000, 0.0001, 11.20, 10, 'forex'),
    'GBPNZD': SymbolSpec(100000, 0.0001, 5.80, 10, 'forex'),
    'NZDJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    'NZDCAD': SymbolSpec(100000, 0.0001, 7.40, 10, 'forex'),
    'NZDCHF': SymbolSpec(100000, 0.0001, 11.20, 10, 'forex'),
    'CADJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    'CADCHF': SymbolSpec(100000, 0.0001, 11.20, 10, 'forex'),
    'CHFJPY': SymbolSpec(100000, 0.01, 6.60, 15, 'forex'),
    # Precious Metals
    'XAUUSD': SymbolSpec(100, 0.01, 1.0, 30, 'metal'),      # Gold: 100 oz per lot
    'XAGUSD': SymbolSpec(5000, 0.001, 5.0, 20, 'metal'),     # Silver: 5000 oz per lot
    # Crypto (contract sizes vary by broker - these are common defaults)
    # min_sl_pips in price units: BTC=$500, ETH=$30, etc. (meaningful minimum SL)
    'BTCUSD': SymbolSpec(1, 0.01, 0.01, 50000, 'crypto'),    # $500 min SL
    'ETHUSD': SymbolSpec(1, 0.01, 0.01, 3000, 'crypto'),      # $30 min SL
    'XRPUSD': SymbolSpec(1, 0.0001, 0.0001, 500, 'crypto'),   # $0.05 min SL
    'ADAUSD': SymbolSpec(1, 0.0001, 0.0001, 300, 'crypto'),    # $0.03 min SL
    'LTCUSD': SymbolSpec(1, 0.01, 0.01, 500, 'crypto'),        # $5 min SL
    'DOGEUSD': SymbolSpec(1, 0.00001, 0.00001, 1000, 'crypto'),# $0.01 min SL
    'SOLUSD': SymbolSpec(1, 0.01, 0.01, 300, 'crypto'),        # $3 min SL
    'DOTUSD': SymbolSpec(1, 0.001, 0.001, 300, 'crypto'),      # $0.30 min SL
    'EOSUSD': SymbolSpec(1, 0.001, 0.001, 200, 'crypto'),      # $0.20 min SL
    'NEOUSD': SymbolSpec(1, 0.01, 0.01, 200, 'crypto'),        # $2 min SL
    'ETCUSD': SymbolSpec(1, 0.01, 0.01, 200, 'crypto'),        # $2 min SL
    'XMRUSD': SymbolSpec(1, 0.01, 0.01, 300, 'crypto'),        # $3 min SL
    'ZECUSD': SymbolSpec(1, 0.01, 0.01, 200, 'crypto'),        # $2 min SL
    'DASHUSD': SymbolSpec(1, 0.01, 0.01, 200, 'crypto'),       # $2 min SL
    'IOTAUSD': SymbolSpec(1, 0.0001, 0.0001, 300, 'crypto'),   # $0.03 min SL
    # Oil / Energy (contract sizes vary by broker — MT5 sync overrides these)
    'USOIL': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),          # WTI Crude: 1000 barrels/lot
    'WTIUSD': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),         # WTI alternate name
    'XTIUSD': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),         # WTI alternate name
    'BRENT': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),          # Brent Crude
    'UKOIL': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),          # Brent alternate name
    'XBRUSD': SymbolSpec(1000, 0.01, 10.0, 50, 'oil'),         # Brent alternate name
    # Indices (contract sizes vary by broker — MT5 sync overrides these)
    'US30': SymbolSpec(1, 1.0, 1.0, 50, 'index'),              # Dow Jones
    'DJ30': SymbolSpec(1, 1.0, 1.0, 50, 'index'),              # Dow Jones alternate
    'NAS100': SymbolSpec(1, 0.1, 0.1, 50, 'index'),            # Nasdaq 100
    'USTEC': SymbolSpec(1, 0.1, 0.1, 50, 'index'),             # Nasdaq alternate
    'US500': SymbolSpec(1, 0.1, 0.1, 30, 'index'),             # S&P 500
    'SP500': SymbolSpec(1, 0.1, 0.1, 30, 'index'),             # S&P 500 alternate
}

# Default spec for unknown symbols - conservative forex-like
_DEFAULT_SYMBOL_SPEC = SymbolSpec(100000, 0.0001, 10.0, 10, 'forex')

# =============================================
# RUNTIME MT5 SPEC CACHE
# Populated at startup with actual broker values.
# get_symbol_spec() checks this FIRST before falling back to hardcoded defaults.
# =============================================
_MT5_RUNTIME_SPECS: Dict[str, SymbolSpec] = {}


def update_symbol_spec_from_mt5(
    symbol: str,
    trade_contract_size: float,
    point: float,
    digits: int,
    tick_value: float = 0.0,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
    swap_long: float = 0.0,
    swap_short: float = 0.0,
):
    """
    Update a symbol's spec with actual values from MT5 broker.
    
    Called during bot initialization for each tradeable symbol.
    This ensures P/L, position sizing, and risk calculations
    use the broker's actual contract_size, not hardcoded defaults.
    
    Args:
        symbol: Trading symbol
        trade_contract_size: MT5's trade_contract_size for this symbol
        point: MT5's point value (smallest price increment)
        digits: MT5's price digits (decimal places)
        tick_value: MT5's trade_tick_value (value of one tick in deposit currency)
        volume_min: Minimum lot size allowed by broker
        volume_max: Maximum lot size allowed by broker
        volume_step: Lot size increment
        swap_long: Overnight swap for long positions (per lot per night)
        swap_short: Overnight swap for short positions (per lot per night)
    """
    symbol = symbol.upper()
    
    # Start with existing spec (hardcoded defaults) and override contract_size
    base_spec = SYMBOL_SPECS.get(symbol, _DEFAULT_SYMBOL_SPEC)
    
    # Determine pip_size from MT5 digits/point
    # For 5-digit forex (e.g. EURUSD 1.18685): pip = 0.0001 (10 * point)
    # For 3-digit JPY (e.g. USDJPY 152.876): pip = 0.01 (10 * point)
    # For 2-digit metals (e.g. XAUUSD 2850.50): pip = 0.01
    # For crypto: pip = point (varies by broker)
    if base_spec.category in ('crypto', 'metal', 'oil', 'index'):
        mt5_pip_size = point  # Use broker's point directly for non-forex
    elif digits == 5 or digits == 3:
        mt5_pip_size = point * 10  # Standard forex: pip = 10 * point
    else:
        mt5_pip_size = point
    
    # Calculate pip_value: value of 1 pip per 1 lot
    # For USD-quoted pairs: pip_value = pip_size * contract_size
    # This is approximate; exact pip_value depends on quote currency
    mt5_pip_value = mt5_pip_size * trade_contract_size
    # Cap at reasonable values (some calcs break with very large pip_values)
    if mt5_pip_value > 1000:
        mt5_pip_value = base_spec.pip_value  # Keep default if unreasonable
    
    _MT5_RUNTIME_SPECS[symbol] = SymbolSpec(
        contract_size=trade_contract_size,
        pip_size=mt5_pip_size if mt5_pip_size > 0 else base_spec.pip_size,
        pip_value=mt5_pip_value if mt5_pip_value > 0 else base_spec.pip_value,
        min_sl_pips=base_spec.min_sl_pips,
        category=base_spec.category,
        tick_value=tick_value,
        volume_min=volume_min if volume_min > 0 else 0.01,
        volume_max=volume_max if volume_max > 0 else 100.0,
        volume_step=volume_step if volume_step > 0 else 0.01,
        swap_long=swap_long,
        swap_short=swap_short,
    )
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"MT5 spec updated: {symbol} contract_size={trade_contract_size}, "
        f"pip_size={mt5_pip_size}, pip_value={mt5_pip_value}, "
        f"tick_value={tick_value}, point={point}, digits={digits}, "
        f"vol_min={volume_min}, vol_max={volume_max}, vol_step={volume_step}, "
        f"swap_long={swap_long}, swap_short={swap_short}"
    )


def get_symbol_spec(symbol: str) -> SymbolSpec:
    """
    Get the specification for a trading symbol.
    
    Checks MT5 runtime cache FIRST (actual broker values),
    then falls back to hardcoded defaults.
    
    Args:
        symbol: Trading symbol (e.g., 'EURUSD', 'XAUUSD')
        
    Returns:
        SymbolSpec with contract parameters
    """
    symbol = symbol.upper()
    
    # Check MT5 runtime cache first (actual broker values)
    if symbol in _MT5_RUNTIME_SPECS:
        return _MT5_RUNTIME_SPECS[symbol]
    
    # Direct lookup in hardcoded defaults
    if symbol in SYMBOL_SPECS:
        return SYMBOL_SPECS[symbol]
    
    # Pattern-based fallback for unknown symbols
    if symbol.startswith('XAU') or 'GOLD' in symbol:
        return SYMBOL_SPECS.get('XAUUSD', SymbolSpec(100, 0.01, 1.0, 30, 'metal'))
    elif symbol.startswith('XAG') or 'SILVER' in symbol:
        return SYMBOL_SPECS.get('XAGUSD', SymbolSpec(5000, 0.001, 5.0, 20, 'metal'))
    elif 'OIL' in symbol or 'WTI' in symbol or 'BRENT' in symbol or symbol.startswith('XTI') or symbol.startswith('XBR'):
        return SYMBOL_SPECS.get('USOIL', SymbolSpec(1000, 0.01, 10.0, 50, 'oil'))
    elif any(idx in symbol for idx in ['US30', 'DJ30', 'NAS100', 'USTEC', 'US500', 'SP500', 'DE30', 'UK100', 'JP225']):
        return SYMBOL_SPECS.get('US30', SymbolSpec(1, 1.0, 1.0, 50, 'index'))
    elif 'JPY' in symbol:
        return SymbolSpec(100000, 0.01, 9.0, 15, 'forex')
    elif any(crypto in symbol for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'LTC', 'DOGE', 'SOL', 'DOT', 'EOS', 'NEO', 'ETC', 'XMR', 'ZEC', 'DASH', 'IOTA']):
        return SymbolSpec(1, 0.01, 0.01, 20, 'crypto')
    
    # Default to standard forex
    return _DEFAULT_SYMBOL_SPEC


def calculate_pl(symbol: str, price_diff: float, volume: float) -> float:
    """
    Calculate profit/loss using broker-provided tick_value when available.
    
    Uses the formula: P/L = (price_diff / point) * volume * tick_value
    Falls back to: P/L = price_diff * volume * contract_size
    
    Args:
        symbol: Trading symbol
        price_diff: Price difference (exit - entry for long, entry - exit for short)
        volume: Position size in lots
        
    Returns:
        Profit/loss in account deposit currency
    """
    spec = get_symbol_spec(symbol)
    
    if spec.tick_value > 0:
        # Use broker-provided tick_value for accurate cross-currency P/L
        # tick_value = value of 1 point move per 1 lot in deposit currency
        # P/L = (price_diff / point) * volume * tick_value
        # We need point (smallest price increment) — derive from pip_size and category
        if spec.category == 'forex':
            point = spec.pip_size / 10  # 5-digit forex: pip=0.0001, point=0.00001
        else:
            point = spec.pip_size  # Crypto/metals: point = pip_size
        
        if point > 0:
            ticks = price_diff / point
            return ticks * volume * spec.tick_value
    
    # Fallback: direct calculation with contract_size
    return price_diff * volume * spec.contract_size


def normalize_lots(symbol: str, lots: float) -> float:
    """
    Normalize lot size to broker-valid values using volume_min/max/step from MT5.
    
    Snaps to the nearest valid step, clamps to broker min/max.
    
    Args:
        symbol: Trading symbol
        lots: Desired lot size
        
    Returns:
        Broker-valid lot size
    """
    spec = get_symbol_spec(symbol)
    
    # Snap to volume_step
    if spec.volume_step > 0:
        lots = round(lots / spec.volume_step) * spec.volume_step
    
    # Clamp to broker min/max
    lots = max(spec.volume_min, min(lots, spec.volume_max))
    
    # Clean float rounding artifacts (e.g. 0.010000000000000002 -> 0.01)
    return round(lots, 8)


class MT5Settings(BaseSettings):
    """MetaTrader 5 connection settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="MT5_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    login: int = Field(default=0, description="MT5 account login number")
    password: str = Field(default="", description="MT5 account password")
    server: str = Field(default="", description="MT5 broker server name")
    path: Optional[str] = Field(default=None, description="Path to MT5 terminal executable")
    timeout: int = Field(default=60000, description="Connection timeout in milliseconds")


class ClaudeSettings(BaseSettings):
    """Claude API settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="ANTHROPIC_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    api_key: str = Field(default="", description="Anthropic API key")
    model: str = Field(default="claude-sonnet-4-20250514", description="Claude model to use")
    max_tokens: int = Field(default=4096, description="Maximum tokens in response")
    temperature: float = Field(default=0.3, description="Temperature for response generation")


class TradingSettings(BaseSettings):
    """Trading configuration settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="TRADING_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    symbols: List[str] = Field(
        default=["EURUSD", "GBPUSD", "XAUUSD"],
        description="List of trading symbols"
    )
    risk_per_trade: float = Field(
        default=0.01,
        description="Risk per trade as decimal (0.01 = 1%)"
    )
    max_daily_trades: int = Field(
        default=30,
        description="Maximum number of trades per day (includes stopped-out trades)"
    )
    min_risk_reward: float = Field(
        default=3.0,
        description="Minimum risk-reward ratio (1:3 = risk 1 to make 3)"
    )
    allowed_sessions: List[str] = Field(
        default=["all"],
        description="Allowed trading sessions. Use ['all'] to trade during all sessions, or specify ['london', 'new_york', 'asian', etc.]"
    )
    max_daily_profit_target: float = Field(
        default=0.50,
        description="Daily profit target to stop opening new trades (0.50 = 50%)"
    )
    max_daily_drawdown: float = Field(
        default=0.03,
        description="Maximum daily drawdown (0.03 = 3%)"
    )
    max_weekly_drawdown: float = Field(
        default=0.06,
        description="Maximum weekly drawdown (0.06 = 6%)"
    )
    allow_simulation_trades: bool = Field(
        default=False,
        description="Allow trades in MT5 simulation mode (for testing only)"
    )
    max_position_size: float = Field(
        default=1.0,
        description="Maximum position size in lots per trade"
    )
    max_total_exposure: float = Field(
        default=5.0,
        description="Maximum total exposure in lots across all positions"
    )


class TimeframeSettings(BaseSettings):
    """Timeframe configuration for multi-timeframe analysis."""
    
    higher_tf: str = Field(default="H4", description="Higher timeframe for bias")
    execution_tf: str = Field(default="M15", description="Execution timeframe")
    confirmation_tf: str = Field(default="H1", description="Confirmation timeframe")
    
    # Candle counts for each timeframe
    higher_tf_candles: int = Field(default=100, description="Candles to fetch for higher TF")
    execution_tf_candles: int = Field(default=200, description="Candles to fetch for execution TF")
    confirmation_tf_candles: int = Field(default=150, description="Candles to fetch for confirmation TF")


class LoggingSettings(BaseSettings):
    """Logging configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    level: str = Field(default="INFO", description="Logging level")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string"
    )
    file_path: Optional[str] = Field(default="logs/trading_bot.log", description="Log file path")


class FirecrawlSettings(BaseSettings):
    """Firecrawl API settings for real-time market intelligence."""
    
    model_config = SettingsConfigDict(
        env_prefix="FIRECRAWL_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    api_key: str = Field(default="", description="Firecrawl API key")
    enabled: bool = Field(default=True, description="Enable Firecrawl intelligence")
    refresh_minutes: int = Field(default=15, description="Cache refresh interval in minutes")


class Settings(BaseSettings):
    """Main settings class aggregating all configuration."""
    
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),  # Load .env first, then .env.local overrides
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    mt5: MT5Settings = Field(default_factory=MT5Settings)
    claude: ClaudeSettings = Field(default_factory=ClaudeSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    timeframes: TimeframeSettings = Field(default_factory=TimeframeSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    firecrawl: FirecrawlSettings = Field(default_factory=FirecrawlSettings)
    
    # Docs directory for strategy reference
    docs_dir: str = Field(default="trading_bot/docs", description="Strategy documentation directory")


# Global settings instance
settings = Settings()


def save_config_to_env_local(updates: dict, prefix: str = "TRADING_"):
    """
    Save configuration updates to .env.local file.
    
    Args:
        updates: Dictionary of config updates (e.g., {"risk_per_trade": 0.02})
        prefix: Environment variable prefix (e.g., "TRADING_", "MT5_")
    """
    from pathlib import Path
    import json
    
    env_path = Path(".env.local")
    
    # Read existing content
    existing_lines = []
    existing_keys = {}
    
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key = line.split('=')[0]
                    existing_keys[key] = len(existing_lines)
                existing_lines.append(line)
    
    # Update or add new values
    for key, value in updates.items():
        env_key = f"{prefix}{key.upper()}"
        
        # Format value appropriately
        if isinstance(value, list):
            # Pydantic-settings can parse JSON strings for List types
            # But also supports comma-separated for simpler cases
            # Use JSON for proper parsing of complex lists
            formatted_value = json.dumps(value)
        elif isinstance(value, bool):
            formatted_value = str(value).lower()
        elif isinstance(value, (int, float)):
            formatted_value = str(value)
        else:
            formatted_value = str(value)
        
        new_line = f"{env_key}={formatted_value}"
        
        if env_key in existing_keys:
            # Update existing line
            existing_lines[existing_keys[env_key]] = new_line
        else:
            # Add new line
            existing_lines.append(new_line)
    
    # Write back
    with open(env_path, 'w') as f:
        f.write('\n'.join(existing_lines))
        if existing_lines and not existing_lines[-1].endswith('\n'):
            f.write('\n')
    
    return True


def reload_settings():
    """Reload settings from environment files."""
    global settings
    settings = Settings()
    return settings


# Export configuration dictionaries for MCP server
def get_mt5_config() -> dict:
    """Get MT5 configuration as dictionary for MCP server."""
    return {
        "login": settings.mt5.login,
        "password": settings.mt5.password,
        "server": settings.mt5.server,
        "path": settings.mt5.path,
        "timeout": settings.mt5.timeout,
    }


def get_claude_config() -> dict:
    """Get Claude configuration as dictionary."""
    return {
        "api_key": settings.claude.api_key,
        "model": settings.claude.model,
        "max_tokens": settings.claude.max_tokens,
        "temperature": settings.claude.temperature,
    }


def get_trading_config() -> dict:
    """Get trading configuration as dictionary."""
    return {
        "symbols": settings.trading.symbols,
        "risk_per_trade": settings.trading.risk_per_trade,
        "max_daily_trades": settings.trading.max_daily_trades,
        "min_risk_reward": settings.trading.min_risk_reward,
        "allowed_sessions": settings.trading.allowed_sessions,
        "max_daily_drawdown": settings.trading.max_daily_drawdown,
        "max_weekly_drawdown": settings.trading.max_weekly_drawdown,
    }
