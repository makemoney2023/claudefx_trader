"""
MT5 Connection Test Script

Run this script on Windows to verify your MT5 connection
before starting the full trading bot.

Usage:
    python test_mt5_connection.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_mt5_connection():
    """Test MetaTrader 5 connection and display account info."""
    
    print("=" * 50)
    print("  MT5 Connection Test")
    print("=" * 50)
    print()
    
    # Try to import MetaTrader5
    try:
        import MetaTrader5 as mt5
        print("[OK] MetaTrader5 package imported successfully")
    except ImportError:
        print("[ERROR] MetaTrader5 package not installed!")
        print("Run: pip install MetaTrader5")
        return False
    
    # Load environment variables
    try:
        from dotenv import load_dotenv
        load_dotenv(".env.local")
        load_dotenv(".env")
        print("[OK] Environment variables loaded")
    except ImportError:
        print("[WARN] python-dotenv not installed, using environment variables directly")
    
    # Get credentials
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    
    print()
    print("Configuration:")
    print(f"  Login:  {login}")
    print(f"  Server: {server}")
    print(f"  Password: {'*' * len(password) if password else 'NOT SET'}")
    print()
    
    if not all([login, password, server]):
        print("[ERROR] Missing credentials in .env.local!")
        print("Required: MT5_LOGIN, MT5_PASSWORD, MT5_SERVER")
        return False
    
    # Initialize MT5
    print("Initializing MT5...")
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialization failed: {mt5.last_error()}")
        print()
        print("Troubleshooting:")
        print("  1. Make sure MetaTrader 5 is installed")
        print("  2. Make sure MT5 is running and logged in")
        print("  3. Check if AutoTrading is enabled")
        return False
    
    print("[OK] MT5 initialized")
    
    # Check if already logged in - don't switch accounts!
    account_info = mt5.account_info()
    if account_info and account_info.login > 0:
        print(f"[OK] Already logged in to account {account_info.login}")
        print("     (Skipping login to avoid switching accounts)")
    else:
        # Only attempt login if not already logged in
        print(f"Logging in to {server}...")
        authorized = mt5.login(int(login), password=password, server=server)
        
        if not authorized:
            print(f"[ERROR] Login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False
        
        print("[OK] Login successful!")
    print()
    
    # Get account info
    account_info = mt5.account_info()
    if account_info:
        print("=" * 50)
        print("  Account Information")
        print("=" * 50)
        print(f"  Login:        {account_info.login}")
        print(f"  Name:         {account_info.name}")
        print(f"  Server:       {account_info.server}")
        print(f"  Currency:     {account_info.currency}")
        print(f"  Leverage:     1:{account_info.leverage}")
        print(f"  Balance:      {account_info.balance:.2f} {account_info.currency}")
        print(f"  Equity:       {account_info.equity:.2f} {account_info.currency}")
        print(f"  Profit:       {account_info.profit:.2f} {account_info.currency}")
        print(f"  Free Margin:  {account_info.margin_free:.2f} {account_info.currency}")
        print()
        
        # Check trading permissions
        print("Trading Permissions:")
        print(f"  Trade allowed:     {'YES' if account_info.trade_allowed else 'NO'}")
        print(f"  Expert allowed:    {'YES' if account_info.trade_expert else 'NO'}")
        print()
        
        if not account_info.trade_allowed:
            print("[WARN] Trading is not allowed on this account!")
        if not account_info.trade_expert:
            print("[WARN] Expert Advisors are not enabled!")
            print("       Enable in: Tools > Options > Expert Advisors")
    
    # Get terminal info
    terminal_info = mt5.terminal_info()
    if terminal_info:
        print("Terminal Info:")
        print(f"  Path:         {terminal_info.path}")
        print(f"  Connected:    {'YES' if terminal_info.connected else 'NO'}")
        print(f"  Trade mode:   {'REAL' if terminal_info.trade_allowed else 'DEMO/DISABLED'}")
        print()
    
    # Test getting symbol data
    print("Testing market data access...")
    symbols_to_test = ["EURUSD", "GBPUSD", "XAUUSD"]
    
    for symbol in symbols_to_test:
        info = mt5.symbol_info(symbol)
        if info:
            print(f"  {symbol}: Bid={info.bid:.5f}, Ask={info.ask:.5f}, Spread={info.spread}")
        else:
            print(f"  {symbol}: Not available")
    
    print()
    
    # Test getting OHLCV data
    print("Testing historical data access...")
    rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_H1, 0, 5)
    if rates is not None and len(rates) > 0:
        print(f"  [OK] Retrieved {len(rates)} H1 candles for EURUSD")
    else:
        print("  [WARN] Could not retrieve historical data")
    
    # Shutdown
    mt5.shutdown()
    
    print()
    print("=" * 50)
    print("  Connection Test Complete!")
    print("=" * 50)
    print()
    print("Your MT5 connection is working correctly.")
    print("You can now start the trading bot with: start_bot.bat")
    print()
    
    return True


if __name__ == "__main__":
    success = test_mt5_connection()
    
    if not success:
        print()
        print("Connection test FAILED. Please fix the issues above.")
        print()
    
    input("Press Enter to exit...")
