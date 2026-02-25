"""
CLI runner for Claude Replay Backtester.

Usage:
    python -m trading_bot.backtesting.run --symbol XAUUSD --start 2025-01-01 --end 2025-06-30
    python -m trading_bot.backtesting.run --symbol XAUUSD --start 2025-01-01 --end 2025-06-30 --dry-run
"""

import argparse
import asyncio
import json
from datetime import datetime

from .replay import ClaudeReplayBacktester


async def main():
    parser = argparse.ArgumentParser(description="Claude Replay Backtester")
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g. XAUUSD)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--interval", type=float, default=1.0, help="Hours between snapshots")
    parser.add_argument("--max-signals", type=int, default=500, help="Max signals to process")
    parser.add_argument("--dry-run", action="store_true", help="Estimate cost without calling Claude")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    claude_client = None
    mt5_client = None

    if not args.dry_run:
        try:
            from ..config import settings
            from ..llm.claude_client import ClaudeClient
            from ..mt5.client import MT5Client

            claude_client = ClaudeClient()
            mt5_client = MT5Client()
            await mt5_client.connect()
        except Exception as e:
            print(f"Error initializing clients: {e}")
            print("Use --dry-run to estimate cost without live clients.")
            return

    bt = ClaudeReplayBacktester(claude_client=claude_client, mt5_client=mt5_client)

    if args.dry_run:
        estimate = await bt.estimate_cost(args.symbol, start, end, args.interval)
        print("\n=== Cost Estimate ===")
        for k, v in estimate.items():
            print(f"  {k}: {v}")
        return

    print(f"\nRunning replay backtest: {args.symbol} {args.start} -> {args.end}")
    print(f"Interval: {args.interval}h, Max signals: {args.max_signals}\n")

    result = await bt.run(
        symbol=args.symbol,
        start_date=start,
        end_date=end,
        interval_hours=args.interval,
        max_signals=args.max_signals,
    )

    print("\n=== Replay Backtest Results ===")
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
