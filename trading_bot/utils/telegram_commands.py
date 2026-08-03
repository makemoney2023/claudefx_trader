"""
Telegram Command Handler for the ICT Trading Bot.

Polls for incoming Telegram messages and dispatches slash commands
to query bot state, manage trades, and view analytics.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Any

from .logging import get_logger
from .notifications import get_notifier

logger = get_logger(__name__)


class TelegramCommandHandler:
    """
    Handles incoming Telegram commands via long polling.
    
    Runs as a background asyncio task alongside the trading bot.
    Only responds to messages from the configured TELEGRAM_CHAT_ID.
    """
    
    def __init__(self, bot_instance=None):
        """
        Initialize the command handler.
        
        Args:
            bot_instance: Reference to the TradingBot instance
        """
        self._bot = bot_instance
        self._notifier = get_notifier()
        self._running = False
        self._update_offset = 0
        self._task: Optional[asyncio.Task] = None
        
        # Command registry: command -> (handler_method, description)
        self._commands = {
            # Status & Monitoring
            '/help': (self._cmd_help, 'List all commands'),
            '/status': (self._cmd_status, 'Bot status overview'),
            '/account': (self._cmd_account, 'Account balance &amp; equity'),
            '/positions': (self._cmd_positions, 'Open positions'),
            '/orders': (self._cmd_orders, 'Pending orders'),
            '/activity': (self._cmd_activity, 'Recent activity feed'),
            # Performance & Analytics
            '/pnl': (self._cmd_pnl, 'Today\'s P/L and stats'),
            '/stats': (self._cmd_stats, 'Overall performance'),
            '/goal': (self._cmd_goal, 'Goal progress &amp; projection'),
            '/session': (self._cmd_session, 'Current session &amp; analytics'),
            '/daily': (self._cmd_daily, 'Send daily summary'),
            '/weekly': (self._cmd_weekly, 'Trigger weekly review'),
            # Trade Actions
            '/close': (self._cmd_close, 'Close position by ticket'),
            '/closeall': (self._cmd_closeall, 'Emergency close all'),
            '/stop': (self._cmd_stop, 'Pause the bot'),
            '/start': (self._cmd_start, 'Resume the bot'),
            '/modify': (self._cmd_modify, 'Modify SL/TP on a position'),
            # Market Intelligence
            '/news': (self._cmd_news, 'News &amp; blackout status'),
            '/calendar': (self._cmd_calendar, 'Economic calendar'),
            '/analysis': (self._cmd_analysis, 'Last signal for a symbol'),
            '/scan': (self._cmd_scan, 'Run opportunity scanner now'),
            '/hot': (self._cmd_hot, 'Show opportunity hot list'),
            # Configuration
            '/symbols': (self._cmd_symbols, 'Trading symbols'),
            '/mode': (self._cmd_mode, 'Scaling mode &amp; tier'),
            '/config': (self._cmd_config, 'Key trading parameters'),
        }
    
    def set_bot_instance(self, bot):
        """Update the bot instance reference (called after bot init)."""
        self._bot = bot
    
    async def start_polling(self):
        """Start the polling loop as a background task."""
        if not self._notifier.enabled:
            logger.warning("Telegram commands disabled - notifier not enabled")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._polling_loop())
        logger.info("Telegram command handler started (polling every 2s)")
        print("[TELEGRAM] Command handler started - send /help to your bot", flush=True)
    
    def stop(self):
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Telegram command handler stopped")
    
    async def _polling_loop(self):
        """Main polling loop - fetches updates and dispatches commands."""
        # Flush old updates on startup so we don't replay stale commands
        try:
            old_updates = await self._notifier.get_updates(offset=0, timeout=0)
            if old_updates:
                self._update_offset = old_updates[-1]["update_id"] + 1
                logger.info(f"Skipped {len(old_updates)} old Telegram updates")
        except Exception:
            pass
        
        while self._running:
            try:
                updates = await self._notifier.get_updates(
                    offset=self._update_offset, 
                    timeout=2
                )
                
                if updates:
                    print(f"[TELEGRAM] Received {len(updates)} update(s)", flush=True)
                
                for update in updates:
                    self._update_offset = update["update_id"] + 1
                    await self._process_update(update)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Telegram polling error: {e}")
                print(f"[TELEGRAM] Polling error: {e}", flush=True)
                await asyncio.sleep(5)  # Back off on error
            
            await asyncio.sleep(0.5)
    
    async def _process_update(self, update: dict):
        """Process a single Telegram update."""
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        
        print(f"[TELEGRAM] Processing: chat_id={chat_id} text={text!r} (expected={self._notifier.chat_id})", flush=True)
        
        # Auth: only respond to configured chat_id
        if chat_id != str(self._notifier.chat_id):
            print(f"[TELEGRAM] Ignoring: chat_id mismatch", flush=True)
            return
        
        if not text.startswith("/"):
            return
        
        # Parse command and args
        parts = text.split()
        command = parts[0].lower().split("@")[0]  # Handle /command@botname
        args = parts[1:]
        
        # Dispatch
        handler_entry = self._commands.get(command)
        if handler_entry:
            handler, _ = handler_entry
            print(f"[TELEGRAM] Dispatching {command}...", flush=True)
            try:
                await handler(args)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[TELEGRAM] Command {command} EXCEPTION: {e}", flush=True)
                logger.error(f"Command {command} error: {e}")
                await self._reply(f"<b>Error:</b> {str(e)[:200]}")
        else:
            await self._reply(
                f"Unknown command: <code>{command}</code>\n"
                f"Send /help for available commands."
            )
    
    async def _reply(self, text: str):
        """Send a reply message."""
        try:
            result = await self._notifier.send_message(text)
            if not result:
                print(f"[TELEGRAM] Reply FAILED (send_message returned False), length={len(text)}", flush=True)
            else:
                print(f"[TELEGRAM] Reply sent ({len(text)} chars)", flush=True)
        except Exception as e:
            print(f"[TELEGRAM] Reply EXCEPTION: {e}", flush=True)
    
    # =========================================================================
    # STATUS & MONITORING COMMANDS
    # =========================================================================
    
    async def _cmd_help(self, args):
        """List all available commands."""
        lines = ["<b>Trading Bot Commands</b>\n"]
        
        categories = {
            "Status &amp; Monitoring": ['/help', '/status', '/account', '/positions', '/orders', '/activity'],
            "Performance": ['/pnl', '/stats', '/goal', '/session', '/daily', '/weekly'],
            "Trade Actions": ['/close', '/closeall', '/stop', '/start', '/modify'],
            "Market Intel": ['/news', '/calendar', '/analysis', '/scan', '/hot'],
            "Configuration": ['/symbols', '/mode', '/config'],
        }
        
        for category, cmds in categories.items():
            lines.append(f"\n<b>{category}</b>")
            for cmd in cmds:
                entry = self._commands.get(cmd)
                if entry:
                    _, desc = entry
                    lines.append(f"  {cmd} - {desc}")
        
        await self._reply("\n".join(lines))
    
    async def _cmd_status(self, args):
        """Bot status overview."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized yet.")
            return
        
        status = bot.get_status_summary()
        running = "RUNNING" if status['running'] else "STOPPED"
        
        msg = f"""<b>Bot Status: {running}</b>

<b>Session:</b> {status['session']}
<b>Symbols:</b> {', '.join(status['symbols'])}
<b>Open Positions:</b> {status['open_positions']}
<b>Pending Orders:</b> {status['pending_orders']}
<b>Win Streak:</b> {status['win_streak']} | <b>Loss Streak:</b> {status['loss_streak']}
<b>Daily P/L:</b> ${status['daily_pnl']:+.2f}
<b>Uptime:</b> {status['uptime']}"""
        
        await self._reply(msg)
    
    async def _cmd_account(self, args):
        """Account balance and equity."""
        bot = self._bot
        if not bot or not bot.mt5_client:
            await self._reply("MT5 not connected.")
            return
        
        account = await bot.mt5_client.get_account_info()
        if not account:
            await self._reply("Could not fetch account info.")
            return
        
        profit_emoji = "+" if account.profit >= 0 else ""
        msg = f"""<b>Account Info</b>

<b>Balance:</b> ${account.balance:,.2f}
<b>Equity:</b> ${account.equity:,.2f}
<b>Margin:</b> ${account.margin:,.2f}
<b>Free Margin:</b> ${account.margin_free:,.2f}
<b>Unrealized P/L:</b> ${profit_emoji}{account.profit:,.2f}
<b>Margin Level:</b> {account.margin_level:.1f}%"""
        
        await self._reply(msg)
    
    async def _cmd_positions(self, args):
        """Open positions with P/L."""
        bot = self._bot
        if not bot or not bot.position_manager:
            await self._reply("Position manager not available.")
            return
        
        positions = bot.position_manager.positions
        if not positions:
            await self._reply("No open positions.")
            return
        
        lines = [f"<b>Open Positions ({len(positions)})</b>\n"]
        for ticket, pos in positions.items():
            direction = "LONG" if pos.direction == "long" else "SHORT"
            pl = pos.unrealized_pl if hasattr(pos, 'unrealized_pl') else 0
            pl_str = f"${pl:+.2f}" if pl else "N/A"
            lines.append(
                f"#{ticket} <b>{pos.symbol}</b> {direction}\n"
                f"  Entry: {pos.entry_price} | Size: {pos.volume}\n"
                f"  SL: {pos.stop_loss or 'None'} | TP: {pos.take_profit or 'None'}\n"
                f"  P/L: {pl_str}"
            )
        
        await self._reply("\n".join(lines))
    
    async def _cmd_orders(self, args):
        """Pending orders."""
        bot = self._bot
        if not bot or not bot.pending_order_manager:
            await self._reply("Order manager not available.")
            return
        
        orders = bot.pending_order_manager.get_active_orders()
        if not orders:
            await self._reply("No pending orders.")
            return
        
        lines = [f"<b>Pending Orders ({len(orders)})</b>\n"]
        for order in orders:
            exp_str = order.expiration.strftime('%H:%M') if order.expiration else 'GTC'
            mins_left = f" ({order.minutes_remaining:.0f}min left)" if order.minutes_remaining > 0 else ""
            lines.append(
                f"#{order.ticket} <b>{order.symbol}</b> {order.order_type}\n"
                f"  Price: {order.price} | SL: {order.stop_loss or 'N/A'} | TP: {order.take_profit or 'N/A'}\n"
                f"  Expires: {exp_str}{mins_left}"
            )
        
        await self._reply("\n".join(lines))
    
    async def _cmd_activity(self, args):
        """Recent activity feed."""
        try:
            from ..api.routes.activity import get_activities
            activities = get_activities(limit=10)
        except Exception:
            await self._reply("Activity feed not available.")
            return
        
        if not activities:
            await self._reply("No recent activity.")
            return
        
        lines = ["<b>Recent Activity</b>\n"]
        for a in activities[:10]:
            ts = a.get('timestamp', '')[:16]
            atype = a.get('type', 'info')
            symbol = a.get('symbol', '')
            msg = a.get('message', '')[:80]
            
            emoji = {'trade_opened': 'NEW', 'trade_closed': 'CLOSE', 
                     'signal_generated': 'SIG', 'error': 'ERR', 
                     'info': 'INFO', 'warning': 'WARN'}.get(atype, atype[:4].upper())
            
            sym_str = f" [{symbol}]" if symbol else ""
            lines.append(f"<code>[{emoji}]{sym_str}</code> {msg}")
        
        await self._reply("\n".join(lines))
    
    # =========================================================================
    # PERFORMANCE & ANALYTICS COMMANDS
    # =========================================================================
    
    async def _cmd_pnl(self, args):
        """Today's P/L and stats."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized.")
            return
        
        daily_pnl = bot.daily_pnl
        pnl_emoji = "+" if daily_pnl >= 0 else ""
        
        # Get session analytics totals
        total_trades = 0
        total_pnl = 0.0
        total_wins = 0
        if bot.session_analytics:
            for session, stats in bot.session_analytics.session_stats.items():
                total_trades += stats.total_trades
                total_pnl += stats.total_pnl
                total_wins += stats.winning_trades
        
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        
        msg = f"""<b>P/L Summary</b>

