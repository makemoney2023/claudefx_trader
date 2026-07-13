"""Tests for test_telegram_connection helpers."""

import pytest

from test_telegram_connection import (
    normalize_env_value,
    validate_telegram_config,
    parse_get_me_response,
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
