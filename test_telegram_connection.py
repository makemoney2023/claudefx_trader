"""
Telegram Connection Test Script

Run this script on Windows (or any host with outbound HTTPS) to verify
Telegram notifications before starting the full trading bot.

Usage:
    python test_telegram_connection.py
"""

import asyncio
import os
import sys

import aiohttp

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def normalize_env_value(value: str | None) -> str:
    """Strip whitespace and surrounding quotes from env values."""
    if not value:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        return cleaned[1:-1].strip()
    return cleaned


def validate_telegram_config(bot_token: str, chat_id: str) -> tuple[bool, list[str]]:
    """Validate Telegram credentials before calling the API."""
    errors: list[str] = []

    if not bot_token:
        errors.append("TELEGRAM_BOT_TOKEN is missing in .env.local")
    if not chat_id:
        errors.append("TELEGRAM_CHAT_ID is missing in .env.local")
    elif not chat_id.lstrip("-").isdigit():
        errors.append("TELEGRAM_CHAT_ID must be numeric (get it from @userinfobot)")

    return len(errors) == 0, errors


def parse_get_me_response(data: dict) -> tuple[bool, str, str]:
    """Parse Telegram getMe API response."""
    if data.get("ok"):
        username = data.get("result", {}).get("username", "")
        return True, username, ""
    return False, "", data.get("description", "Unknown error")


def _telegram_connector() -> aiohttp.TCPConnector:
    """Match live-bot SSL policy (supports TELEGRAM_SSL_VERIFY=false)."""
    from trading_bot.utils.notifications import build_telegram_ssl_context

    return aiohttp.TCPConnector(ssl=build_telegram_ssl_context())


def _format_network_error(exc: BaseException) -> str:
    msg = str(exc)
    lowered = msg.lower()
    if (
        "certificate verify failed" in lowered
        or "self-signed certificate" in lowered
        or "sslcertverificationerror" in lowered
    ):
        return (
            f"SSL error: {exc}\n"
            "  Fix: add TELEGRAM_SSL_VERIFY=false to .env.local (common on Windows VPS "
            "with antivirus HTTPS scanning), then re-run this test."
        )
    return f"Network error: {exc}"


async def fetch_bot_info(bot_token: str) -> tuple[bool, str, str]:
    """Validate bot token via Telegram getMe endpoint."""
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        async with aiohttp.ClientSession(connector=_telegram_connector()) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                data = await response.json()
                if response.status != 200:
                    return False, "", f"HTTP {response.status}: {data}"
                return parse_get_me_response(data)
    except aiohttp.ClientError as exc:
        return False, "", _format_network_error(exc)
    except asyncio.TimeoutError:
        return False, "", "Request timed out — check VPS outbound HTTPS to api.telegram.org"


async def send_test_message(bot_token: str, chat_id: str) -> tuple[bool, str]:
    """Send a test notification to the configured chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": (
            "✅ <b>ICT Trading Bot — Telegram Test</b>\n\n"
            "If you received this message, notifications are configured correctly.\n"
            "Reply with /help once the backend API is running."
        ),
        "parse_mode": "HTML",
    }

    try:
        async with aiohttp.ClientSession(connector=_telegram_connector()) as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                data = await response.json()
                if response.status == 200 and data.get("ok"):
                    return True, ""
                description = data.get("description", await response.text())
                return False, description[:300]
    except aiohttp.ClientError as exc:
        return False, _format_network_error(exc)
    except asyncio.TimeoutError:
        return False, "Request timed out"


async def test_telegram_connection(
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Run full Telegram connectivity test."""
    print("=" * 50)
    print("  Telegram Connection Test")
    print("=" * 50)
    print()

    try:
        from dotenv import load_dotenv

        load_dotenv(".env.local")
        load_dotenv(".env")
        print("[OK] Environment variables loaded")
    except ImportError:
        print("[WARN] python-dotenv not installed, using environment variables directly")

    token = normalize_env_value(bot_token or os.getenv("TELEGRAM_BOT_TOKEN"))
    chat = normalize_env_value(chat_id or os.getenv("TELEGRAM_CHAT_ID"))

    print()
    from trading_bot.utils.notifications import telegram_ssl_verify_enabled

    print("Configuration:")
    print(f"  Bot token: {'SET (' + token[:8] + '...)' if token else 'NOT SET'}")
    print(f"  Chat ID:   {chat or 'NOT SET'}")
    print(f"  SSL verify: {'ON' if telegram_ssl_verify_enabled() else 'OFF (TELEGRAM_SSL_VERIFY=false)'}")
    print()

    ok, errors = validate_telegram_config(token, chat)
    if not ok:
        for err in errors:
            print(f"[ERROR] {err}")
        print()
        print("Setup:")
        print("  1. Create a bot via @BotFather and copy the token")
        print("  2. Get your chat ID from @userinfobot")
        print("  3. Send /start to your bot in Telegram")
        print("  4. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env.local")
        return False

    print("Validating bot token (getMe)...")
    bot_ok, username, bot_error = await fetch_bot_info(token)
    if not bot_ok:
        print(f"[ERROR] Bot token invalid: {bot_error}")
        print()
        print("Troubleshooting:")
        print("  1. Copy the token again from @BotFather")
        print("  2. Do not wrap the token in quotes in .env.local")
        return False

    print(f"[OK] Bot token valid (@{username})")
    print()

    print("Sending test message...")
    sent, send_error = await send_test_message(token, chat)
    if not sent:
        print(f"[ERROR] Failed to send test message: {send_error}")
        print()
        print("Troubleshooting:")
        if "chat not found" in send_error.lower():
            print("  - Verify TELEGRAM_CHAT_ID with @userinfobot")
        if "blocked" in send_error.lower() or "bot was blocked" in send_error.lower():
            print("  - Unblock the bot in Telegram and send /start again")
        if "can't initiate conversation" in send_error.lower() or "forbidden" in send_error.lower():
            print("  - Open your bot in Telegram and send /start first")
        print("  - Confirm the VPS can reach https://api.telegram.org")
        return False

    print("[OK] Test message delivered — check your Telegram app")
    print()

    # Optional: verify via the app's notifier class (same code path as live bot)
    try:
        from trading_bot.utils.notifications import TelegramNotifier

        notifier = TelegramNotifier(bot_token=token, chat_id=chat)
        if notifier.enabled:
            print("[OK] TelegramNotifier initialized (same path as live bot)")
        else:
            print("[WARN] TelegramNotifier reports disabled")
    except Exception as exc:
        print(f"[WARN] Could not import TelegramNotifier: {exc}")

    print()
    print("=" * 50)
    print("  Telegram Test Complete!")
    print("=" * 50)
    print()
    print("Notifications will work when the backend API is running.")
    print("Start with: start_bot_production.bat")
    print("Then send /help to your bot for command polling.")
    print()

    return True


def main() -> bool:
    return asyncio.run(test_telegram_connection())


if __name__ == "__main__":
    success = main()

    if not success:
        print()
        print("Telegram test FAILED. Please fix the issues above.")
        print()

    input("Press Enter to exit...")
