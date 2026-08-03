"""Mechanical opportunity scanner: scoring, hot list, ICT scan-mode, universe filters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ICTStrategy require_tradeable_session
# ---------------------------------------------------------------------------


class TestIctScanModeSessionGate:
    def test_default_requires_tradeable_session(self):
        from trading_bot.strategy.ict_strategy import ICTStrategy

        kz = MagicMock()
        kz.get_current_session.return_value = SimpleNamespace(
            is_tradeable=False,
            is_kill_zone=False,
            session_name="Asian",
        )
        strategy = ICTStrategy(kill_zone_checker=kz)
        with patch.object(strategy, "structure_analyzer") as struct:
            result = strategy.analyze(
                htf_data=MagicMock(),
                ltf_data=MagicMock(),
                symbol="XAUUSD",
            )
        assert result is None
        struct.analyze.assert_not_called()

    def test_scan_mode_skips_tradeable_session_gate(self):
        from trading_bot.strategy.ict_strategy import ICTStrategy
        from trading_bot.analysis.market_structure import TrendDirection

        kz = MagicMock()
        kz.get_current_session.return_value = SimpleNamespace(
            is_tradeable=False,
            is_kill_zone=False,
            session_name="Asian",
        )
        strategy = ICTStrategy(kill_zone_checker=kz)

        htf_struct = SimpleNamespace(trend=TrendDirection.RANGING)
        with patch.object(strategy.structure_analyzer, "analyze", return_value=htf_struct):
            with patch.object(strategy, "_find_ranging_setup", return_value=None) as find_range:
                with patch.object(strategy.fvg_detector, "detect", return_value=MagicMock()):
                    with patch.object(strategy.ob_detector, "detect", return_value=MagicMock()):
                        with patch.object(
                            strategy.liquidity_mapper, "analyze", return_value=MagicMock()
                        ):
                            result = strategy.analyze(
                                htf_data=MagicMock(),
                                ltf_data=MagicMock(),
                                symbol="XAUUSD",
                                require_tradeable_session=False,
                            )
        assert result is None
        find_range.assert_called_once()


# ---------------------------------------------------------------------------
# Pure helpers: universe, zone, score, hot list
# ---------------------------------------------------------------------------


class TestUniverseFilters:
    def test_excludes_blocked_and_btc_quote_pairs(self):
        from trading_bot.services.opportunity_scanner import filter_scan_universe

        symbols = [
            "XAUUSD",
            "BTCUSD",
            "ETHBTC",
            "EURUSD",
            "XRPBIT",
            "US30",
            "GBPJPY",
        ]
        blocked = {"ETHBTC", "XRPBIT"}
        crypto = {"BTCUSD", "ETHUSD", "XRPUSD"}
        out = filter_scan_universe(
            symbols,
            blocked_pairs=blocked,
            crypto_symbols=crypto,
            max_universe=40,
        )
        assert "XAUUSD" in out
        assert "BTCUSD" in out
        assert "EURUSD" in out
        assert "ETHBTC" not in out
        assert "XRPBIT" not in out
        assert "GBPJPY" not in out  # non-USD forex excluded in v1
        assert "US30" not in out

    def test_caps_universe_size(self):
        from trading_bot.services.opportunity_scanner import filter_scan_universe

        symbols = [f"PAIR{i}USD" for i in range(50)] + ["XAUUSD"]
        out = filter_scan_universe(
            symbols,
            blocked_pairs=set(),
            crypto_symbols=set(),
            max_universe=10,
        )
        assert len(out) <= 10


class TestZoneAndScore:
    def test_zone_ok_long_discount(self):
        from trading_bot.services.opportunity_scanner import direction_zone_ok

        assert direction_zone_ok("long", 0.25) is True
        assert direction_zone_ok("long", 0.85) is False

    def test_zone_ok_short_premium(self):
        from trading_bot.services.opportunity_scanner import direction_zone_ok

        assert direction_zone_ok("short", 0.75) is True
        assert direction_zone_ok("short", 0.20) is False

    def test_score_formula(self):
        from trading_bot.services.opportunity_scanner import compute_opportunity_score

        score = compute_opportunity_score(
            confluence_count=4,
            confidence=0.8,
            risk_reward=2.0,
            in_kill_zone=True,
            is_crypto=False,
        )
        # 4*0.25 + 0.8*0.35 + min(2,4)/4*0.25 + 0.15 = 1.0 + 0.28 + 0.125 + 0.15
        assert abs(score - 1.555) < 1e-9

    def test_hard_reject_low_rr(self):
        from trading_bot.services.opportunity_scanner import is_promotable

        assert (
            is_promotable(
                has_setup=True,
                zone_ok=True,
                spread_ok=True,
                risk_reward=1.2,
                min_rr=1.5,
            )
            is False
        )
        assert (
            is_promotable(
                has_setup=True,
                zone_ok=True,
                spread_ok=True,
                risk_reward=2.0,
                min_rr=1.5,
            )
            is True
        )


class TestHotList:
    def test_promote_and_ttl_evict(self):
        from trading_bot.services.opportunity_scanner import HotList

        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        hot = HotList(max_size=2, ttl_minutes=60)
        hot.promote(
            "BTCUSD",
            score=1.2,
            direction="long",
            reason="mech setup",
            now=now,
        )
        assert "BTCUSD" in hot.active_symbols(now)
        later = now + timedelta(minutes=61)
        assert "BTCUSD" not in hot.active_symbols(later)

    def test_refresh_ttl_on_repromote(self):
        from trading_bot.services.opportunity_scanner import HotList

        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        hot = HotList(max_size=2, ttl_minutes=60)
        hot.promote("ETHUSD", score=1.0, direction="long", reason="a", now=now)
        mid = now + timedelta(minutes=50)
        hot.promote("ETHUSD", score=1.1, direction="long", reason="b", now=mid)
        assert "ETHUSD" in hot.active_symbols(mid + timedelta(minutes=50))

    def test_max_size_keeps_highest_scores(self):
        from trading_bot.services.opportunity_scanner import HotList

        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        hot = HotList(max_size=2, ttl_minutes=60)
        hot.promote("A", score=1.0, direction="long", reason="a", now=now)
        hot.promote("B", score=2.0, direction="long", reason="b", now=now)
        hot.promote("C", score=1.5, direction="long", reason="c", now=now)
        active = set(hot.active_symbols(now))
        assert active == {"B", "C"}

    def test_merge_with_base_symbols(self):
        from trading_bot.services.opportunity_scanner import HotList, merge_cycle_symbols

        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        hot = HotList(max_size=3, ttl_minutes=60)
        hot.promote("BTCUSD", score=1.0, direction="long", reason="x", now=now)
        merged = merge_cycle_symbols(["XAUUSD"], hot, now=now)
        assert merged == ["XAUUSD", "BTCUSD"]


class TestPreJudgeZoneBlock:
    def test_buy_limit_premium_blocked_helper(self):
        from trading_bot.services.opportunity_scanner import pre_judge_zone_block_reason

        reason = pre_judge_zone_block_reason(
            order_type="buy_limit",
            direction="long",
            retrace_pct=0.85,
        )
        assert reason is not None
        assert "premium" in reason.lower() or "zone" in reason.lower()

    def test_aligned_buy_limit_passes(self):
        from trading_bot.services.opportunity_scanner import pre_judge_zone_block_reason

        assert (
            pre_judge_zone_block_reason(
                order_type="buy_limit",
                direction="long",
                retrace_pct=0.30,
            )
            is None
        )
