"""
Phase 1: Measurement integrity — OHLCV quality, MFE/MAE persistence, excursion fix.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest


def _m15_bars(n: int = 40, *, gap_at: int | None = None, bad_ohlc: bool = False):
    """Build a clean M15 OHLCV frame; optionally inject a gap or bad bar."""
    start = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    rows = []
    times = []
    t = start
    for i in range(n):
        if gap_at is not None and i == gap_at:
            t = t + timedelta(hours=2)  # > 3x 15m
        else:
            t = t + timedelta(minutes=15) if i else t
        o = 2000.0 + i
        h = o + 1.0
        l = o - 1.0
        c = o + 0.5
        if bad_ohlc and i == n - 1:
            h, l = l, h  # high < low
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 100})
        times.append(t)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(times, name="time"))


def _now_after(df: pd.DataFrame, minutes: int = 5) -> datetime:
    last = df.index[-1].to_pydatetime()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last + timedelta(minutes=minutes)


class TestValidateOhlcv:
    def test_clean_frame_passes(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        df = _m15_bars(40)
        result = validate_ohlcv(
            df, symbol="XAUUSD", timeframe="M15", expected_count=40, now=_now_after(df)
        )
        assert result.valid is True
        assert result.reason == ""

    def test_too_few_bars_fails(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        df = _m15_bars(5)
        result = validate_ohlcv(
            df, symbol="XAUUSD", timeframe="M15", expected_count=40, now=_now_after(df)
        )
        assert result.valid is False
        assert "bar_count" in result.gate_id or "bar_count" in result.reason

    def test_ohlc_integrity_fails(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        df = _m15_bars(40, bad_ohlc=True)
        result = validate_ohlcv(
            df, symbol="XAUUSD", timeframe="M15", expected_count=40, now=_now_after(df)
        )
        assert result.valid is False
        assert "ohlc" in result.gate_id or "ohlc" in result.reason.lower()

    def test_large_gap_fails_outside_weekend(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        # Thursday 10:00 UTC series with mid-session 2h gap — not a weekend close
        df = _m15_bars(40, gap_at=20)
        result = validate_ohlcv(
            df, symbol="XAUUSD", timeframe="M15", expected_count=40, now=_now_after(df)
        )
        assert result.valid is False
        assert "gap" in result.gate_id or "gap" in result.reason.lower()

    def test_nan_fails(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        df = _m15_bars(40)
        df.iloc[-1, df.columns.get_loc("close")] = float("nan")
        result = validate_ohlcv(
            df, symbol="XAUUSD", timeframe="M15", expected_count=40, now=_now_after(df)
        )
        assert result.valid is False
        assert "nan" in result.gate_id or "nan" in result.reason.lower()

    def test_stale_last_bar_fails(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        start = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        times = [start + timedelta(minutes=15 * i) for i in range(40)]
        df = pd.DataFrame(
            {
                "open": [1.0] * 40,
                "high": [1.1] * 40,
                "low": [0.9] * 40,
                "close": [1.05] * 40,
                "volume": [100] * 40,
            },
            index=pd.DatetimeIndex(times, name="time"),
        )
        result = validate_ohlcv(
            df,
            symbol="XAUUSD",
            timeframe="M15",
            expected_count=40,
            now=datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc),
        )
        assert result.valid is False
        assert "stale" in result.gate_id or "stale" in result.reason.lower()

    def test_none_or_empty_fails(self):
        from trading_bot.mt5.ohlcv_quality import validate_ohlcv

        assert validate_ohlcv(None, symbol="XAUUSD", timeframe="M15").valid is False
        assert validate_ohlcv(pd.DataFrame(), symbol="XAUUSD", timeframe="M15").valid is False


class TestTroughRTracking:
    def test_trough_updates_on_adverse_excursion(self):
        from trading_bot.execution.position_manager import Position, PositionManager, PositionStatus

        pm = PositionManager(order_manager=None)
        pos = Position(
            ticket=1,
            symbol="XAUUSD",
            direction="long",
            volume=0.1,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2030.0,
            open_time=datetime.now(timezone.utc),
            status=PositionStatus.OPEN,
        )
        pm.add_position(pos)
        # Adverse move: price down 5 points = -0.5R
        pm.update_price(1, 1995.0)
        assert pos.trough_r_multiple <= -0.5 + 1e-9
        # Favorable then more adverse
        pm.update_price(1, 2010.0)
        assert pos.peak_r_multiple >= 1.0 - 1e-9
        pm.update_price(1, 1992.0)
        assert pos.trough_r_multiple <= -0.8 + 1e-9


class TestExcursionUsesMeasuredExtrema:
    @pytest.mark.asyncio
    async def test_compute_uses_peak_and_trough_not_heuristics(self):
        from trading_bot.analysis.excursion_analysis import ExcursionAnalyzer

        trades = [
            SimpleNamespace(
                symbol="XAUUSD",
                direction="long",
                entry_price=2000.0,
                stop_loss=1990.0,
                exit_price=2015.0,
                profit_loss=150.0,
                peak_r_multiple=2.0,
                trough_r_multiple=-0.4,
                r_multiple=1.5,
                timestamp=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                symbol="XAUUSD",
                direction="long",
                entry_price=2000.0,
                stop_loss=1990.0,
                exit_price=1988.0,
                profit_loss=-120.0,
                peak_r_multiple=0.5,
                trough_r_multiple=-1.0,
                r_multiple=-1.2,
                timestamp=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                symbol="XAUUSD",
                direction="short",
                entry_price=2000.0,
                stop_loss=2010.0,
                exit_price=1980.0,
                profit_loss=200.0,
                peak_r_multiple=2.0,
                trough_r_multiple=-0.3,
                r_multiple=2.0,
                timestamp=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                symbol="XAUUSD",
                direction="long",
                entry_price=2000.0,
                stop_loss=1990.0,
                exit_price=2020.0,
                profit_loss=200.0,
                peak_r_multiple=2.5,
                trough_r_multiple=-0.2,
                r_multiple=2.0,
                timestamp=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                symbol="XAUUSD",
                direction="long",
                entry_price=2000.0,
                stop_loss=1990.0,
                exit_price=2010.0,
                profit_loss=100.0,
                peak_r_multiple=1.5,
                trough_r_multiple=-0.5,
                r_multiple=1.0,
                timestamp=datetime.now(timezone.utc),
            ),
        ]

        analyzer = ExcursionAnalyzer()
        # Inject trades without hitting the DB
        result = analyzer.compute_from_trades(trades, symbol="XAUUSD", direction="all")
        assert result is not None
        assert result.sample_size == 5
        # Winner MAE must use trough magnitudes, not 0.3 * sl_dist
        # Winner troughs: 0.4, 0.3, 0.2, 0.5 → p90 of abs trough * sl_dist
        assert result.optimal_sl != pytest.approx(10.0 * 0.3, abs=0.01)
        # Median winner MFE R from peaks: 2.0, 2.0, 2.5, 1.5 → median 2.0
        assert result.median_winner_mfe_r == pytest.approx(2.0, abs=0.01)


class TestTradeModelExcursionColumns:
    def test_trade_model_has_peak_and_trough_columns(self):
        from trading_bot.api.database import TradeModel, PositionStateModel

        assert hasattr(TradeModel, "peak_r_multiple")
        assert hasattr(TradeModel, "trough_r_multiple")
        assert hasattr(TradeModel, "mfe_r")
        assert hasattr(TradeModel, "mae_r")
        assert hasattr(TradeModel, "regime")
        assert hasattr(TradeModel, "setup_fingerprint")
        assert hasattr(TradeModel, "entry_spread")
        assert hasattr(TradeModel, "slippage")
        assert hasattr(PositionStateModel, "trough_r_multiple")
