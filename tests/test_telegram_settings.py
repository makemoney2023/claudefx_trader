"""Unit tests for allowlisted Telegram settings (no raw .env edits)."""

from __future__ import annotations

import time

import pytest

from trading_bot.services.telegram_settings import (
    FLAG_SPECS,
    MODE_LOCK_SPEC,
    VALUE_SPECS,
    SettingError,
    apply_setting,
    apply_symbols,
    clamp_number,
    format_value,
    get_current,
    get_spec,
    is_blocked_symbol,
    is_dangerous_change,
    is_dangerous_mode,
    is_pending_expired,
    list_flag_specs,
    list_value_specs,
    new_pending,
    normalize_mode_lock,
    parse_percent_or_decimal,
    parse_set,
    parse_toggle,
    pending_expires_at,
    resolve_telegram_locked_mode,
    validate_symbol,
)


@pytest.fixture
def restore_settings():
    from trading_bot.config import settings

    trading = settings.trading
    snapshot = {
        "claude_signal_trust_mode": trading.claude_signal_trust_mode,
        "ict_confirmation_mode": trading.ict_confirmation_mode,
        "liquidity_reversal_lean_mode": trading.liquidity_reversal_lean_mode,
        "claude_analysis_window": trading.claude_analysis_window,
        "news_gates_enabled": trading.news_gates_enabled,
        "pyramid_enabled": trading.pyramid_enabled,
        "opportunity_scanner_enabled": trading.opportunity_scanner_enabled,
        "dry_run": trading.dry_run,
        "demo_data_collection_mode": trading.demo_data_collection_mode,
        "risk_per_trade": trading.risk_per_trade,
        "max_position_size": trading.max_position_size,
        "max_total_exposure": trading.max_total_exposure,
        "max_daily_trades": trading.max_daily_trades,
        "min_risk_reward": trading.min_risk_reward,
        "gate_min_confidence": trading.gate_min_confidence,
        "claude_ny_lead_minutes": trading.claude_ny_lead_minutes,
        "opportunity_scanner_hot_list_size": trading.opportunity_scanner_hot_list_size,
        "symbols": list(trading.symbols),
        "telegram_mode_lock": getattr(trading, "telegram_mode_lock", ""),
    }
    root_strict = settings.strict_ict_sessions
    yield
    for key, value in snapshot.items():
        if hasattr(trading, key):
            setattr(trading, key, value)
    settings.strict_ict_sessions = root_strict


def test_allowlist_aliases():
    names = {s.name for s in list_flag_specs()}
    assert names == {
        "trust",
        "ict",
        "lean",
        "window",
        "news",
        "pyramid",
        "scanner",
        "dryrun",
        "demo",
        "strictkz",
    }
    value_names = {s.name for s in list_value_specs()}
    assert value_names == {
        "risk",
        "maxlot",
        "exposure",
        "trades",
        "rr",
        "conf",
        "lead",
        "hot",
    }


def test_unknown_key_rejected():
    with pytest.raises(SettingError, match="Unknown"):
        get_spec("api_key")
    with pytest.raises(SettingError, match="Unknown"):
        get_spec("password")
    with pytest.raises(SettingError, match="Unknown"):
        get_spec("BOT_API_KEY")


def test_parse_toggle_bool_flip_and_explicit(restore_settings):
    spec = get_spec("news")
    from trading_bot.config import settings

    settings.trading.news_gates_enabled = True
    assert parse_toggle(spec, None) is False
    assert parse_toggle(spec, "on") is True
    assert parse_toggle(spec, "OFF") is False
    with pytest.raises(SettingError):
        parse_toggle(spec, "maybe")


def test_parse_toggle_mode_requires_value():
    spec = get_spec("trust")
    with pytest.raises(SettingError, match="off, active"):
        parse_toggle(spec, None)
    assert parse_toggle(spec, "active") == "active"
    with pytest.raises(SettingError):
        parse_toggle(spec, "shadow")


def test_parse_set_risk_percent_and_decimal():
    spec = get_spec("risk")
    assert parse_set(spec, "1%") == pytest.approx(0.01)
    assert parse_set(spec, "0.01") == pytest.approx(0.01)
    with pytest.raises(SettingError, match="above max"):
        parse_set(spec, "5%")
    with pytest.raises(SettingError, match="below min"):
        parse_set(spec, "0.1%")


def test_parse_percent_or_decimal():
    assert parse_percent_or_decimal("1%") == pytest.approx(0.01)
    assert parse_percent_or_decimal("0.75") == pytest.approx(0.75)


def test_parse_set_rejects_raw_lot_alias():
    with pytest.raises(SettingError, match="Unknown"):
        get_spec("lot")
    with pytest.raises(SettingError, match="Unknown"):
        get_spec("lots")


