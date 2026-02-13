"""
Notification utilities for the ICT Trading Bot.

Supports:
- Telegram notifications for trades, errors, and daily summaries
- Extensible for other notification channels
"""

import aiohttp
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from .logging import get_logger

logger = get_logger(__name__)


class NotificationType(Enum):
    """Types of notifications."""
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    SIGNAL_GENERATED = "signal_generated"
    ERROR = "error"
    WARNING = "warning"
    DAILY_SUMMARY = "daily_summary"
    INFO = "info"


class TelegramNotifier:
    """
    Send notifications via Telegram Bot API.
    
    Setup:
    1. Create a bot via @BotFather
    2. Get the bot token
    3. Get your chat ID (send /start to the bot, then use @userinfobot)
    4. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
    """
    
    @staticmethod
    def _format_price(price: float, symbol: str = "") -> str:
        """Format price with appropriate decimal places for the symbol."""
        symbol = symbol.upper()
        if symbol in ('XAUUSD', 'GOLD'):
            return f"{price:.2f}"
        elif symbol in ('XAGUSD', 'SILVER'):
            return f"{price:.3f}"
        elif 'JPY' in symbol:
            return f"{price:.3f}"
        elif symbol.endswith('USD') and symbol[:3] in ('BTC', 'ETH', 'SOL', 'DOT', 'ADA', 'XRP', 'LTC',
                                                         'DOG', 'EOS', 'NEO', 'ETC', 'XMR', 'ZEC', 'DAS', 'IOT'):
            # Crypto: show 2 decimals for BTC/ETH, 4 for smaller coins
            if price > 1000:
                return f"{price:.2f}"
            elif price > 1:
                return f"{price:.4f}"
            else:
                return f"{price:.6f}"
        else:
            return f"{price:.5f}"
    
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """
        Initialize Telegram notifier.
        
        Args:
            bot_token: Telegram bot token (or from env TELEGRAM_BOT_TOKEN)
            chat_id: Target chat ID (or from env TELEGRAM_CHAT_ID)
        """
        import os
        
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            logger.warning("Telegram notifications disabled - missing bot token or chat ID")
        else:
            logger.info("Telegram notifications enabled")
    
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message via Telegram.
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: HTML or Markdown
            
        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"Telegram API error: {error}")
                        return False
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False
    
    async def notify_trade_opened(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        lots: float,
        confidence: float,
        ticket: Optional[int] = None
    ) -> bool:
        """Send trade opened notification."""
        emoji = "📈" if direction.lower() == "long" else "📉"
        fp = lambda p: self._format_price(p, symbol)
        
        # Calculate risk/reward
        sl_dist = abs(entry_price - stop_loss) if stop_loss else 0
        tp_dist = abs(take_profit - entry_price) if take_profit else 0
        rr = f"{tp_dist / sl_dist:.1f}" if sl_dist > 0 else "N/A"
        
        # Get swap cost info from symbol spec
        swap_line = ""
        try:
            from ..config import get_symbol_spec
            _spec = get_symbol_spec(symbol)
            swap_rate = _spec.swap_long if direction.lower() == "long" else _spec.swap_short
            if swap_rate != 0:
                swap_cost = swap_rate * lots
                swap_line = f"\n<b>Swap/night:</b> ${swap_cost:+.2f} ({swap_rate:+.2f}/lot)"
        except Exception:
            pass
        
        message = f"""
{emoji} <b>Trade Opened</b>

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction.upper()}
<b>Entry:</b> {fp(entry_price)}
<b>Stop Loss:</b> {fp(stop_loss)}
<b>Take Profit:</b> {fp(take_profit)}
<b>R:R:</b> 1:{rr}
<b>Size:</b> {lots} lots
<b>Confidence:</b> {confidence:.0%}
{f'<b>Ticket:</b> {ticket}' if ticket else ''}{swap_line}
"""
        return await self.send_message(message.strip())
    
    async def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        profit_loss: float,
        pips: float,
        ticket: Optional[int] = None
    ) -> bool:
        """Send trade closed notification."""
        emoji = "✅" if profit_loss >= 0 else "❌"
        result_word = "WIN" if profit_loss >= 0 else "LOSS"
        fp = lambda p: self._format_price(p, symbol)
        
        message = f"""
{emoji} <b>Trade Closed — {result_word}</b>

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction.upper()}
<b>Entry:</b> {fp(entry_price)}
<b>Exit:</b> {fp(exit_price)}
<b>P/L:</b> ${profit_loss:+.2f} ({pips:+.1f} pips)
{f'<b>Ticket:</b> {ticket}' if ticket else ''}
"""
        return await self.send_message(message.strip())
    
    async def notify_error(self, error_message: str, context: Optional[str] = None) -> bool:
        """Send error notification."""
        message = f"""
⚠️ <b>Trading Bot Error</b>

<b>Error:</b> {error_message}
{f'<b>Context:</b> {context}' if context else ''}
<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message.strip())
    
    async def notify_daily_summary(
        self,
        date: str,
        trades_opened: int,
        trades_closed: int,
        total_pnl: float,
        win_rate: float,
        balance: float,
        equity: float
    ) -> bool:
        """Send daily summary notification."""
        emoji = "📊"
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        message = f"""
{emoji} <b>Daily Summary - {date}</b>