<b>Today's P/L:</b> ${pnl_emoji}{daily_pnl:.2f}
<b>Overall P/L:</b> ${total_pnl:+.2f}
<b>Total Trades:</b> {total_trades}
<b>Win Rate:</b> {win_rate:.1f}%
<b>Win Streak:</b> {bot.win_streak} | <b>Loss Streak:</b> {bot.loss_streak}"""
        
        await self._reply(msg)
    
    async def _cmd_stats(self, args):
        """Overall performance stats."""
        bot = self._bot
        if not bot or not bot.session_analytics:
            await self._reply("Analytics not available.")
            return
        
        summary = bot.session_analytics.get_summary()
        total_trades = summary.get('total_trades', 0)
        total_pnl = summary.get('total_pnl', 0)
        
        # Calculate aggregate stats
        total_wins = 0
        total_r = 0.0
        for session_data in summary.get('sessions', {}).values():
            total_wins += session_data.get('winning_trades', 0)
            total_r += session_data.get('total_r', 0)
        
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        avg_r = total_r / total_trades if total_trades > 0 else 0
        
        msg = f"""<b>Performance Stats</b>

<b>Total Trades:</b> {total_trades}
<b>Total P/L:</b> ${total_pnl:+.2f}
<b>Win Rate:</b> {win_rate:.1f}%
<b>Average R:</b> {avg_r:+.2f}R
<b>Total R:</b> {total_r:+.2f}R"""
        
        await self._reply(msg)
    
    async def _cmd_goal(self, args):
        """Goal progress and projection."""
        bot = self._bot
        if not bot or not bot.goal_tracker or not bot.mt5_client:
            await self._reply("Goal tracker not available.")
            return
        
        account = await bot.mt5_client.get_account_info()
        if not account:
            await self._reply("Could not fetch account.")
            return
        
        progress = bot.goal_tracker.calculate_progress(account.equity)
        
        msg = f"""<b>Goal Progress</b>

