"""
Notification utilities for the ICT Trading Bot.

Supports:
- Telegram notifications for trades, errors, and daily summaries
- Extensible for other notification channels
"""

import aiohttp
import asyncio
import os
import ssl
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from .logging import get_logger

logger = get_logger(__name__)


def telegram_ssl_verify_enabled() -> bool:
    """
    Whether Telegram HTTPS should verify TLS certificates.

    Windows VPS hosts often run antivirus HTTPS scanning that injects a
    self-signed cert into the chain, which breaks Python's default verify.
    Set TELEGRAM_SSL_VERIFY=false in .env.local on those hosts.
    """
    raw = (os.environ.get("TELEGRAM_SSL_VERIFY") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def build_telegram_ssl_context(verify: Optional[bool] = None) -> ssl.SSLContext:
    """Build an SSL context for Telegram API calls."""
    if verify is None:
        verify = telegram_ssl_verify_enabled()

    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _is_ssl_cert_error(exc: BaseException) -> bool:
    """Return True for certificate verification / TLS MITM failures."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    name = type(exc).__name__
    if "Certificate" in name or "SSLCert" in name:
        return True
    msg = str(exc).lower()
    return (
        "certificate verify failed" in msg
        or "sslcertverificationerror" in msg
        or "self-signed certificate" in msg
    )


class NotificationType(Enum):
    """Types of notifications."""
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    PENDING_PLACED = "pending_placed"
    PENDING_FILLED = "pending_filled"
    PENDING_CANCELLED = "pending_cancelled"
    TRADING_HALTED = "trading_halted"
    CONNECTION = "connection"
    SIGNAL_GENERATED = "signal_generated"
    ERROR = "error"
    WARNING = "warning"
    ALERT = "alert"
    DAILY_SUMMARY = "daily_summary"
    INFO = "info"


_CANCEL_REASON_LABELS = {
    "expired": "expired (not filled)",
    "replaced_by_newer": "replaced by newer signal",
    "volatility_spike": "volatility spike",
    "external": "cancelled on broker",
    "manual": "cancelled",
}


def _risk_reward_label(entry_price: float, stop_loss: float, take_profit: float) -> str:
    sl_dist = abs(float(entry_price or 0) - float(stop_loss or 0)) if stop_loss else 0
    tp_dist = abs(float(take_profit or 0) - float(entry_price or 0)) if take_profit else 0
    if sl_dist <= 0:
        return "N/A"
    return f"{tp_dist / sl_dist:.1f}"


def format_pending_placed(
    symbol: str,
    direction: str,
    order_type: str,
    entry_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    lots: float,
    ticket: Optional[int] = None,
    expires_min: Optional[float] = None,
    confidence: Optional[float] = None,
) -> str:
    """HTML card for a newly placed pending order."""
    fp = lambda p: TelegramNotifier._format_price(float(p or 0), symbol)
    rr = _risk_reward_label(entry_price, stop_loss or 0, take_profit or 0)
    conf_line = ""
    if confidence:
        conf_line = f"\n<b>Confidence:</b> {float(confidence):.0%}"
    exp_line = ""
    if expires_min is not None:
        exp_line = f"\n<b>Expires:</b> ~{max(0, int(round(expires_min)))} min"
    ticket_line = f"\n<b>Ticket:</b> {ticket}" if ticket else ""
    return f"""⏳ <b>Pending Placed</b>

<b>Symbol:</b> {symbol}
<b>Type:</b> {str(order_type or '').replace('_', ' ').upper()}
<b>Direction:</b> {str(direction or '').upper()}
<b>Entry:</b> {fp(entry_price)}
<b>Stop Loss:</b> {fp(stop_loss) if stop_loss else '—'}
<b>Take Profit:</b> {fp(take_profit) if take_profit else '—'}
<b>R:R:</b> 1:{rr}
<b>Size:</b> {lots} lots{conf_line}{exp_line}{ticket_line}"""


def format_pending_filled(
    symbol: str,
    direction: str,
    fill_price: float,
    ticket: Optional[int] = None,
    position_ticket: Optional[int] = None,
    lots: Optional[float] = None,
) -> str:
    """HTML card when a pending order becomes a position."""
    fp = lambda p: TelegramNotifier._format_price(float(p or 0), symbol)
    extra = ""
    if lots:
        extra += f"\n<b>Size:</b> {lots} lots"
    if ticket:
        extra += f"\n<b>Order:</b> {ticket}"
    if position_ticket:
        extra += f"\n<b>Position:</b> {position_ticket}"
    return f"""✅ <b>Pending Filled</b>

<b>Symbol:</b> {symbol}
<b>Direction:</b> {str(direction or '').upper()}
<b>Fill:</b> {fp(fill_price)}{extra}"""


def format_pending_cancelled(
    symbol: str,
    direction: str,
    order_type: str,
    entry_price: float,
    ticket: Optional[int] = None,
    reason: str = "",
) -> str:
    """HTML card when a pending order is cancelled or expired."""
    fp = lambda p: TelegramNotifier._format_price(float(p or 0), symbol)
    label = _CANCEL_REASON_LABELS.get(
        str(reason or "").strip().lower(),
        str(reason or "cancelled").replace("_", " "),
    )
    ticket_line = f"\n<b>Ticket:</b> {ticket}" if ticket else ""
    return f"""🚫 <b>Pending Cancelled</b>

<b>Symbol:</b> {symbol}
<b>Type:</b> {str(order_type or '').replace('_', ' ').upper()}
<b>Direction:</b> {str(direction or '').upper()}
<b>Entry:</b> {fp(entry_price)}
<b>Reason:</b> {label}{ticket_line}"""


def format_trading_halted(reason: str, detail: str = "") -> str:
    extra = f"\n{detail}" if detail else ""
    return (
        f"🛑 <b>Trading Halted</b>\n\n"
        f"<b>Reason:</b> {reason}{extra}\n"
        f"New entries paused. Position management continues."
    )


def format_connection_alert(reconnected: bool = False, detail: str = "") -> str:
    if reconnected:
        extra = f"\n{detail}" if detail else ""
        return f"🔌 <b>MT5 Reconnected</b>{extra}"
    extra = f"\n{detail}" if detail else "\nReconnection failed. Trading paused until MT5 is back."
    return f"🔌 <b>MT5 Disconnected</b>{extra}"


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
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.bot_token and self.chat_id)
        self._ssl_verify = telegram_ssl_verify_enabled()
        self._ssl_fallback_warned = False
        
        if not self.enabled:
            logger.warning("Telegram notifications disabled - missing bot token or chat ID")
        else:
            verify_note = "SSL verify ON" if self._ssl_verify else "SSL verify OFF (TELEGRAM_SSL_VERIFY=false)"
            logger.info(f"Telegram notifications enabled ({verify_note})")
            if not self._ssl_verify:
                logger.warning(
                    "Telegram TLS verification disabled — only use on VPS hosts where "
                    "antivirus/proxy injects a self-signed HTTPS certificate"
                )

    def _client_session(self, verify: Optional[bool] = None) -> aiohttp.ClientSession:
        """Create an aiohttp session with the Telegram SSL policy."""
        if verify is None:
            verify = self._ssl_verify
        connector = aiohttp.TCPConnector(ssl=build_telegram_ssl_context(verify=verify))
        return aiohttp.ClientSession(connector=connector)

    def _note_ssl_fallback(self, context: str) -> None:
        if self._ssl_fallback_warned:
            return
        msg = (
            f"Telegram SSL verification failed during {context} (common on Windows VPS "
            "with antivirus HTTPS scanning). Retrying with verify disabled. "
            "Add TELEGRAM_SSL_VERIFY=false to .env.local to make this permanent."
        )
        print(f"[TELEGRAM] {msg}", flush=True)
        logger.warning(msg)
        self._ssl_fallback_warned = True
        self._ssl_verify = False

    async def _telegram_post(
        self,
        url: str,
        payload: dict,
        *,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> tuple[int, Any]:
        """
        POST JSON to Telegram and return (status, json_or_text).

        Retries once with SSL verify disabled when the VPS TLS chain is broken
        by antivirus HTTPS inspection.
        """
        verify_attempts = [self._ssl_verify]
        if self._ssl_verify:
            verify_attempts.append(False)

        last_error: Optional[BaseException] = None
        for idx, verify in enumerate(verify_attempts):
            try:
                async with self._client_session(verify=verify) as session:
                    async with session.post(url, json=payload, timeout=timeout) as response:
                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            data = await response.text()
                        if verify is False and idx > 0:
                            self._note_ssl_fallback(url.split("/")[-1])
                        return response.status, data
            except asyncio.TimeoutError:
                raise
            except Exception as e:
                last_error = e
                if verify and _is_ssl_cert_error(e) and False in verify_attempts:
                    self._note_ssl_fallback(url.split("/")[-1])
                    continue
                raise
        assert last_error is not None
        raise last_error
    
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
            status, data = await self._telegram_post(url, payload)
            if status == 200:
                return True
            error = data if isinstance(data, str) else str(data)
            print(f"[TELEGRAM] send_message FAILED ({status}): {error[:300]}", flush=True)
            logger.error(f"Telegram API error: {error}")
            return False
        except Exception as e:
            print(f"[TELEGRAM] send_message EXCEPTION: {e}", flush=True)
            logger.error(f"Error sending Telegram message: {e}")
            if _is_ssl_cert_error(e):
                logger.error(
                    "Hint: set TELEGRAM_SSL_VERIFY=false in .env.local on the VPS, "
                    "then restart the API"
                )
            return False
    
    async def get_updates(self, offset: int = 0, timeout: int = 1) -> list:
        """
        Get incoming updates (messages) from Telegram using long polling.
        
        Args:
            offset: Update ID offset (to acknowledge previous updates)
            timeout: Long poll timeout in seconds
            
        Returns:
            List of update dicts from Telegram API
        """
        if not self.enabled:
            return []
        
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset:
            payload["offset"] = offset
        
        try:
            status, data = await self._telegram_post(
                url,
                payload,
                timeout=aiohttp.ClientTimeout(total=timeout + 10),
            )
            if status == 200 and isinstance(data, dict):
                results = data.get("result", [])
                if results:
                    logger.debug(f"getUpdates: received {len(results)} update(s)")
                return results
            if status != 200:
                logger.warning(f"getUpdates HTTP {status}: {str(data)[:200]}")
            return []
        except asyncio.TimeoutError:
            return []  # Normal for long polling
        except Exception as e:
            logger.debug(f"getUpdates error: {e}")
            return []
   
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
        
        rr = _risk_reward_label(entry_price, stop_loss, take_profit)
        
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
        ticket: Optional[int] = None,
        unconfirmed: bool = False
    ) -> bool:
        """Send trade closed notification."""
        emoji = "✅" if profit_loss >= 0 else "❌"
        result_word = "WIN" if profit_loss >= 0 else "LOSS"
        if unconfirmed:
            emoji = "⚠️"
            result_word = f"UNCONFIRMED {result_word}"
        fp = lambda p: self._format_price(p, symbol)
        
        confirm_line = ""
        if unconfirmed:
            confirm_line = "\n<b>⚠️ P/L is estimated (no MT5 confirmation) — verify manually</b>"
        
        message = f"""
{emoji} <b>Trade Closed — {result_word}</b>

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction.upper()}
<b>Entry:</b> {fp(entry_price)}
<b>Exit:</b> {fp(exit_price)}
<b>P/L:</b> ${profit_loss:+.2f} ({pips:+.1f} pips)
{f'<b>Ticket:</b> {ticket}' if ticket else ''}{confirm_line}
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
            ticket=kwargs.get('ticket'),
            unconfirmed=kwargs.get('unconfirmed', False)
        )
    
    elif notification_type == NotificationType.PENDING_PLACED:
        return await notifier.send_message(
            format_pending_placed(
                symbol=kwargs.get("symbol", "Unknown"),
                direction=kwargs.get("direction", ""),
                order_type=kwargs.get("order_type", "pending"),
                entry_price=kwargs.get("entry_price", 0),
                stop_loss=kwargs.get("stop_loss"),
                take_profit=kwargs.get("take_profit"),
                lots=kwargs.get("lots", 0),
                ticket=kwargs.get("ticket"),
                expires_min=kwargs.get("expires_min"),
                confidence=kwargs.get("confidence"),
            )
        )

    elif notification_type == NotificationType.PENDING_FILLED:
        return await notifier.send_message(
            format_pending_filled(
                symbol=kwargs.get("symbol", "Unknown"),
                direction=kwargs.get("direction", ""),
                fill_price=kwargs.get("fill_price", kwargs.get("entry_price", 0)),
                ticket=kwargs.get("ticket"),
                position_ticket=kwargs.get("position_ticket"),
                lots=kwargs.get("lots"),
            )
        )

    elif notification_type == NotificationType.PENDING_CANCELLED:
        return await notifier.send_message(
            format_pending_cancelled(
                symbol=kwargs.get("symbol", "Unknown"),
                direction=kwargs.get("direction", ""),
                order_type=kwargs.get("order_type", "pending"),
                entry_price=kwargs.get("entry_price", 0),
                ticket=kwargs.get("ticket"),
                reason=kwargs.get("reason", ""),
            )
        )

    elif notification_type == NotificationType.TRADING_HALTED:
        return await notifier.send_message(
            format_trading_halted(
                reason=kwargs.get("reason") or message,
                detail=kwargs.get("detail", ""),
            )
        )

    elif notification_type == NotificationType.CONNECTION:
        return await notifier.send_message(
            format_connection_alert(
                reconnected=bool(kwargs.get("reconnected")),
                detail=kwargs.get("detail", ""),
            )
        )

    elif notification_type == NotificationType.ALERT:
        return await notifier.send_message(message)

    elif notification_type == NotificationType.WARNING:
        return await notifier.send_message(f"⚠️ {message}")

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


async def safe_notify(notification_type: NotificationType, message: str = "", **kwargs) -> bool:
    """Best-effort notify — never raises into trading paths."""
    try:
        return await notify(notification_type, message, **kwargs)
    except Exception as exc:
        logger.debug(f"Notification skipped ({notification_type}): {exc}")
        return False