def test_dangerous_thresholds():
    assert is_dangerous_change(get_spec("trust"), "active")
    assert not is_dangerous_change(get_spec("trust"), "off")
    assert is_dangerous_change(get_spec("dryrun"), False)
    assert is_dangerous_change(get_spec("demo"), True)
    assert is_dangerous_change(get_spec("risk"), 0.016)
    assert not is_dangerous_change(get_spec("risk"), 0.01)
    assert is_dangerous_change(get_spec("maxlot"), 0.25)
    assert is_dangerous_change(get_spec("exposure"), 3.5)
    assert is_dangerous_change(get_spec("trades"), 9)
    assert is_dangerous_mode("aggressive")
    assert not is_dangerous_mode("normal")


def test_blocked_symbols():
    assert is_blocked_symbol("ETHBTC")
    assert is_blocked_symbol("xrpbit")
    assert is_blocked_symbol("FOOBTC")
    assert not is_blocked_symbol("EURUSD")
    with pytest.raises(SettingError, match="Blocked"):
        validate_symbol("ETHBTC")
    with pytest.raises(SettingError, match="Invalid"):
        validate_symbol("??")


def test_apply_setting_mutates_live_and_persists(restore_settings, monkeypatch, tmp_path):
    saved = {}

    def fake_save(updates, prefix="TRADING_"):
        saved["updates"] = dict(updates)
        saved["prefix"] = prefix
        return True

    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.save_config_to_env_local",
        fake_save,
    )
    from trading_bot.config import settings

    settings.trading.claude_signal_trust_mode = "off"
    spec = get_spec("trust")
    result = apply_setting(spec, "active", record_activity=False)
    assert settings.trading.claude_signal_trust_mode == "active"
    assert result.old == "off"
    assert result.new == "active"
    assert saved["prefix"] == "TRADING_"
    assert saved["updates"]["claude_signal_trust_mode"] == "active"


def test_apply_setting_strictkz_uses_root_and_empty_prefix(
    restore_settings, monkeypatch
):
    saved = {}

    def fake_save(updates, prefix="TRADING_"):
        saved["updates"] = dict(updates)
        saved["prefix"] = prefix
        return True

    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.save_config_to_env_local",
        fake_save,
    )
    from trading_bot.config import settings

    settings.strict_ict_sessions = False
    result = apply_setting(get_spec("strictkz"), True, record_activity=False)
    assert settings.strict_ict_sessions is True
    assert result.needs_apply is True
    assert saved["prefix"] == ""
    assert "strict_ict_sessions" in saved["updates"]


def test_scanner_needs_apply(restore_settings, monkeypatch):
    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.save_config_to_env_local",
        lambda *a, **k: True,
    )
    result = apply_setting(get_spec("scanner"), True, record_activity=False)
    assert result.needs_apply is True


def test_apply_symbols(restore_settings, monkeypatch):
    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.save_config_to_env_local",
        lambda *a, **k: True,
    )
    from trading_bot.config import settings

    settings.trading.symbols = ["XAUUSD"]
    result = apply_symbols(["XAUUSD", "EURUSD"], record_activity=False)
    assert settings.trading.symbols == ["XAUUSD", "EURUSD"]
    assert result.needs_apply is True
    with pytest.raises(SettingError):
        apply_symbols(["ETHBTC"], record_activity=False)
    settings.trading.symbols = ["XAUUSD"]
    result = apply_symbols(["XAUUSD", "EURUSD"], validate=False, record_activity=False)
    assert result.new == ["XAUUSD", "EURUSD"]


def test_pending_ttl():
    now = 1000.0
    expires = pending_expires_at(now)
    assert expires == 1060.0
    assert not is_pending_expired(expires, now + 59)
    assert is_pending_expired(expires, now + 60)
    pending = new_pending("setting", "trust", "active", now=now)
    assert len(pending.code) == 2
    assert pending.code.isdigit()


def test_format_risk_percent(restore_settings):
    spec = get_spec("risk")
    assert format_value(spec, 0.01) == "1.00%"


def test_mode_lock_spec_allows_empty():
    assert "" in (MODE_LOCK_SPEC.allowed or ())
    assert get_spec("modelock") is MODE_LOCK_SPEC


def test_clamp_number_safety_net():
    spec = get_spec("hot")
    assert clamp_number(spec, 99) == 8
    assert clamp_number(spec, 0) == 1


def test_flag_and_value_spec_counts():
    assert len(FLAG_SPECS) == 10
    assert len(VALUE_SPECS) == 8


def test_get_current_bool(restore_settings):
    from trading_bot.config import settings

    settings.trading.dry_run = True
    assert get_current(get_spec("dryrun")) is True
    assert format_value(get_spec("dryrun")) == "on"


def test_resolve_telegram_locked_mode():
    from trading_bot.services.scaling_manager import TradingMode

    assert resolve_telegram_locked_mode("normal", data_collection=True) == TradingMode.AGGRESSIVE
    assert (
        resolve_telegram_locked_mode(
            "aggressive",
            auto_mode=TradingMode.DEFENSIVE,
        )
        == TradingMode.DEFENSIVE
    )
    assert resolve_telegram_locked_mode("conservative") == TradingMode.CONSERVATIVE
    assert resolve_telegram_locked_mode("auto") is None
    assert resolve_telegram_locked_mode("") is None
    assert normalize_mode_lock("AUTO") == ""
    assert normalize_mode_lock("aggressive") == "aggressive"
