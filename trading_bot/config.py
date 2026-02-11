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
}

# Default spec for unknown symbols - conservative forex-like
_DEFAULT_SYMBOL_SPEC = SymbolSpec(100000, 0.0001, 10.0, 10, 'forex')


def get_symbol_spec(symbol: str) -> SymbolSpec:
    """
    Get the specification for a trading symbol.
    
    Falls back to intelligent defaults based on symbol name patterns
    if the exact symbol isn't in the map.
    
    Args:
        symbol: Trading symbol (e.g., 'EURUSD', 'XAUUSD')
        
    Returns:
        SymbolSpec with contract parameters
    """
    symbol = symbol.upper()
    
    # Direct lookup first
    if symbol in SYMBOL_SPECS:
        return SYMBOL_SPECS[symbol]
    
    # Pattern-based fallback for unknown symbols
    if symbol.startswith('XAU') or 'GOLD' in symbol:
        return SYMBOL_SPECS.get('XAUUSD', SymbolSpec(100, 0.01, 1.0, 30, 'metal'))
    elif symbol.startswith('XAG') or 'SILVER' in symbol:
        return SYMBOL_SPECS.get('XAGUSD', SymbolSpec(5000, 0.001, 5.0, 20, 'metal'))
    elif 'JPY' in symbol:
        return SymbolSpec(100000, 0.01, 9.0, 15, 'forex')
    elif any(crypto in symbol for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'LTC', 'DOGE', 'SOL', 'DOT', 'EOS', 'NEO', 'ETC', 'XMR', 'ZEC', 'DASH', 'IOTA']):
        return SymbolSpec(1, 0.01, 0.01, 20, 'crypto')
    
    # Default to standard forex
    return _DEFAULT_SYMBOL_SPEC


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