<b>Current Equity:</b> ${account.equity:,.2f}
<b>Target:</b> ${progress.get('target_equity', 10000):,.2f}
<b>Progress:</b> {progress.get('progress_pct', 0):.1f}%
<b>Remaining:</b> ${progress.get('remaining', 0):,.2f}
<b>Start Equity:</b> ${progress.get('start_equity', 0):,.2f}"""
        
        await self._reply(msg)
    
    async def _cmd_session(self, args):
        """Current session and analytics."""
        bot = self._bot
        if not bot or not bot.session_analytics:
            await self._reply("Session analytics not available.")
            return
        
        current = bot.session_analytics.get_current_session()
        current_stats = bot.session_analytics.get_session_stats(current)
        
        best = bot.session_analytics.get_best_session()
        worst = bot.session_analytics.get_worst_session()
        
        session_info = ""
        if bot.kill_zone_checker:
            try:
                kz = bot.kill_zone_checker.get_current_session()
                session_info = f"\n<b>Kill Zone:</b> {kz.session_name}" if kz else ""
            except Exception:
                pass
        
        msg = f"""<b>Session Analytics</b>
{session_info}
<b>Current:</b> {current.value}
<b>Trades:</b> {current_stats.total_trades} | <b>Win Rate:</b> {current_stats.win_rate:.0f}%
<b>P/L:</b> ${current_stats.total_pnl:+.2f} | <b>Avg R:</b> {current_stats.avg_r:+.2f}