<b>Trades Opened:</b> {trades_opened}
<b>Trades Closed:</b> {trades_closed}
<b>Win Rate:</b> {win_rate:.1%}
{pnl_emoji} <b>P/L:</b> ${total_pnl:+.2f}

<b>Balance:</b> ${balance:,.2f}
<b>Equity:</b> ${equity:,.2f}
"""
        return await self.send_message(message.strip())


# Global notifier instance
_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    """Get the global notifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


async def notify(
    notification_type: NotificationType,
    message: str,
    **kwargs
) -> bool:
    """
    Send a notification using the configured channel.
    
    Args:
        notification_type: Type of notification
        message: Message text
        **kwargs: Additional data for specific notification types
        
    Returns:
        True if notification was sent
    """
    notifier = get_notifier()
    
    if notification_type == NotificationType.TRADE_OPENED:
        return await notifier.notify_trade_opened(
            symbol=kwargs.get('symbol', 'Unknown'),
            direction=kwargs.get('direction', 'unknown'),
            entry_price=kwargs.get('entry_price', 0),
            stop_loss=kwargs.get('stop_loss', 0),
            take_profit=kwargs.get('take_profit', 0),
            lots=kwargs.get('lots', 0),
            confidence=kwargs.get('confidence', 0),
            ticket=kwargs.get('ticket')
        )
    
    elif notification_type == NotificationType.TRADE_CLOSED:
        return await notifier.notify_trade_closed(
            symbol=kwargs.get('symbol', 'Unknown'),
            direction=kwargs.get('direction', 'unknown'),
            entry_price=kwargs.get('entry_price', 0),
            exit_price=kwargs.get('exit_price', 0),
            profit_loss=kwargs.get('profit_loss', 0),
            pips=kwargs.get('pips', 0),
            ticket=kwargs.get('ticket')
        )
    
    elif notification_type == NotificationType.ERROR:
        return await notifier.notify_error(message, kwargs.get('context'))
    
    elif notification_type == NotificationType.DAILY_SUMMARY:
        return await notifier.notify_daily_summary(
            date=kwargs.get('date', datetime.now().strftime('%Y-%m-%d')),
            trades_opened=kwargs.get('trades_opened', 0),
            trades_closed=kwargs.get('trades_closed', 0),
            total_pnl=kwargs.get('total_pnl', 0),
            win_rate=kwargs.get('win_rate', 0),
            balance=kwargs.get('balance', 0),
            equity=kwargs.get('equity', 0)
        )
    
    else:
        # Generic message
        return await notifier.send_message(f"ℹ️ {message}")
