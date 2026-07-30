"""
MetaTrader 5 MCP Client Wrapper.

Provides a clean interface to the metatrader-mcp-server
for account operations, market data, and order execution.

Uses the Model Context Protocol (MCP) to communicate with MT5.
"""

import asyncio
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AccountInfo:
    """MT5 account information."""
    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    profit: float
    currency: str
    leverage: int
    
    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "balance": self.balance,
            "equity": self.equity,
            "margin": self.margin,
            "free_margin": self.free_margin,
            "margin_level": self.margin_level,
            "profit": self.profit,
            "currency": self.currency,
            "leverage": self.leverage
        }


@dataclass
class SymbolInfo:
    """MT5 symbol information."""
    name: str
    bid: float
    ask: float
    spread: float
    digits: int
    point: float
    trade_contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_stops_level: int = 0  # Minimum stop distance in points
    trade_tick_value: float = 0.0  # Value of one tick in deposit currency
    swap_long: float = 0.0        # Overnight swap for long positions
    swap_short: float = 0.0       # Overnight swap for short positions
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "digits": self.digits,
            "point": self.point,
            "contract_size": self.trade_contract_size,
            "stops_level": self.trade_stops_level,
            "tick_value": self.trade_tick_value,
            "swap_long": self.swap_long,
            "swap_short": self.swap_short,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step
        }
    
    def get_min_stop_distance(self) -> float:
        """Get minimum stop loss distance in price terms."""
        # stops_level is in points, convert to price
        # Add a buffer of 5 points for safety
        return (self.trade_stops_level + 5) * self.point


@dataclass
class Position:
    """MT5 open position."""
    ticket: int
    symbol: str
    type: str  # 'buy' or 'sell'
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    magic: int
    comment: str
    time: datetime
    
    def to_dict(self) -> dict:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "type": self.type,
            "volume": self.volume,
            "price_open": self.price_open,
            "price_current": self.price_current,
            "sl": self.sl,
            "tp": self.tp,
            "profit": self.profit,
            "magic": self.magic,
            "comment": self.comment,
            "time": self.time.isoformat() if self.time else None
        }


class MCPConnectionError(Exception):
    """Raised when MCP connection fails."""
    pass


