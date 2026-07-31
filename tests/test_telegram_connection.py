"""Tests for test_telegram_connection helpers and Telegram SSL connector."""

import os
import ssl
from unittest.mock import patch

import pytest

from test_telegram_connection import (
    normalize_env_value,
    validate_telegram_config,
    parse_get_me_response,
)
from trading_bot.utils.notifications import (
    telegram_ssl_verify_enabled,
    build_telegram_ssl_context,
)


def test_normalize_env_value_strips_quotes_and_whitespace():
    assert normalize_env_value(' "abc123" ') == "abc123"
    assert normalize_env_value("'token'") == "token"
    assert normalize_env_value(None) == ""


def test_validate_telegram_config_requires_both_values():
    ok, errors = validate_telegram_config("", "")
    assert ok is False
    assert "TELEGRAM_BOT_TOKEN" in errors[0]
    assert "TELEGRAM_CHAT_ID" in errors[1]


def test_validate_telegram_config_rejects_non_numeric_chat_id():
    ok, errors = validate_telegram_config("123:abc", "not-a-number")
    assert ok is False
    assert any("numeric" in err for err in errors)


def test_validate_telegram_config_accepts_valid_values():
    ok, errors = validate_telegram_config("123456:ABC-DEF", "716001905")
    assert ok is True
    assert errors == []


def test_parse_get_me_response_success():
    data = {"ok": True, "result": {"username": "my_bot", "first_name": "ICT Bot"}}
    ok, username, error = parse_get_me_response(data)
    assert ok is True
    assert username == "my_bot"
    assert error == ""


def test_parse_get_me_response_failure():
    data = {"ok": False, "description": "Unauthorized"}
    ok, username, error = parse_get_me_response(data)
    assert ok is False
    assert username == ""
    assert "Unauthorized" in error


class TestTelegramSslHelpers:
    def test_ssl_verify_defaults_true(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_SSL_VERIFY", None)
            assert telegram_ssl_verify_enabled() is True

    def test_ssl_verify_false_from_env(self):
        with patch.dict(os.environ, {"TELEGRAM_SSL_VERIFY": "false"}):
            assert telegram_ssl_verify_enabled() is False
        with patch.dict(os.environ, {"TELEGRAM_SSL_VERIFY": "0"}):
            assert telegram_ssl_verify_enabled() is False
        with patch.dict(os.environ, {"TELEGRAM_SSL_VERIFY": "no"}):
            assert telegram_ssl_verify_enabled() is False

    def test_ssl_verify_true_from_env(self):
        with patch.dict(os.environ, {"TELEGRAM_SSL_VERIFY": "true"}):
            assert telegram_ssl_verify_enabled() is True

    def test_build_context_verify_disabled(self):
        ctx = build_telegram_ssl_context(verify=False)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_build_context_verify_enabled(self):
        ctx = build_telegram_ssl_context(verify=True)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode != ssl.CERT_NONE

    @pytest.mark.asyncio
    async def test_send_message_retries_without_ssl_verify(self):
        """VPS SSL MITM should trigger one retry with verify disabled."""
        from trading_bot.utils.notifications import TelegramNotifier

        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="999")
        notifier._ssl_verify = True

        class _Resp:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self, content_type=None):
                return {"ok": True}

            async def text(self):
                return ""

        class _Session:
            def __init__(self, fail_first=True):
                self.fail_first = fail_first
                self.calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.fail_first and self.calls == 1:
                    raise ssl.SSLCertVerificationError(
                        "self-signed certificate in certificate chain"
                    )
                return _Resp()

        sessions = []

        def fake_client_session(verify=None):
            sess = _Session(fail_first=True)
            # Second session (verify=False) should succeed
            if verify is False:
                sess = _Session(fail_first=False)
            sessions.append((verify, sess))
            return sess

        with patch.object(notifier, "_client_session", side_effect=fake_client_session):
            ok = await notifier.send_message("hello")

        assert ok is True
        assert notifier._ssl_verify is False
        assert any(v is False for v, _ in sessions)