<b>Best Session:</b> {best.value if best else 'N/A'}
<b>Worst Session:</b> {worst.value if worst else 'N/A'}"""
        
        await self._reply(msg)
    
    async def _cmd_daily(self, args):
        """Send daily summary."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized.")
            return
        
        try:
            await bot._send_daily_summary()
            await self._reply("Daily summary sent.")
        except Exception as e:
            await self._reply(f"Could not send daily summary: {e}")
    
    async def _cmd_weekly(self, args):
        """Trigger weekly review."""
        bot = self._bot
        if not bot or not bot.learning_service:
            await self._reply("Learning service not available.")
            return
        
        if not bot.claude_client:
            await self._reply("Claude client not available for weekly review.")
            return
        
        await self._reply("Generating weekly review... (this may take a minute)")
        try:
            result = await bot.learning_service.consolidate_weekly(bot.claude_client)
            if result:
                await self._reply("Weekly review complete. Check your Telegram for the report.")
            else:
                await self._reply("Weekly review completed (no data to consolidate).")
        except Exception as e:
            await self._reply(f"Weekly review failed: {e}")
    
    # =========================================================================
    # TRADE ACTION COMMANDS
    # =========================================================================
    
    async def _cmd_close(self, args):
        """Close a specific position."""
        if not args:
            await self._reply("Usage: /close &lt;ticket&gt;\nExample: /close 5183307")
            return
        
        bot = self._bot
        if not bot or not bot.order_manager:
            await self._reply("Order manager not available.")
            return
        
        try:
            ticket = int(args[0])
        except ValueError:
            await self._reply("Invalid ticket number.")
            return
        
        # Verify position exists
        if bot.position_manager and ticket not in bot.position_manager.positions:
            await self._reply(f"Position #{ticket} not found in tracked positions.")
            return
        
        await self._reply(f"Closing position #{ticket}...")
        try:
            result = await bot.order_manager.close_position(ticket=ticket)
            if result.success:
                await self._reply(f"Position #{ticket} closed successfully.")
                if bot.position_manager:
                    bot.position_manager.remove_position(ticket)
            else:
                await self._reply(f"Close failed: {result.error}")
        except Exception as e:
            await self._reply(f"Close error: {e}")
    
    async def _cmd_closeall(self, args):
        """Emergency close all positions."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized.")
            return
        
        await self._reply("EMERGENCY CLOSE ALL - closing all positions...")
        try:
            await bot.emergency_close_all("Telegram /closeall command")
            await self._reply("All positions closed.")
        except Exception as e:
            await self._reply(f"Emergency close error: {e}")
    
    async def _cmd_stop(self, args):
        """Pause the bot."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized.")
            return
        
        bot.running = False
        await self._reply("Bot PAUSED. Trading cycles stopped.\nSend /start to resume.")
    
    async def _cmd_start(self, args):
        """Resume the bot."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized.")
            return
        
        if bot.running:
            await self._reply("Bot is already running.")
            return
        
        bot.running = True
        
        # Re-launch the trading loop from api/main.py
        try:
            from ..api.main import start_bot_task
            await start_bot_task()
            await self._reply("Bot RESUMED. Trading cycles restarted.")
        except Exception:
            await self._reply("Bot running flag set. It will resume on next cycle.")
    
    async def _cmd_modify(self, args):
        """Modify SL/TP on a position."""
        if len(args) < 3:
            await self._reply("Usage: /modify &lt;ticket&gt; &lt;sl&gt; &lt;tp&gt;\nExample: /modify 5183307 1.4500 1.5000")
            return
        
        bot = self._bot
        if not bot or not bot.mt5_client:
            await self._reply("MT5 not connected.")
            return
        
        try:
            ticket = int(args[0])
            sl = float(args[1])
            tp = float(args[2])
        except ValueError:
            await self._reply("Invalid arguments. Use numbers for ticket, SL, and TP.")
            return
        
        await self._reply(f"Modifying #{ticket}: SL={sl}, TP={tp}...")
        try:
            result = await bot.mt5_client.modify_position(ticket, stop_loss=sl, take_profit=tp)
            if result and result.get('success'):
                await self._reply(f"Position #{ticket} modified: SL={sl}, TP={tp}")
            else:
                error = result.get('error', 'Unknown error') if result else 'No response'
                await self._reply(f"Modify failed: {error}")
        except Exception as e:
            await self._reply(f"Modify error: {e}")
    
    # =========================================================================
    # MARKET INTELLIGENCE COMMANDS
    # =========================================================================
    
    async def _cmd_news(self, args):
        """News and blackout status."""
        bot = self._bot
        if not bot or not bot.news_service:
            await self._reply("News service not available.")
            return
        
        is_blackout, reason = bot.news_service.is_blackout_period()
        blackout_str = f"IN BLACKOUT: {reason}" if is_blackout else "No blackout"
        
        lines = [f"<b>News Status</b>\n<b>Blackout:</b> {blackout_str}\n"]
        
        try:
            upcoming = await bot.news_service.get_upcoming_events(hours=4)
            if upcoming:
                lines.append("<b>Upcoming (next 4h):</b>")
                for event in upcoming[:5]:
                    time_str = event.get('time', '')[:16] if event.get('time') else ''
                    impact = event.get('impact', '').upper()[:4]
                    currency = event.get('currency', '')
                    title = event.get('title', '')[:40]
                    lines.append(f"  [{impact}] {currency} {title} @ {time_str}")
            else:
                lines.append("No upcoming high-impact events.")
        except Exception:
            lines.append("Could not fetch upcoming events.")
        
        await self._reply("\n".join(lines))
    
    async def _cmd_calendar(self, args):
        """Economic calendar for today."""
        bot = self._bot
        if not bot or not bot.news_service:
            await self._reply("News service not available.")
            return
        
        try:
            upcoming = await bot.news_service.get_upcoming_events(hours=24)
            if not upcoming:
                await self._reply("No events in the next 24 hours.")
                return
            
            lines = ["<b>Economic Calendar (24h)</b>\n"]
            for event in upcoming[:15]:
                time_str = event.get('time', '')[:16] if event.get('time') else ''
                impact = event.get('impact', '').upper()[:4]
                currency = event.get('currency', '')
                title = event.get('title', '')[:40]
                lines.append(f"[{impact}] {currency} {title}\n  @ {time_str}")
            
            if len(upcoming) > 15:
                lines.append(f"\n... and {len(upcoming) - 15} more events")
            
            await self._reply("\n".join(lines))
        except Exception as e:
            await self._reply(f"Calendar error: {e}")
    
    async def _cmd_analysis(self, args):
        """Last signal for a symbol."""
        if not args:
            await self._reply("Usage: /analysis &lt;symbol&gt;\nExample: /analysis BTCUSD")
            return
        
        symbol = args[0].upper()
        
        # Search activity feed for last signal
        try:
            from ..api.routes.activity import get_activities
            activities = get_activities(limit=50)
            
            for a in activities:
                if a.get('type') == 'signal_generated' and symbol in a.get('message', '').upper():
                    msg = a.get('message', '')
                    ts = a.get('timestamp', '')[:19]
                    details = a.get('details', {})
                    
                    direction = details.get('direction', 'N/A')
                    confidence = details.get('confidence', 0)
                    
                    await self._reply(
                        f"<b>Last Signal: {symbol}</b>\n\n"
                        f"<b>Direction:</b> {direction}\n"
                        f"<b>Confidence:</b> {confidence}%\n"
                        f"<b>Time:</b> {ts}\n"
                        f"<b>Details:</b> {msg[:200]}"
                    )
                    return
            
            await self._reply(f"No recent signal found for {symbol}.")
        except Exception as e:
            await self._reply(f"Analysis error: {e}")

    async def _cmd_scan(self, args):
        """Run the mechanical opportunity scanner and report hot list."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized yet. Start the bot first.")
            return

        scanner = getattr(bot, "opportunity_scanner", None)
        if scanner is None:
            await self._reply(
                "Opportunity scanner not available. Start the bot, then try /scan again."
            )
            return

        if getattr(scanner, "scan_in_progress", False):
            await self._reply("Scan already running — try /hot in a minute.")
            return

        await self._reply(
            "Starting mechanical opportunity scan (no Claude)…\n"
            "This can take 30–90s. I'll reply when done."
        )

        try:
            from ..config import settings

            results = await scanner.scan_once(base_symbols=list(settings.trading.symbols))
        except Exception as e:
            await self._reply(f"<b>Scan failed:</b> {str(e)[:200]}")
            return

        promotable = [r for r in results if getattr(r, "promotable", False)]
        hot = scanner.hot.to_list() if getattr(scanner, "hot", None) else []
        top = sorted(results, key=lambda r: getattr(r, "score", 0), reverse=True)[:8]

        lines = [
            "<b>Opportunity Scan Complete</b>",
            f"Universe scored: {len(results)}",
            f"Promotable: {len(promotable)}",
            "",
            "<b>Hot list</b>",
        ]
        if hot:
            for h in hot:
                lines.append(
                    f"  {h.get('symbol')} {h.get('direction', '')} "
                    f"score={float(h.get('score', 0)):.2f} "
                    f"ttl={h.get('ttl_minutes_remaining', '?')}m"
                )
        else:
            lines.append("  (empty — nothing new beyond base symbols)")

        lines.append("")
        lines.append("<b>Top scores</b>")
        if top:
            for r in top:
                flag = "✓" if r.promotable else "·"
                direction = r.direction or "-"
                lines.append(
                    f"  {flag} {r.symbol} {direction} "
                    f"{r.score:.2f} ({r.reason})"
                )
        else:
            lines.append("  (no results)")

        await self._reply("\n".join(lines))

    async def _cmd_hot(self, args):
        """Show current opportunity hot list + TTLs."""
        bot = self._bot
        if not bot:
            await self._reply("Bot not initialized yet.")
            return
        scanner = getattr(bot, "opportunity_scanner", None)
        if scanner is None:
            await self._reply("Opportunity scanner not available.")
            return

        hot = scanner.hot.to_list()
        if not hot:
            await self._reply("<b>Hot list empty</b>\nSend /scan to refresh.")
            return

        lines = [f"<b>Hot List ({len(hot)})</b>\n"]
        for h in hot:
            lines.append(
                f"  {h.get('symbol')} {h.get('direction', '')} "
                f"score={float(h.get('score', 0)):.2f} "
                f"ttl={h.get('ttl_minutes_remaining', '?')}m\n"
                f"    {h.get('reason', '')}"
            )
        await self._reply("\n".join(lines))
    
    # =========================================================================
    # CONFIGURATION COMMANDS
    # =========================================================================
    
    async def _cmd_symbols(self, args):
        """Show trading symbols."""
        try:
            from ..config import settings
            symbols = settings.trading.symbols
            
            lines = [f"<b>Trading Symbols ({len(symbols)})</b>\n"]
            for sym in symbols:
                from ..utils.market_hours import get_market_type, is_market_open
                mtype = get_market_type(sym)
                is_open, reason = is_market_open(sym)
                status = "OPEN" if is_open else "CLOSED"
                lines.append(f"  {sym} ({mtype.value}) - {status}")
            
            await self._reply("\n".join(lines))
        except Exception as e:
            await self._reply(f"Symbols error: {e}")
    
    async def _cmd_mode(self, args):
        """Scaling mode and tier."""
        bot = self._bot
        if not bot or not bot.scaling_manager:
            await self._reply("Scaling manager not available.")
            return
        
        sm = bot.scaling_manager
        mode = getattr(sm, 'current_mode', 'unknown')
        tier = getattr(sm, 'current_tier', 'unknown')
        risk = getattr(sm, 'current_risk_pct', 0)
        
        msg = f"""<b>Scaling Status</b>

<b>Mode:</b> {mode}
<b>Tier:</b> {tier}
<b>Risk Per Trade:</b> {risk:.1%}"""
        
        await self._reply(msg)
    
    async def _cmd_config(self, args):
        """Show key trading parameters."""
        try:
            from ..config import settings
            
            msg = f"""<b>Trading Configuration</b>

<b>Risk Per Trade:</b> {settings.trading.risk_per_trade:.1%}
<b>Max Daily Trades:</b> {settings.trading.max_daily_trades}
<b>Min R:R:</b> {settings.trading.min_risk_reward}
<b>Max Position Size:</b> {settings.trading.max_position_size}
<b>Max Exposure:</b> {settings.trading.max_total_exposure}
<b>Max Daily DD:</b> {settings.trading.max_daily_drawdown:.1%}
<b>Max Weekly DD:</b> {settings.trading.max_weekly_drawdown:.1%}
<b>Sessions:</b> {', '.join(settings.trading.allowed_sessions)}"""
            
            await self._reply(msg)
        except Exception as e:
            await self._reply(f"Config error: {e}")