class MT5Client:
    """
    Client wrapper for MetaTrader 5 MCP server.
    
    Connects to the metatrader-mcp-server to interact with MT5
    for market data, account info, and trading operations.
    
    All synchronous MT5 calls are wrapped in asyncio.to_thread() to avoid
    blocking the async event loop. An asyncio.Lock ensures thread-safe
    sequential access (MT5 Python module is not thread-safe).
    
    The MCP server must be running and accessible for this client to work.
    Install via: pip install metatrader-mcp-server
    """
    
    # Timeframe mapping (MT5 constants)
    TIMEFRAMES = {
        'M1': 1,
        'M5': 5,
        'M15': 15,
        'M30': 30,
        'H1': 60,
        'H4': 240,
        'D1': 1440,
        'W1': 10080,
        'MN1': 43200
    }
    
    @staticmethod
    def _get_filling_modes(symbol_info) -> list:
        """
        Allowed filling modes in preference order.
        Priority: FOK > IOC > RETURN.
        MT5 filling_mode bitmask: bit 0 = FOK, bit 1 = IOC.
        """
        modes = []
        allowed = getattr(symbol_info, "filling_mode", 0) or 0
        if allowed & 1:
            modes.append(0)  # ORDER_FILLING_FOK
        if allowed & 2:
            modes.append(1)  # ORDER_FILLING_IOC
        # Always offer RETURN as last resort when not already present
        if 2 not in modes:
            modes.append(2)  # ORDER_FILLING_RETURN
        return modes or [2]

    @staticmethod
    def _get_filling_type(symbol_info) -> int:
        """Best single filling mode for a symbol (FOK > IOC > RETURN)."""
        return MT5Client._get_filling_modes(symbol_info)[0]
    
    # Order type mapping
    ORDER_TYPES = {
        'buy': 0,
        'sell': 1,
        'buy_limit': 2,
        'sell_limit': 3,
        'buy_stop': 4,
        'sell_stop': 5,
        'buy_stop_limit': 6,
        'sell_stop_limit': 7
    }
    
    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        mcp_endpoint: Optional[str] = None
    ):
        """
        Initialize the MT5 client.
        
        Args:
            login: MT5 account login
            password: MT5 account password
            server: MT5 broker server
            mcp_endpoint: MCP server endpoint (default: stdio)
        """
        self.login = login or settings.mt5.login
        self.password = password or settings.mt5.password
        self.server = server or settings.mt5.server
        self.mcp_endpoint = mcp_endpoint
        
        self._connected = False
        self._mcp_client = None
        self._use_simulation = False  # Set to True for demo mode
        self._lock = asyncio.Lock()  # Concurrency lock for MT5 thread safety
        self._closing_tickets: set = set()  # Guard against concurrent close of same position
        
        logger.info("MT5 client initialized")
    
    async def connect(self) -> bool:
        """
        Connect to MT5 terminal.
        
        Returns:
            True if connection successful
        """
        async with self._lock:
            try:
                logger.info(f"Connecting to MT5 server: {self.server}")
                
                # Try to import MetaTrader5 package (Windows only)
                try:
                    import MetaTrader5 as mt5
                    
                    self._mcp_client = mt5
                    
                    # Initialize MT5 connection (offload blocking call)
                    init_ok = await asyncio.to_thread(mt5.initialize)
                    if not init_ok:
                        error = await asyncio.to_thread(mt5.last_error)
                        logger.warning(f"MT5 initialization failed: {error}, using simulation mode")
                        self._use_simulation = True
                    else:
                        # Check if already logged in to any account
                        account_info = await asyncio.to_thread(mt5.account_info)
                        if account_info and account_info.login > 0:
                            configured_login = int(settings.mt5.login or 0)
                            if configured_login > 0 and account_info.login != configured_login:
                                error = await asyncio.to_thread(mt5.last_error)
                                logger.error(
                                    f"MT5 terminal logged into account {account_info.login} "
                                    f"but MT5_LOGIN is configured as {configured_login}. "
                                    f"Refusing connection to prevent trading on wrong account."
                                )
                                if error:
                                    logger.error(f"MT5 last_error: {error}")
                                await asyncio.to_thread(mt5.shutdown)
                                self._connected = False
                                self._use_simulation = False
                                return False
                            self._use_simulation = False
                            self.login = account_info.login
                            self.server = account_info.server
                            logger.info(f"MT5 connected to account {account_info.login} on {account_info.server}")
                        else:
                            logger.info("MT5 not logged in, attempting login...")
                            authorized = await asyncio.to_thread(
                                mt5.login,
                                int(self.login) if self.login else 0,
                                str(self.password) if self.password else "",
                                str(self.server) if self.server else ""
                            )
                            
                            if not authorized:
                                error = await asyncio.to_thread(mt5.last_error)
                                logger.warning(f"MT5 login failed: {error}, using simulation mode")
                                self._use_simulation = True
                            else:
                                self._use_simulation = False
                                logger.info("Successfully logged in to MT5")
                            
                except ImportError:
                    logger.warning(
                        "MetaTrader5 package not installed or not available (Windows only). "
                        "Using simulation mode. Install with: pip install MetaTrader5"
                    )
                    self._use_simulation = True
                
                # Verify connection by getting account info (uses its own lock, so release first)
                # We need to release the lock before calling get_account_info which also acquires it
                self._connected = True  # Temporarily set so get_account_info works
                
            except Exception as e:
                logger.error(f"Failed to connect to MT5: {e}")
                self._use_simulation = True
                self._connected = True
                return True
        
        # Now outside the lock, verify connection
        account = await self.get_account_info()
        
        if account:
            configured_login = int(settings.mt5.login or 0)
            if configured_login > 0 and account.login != configured_login:
                logger.error(
                    f"MT5 account mismatch after connect: terminal={account.login}, "
                    f"configured={configured_login}. Connection rejected."
                )
                self._connected = False
                return False
            self._connected = True
            logger.info(
                f"MT5 {'(Simulated)' if self._use_simulation else ''}: "
                f"Account {account.login}, Balance: {account.balance}"
            )
            return True
        
        self._connected = False
        return False
    
    async def disconnect(self):
        """Disconnect from MT5."""
        async with self._lock:
            if self._mcp_client and not self._use_simulation:
                try:
                    await asyncio.to_thread(self._mcp_client.shutdown)
                except Exception as e:
                    logger.error(f"Error during disconnect: {e}")
            
            self._connected = False
            logger.info("Disconnected from MT5")
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to MT5."""
        return self._connected
    
    @property
    def is_simulation(self) -> bool:
        """Check if running in simulation mode."""
        return self._use_simulation
    
    async def ensure_connected(self) -> bool:
        """
        Ensure MT5 connection is active, reconnecting if needed.
        
        Returns:
            True if connected (or reconnected successfully)
        """
        if self._connected and not self._use_simulation:
            # Verify connection is still alive
            try:
                import MetaTrader5 as mt5
                async with self._lock:
                    info = await asyncio.to_thread(mt5.terminal_info)
                if info is not None:
                    return True
            except Exception as e:
                logger.debug(f"ensure_connected check failed: {e}")
            
            # Connection lost - try to reconnect
            logger.warning("MT5 connection lost, attempting reconnect...")
            self._connected = False
        
        # Try to reconnect
        return await self.reconnect()
    
    async def reconnect(self, max_attempts: int = 3, delay: float = 5.0) -> bool:
        """
        Attempt to reconnect to MT5.
        
        Args:
            max_attempts: Maximum reconnection attempts
            delay: Delay between attempts in seconds
            
        Returns:
            True if reconnected successfully
        """
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Reconnection attempt {attempt}/{max_attempts}...")
            
            try:
                import MetaTrader5 as mt5
                
                async with self._lock:
                    # Shutdown existing connection
                    await asyncio.to_thread(mt5.shutdown)
                    await asyncio.sleep(1)
                    
                    # Reinitialize
                    init_ok = await asyncio.to_thread(mt5.initialize)
                    if init_ok:
                        account_info = await asyncio.to_thread(mt5.account_info)
                        if account_info and account_info.login > 0:
                            self._connected = True
                            self._use_simulation = False
                            logger.info(f"Reconnected to MT5 account {account_info.login}")
                            return True
                        
            except Exception as e:
                logger.error(f"Reconnection attempt {attempt} failed: {e}")
            
            if attempt < max_attempts:
                logger.info(f"Waiting {delay}s before next attempt...")
                await asyncio.sleep(delay)
        
        logger.error(f"Failed to reconnect after {max_attempts} attempts")
        return False
    
    async def get_account_info(self) -> Optional[AccountInfo]:
        """
        Get current account information.
        
        Returns:
            AccountInfo or None if failed
        """
        try:
            if self._use_simulation:
                return self._get_simulated_account()
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            async with self._lock:
                result = await asyncio.to_thread(mt5.account_info)
            
            if result:
                return AccountInfo(
                    login=result.login,
                    balance=result.balance,
                    equity=result.equity,
                    margin=result.margin,
                    free_margin=result.margin_free,
                    margin_level=result.margin_level if result.margin_level else 0,
                    profit=result.profit,
                    currency=result.currency,
                    leverage=result.leverage
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            if self._use_simulation:
                return self._get_simulated_account()
            return None  # Don't fake data when connected to real MT5
    
    def _get_simulated_account(self) -> AccountInfo:
        """Return simulated account info."""
        return AccountInfo(
            login=self.login or 12345678,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            free_margin=10000.0,
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=100
        )
    
    async def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """
        Get symbol information.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            SymbolInfo or None if failed
        """
        try:
            if self._use_simulation:
                return self._get_simulated_symbol(symbol)
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            async with self._lock:
                result = await asyncio.to_thread(mt5.symbol_info, symbol)
            
            if result:
                # Get stops_level - minimum stop distance in points
                stops_level = getattr(result, 'trade_stops_level', 0)
                if stops_level == 0:
                    stops_level = getattr(result, 'freeze_level', 10)
                
                return SymbolInfo(
                    name=result.name,
                    bid=result.bid,
                    ask=result.ask,
                    spread=result.spread,
                    digits=result.digits,
                    point=result.point,
                    trade_contract_size=result.trade_contract_size,
                    volume_min=result.volume_min,
                    volume_max=result.volume_max,
                    volume_step=result.volume_step,
                    trade_stops_level=stops_level,
                    trade_tick_value=getattr(result, 'trade_tick_value', 0.0),
                    swap_long=getattr(result, 'swap_long', 0.0),
                    swap_short=getattr(result, 'swap_short', 0.0),
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting symbol info: {e}")
            if self._use_simulation:
                return self._get_simulated_symbol(symbol)
            return None  # Don't fake data when connected to real MT5
    
    def _get_simulated_symbol(self, symbol: str) -> SymbolInfo:
        """Return simulated symbol info."""
        # Base prices for common symbols
        prices = {
            "EURUSD": (1.0850, 1.0852),
            "GBPUSD": (1.2650, 1.2653),
            "USDJPY": (148.50, 148.53),
            "USDCHF": (0.8850, 0.8853),
            "AUDUSD": (0.6550, 0.6552),
            "NZDUSD": (0.6050, 0.6052),
            "USDCAD": (1.3650, 1.3653),
            "XAUUSD": (2050.00, 2050.50),
        }
        
        bid, ask = prices.get(symbol.upper(), (1.0000, 1.0002))
        # Use centralized symbol spec for digits and point instead of hardcoded JPY checks
        from ..config import get_symbol_spec
        _sim_spec = get_symbol_spec(symbol)
        # Derive digits from pip_size (e.g. 0.0001 -> 5 digits, 0.01 -> 3 digits for forex, 2 for metals)
        if _sim_spec.category == 'crypto':
            # Crypto: pip_size is point, digits based on price magnitude
            point = _sim_spec.pip_size
            digits = max(2, len(str(_sim_spec.pip_size).rstrip('0').split('.')[-1]))
        elif _sim_spec.category == 'metal':
            point = _sim_spec.pip_size  # For metals, point = pip_size
            digits = 2 if _sim_spec.pip_size >= 0.01 else 3
        elif _sim_spec.pip_size >= 0.01:
            # JPY pairs: pip = 0.01, point = 0.001, digits = 3
            point = _sim_spec.pip_size / 10
            digits = 3
        else:
            # Standard forex: pip = 0.0001, point = 0.00001, digits = 5
            point = _sim_spec.pip_size / 10
            digits = 5
        
        # Default stops level varies by instrument type
        # Crypto and metals typically need larger stop distances
        if any(x in symbol.upper() for x in ['BTC', 'ETH', 'XRP', 'ADA', 'LTC', 'DOGE', 'SOL']):
            stops_level = 100  # Crypto needs larger stops
        elif any(x in symbol.upper() for x in ['XAU', 'XAG', 'GOLD', 'SILVER']):
            stops_level = 50   # Metals
        elif any(x in symbol.upper() for x in ['US30', 'DJ30', 'NAS100', 'USTEC', 'US500', 'SP500']):
            stops_level = 50   # Indices
        elif any(x in symbol.upper() for x in ['OIL', 'WTI', 'BRENT', 'XTI', 'XBR']):
            stops_level = 30   # Oil
        else:
            stops_level = 10   # Forex
        
        return SymbolInfo(
            name=symbol,
            bid=bid,
            ask=ask,
            spread=int((ask - bid) / point),
            digits=digits,
            point=point,
            trade_contract_size=self._get_sim_contract_size(symbol),
            volume_min=_sim_spec.volume_min,
            volume_max=_sim_spec.volume_max,
            volume_step=_sim_spec.volume_step,
            trade_stops_level=stops_level,
            trade_tick_value=0.0,  # Not available in simulation
            swap_long=0.0,
            swap_short=0.0,
        )
    
    @staticmethod
    def _get_sim_contract_size(symbol: str) -> float:
        """Get contract size for simulation mode."""
        from ..config import get_symbol_spec
        spec = get_symbol_spec(symbol.upper())
        return spec.contract_size
    
    async def calc_margin(self, symbol: str, volume: float, order_type: str = "buy") -> Optional[float]:
        """
        Calculate required margin for a trade using MT5's native calculation.
        
        This uses the broker's exact margin requirements, handling leverage,
        contract sizes, and instrument-specific rules automatically.
        
        Args:
            symbol: Trading symbol
            volume: Position size in lots
            order_type: 'buy' or 'sell'
            
        Returns:
            Required margin in account currency, or None on error
        """
        try:
            if self._use_simulation:
                # Fallback calculation for simulation
                info = await self.get_symbol_info(symbol)
                if info:
                    price = info.ask if order_type == "buy" else info.bid
                    # Use simulated account leverage (default 100) instead of hardcoded value
                    sim_account = self._get_simulated_account()
                    leverage = sim_account.leverage if sim_account.leverage else 100
                    return (volume * info.trade_contract_size * price) / leverage
                return None
            
            mt5 = self._mcp_client
            import MetaTrader5 as mt5_module
            
            action = mt5_module.ORDER_TYPE_BUY if order_type.lower() == "buy" else mt5_module.ORDER_TYPE_SELL
            
            async with self._lock:
                # Get current price
                info = await asyncio.to_thread(mt5_module.symbol_info, symbol)
                if not info:
                    return None
                price = info.ask if order_type.lower() == "buy" else info.bid
                
                margin = await asyncio.to_thread(mt5_module.order_calc_margin, action, symbol, volume, price)
            
            if margin is not None:
                logger.debug(f"MT5 calc_margin: {symbol} {volume} lots = ${margin:.2f}")
                return margin
            else:
                logger.warning(f"MT5 order_calc_margin returned None for {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"Error calculating margin for {symbol}: {e}")
            return None
    
    async def get_current_price(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Get current bid/ask prices for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with 'bid' and 'ask' or None
        """
        info = await self.get_symbol_info(symbol)
        if info:
            return {"bid": info.bid, "ask": info.ask, "spread": info.spread}
        return None
    
    async def get_all_symbols(
        self,
        group: Optional[str] = None,
        include_info: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all symbols available from the broker.
        
        Args:
            group: Optional filter pattern (e.g., "*USD*", "Forex*")
            include_info: Whether to include detailed symbol info
            
        Returns:
            List of symbol dicts with name and optional details
        """
        try:
            if self._use_simulation:
                return self._get_simulated_all_symbols(group, include_info)
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            
            async with self._lock:
                if group:
                    symbols = await asyncio.to_thread(mt5.symbols_get, group=group)
                else:
                    symbols = await asyncio.to_thread(mt5.symbols_get)
            
            if not symbols:
                logger.warning("No symbols returned from MT5")
                return self._get_simulated_all_symbols(group, include_info)
            
            result = []
            for sym in symbols:
                symbol_data = {
                    "name": sym.name,
                    "description": sym.description if hasattr(sym, 'description') else "",
                    "path": sym.path if hasattr(sym, 'path') else "",
                    "visible": bool(sym.visible) if hasattr(sym, 'visible') else False,
                    "tradeable": bool(sym.trade_mode > 0) if hasattr(sym, 'trade_mode') else True
                }
                
                if include_info:
                    symbol_data.update({
                        "bid": float(sym.bid) if hasattr(sym, 'bid') else 0.0,
                        "ask": float(sym.ask) if hasattr(sym, 'ask') else 0.0,
                        "spread": int(sym.spread) if hasattr(sym, 'spread') else 0,
                        "digits": int(sym.digits) if hasattr(sym, 'digits') else 5,
                        "volume_min": float(sym.volume_min) if hasattr(sym, 'volume_min') else 0.01,
                        "volume_max": float(sym.volume_max) if hasattr(sym, 'volume_max') else 100.0
                    })
                
                result.append(symbol_data)
            
            logger.info(f"Retrieved {len(result)} symbols from MT5")
            return result
            
        except Exception as e:
            logger.error(f"Error getting all symbols: {e}")
            return self._get_simulated_all_symbols(group, include_info)
    
    def _get_simulated_all_symbols(
        self,
        group: Optional[str] = None,
        include_info: bool = False
    ) -> List[Dict[str, Any]]:
        """Return simulated list of symbols for demo mode."""
        # Common forex pairs and gold
        all_symbols = [
            {"name": "EURUSD", "description": "Euro vs US Dollar", "path": "Forex\\EURUSD", "category": "forex"},
            {"name": "GBPUSD", "description": "British Pound vs US Dollar", "path": "Forex\\GBPUSD", "category": "forex"},
            {"name": "USDJPY", "description": "US Dollar vs Japanese Yen", "path": "Forex\\USDJPY", "category": "forex"},
            {"name": "USDCHF", "description": "US Dollar vs Swiss Franc", "path": "Forex\\USDCHF", "category": "forex"},
            {"name": "AUDUSD", "description": "Australian Dollar vs US Dollar", "path": "Forex\\AUDUSD", "category": "forex"},
            {"name": "NZDUSD", "description": "New Zealand Dollar vs US Dollar", "path": "Forex\\NZDUSD", "category": "forex"},
            {"name": "USDCAD", "description": "US Dollar vs Canadian Dollar", "path": "Forex\\USDCAD", "category": "forex"},
            {"name": "EURGBP", "description": "Euro vs British Pound", "path": "Forex\\EURGBP", "category": "forex"},
            {"name": "EURJPY", "description": "Euro vs Japanese Yen", "path": "Forex\\EURJPY", "category": "forex"},
            {"name": "GBPJPY", "description": "British Pound vs Japanese Yen", "path": "Forex\\GBPJPY", "category": "forex"},
            {"name": "XAUUSD", "description": "Gold vs US Dollar", "path": "Metals\\XAUUSD", "category": "metals"},
            {"name": "XAGUSD", "description": "Silver vs US Dollar", "path": "Metals\\XAGUSD", "category": "metals"},
            {"name": "BTCUSD", "description": "Bitcoin vs US Dollar", "path": "Crypto\\BTCUSD", "category": "crypto"},
            {"name": "ETHUSD", "description": "Ethereum vs US Dollar", "path": "Crypto\\ETHUSD", "category": "crypto"},
            {"name": "US30", "description": "Dow Jones Industrial Average", "path": "Indices\\US30", "category": "indices"},
            {"name": "US500", "description": "S&P 500 Index", "path": "Indices\\US500", "category": "indices"},
            {"name": "NAS100", "description": "Nasdaq 100 Index", "path": "Indices\\NAS100", "category": "indices"},
        ]
        
        # Apply group filter if provided
        if group:
            pattern = group.replace("*", "").upper()
            all_symbols = [s for s in all_symbols if pattern in s["name"].upper() or pattern in s["description"].upper()]
        
        # Add simulation-specific fields
        for sym in all_symbols:
            sym["visible"] = sym["name"] in ["EURUSD", "GBPUSD", "XAUUSD"]  # Default Market Watch
            sym["tradeable"] = True
            
            if include_info:
                # Add price info from simulated data
                sim_info = self._get_simulated_symbol(sym["name"])
                sym["bid"] = sim_info.bid
                sym["ask"] = sim_info.ask
                sym["spread"] = sim_info.spread
                sym["digits"] = sim_info.digits
                sym["volume_min"] = sim_info.volume_min
                sym["volume_max"] = sim_info.volume_max
        
        return all_symbols
    
    async def get_market_watch_symbols(self, include_info: bool = False) -> List[Dict[str, Any]]:
        """
        Get symbols currently visible in the MT5 Market Watch.
        
        Args:
            include_info: Whether to include detailed symbol info
            
        Returns:
            List of symbol dicts that are visible in Market Watch
        """
        try:
            if self._use_simulation:
                # In simulation, return a subset as "visible"
                all_syms = self._get_simulated_all_symbols(include_info=include_info)
                return [s for s in all_syms if s.get("visible", False)]
            
            # Real MT5 call - get only visible symbols (non-blocking)
            mt5 = self._mcp_client
            async with self._lock:
                symbols = await asyncio.to_thread(mt5.symbols_get)
            
            if not symbols:
                logger.warning("No symbols returned from MT5")
                return []
            
            # Filter to only visible (in Market Watch) symbols
            result = []
            for sym in symbols:
                if hasattr(sym, 'visible') and sym.visible:
                    symbol_data = {
                        "name": sym.name,
                        "description": sym.description if hasattr(sym, 'description') else "",
                        "path": sym.path if hasattr(sym, 'path') else "",
                        "visible": True,
                        "tradeable": bool(sym.trade_mode > 0) if hasattr(sym, 'trade_mode') else True
                    }
                    
                    if include_info:
                        symbol_data.update({
                            "bid": float(sym.bid) if hasattr(sym, 'bid') else 0.0,
                            "ask": float(sym.ask) if hasattr(sym, 'ask') else 0.0,
                            "spread": int(sym.spread) if hasattr(sym, 'spread') else 0,
                            "digits": int(sym.digits) if hasattr(sym, 'digits') else 5,
                            "volume_min": float(sym.volume_min) if hasattr(sym, 'volume_min') else 0.01,
                            "volume_max": float(sym.volume_max) if hasattr(sym, 'volume_max') else 100.0
                        })
                    
                    result.append(symbol_data)
            
            logger.info(f"Retrieved {len(result)} symbols from Market Watch")
            return result
            
        except Exception as e:
            logger.error(f"Error getting Market Watch symbols: {e}")
            return []
    
    async def add_symbol_to_market_watch(self, symbol: str) -> bool:
        """
        Add a symbol to the MT5 Market Watch.
        
        Args:
            symbol: Symbol name to add
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self._use_simulation:
                logger.info(f"[Simulation] Added {symbol} to Market Watch")
                return True
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            async with self._lock:
                result = await asyncio.to_thread(mt5.symbol_select, symbol, True)
            
            if result:
                logger.info(f"Added {symbol} to Market Watch")
                return True
            else:
                async with self._lock:
                    error = await asyncio.to_thread(mt5.last_error)
                logger.warning(f"Failed to add {symbol} to Market Watch: {error}")
                return False
                
        except Exception as e:
            logger.error(f"Error adding symbol to Market Watch: {e}")
            return False
    
    async def remove_symbol_from_market_watch(self, symbol: str) -> bool:
        """
        Remove a symbol from the MT5 Market Watch.
        
        Args:
            symbol: Symbol name to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self._use_simulation:
                logger.info(f"[Simulation] Removed {symbol} from Market Watch")
                return True
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            async with self._lock:
                result = await asyncio.to_thread(mt5.symbol_select, symbol, False)
            
            if result:
                logger.info(f"Removed {symbol} from Market Watch")
                return True
            else:
                async with self._lock:
                    error = await asyncio.to_thread(mt5.last_error)
                logger.warning(f"Failed to remove {symbol} from Market Watch: {error}")
                return False
                
        except Exception as e:
            logger.error(f"Error removing symbol from Market Watch: {e}")
            return False
    
    async def get_ohlcv_data(
        self,
        symbol: str,
        timeframe: str,
        count: int = 100,
        start_time: Optional[datetime] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get OHLCV (candlestick) data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1)
            count: Number of bars to retrieve
            start_time: Optional start time
            
        Returns:
            List of OHLCV dicts or None
        """
        try:
            tf_value = self.TIMEFRAMES.get(timeframe.upper())
            if not tf_value:
                logger.error(f"Invalid timeframe: {timeframe}")
                return None
            
            if self._use_simulation:
                return self._get_simulated_ohlcv(symbol, timeframe, count)
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            tf_map = {
                1: mt5.TIMEFRAME_M1,
                5: mt5.TIMEFRAME_M5,
                15: mt5.TIMEFRAME_M15,
                30: mt5.TIMEFRAME_M30,
                60: mt5.TIMEFRAME_H1,
                240: mt5.TIMEFRAME_H4,
                1440: mt5.TIMEFRAME_D1,
                10080: mt5.TIMEFRAME_W1,
                43200: mt5.TIMEFRAME_MN1,
            }
            mt5_tf = tf_map.get(tf_value, mt5.TIMEFRAME_H1)
            
            async with self._lock:
                if start_time:
                    result = await asyncio.to_thread(mt5.copy_rates_from, symbol, mt5_tf, start_time, count)
                else:
                    result = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, mt5_tf, 0, count)
            
            if result is not None and len(result) > 0:
                bars = []
                for bar in result:
                    bars.append({
                        'time': datetime.fromtimestamp(bar['time'], tz=timezone.utc).isoformat(),
                        'open': float(bar['open']),
                        'high': float(bar['high']),
                        'low': float(bar['low']),
                        'close': float(bar['close']),
                        'volume': int(bar['tick_volume'])
                    })
                return bars
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting OHLCV data: {e}")
            if self._use_simulation:
                return self._get_simulated_ohlcv(symbol, timeframe, count)
            return None  # Don't fake data when connected to real MT5
    
    async def get_ohlcv_range(
        self,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get OHLCV data for a date range using MT5 copy_rates_range.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe string (M1, M5, M15, H1, D1, etc.)
            date_from: Start datetime
            date_to: End datetime

        Returns:
            List of OHLCV dicts or None
        """
        try:
            tf_value = self.TIMEFRAMES.get(timeframe.upper())
            if not tf_value:
                logger.error(f"Invalid timeframe: {timeframe}")
                return None

            if self._use_simulation:
                return self._get_simulated_ohlcv(symbol, timeframe, 500)

            import MetaTrader5 as mt5
            tf_map = {
                1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5,
                15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30,
                60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
                1440: mt5.TIMEFRAME_D1, 10080: mt5.TIMEFRAME_W1,
                43200: mt5.TIMEFRAME_MN1,
            }
            mt5_tf = tf_map.get(tf_value, mt5.TIMEFRAME_H1)

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(mt5.copy_rates_range, symbol, mt5_tf, date_from, date_to),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning(f"MT5 copy_rates_range timed out after 30s for {symbol} {timeframe}")
                return None

            if result is not None and len(result) > 0:
                bars = []
                for bar in result:
                    bars.append({
                        'time': datetime.fromtimestamp(bar['time'], tz=timezone.utc).isoformat(),
                        'open': float(bar['open']),
                        'high': float(bar['high']),
                        'low': float(bar['low']),
                        'close': float(bar['close']),
                        'volume': int(bar['tick_volume']),
                    })
                return bars

            return None

        except Exception as e:
            logger.error(f"Error getting OHLCV range data: {e}")
            return None

    def _get_simulated_ohlcv(self, symbol: str, timeframe: str, count: int) -> List[Dict[str, Any]]:
        """Generate simulated OHLCV data."""
        import numpy as np
        
        tf_minutes = self.TIMEFRAMES.get(timeframe.upper(), 60)
        base_price = self._get_simulated_symbol(symbol).bid
        
        bars = []
        current_time = datetime.now(timezone.utc)
        price = base_price
        
        for i in range(count - 1, -1, -1):
            bar_time = current_time - timedelta(minutes=tf_minutes * i)
            
            # Generate random price movement
            change = np.random.randn() * 0.0005
            price = price * (1 + change)
            
            # Generate OHLC
            high = price * (1 + abs(np.random.randn()) * 0.0003)
            low = price * (1 - abs(np.random.randn()) * 0.0003)
            open_price = price * (1 + np.random.randn() * 0.0002)
            
            bars.append({
                'time': bar_time.isoformat(),
                'open': open_price,
                'high': high,
                'low': low,
                'close': price,
                'volume': int(np.random.exponential(1000))
            })
        
        return bars
    
    async def place_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        deviation: int = 20,
        magic: int = 12345,
        comment: str = "ICT_Bot",
        expiration: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Place a trading order.
        
        Args:
            symbol: Trading symbol
            order_type: Order type (buy, sell, buy_limit, etc.)
            volume: Position size in lots
            price: Price for limit/stop orders
            stop_loss: Stop loss price
            take_profit: Take profit price
            deviation: Maximum price deviation in points
            magic: Magic number for order identification
            comment: Order comment
            expiration: Expiration time for pending orders
            
        Returns:
            Dict with order result
        """
        try:
            logger.info(f"Placing {order_type} order: {symbol} {volume} lots")
            
            if self._use_simulation:
                info = await self.get_symbol_info(symbol)
                is_buy_sim = order_type.lower() in ('buy', 'buy_limit', 'buy_stop', 'buy_stop_limit')
                fill_price = info.ask if is_buy_sim else info.bid
                
                # For pending orders, use the requested price as entry
                is_pending_sim = order_type.lower() in ('buy_limit', 'sell_limit', 'buy_stop', 'sell_stop')
                if is_pending_sim and price is not None:
                    fill_price = price
                
                import random
                sim_ticket = int(datetime.now(timezone.utc).timestamp() * 1000) + random.randint(0, 999)
                return {
                    "success": True,
                    "order_id": sim_ticket,
                    "ticket": sim_ticket,
                    "price": fill_price,
                    "volume": volume,
                    "simulated": True,
                    "is_pending": is_pending_sim,
                    "converted_to_market": False,
                    "final_order_type": order_type.lower(),
                    "sl": stop_loss,
                    "tp": take_profit,
                }
            
            # Ensure MT5 connection before critical order operation
            if not await self.ensure_connected():
                return {"success": False, "error": "MT5 connection lost and reconnection failed"}

            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            
            # Get symbol info for price and filling mode
            async with self._lock:
                symbol_info = await asyncio.to_thread(mt5.symbol_info, symbol)
            if symbol_info is None:
                return {"success": False, "error": f"Symbol {symbol} not found"}
            
            # Get current price if not provided
            if price is None:
                price = symbol_info.ask if order_type.lower() == 'buy' else symbol_info.bid
            else:
                # Round user-provided price (e.g. pending order entry) to symbol digits
                price = round(price, symbol_info.digits)
            
            # Determine order type and whether this is a pending order
            is_pending = order_type.lower() in ('buy_limit', 'sell_limit', 'buy_stop', 'sell_stop', 'buy_stop_limit', 'sell_stop_limit')
            converted_to_market = False
            requested_order_type = order_type.lower()

            # =============================================
            # PENDING ORDER PRICE VALIDATION (prevents 10015 "Invalid Price")
            # MT5 requires:
            #   buy_limit  < Ask  (buy below market)
            #   sell_limit > Bid  (sell above market)
            #   buy_stop   > Ask  (buy above market / breakout)
            #   sell_stop  < Bid  (sell below market / breakdown)
            # Also must respect trade_stops_level minimum distance.
            # =============================================
            if is_pending:
                current_ask = symbol_info.ask
                current_bid = symbol_info.bid
                stops_level = getattr(symbol_info, 'trade_stops_level', 0)
                min_pending_distance = max(stops_level, 5) * symbol_info.point  # At least 5 points buffer
                ot = order_type.lower()
                
                if ot == 'buy_limit':
                    # buy_limit price must be BELOW ask
                    if price >= current_ask:
                        # Price is at or above market -- convert to market buy
                        logger.warning(
                            f"[PRICE-FIX] buy_limit price {price:.{symbol_info.digits}f} >= Ask {current_ask:.{symbol_info.digits}f} "
                            f"-- converting to MARKET BUY (price already reached)"
                        )
                        order_type = 'buy'
                        is_pending = False
                        converted_to_market = True
                        price = current_ask
                    elif (current_ask - price) < min_pending_distance:
                        adjusted = round(current_ask - min_pending_distance, symbol_info.digits)
                        logger.warning(
                            f"[PRICE-FIX] buy_limit price {price:.{symbol_info.digits}f} too close to Ask {current_ask:.{symbol_info.digits}f} "
                            f"(min distance: {min_pending_distance:.{symbol_info.digits}f}) -- adjusted to {adjusted:.{symbol_info.digits}f}"
                        )
                        price = adjusted
                
                elif ot == 'sell_limit':
                    # sell_limit price must be ABOVE bid
                    if price <= current_bid:
                        logger.warning(
                            f"[PRICE-FIX] sell_limit price {price:.{symbol_info.digits}f} <= Bid {current_bid:.{symbol_info.digits}f} "
                            f"-- converting to MARKET SELL (price already reached)"
                        )
                        order_type = 'sell'
                        is_pending = False
                        converted_to_market = True
                        price = current_bid
                    elif (price - current_bid) < min_pending_distance:
                        adjusted = round(current_bid + min_pending_distance, symbol_info.digits)
                        logger.warning(
                            f"[PRICE-FIX] sell_limit price {price:.{symbol_info.digits}f} too close to Bid {current_bid:.{symbol_info.digits}f} "
                            f"(min distance: {min_pending_distance:.{symbol_info.digits}f}) -- adjusted to {adjusted:.{symbol_info.digits}f}"
                        )
                        price = adjusted
                
                elif ot == 'buy_stop':
                    # buy_stop price must be ABOVE ask
                    if price <= current_ask:
                        # Price is at or below market -- convert to market buy
                        logger.warning(
                            f"[PRICE-FIX] buy_stop price {price:.{symbol_info.digits}f} <= Ask {current_ask:.{symbol_info.digits}f} "
                            f"-- converting to MARKET BUY (price already passed)"
                        )
                        order_type = 'buy'
                        is_pending = False
                        converted_to_market = True
                        price = current_ask
                    elif (price - current_ask) < min_pending_distance:
                        adjusted = round(current_ask + min_pending_distance, symbol_info.digits)
                        logger.warning(
                            f"[PRICE-FIX] buy_stop price {price:.{symbol_info.digits}f} too close to Ask {current_ask:.{symbol_info.digits}f} "
                            f"(min distance: {min_pending_distance:.{symbol_info.digits}f}) -- adjusted to {adjusted:.{symbol_info.digits}f}"
                        )
                        price = adjusted
                
                elif ot == 'sell_stop':
                    # sell_stop price must be BELOW bid
                    if price >= current_bid:
                        logger.warning(
                            f"[PRICE-FIX] sell_stop price {price:.{symbol_info.digits}f} >= Bid {current_bid:.{symbol_info.digits}f} "
                            f"-- converting to MARKET SELL (price already passed)"
                        )
                        order_type = 'sell'
                        is_pending = False
                        converted_to_market = True
                        price = current_bid
                    elif (current_bid - price) < min_pending_distance:
                        adjusted = round(current_bid - min_pending_distance, symbol_info.digits)
                        logger.warning(
                            f"[PRICE-FIX] sell_stop price {price:.{symbol_info.digits}f} too close to Bid {current_bid:.{symbol_info.digits}f} "
                            f"(min distance: {min_pending_distance:.{symbol_info.digits}f}) -- adjusted to {adjusted:.{symbol_info.digits}f}"
                        )
                        price = adjusted
            
            if order_type.lower() == 'buy':
                mt5_type = mt5.ORDER_TYPE_BUY
            elif order_type.lower() == 'sell':
                mt5_type = mt5.ORDER_TYPE_SELL
            else:
                mt5_type = self.ORDER_TYPES.get(order_type.lower())
                if mt5_type is None:
                    logger.error(f"Invalid order type: '{order_type}'. Valid types: {list(self.ORDER_TYPES.keys())}")
                    return {"success": False, "error": f"Invalid order type: {order_type}"}
            
            # Build request - determine filling mode from symbol
            filling_modes = self._get_filling_modes(symbol_info)
            filling_type = filling_modes[0]
            filling_mode_idx = 0
            
            # Use TRADE_ACTION_PENDING for limit/stop orders, TRADE_ACTION_DEAL for market
            trade_action = mt5.TRADE_ACTION_PENDING if is_pending else mt5.TRADE_ACTION_DEAL
            
            request = {
                "action": trade_action,
                "symbol": symbol,
                "volume": volume,
                "type": mt5_type,
                "price": price,
                "magic": magic,
                "comment": comment,
                "type_filling": filling_type,
            }
            
            # Market orders need deviation; pending orders need expiration
            if is_pending:
                # Determine which expiration modes the symbol supports
                # expiration_mode bitmask: bit0=GTC(1), bit1=DAY(2), bit2=SPECIFIED(4), bit3=SPECIFIED_DAY(8)
                exp_mode = getattr(symbol_info, 'expiration_mode', 0)
                supports_gtc = bool(exp_mode & 1)
                supports_day = bool(exp_mode & 2)
                supports_specified = bool(exp_mode & 4)
                
                print(f"[MT5] {symbol} expiration_mode={exp_mode} (GTC={supports_gtc}, DAY={supports_day}, SPECIFIED={supports_specified})", flush=True)
                
                # Strategy: try the safest mode the symbol supports
                # The bot's PendingOrderManager handles expiration internally,
                # so we prefer GTC to avoid broker rejections
                if supports_gtc:
                    request["type_time"] = mt5.ORDER_TIME_GTC
                elif supports_day:
                    request["type_time"] = mt5.ORDER_TIME_DAY
                elif supports_specified and expiration:
                    exp_timestamp = int(expiration.timestamp())
                    request["type_time"] = mt5.ORDER_TIME_SPECIFIED
                    request["expiration"] = exp_timestamp
                else:
                    # Last resort: try GTC anyway (most common default)
                    request["type_time"] = mt5.ORDER_TIME_GTC
                    print(f"[MT5] WARNING: {symbol} expiration_mode={exp_mode} unknown, defaulting to GTC", flush=True)
            else:
                request["deviation"] = deviation
                request["type_time"] = mt5.ORDER_TIME_GTC
            
            logger.info(f"Using filling mode: {filling_type}, action: {'PENDING' if is_pending else 'DEAL'} for {symbol}")
            
            # Validate and adjust stop loss if needed (prevents "Invalid Stops" error)
            min_distance = (symbol_info.trade_stops_level + 5) * symbol_info.point
            
            # Determine direction from order type (handles both market and pending orders)
            is_buy_direction = order_type.lower() in ('buy', 'buy_limit', 'buy_stop', 'buy_stop_limit')
            
            if stop_loss:
                if is_buy_direction:
                    # For BUY direction, stop loss must be BELOW price
                    max_sl = price - min_distance
                    if stop_loss > max_sl:
                        logger.warning(f"Stop loss {stop_loss:.5f} too close to price {price:.5f}")
                        logger.warning(f"Adjusting SL from {stop_loss:.5f} to {max_sl:.5f} (min distance: {min_distance:.5f})")
                        stop_loss = max_sl
                else:
                    # For SELL direction, stop loss must be ABOVE price
                    min_sl = price + min_distance
                    if stop_loss < min_sl:
                        logger.warning(f"Stop loss {stop_loss:.5f} too close to price {price:.5f}")
                        logger.warning(f"Adjusting SL from {stop_loss:.5f} to {min_sl:.5f} (min distance: {min_distance:.5f})")
                        stop_loss = min_sl
                
                # Round to symbol digits
                stop_loss = round(stop_loss, symbol_info.digits)
                request["sl"] = stop_loss
            
            # Validate and adjust take profit if needed
            if take_profit:
                # CRITICAL: TP direction validation -- reject trades with TP on wrong side
                if is_buy_direction and take_profit <= price:
                    logger.error(
                        f"[TP-REJECT] BUY TP {take_profit:.{symbol_info.digits}f} is BELOW entry {price:.{symbol_info.digits}f} "
                        f"-- this is an invalid trade setup. Rejecting order."
                    )
                    return {
                        "success": False,
                        "error": f"Invalid TP: BUY take_profit ({take_profit}) must be ABOVE entry ({price})"
                    }
                elif not is_buy_direction and take_profit >= price:
                    logger.error(
                        f"[TP-REJECT] SELL TP {take_profit:.{symbol_info.digits}f} is ABOVE entry {price:.{symbol_info.digits}f} "
                        f"-- this is an invalid trade setup. Rejecting order."
                    )
                    return {
                        "success": False,
                        "error": f"Invalid TP: SELL take_profit ({take_profit}) must be BELOW entry ({price})"
                    }
                
                if is_buy_direction:
                    # For BUY direction, take profit must be ABOVE price with min distance
                    min_tp = price + min_distance
                    if take_profit < min_tp:
                        logger.warning(f"Take profit {take_profit:.5f} too close to price {price:.5f}")
                        logger.warning(f"Adjusting TP from {take_profit:.5f} to {min_tp:.5f}")
                        take_profit = min_tp
                else:
                    # For SELL direction, take profit must be BELOW price with min distance
                    max_tp = price - min_distance
                    if take_profit > max_tp:
                        logger.warning(f"Take profit {take_profit:.5f} too close to price {price:.5f}")
                        logger.warning(f"Adjusting TP from {take_profit:.5f} to {max_tp:.5f}")
                        take_profit = max_tp
                
                # Round to symbol digits
                take_profit = round(take_profit, symbol_info.digits)
                request["tp"] = take_profit
            
            logger.info(f"Order request: {order_type} {symbol} @ {price:.5f}, SL: {stop_loss}, TP: {take_profit}")
            
            # T2-5: Retry loop for requotes (10004) and rejections (10006)
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                async with self._lock:
                    # For market orders, refresh price right before sending to minimize TOCTOU gap
                    if not is_pending and price is not None:
                        fresh_info = await asyncio.to_thread(mt5.symbol_info, symbol)
                        if fresh_info is not None:
                            fresh_price = fresh_info.ask if is_buy_direction else fresh_info.bid
                            if fresh_price != request["price"]:
                                logger.info(f"Refreshed market price: {request['price']:.5f} -> {fresh_price:.5f}")
                                request["price"] = fresh_price
                    
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(mt5.order_send, request),
                            timeout=30.0  # 30s timeout prevents deadlocking the entire bot
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"MT5 order_send TIMED OUT after 30s (attempt {attempt}/{max_retries})")
                        result = None
                
                if result is None:
                    async with self._lock:
                        last_error = await asyncio.to_thread(mt5.last_error)
                    logger.error(f"MT5 order_send returned None! Last MT5 error: {last_error}")
                    logger.error(f"Order request was: {request}")
                    logger.error("Possible causes: AutoTrading disabled, MT5 not responding, account restrictions")
                    return {
                        "success": False,
                        "error": f"MT5 not responding: {last_error}",
                        "retcode": None
                    }
                
                if result.retcode == mt5.TRADE_RETCODE_DONE or (is_pending and result.retcode == mt5.TRADE_RETCODE_PLACED):
                    # Pending: order ticket. Market: prefer resolved position ticket.
                    ticket = result.order or result.deal
                    if not is_pending and result.order:
                        try:
                            resolved = await self.resolve_fill_position_ticket(
                                symbol,
                                int(result.order),
                                int(result.deal) if result.deal else None,
                            )
                            if resolved:
                                ticket = resolved
                        except Exception as resolve_exc:
                            logger.debug(
                                f"Position ticket resolve skipped: {resolve_exc}"
                            )
                    # Partial fill detection
                    fill_volume = result.volume if result.volume else volume
                    if abs(fill_volume - volume) > 0.001:
                        logger.warning(
                            f"PARTIAL FILL: requested {volume} lots, filled {fill_volume} lots "
                            f"(slippage: {volume - fill_volume:.3f} lots)"
                        )
                    final_price = result.price if result.price else request.get("price", price)
                    logger.info(
                        f"Order successful: ticket={ticket}, order={result.order}, "
                        f"deal={result.deal}, price={final_price}, filled={fill_volume}, "
                        f"converted_to_market={converted_to_market}, "
                        f"final_order_type={order_type.lower()}"
                    )
                    return {
                        "success": True,
                        "order_id": result.order,
                        "ticket": ticket,
                        "price": final_price,
                        "volume": fill_volume,
                        "requested_volume": volume,
                        "partial_fill": abs(fill_volume - volume) > 0.001,
                        "converted_to_market": converted_to_market,
                        "final_order_type": order_type.lower(),
                        "requested_order_type": requested_order_type,
                        "sl": request.get("sl", stop_loss),
                        "tp": request.get("tp", take_profit),
                    }
                elif result.retcode == 10030 or (
                    isinstance(getattr(result, "comment", ""), str)
                    and "filling" in result.comment.lower()
                    and attempt < max_retries
                ):
                    # Unsupported filling mode — try next allowed mode
                    if filling_mode_idx + 1 < len(filling_modes):
                        filling_mode_idx += 1
                        filling_type = filling_modes[filling_mode_idx]
                        request["type_filling"] = filling_type
                        logger.warning(
                            f"Filling mode rejected (retcode={result.retcode}, "
                            f"{result.comment}) — retrying with type_filling={filling_type}"
                        )
                        continue
                    logger.error(
                        f"Order REJECTED by MT5: retcode={result.retcode}, "
                        f"comment='{result.comment}' (no remaining filling modes)"
                    )
                    return {
                        "success": False,
                        "error": f"MT5 rejected: {result.comment} (code: {result.retcode})",
                        "retcode": result.retcode,
                    }
                elif result.retcode == 10014:
                    logger.error(f"INVALID VOLUME: {volume} lots for {symbol} — {result.comment}")
                    return {
                        "success": False,
                        "error": f"Invalid volume: {volume} lots not accepted for {symbol}",
                        "retcode": 10014
                    }
                elif result.retcode == 10019:
                    logger.error(f"INSUFFICIENT MARGIN for {volume} lots {symbol} — {result.comment}")
                    return {
                        "success": False,
                        "error": f"Insufficient margin for {volume} lots {symbol}",
                        "retcode": 10019
                    }
                elif result.retcode == 10031:
                    logger.error(f"POSITION NOT FOUND during order on {symbol} — {result.comment}")
                    return {
                        "success": False,
                        "error": "Position not found",
                        "retcode": 10031,
                        "position_not_found": True
                    }
                elif result.retcode in (10004, 10006) and attempt < max_retries:
                    # 10004 = requote, 10006 = rejected - retry with refreshed price
                    logger.warning(
                        f"Order attempt {attempt}/{max_retries} got retcode {result.retcode} "
                        f"({result.comment}) - retrying in 500ms"
                    )
                    await asyncio.sleep(0.5)
                    
                    # Only refresh price for market orders — pending orders keep their target price
                    if not is_pending:
                        async with self._lock:
                            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
                        if tick:
                            if order_type.lower() == 'buy':
                                request["price"] = tick.ask
                            else:
                                request["price"] = tick.bid
                            logger.info(f"Refreshed price for retry: {request['price']:.5f}")
                    continue
                else:
                    # Log detailed error info
                    logger.error(f"Order REJECTED by MT5: retcode={result.retcode}, comment='{result.comment}'")
                    logger.error(f"Order request was: {request}")
                    return {
                        "success": False,
                        "error": f"MT5 rejected: {result.comment} (code: {result.retcode})",
                        "retcode": result.retcode
                    }
            
            # All retries exhausted (shouldn't reach here, but safety net)
            logger.error(f"Order failed after {max_retries} attempts: retcode={result.retcode}")
            return {
                "success": False,
                "error": f"Failed after {max_retries} retries: {result.comment} (code: {result.retcode})",
                "retcode": result.retcode
            }
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"success": False, "error": str(e)}
    
    async def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Modify an existing position's SL/TP.
        
        Args:
            ticket: Position ticket
            stop_loss: New stop loss
            take_profit: New take profit
            
        Returns:
            Dict with modification result
        """
        try:
            logger.info(f"Modifying position {ticket}")
            
            if self._use_simulation:
                return {"success": True, "simulated": True}
            
            # Ensure MT5 connection before critical modification
            if not await self.ensure_connected():
                return {"success": False, "error": "MT5 connection lost and reconnection failed"}

            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            
            async with self._lock:
                # Get position info
                position = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
                if not position:
                    logger.warning(f"modify_position: position {ticket} not found in MT5 — may have been closed")
                    return {
                        "success": False,
                        "error": "Position not found",
                        "position_not_found": True
                    }
                
                position = position[0]
                
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": position.symbol,
                    "position": ticket,
                }
                
                # TRADE_ACTION_SLTP requires BOTH sl and tp in the request.
                # If only one is provided, use the position's current value for the other
                # to avoid accidentally clearing it.
                request["sl"] = stop_loss if stop_loss is not None else position.sl
                request["tp"] = take_profit if take_profit is not None else position.tp
                
                result = await asyncio.to_thread(mt5.order_send, request)
            
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {"success": True}
            elif result and result.retcode == 10031:
                logger.warning(f"modify_position: retcode 10031 for ticket {ticket} — position closed between check and send")
                return {
                    "success": False,
                    "error": "Position not found",
                    "retcode": 10031,
                    "position_not_found": True
                }
            else:
                return {
                    "success": False,
                    "error": result.comment if result else "Modification failed",
                    "retcode": result.retcode if result else None
                }
            
        except Exception as e:
            logger.error(f"Error modifying position: {e}")
            return {"success": False, "error": str(e)}
    
    async def modify_order(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Modify a pending order's price, SL, and/or TP.
        Uses TRADE_ACTION_MODIFY for pending orders.
        Falls back to modify_position if the ticket is an open position.
        """
        if self._use_simulation:
            return {"success": True, "simulated": True}

        try:
            mt5 = self._mcp_client

            async with self._lock:
                orders = await asyncio.to_thread(mt5.orders_get, ticket=ticket)

            if orders and len(orders) > 0:
                order = orders[0]
                request = {
                    "action": mt5.TRADE_ACTION_MODIFY,
                    "order": ticket,
                    "symbol": order.symbol,
                    "price": price if price is not None else order.price_open,
                    "sl": stop_loss if stop_loss is not None else (order.sl or 0.0),
                    "tp": take_profit if take_profit is not None else (order.tp or 0.0),
                    "type_time": order.type_time,
                    "expiration": order.expiration,
                }

                async with self._lock:
                    result = await asyncio.to_thread(mt5.order_send, request)

                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Pending order {ticket} modified successfully")
                    return {"success": True, "ticket": ticket}
                else:
                    err = getattr(result, 'comment', 'unknown') if result else 'no result'
                    code = getattr(result, 'retcode', -1) if result else -1
                    logger.warning(f"Pending order modify failed: {err} (code {code})")
                    return {"success": False, "error": err, "retcode": code}
            else:
                return await self.modify_position(ticket, stop_loss, take_profit)

        except Exception as e:
            logger.error(f"Error modifying order {ticket}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get pending orders.
        
        Args:
            symbol: Filter by symbol (optional)
            
        Returns:
            List of pending orders
        """
        try:
            if self._use_simulation:
                return []  # No pending orders in simulation
            
            mt5 = self._mcp_client
            
            async with self._lock:
                if symbol:
                    orders = await asyncio.to_thread(mt5.orders_get, symbol=symbol)
                else:
                    orders = await asyncio.to_thread(mt5.orders_get)
            
            if orders is None:
                return []
            
            return [
                {
                    "ticket": int(o.ticket),
                    "symbol": str(o.symbol),
                    "type": int(o.type),
                    "volume": float(o.volume_current),
                    "price_open": float(o.price_open),
                    "sl": float(o.sl) if o.sl else None,
                    "tp": float(o.tp) if o.tp else None,
                    "time_setup": o.time_setup,
                    "magic": int(o.magic) if hasattr(o, 'magic') else 0,
                    "comment": str(o.comment) if o.comment else ""
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def close_position(
        self,
        ticket: int,
        volume: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Close a position.
        
        Args:
            ticket: Position ticket
            volume: Volume to close (partial close if less than position)
            
        Returns:
            Dict with close result
        """
        try:
            logger.info(f"Closing position {ticket}")
            
            # Guard: prevent concurrent close of the same ticket
            if ticket in self._closing_tickets:
                logger.warning(f"Position {ticket} close already in progress — skipping duplicate")
                return {"success": False, "error": "Close already in progress for this position"}
            self._closing_tickets.add(ticket)
            
            if self._use_simulation:
                self._closing_tickets.discard(ticket)
                # Use a non-hardcoded simulated close price
                return {
                    "success": True,
                    "price": 0.0,  # Caller should use current market price
                    "profit": 0.0,  # Actual P&L calculated by position manager
                    "simulated": True
                }
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            
            async with self._lock:
                # Get position info
                position = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
                if not position:
                    self._closing_tickets.discard(ticket)
                    return {"success": False, "error": "Position not found"}
                
                position = position[0]
                close_volume = volume if volume else position.volume
                
                # Determine close type (opposite of position type)
                if position.type == mt5.ORDER_TYPE_BUY:
                    close_type = mt5.ORDER_TYPE_SELL
                    tick = await asyncio.to_thread(mt5.symbol_info_tick, position.symbol)
                    price = tick.bid
                else:
                    close_type = mt5.ORDER_TYPE_BUY
                    tick = await asyncio.to_thread(mt5.symbol_info_tick, position.symbol)
                    price = tick.ask
                
                # Get symbol info for proper filling mode
                sym_info = await asyncio.to_thread(mt5.symbol_info, position.symbol)
                filling_type = self._get_filling_type(sym_info) if sym_info else 1
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": position.symbol,
                    "volume": close_volume,
                    "type": close_type,
                    "position": ticket,
                    "price": price,
                    "deviation": 30,  # Higher deviation for closes — must succeed
                    "magic": position.magic,
                    "comment": "Close by ICT_Bot",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_type,
                }
            
            # Retry loop for closes (requotes are common during volatility)
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                async with self._lock:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(mt5.order_send, request),
                            timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"MT5 close order_send TIMED OUT after 30s (attempt {attempt}/{max_retries})")
                        result = None
                
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    self._closing_tickets.discard(ticket)
                    return {
                        "success": True,
                        "price": result.price,
                        "profit": position.profit
                    }
                elif result and result.retcode in (10004, 10006) and attempt < max_retries:
                    # Requote or rejected — refresh price and retry
                    logger.warning(f"Close requote (attempt {attempt}/{max_retries}), retrying...")
                    await asyncio.sleep(0.3)
                    async with self._lock:
                        tick = await asyncio.to_thread(mt5.symbol_info_tick, request["symbol"])
                    if tick:
                        if request["type"] == mt5.ORDER_TYPE_SELL:
                            request["price"] = tick.bid
                        else:
                            request["price"] = tick.ask
                else:
                    break
            
            self._closing_tickets.discard(ticket)
            return {
                "success": False,
                "error": result.comment if result else "Close failed"
            }
            
        except Exception as e:
            self._closing_tickets.discard(ticket)
            logger.error(f"Error closing position: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_positions(
        self,
        symbol: Optional[str] = None
    ) -> List[Position]:
        """
        Get open positions.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of Position objects
        """
        try:
            if self._use_simulation:
                return []  # No positions in simulation
            
            # Real MT5 call (non-blocking)
            mt5 = self._mcp_client
            
            async with self._lock:
                if symbol:
                    result = await asyncio.to_thread(mt5.positions_get, symbol=symbol)
                else:
                    result = await asyncio.to_thread(mt5.positions_get)
            
            positions = []
            for pos in (result or []):
                positions.append(Position(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    type='buy' if pos.type == 0 else 'sell',
                    volume=pos.volume,
                    price_open=pos.price_open,
                    price_current=pos.price_current,
                    sl=pos.sl,
                    tp=pos.tp,
                    profit=pos.profit,
                    magic=pos.magic,
                    comment=pos.comment,
                    time=datetime.fromtimestamp(pos.time, tz=timezone.utc)
                ))
            
            return positions
            
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    async def get_pending_orders(
        self,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get pending orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of order dicts
        """
        try:
            if self._use_simulation:
                return []
            
            # Real MCP call
            if symbol:
                result = self._mcp_client.order.get_orders(symbol=symbol)
            else:
                result = self._mcp_client.order.get_orders()
            
            return result or []
            
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def cancel_order(self, ticket: int) -> Dict[str, Any]:
        """
        Cancel a pending order.
        
        Args:
            ticket: Order ticket
            
        Returns:
            Dict with cancellation result
        """
        try:
            logger.info(f"Cancelling order {ticket}")
            
            if self._use_simulation:
                return {"success": True, "simulated": True}
            
            mt5 = self._mcp_client
            
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": ticket,
            }
            
            async with self._lock:
                result = await asyncio.to_thread(mt5.order_send, request)
            
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {"success": True}
            else:
                error_msg = getattr(result, 'comment', 'Cancel failed') if result else 'No response from MT5'
                retcode = getattr(result, 'retcode', 'N/A') if result else 'N/A'
                return {
                    "success": False,
                    "error": f"{error_msg} (retcode={retcode})"
                }
            
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get trade history (closed deals).
        
        Args:
            start_time: Start of history period
            end_time: End of history period
            symbol: Optional symbol filter
            
        Returns:
            List of historical trade dicts
        """
        try:
            if self._use_simulation:
                return []
            
            mt5 = self._mcp_client
            
            # Use MT5's history_deals_get function (non-blocking)
            async with self._lock:
                if symbol:
                    deals = await asyncio.to_thread(mt5.history_deals_get, start_time, end_time, group=f"*{symbol}*")
                else:
                    deals = await asyncio.to_thread(mt5.history_deals_get, start_time, end_time)
            
            if deals is None or len(deals) == 0:
                logger.info(f"No deals found in history from {start_time} to {end_time}")
                return []
            
            # Convert to list of dicts
            result = []
            for deal in deals:
                deal_dict = {
                    'ticket': deal.ticket,
                    'deal': deal.deal if hasattr(deal, 'deal') else deal.ticket,
                    'order': deal.order,
                    'time': datetime.fromtimestamp(deal.time, tz=timezone.utc),
                    'type': 'buy' if deal.type == 0 else 'sell' if deal.type == 1 else str(deal.type),
                    'entry': deal.entry,  # 0=in, 1=out, 2=inout, 3=state
                    'position_id': deal.position_id,
                    'symbol': deal.symbol,
                    'volume': deal.volume,
                    'price': deal.price,
                    'commission': deal.commission,
                    'swap': deal.swap,
                    'profit': deal.profit,
                    'fee': deal.fee if hasattr(deal, 'fee') else 0,
                    'comment': deal.comment,
                    'magic': deal.magic,
                    'reason': deal.reason,
                    'external_id': deal.external_id if hasattr(deal, 'external_id') else ''
                }
                result.append(deal_dict)
            
            logger.info(f"Retrieved {len(result)} deals from MT5 history")
            return result
            
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def get_order_history(
        self,
        start_time: datetime,
        end_time: datetime,
        ticket: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get historical orders from MT5.
        
        Unlike get_history() which returns deals, this returns the orders
        themselves with their final state (filled, cancelled, etc.).
        
        Args:
            start_time: Start of history period
            end_time: End of history period
            ticket: Optional specific order ticket to look up
            
        Returns:
            List of historical order dicts with fields:
            - ticket: order ticket
            - state: 0=started, 1=placed, 2=canceled, 3=partial, 4=filled, 5=rejected
            - position_id: position ticket (set when order fills)
            - symbol, volume, price_open, price_current, sl, tp, type, etc.
        """
        try:
            if self._use_simulation:
                return []
            
            mt5 = self._mcp_client
            
            async with self._lock:
                if ticket:
                    orders = await asyncio.to_thread(
                        mt5.history_orders_get, ticket=ticket
                    )
                else:
                    orders = await asyncio.to_thread(
                        mt5.history_orders_get, start_time, end_time
                    )
            
            if orders is None or len(orders) == 0:
                return []
            
            result = []
            for o in orders:
                order_dict = {
                    'ticket': o.ticket,
                    'time_setup': datetime.fromtimestamp(o.time_setup, tz=timezone.utc) if o.time_setup else None,
                    'time_done': datetime.fromtimestamp(o.time_done, tz=timezone.utc) if o.time_done else None,
                    'type': int(o.type),
                    'state': int(o.state),
                    'position_id': o.position_id,
                    'symbol': o.symbol,
                    'volume_initial': o.volume_initial,
                    'volume_current': o.volume_current,
                    'price_open': o.price_open,
                    'price_current': o.price_current,
                    'sl': o.sl,
                    'tp': o.tp,
                    'comment': o.comment if hasattr(o, 'comment') else '',
                    'magic': o.magic if hasattr(o, 'magic') else 0,
                }
                result.append(order_dict)
            
            logger.info(f"Retrieved {len(result)} historical orders from MT5")
            return result
            
        except Exception as e:
            logger.error(f"Error getting order history: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    @staticmethod
    def _to_utc_datetime(timestamp: float) -> datetime:
        """Convert broker epoch seconds to aware UTC datetime."""
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    async def _resolve_position_ticket_from_history(
        self,
        symbol: str,
        order_ticket: int,
        deal_ticket: Optional[int] = None,
    ) -> Optional[int]:
        """Resolve actual position ticket from broker order/deal history."""
        try:
            if self._use_simulation:
                return order_ticket

            history = await self.get_history_orders(
                start_time=datetime.now(timezone.utc) - timedelta(hours=24),
                end_time=datetime.now(timezone.utc),
                ticket=order_ticket,
            )
            for order in history:
                position_id = order.get("position_id")
                if position_id:
                    return int(position_id)
        except Exception as exc:
            logger.debug(f"Could not resolve position ticket for order {order_ticket}: {exc}")
        return order_ticket

    async def resolve_fill_position_ticket(
        self,
        symbol: str,
        order_ticket: int,
        deal_ticket: Optional[int] = None,
    ) -> int:
        """Return broker position identity for a market fill (not the order ticket)."""
        resolved = await self._resolve_position_ticket_from_history(
            symbol, order_ticket, deal_ticket
        )
        return resolved or order_ticket

    async def health_check(self) -> Dict[str, Any]:
        """
        Check MT5 connection health.
        
        Returns:
            Dict with health status
        """
        try:
            account = await self.get_account_info()
            
            return {
                "connected": self._connected,
                "simulation_mode": self._use_simulation,
                "account_accessible": account is not None,
                "balance": account.balance if account else 0,
                "server": self.server
            }
            
        except Exception as e:
            return {
                "connected": False,
                "simulation_mode": self._use_simulation,
                "error": str(e)
            }