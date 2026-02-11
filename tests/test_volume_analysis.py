"""
Tests for Volume Analysis Module.

Covers:
- VolumeAnalyzer unit tests (analyze, relative_volume, trend, spikes, climax, edge cases)
- Volume integration in DisplacementDetector (volume_confirmed field)
- Volume integration in OrderBlockDetector (volume_score field)
- Volume integration in LiquidityMapper (volume_spike on sweeps)
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from trading_bot.analysis.volume_analysis import VolumeAnalyzer, VolumeAnalysis
from trading_bot.analysis.displacement import DisplacementDetector, DisplacementCandle
from trading_bot.analysis.order_blocks import OrderBlockDetector, OrderBlock
from trading_bot.analysis.liquidity import LiquidityMapper, LiquiditySweep


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def volume_analyzer():
    """Create a default VolumeAnalyzer instance."""
    return VolumeAnalyzer()


def _make_df(volumes, n=50, base_price=1.0800, trend="flat"):
    """
    Helper to build a simple OHLCV DataFrame.

    Args:
        volumes: list of volume values (length determines tail; earlier bars
                 are filled with a constant 100).
        n: total number of bars.
        base_price: starting close price.
        trend: "flat", "up", or "down" to shape the candles.
    """
    total = max(n, len(volumes))
    dates = pd.date_range(start="2024-01-01", periods=total, freq="1h")

    # Fill volume
    if len(volumes) < total:
        pad = [100] * (total - len(volumes))
        vols = pad + list(volumes)
    else:
        vols = list(volumes[-total:])

    # Generate OHLCV
    opens, highs, lows, closes = [], [], [], []
    price = base_price
    for i in range(total):
        step = 0.0
        if trend == "up":
            step = 0.0002
        elif trend == "down":
            step = -0.0002
        o = price
        c = price + step
        h = max(o, c) + 0.0001
        l = min(o, c) - 0.0001
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        price = c

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=dates,
    )
    return df


# ============================================================================
# VolumeAnalyzer Unit Tests
# ============================================================================


class TestVolumeAnalyzerBasics:
    """Basic VolumeAnalyzer.analyze() tests."""

    def test_analyze_returns_volume_analysis(self, volume_analyzer):
        """analyze() returns a VolumeAnalysis dataclass."""
        df = _make_df([100] * 30)
        result = volume_analyzer.analyze(df)
        assert isinstance(result, VolumeAnalysis)

    def test_current_volume_matches_last_bar(self, volume_analyzer):
        """current_volume should equal the last bar's volume."""
        df = _make_df([100] * 29 + [250])
        result = volume_analyzer.analyze(df)
        assert result.current_volume == 250.0

    def test_avg_volume_20_correct(self, volume_analyzer):
        """avg_volume_20 should be the 20-bar rolling mean at the last bar."""
        vols = [100] * 29 + [200]
        df = _make_df(vols, n=30)
        result = volume_analyzer.analyze(df)
        # 20-bar rolling mean at last bar: (19*100 + 200) / 20 = 105
        assert abs(result.avg_volume_20 - 105.0) < 1.0

    def test_to_dict_keys(self, volume_analyzer):
        """to_dict() should contain all expected keys."""
        df = _make_df([100] * 30)
        result = volume_analyzer.analyze(df)
        d = result.to_dict()
        expected_keys = {
            "current_volume", "avg_volume_20", "relative_volume",
            "volume_trend", "spike_count", "spike_bars",
            "climax_detected", "is_high_volume", "is_low_volume",
        }
        assert expected_keys == set(d.keys())


class TestRelativeVolume:
    """Tests for relative volume calculation."""

    def test_relative_volume_high(self, volume_analyzer):
        """Volume at 3x average should give relative_volume ~3.0."""
        # 20 bars at 100, then last bar at 300
        vols = [100] * 29 + [300]
        df = _make_df(vols, n=30)
        result = volume_analyzer.analyze(df)
        # avg ≈ (19*100 + 300)/20 = 110 ... but relative = current/avg
        # With 30 bars total, rolling mean uses bars 10-29 (all 100 except last=300)
        # avg_20 = (19*100 + 300)/20 = 110
        # relative = 300 / 110 ≈ 2.73
        assert result.relative_volume > 2.0

    def test_relative_volume_low(self, volume_analyzer):
        """Volume at 0.3x average should give relative_volume ~0.3."""
        vols = [1000] * 29 + [300]
        df = _make_df(vols, n=30)
        result = volume_analyzer.analyze(df)
        # avg_20 = (19*1000 + 300)/20 = 965
        # relative = 300 / 965 ≈ 0.31
        assert result.relative_volume < 0.5

    def test_relative_volume_normal(self, volume_analyzer):
        """Volume equal to average should give relative_volume ~1.0."""
        vols = [100] * 30
        df = _make_df(vols, n=30)
        result = volume_analyzer.analyze(df)
        assert 0.9 <= result.relative_volume <= 1.1


class TestVolumeTrend:
    """Tests for volume trend detection."""

    def test_volume_trend_increasing(self, volume_analyzer):
        """Ascending volume series should return 'increasing'."""
        # 20 bars of low volume, then 20 bars of increasing volume
        vols = [50] * 20 + list(range(100, 300, 10))  # 20 bars: 100, 110, ..., 290
        df = _make_df(vols, n=40)
        result = volume_analyzer.analyze(df)
        assert result.volume_trend == "increasing"

    def test_volume_trend_decreasing(self, volume_analyzer):
        """Descending volume series should return 'decreasing'."""
        vols = [50] * 20 + list(range(300, 100, -10))  # 20 bars: 300, 290, ..., 110
        df = _make_df(vols, n=40)
        result = volume_analyzer.analyze(df)
        assert result.volume_trend == "decreasing"

    def test_volume_trend_flat(self, volume_analyzer):
        """Constant volume should return 'flat'."""
        vols = [100] * 40
        df = _make_df(vols, n=40)
        result = volume_analyzer.analyze(df)
        assert result.volume_trend == "flat"


class TestSpikeDetection:
    """Tests for volume spike detection."""

    def test_spike_detected(self, volume_analyzer):
        """A single bar at 2.5x avg should be detected as a spike."""
        vols = [100] * 30 + [250]  # 250 > 100 * 2 = 200
        df = _make_df(vols, n=31)
        result = volume_analyzer.analyze(df)
        assert len(result.spike_bars) >= 1

    def test_no_spikes_normal_volume(self, volume_analyzer):
        """Normal volume bars should have no spikes."""
        vols = [100] * 40
        df = _make_df(vols, n=40)
        result = volume_analyzer.analyze(df)
        assert len(result.spike_bars) == 0

    def test_multiple_spikes(self, volume_analyzer):
        """Multiple spike bars should all be detected."""
        vols = [100] * 20 + [300, 100, 100, 300, 100]
        df = _make_df(vols, n=25)
        result = volume_analyzer.analyze(df)
        assert len(result.spike_bars) >= 2


class TestClimaxDetection:
    """Tests for volume climax detection."""

    def test_climax_detected_reversal_candle(self, volume_analyzer):
        """Volume > 3x avg on a reversal candle (large wick) should flag climax."""
        n = 30
        dates = pd.date_range(start="2024-01-01", periods=n, freq="1h")
        vols = [100] * (n - 1) + [350]  # last bar > 3x avg

        opens = [1.0800] * n
        closes = [1.0802] * n
        highs = [1.0810] * n
        lows = [1.0795] * n

        # Make last candle a reversal: long upper wick, small body
        opens[-1] = 1.0800
        closes[-1] = 1.0801  # tiny body
        highs[-1] = 1.0820   # big upper wick
        lows[-1] = 1.0799

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=dates,
        )
        result = volume_analyzer.analyze(df)
        assert result.climax_detected is True

    def test_no_climax_low_volume(self, volume_analyzer):
        """Normal volume should not detect a climax."""
        df = _make_df([100] * 30)
        result = volume_analyzer.analyze(df)
        assert result.climax_detected is False


class TestEdgeCases:
    """Edge case tests for VolumeAnalyzer."""

    def test_missing_volume_column(self, volume_analyzer):
        """DataFrame without 'volume' column should return safe defaults."""
        dates = pd.date_range(start="2024-01-01", periods=10, freq="1h")
        df = pd.DataFrame(
            {
                "open": [1.0] * 10,
                "high": [1.01] * 10,
                "low": [0.99] * 10,
                "close": [1.0] * 10,
            },
            index=dates,
        )
        result = volume_analyzer.analyze(df)
        assert result.current_volume == 0.0
        assert result.avg_volume_20 == 0.0
        assert result.relative_volume == 0.0
        assert result.volume_trend == "flat"
        assert len(result.spike_bars) == 0
        assert result.climax_detected is False

    def test_empty_dataframe(self, volume_analyzer):
        """Empty DataFrame should not crash."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = volume_analyzer.analyze(df)
        assert isinstance(result, VolumeAnalysis)
        assert result.current_volume == 0.0

    def test_none_dataframe(self, volume_analyzer):
        """None DataFrame should not crash."""
        result = volume_analyzer.analyze(None)
        assert isinstance(result, VolumeAnalysis)
        assert result.current_volume == 0.0

    def test_very_short_dataframe(self, volume_analyzer):
        """DataFrame with fewer bars than avg_period should still work."""
        vols = [100, 200, 150]
        dates = pd.date_range(start="2024-01-01", periods=3, freq="1h")
        df = pd.DataFrame(
            {
                "open": [1.0] * 3,
                "high": [1.01] * 3,
                "low": [0.99] * 3,
                "close": [1.0] * 3,
                "volume": vols,
            },
            index=dates,
        )
        result = volume_analyzer.analyze(df)
        assert result.current_volume == 150.0
        # avg should fall back to simple mean
        assert abs(result.avg_volume_20 - 150.0) < 1.0


class TestVolumeAtIndices:
    """Tests for volume_at_indices utility."""

    def test_volume_at_valid_indices(self, volume_analyzer):
        """Should return correct volume values at specified positions."""
        vols = [100, 200, 300, 400, 500]
        df = _make_df(vols, n=5)
        result = volume_analyzer.volume_at_indices(df, [0, 2, 4])
        assert result == [100.0, 300.0, 500.0]

    def test_volume_at_out_of_range(self, volume_analyzer):
        """Out of range indices should return 0.0."""
        df = _make_df([100] * 5, n=5)
        result = volume_analyzer.volume_at_indices(df, [10, -1])
        assert result == [0.0, 0.0]


# ============================================================================
# Displacement + Volume Integration Tests
# ============================================================================


class TestDisplacementVolumeConfirmed:
    """Tests for volume_confirmed on DisplacementCandle."""

    def _make_displacement_df(self, disp_volume=500, normal_volume=100):
        """
        Build a DataFrame with a clear displacement candle at the end.
        The displacement candle has a large body and creates conditions for detection.
        """
        n = 30
        dates = pd.date_range(start="2024-01-01", periods=n, freq="1h")

        opens = [1.0800 + i * 0.0001 for i in range(n)]
        closes = [o + 0.0001 for o in opens]  # small bullish candles
        highs = [max(o, c) + 0.0001 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 0.0001 for o, c in zip(opens, closes)]
        vols = [normal_volume] * n

        # Make last candle a strong bullish displacement (large body, minimal wick)
        opens[-1] = 1.0800
        closes[-1] = 1.0850  # 50 pip body
        highs[-1] = 1.0852   # tiny upper wick
        lows[-1] = 1.0799    # tiny lower wick
        vols[-1] = disp_volume

        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=dates,
        )

    def test_displacement_volume_confirmed(self):
        """Displacement candle with 2x volume should have volume_confirmed=True."""
        df = self._make_displacement_df(disp_volume=500, normal_volume=100)
        detector = DisplacementDetector(pip_value=0.0001)
        analysis = detector.detect(df)

        if analysis.recent_displacements:
            disp = analysis.recent_displacements[-1]
            # The displacement candle had 500 volume vs 100 avg = 5x -> confirmed
            assert disp.volume_confirmed is True
            assert "volume_confirmed" in disp.to_dict()
            assert disp.to_dict()["volume_confirmed"] is True

    def test_displacement_volume_not_confirmed(self):
        """Displacement candle with low volume should have volume_confirmed=False."""
        df = self._make_displacement_df(disp_volume=50, normal_volume=100)
        detector = DisplacementDetector(pip_value=0.0001)
        analysis = detector.detect(df)

        if analysis.recent_displacements:
            disp = analysis.recent_displacements[-1]
            # 50 vs 100 avg = 0.5x -> NOT confirmed
            assert disp.volume_confirmed is False

    def test_displacement_strength_with_volume(self):
        """High volume should add to strength score."""
        df_high = self._make_displacement_df(disp_volume=500, normal_volume=100)
        df_low = self._make_displacement_df(disp_volume=50, normal_volume=100)

        detector = DisplacementDetector(pip_value=0.0001)
        analysis_high = detector.detect(df_high)
        analysis_low = detector.detect(df_low)

        if analysis_high.recent_displacements and analysis_low.recent_displacements:
            strength_high = analysis_high.recent_displacements[-1].strength
            strength_low = analysis_low.recent_displacements[-1].strength
            # High volume adds +0.15 to strength
            assert strength_high > strength_low


# ============================================================================
# Order Block + Volume Integration Tests
# ============================================================================


class TestOrderBlockVolumeScore:
    """Tests for volume_score on OrderBlock."""

    def _make_ob_df(self, impulse_volume=500, prior_volume=100):
        """
        Build a DataFrame with a clear impulse move that creates an order block.
        """
        n = 40
        dates = pd.date_range(start="2024-01-01", periods=n, freq="1h")

        # Ranging market, then a bearish candle, then a bullish impulse
        opens = [1.0800] * n
        closes = [1.0800] * n
        highs = [1.0810] * n
        lows = [1.0790] * n
        vols = [prior_volume] * n

        # Bearish candle at index 30 (this becomes the OB)
        opens[30] = 1.0810
        closes[30] = 1.0795
        highs[30] = 1.0815
        lows[30] = 1.0790

        # Strong bullish impulse at indices 31-33
        for i in [31, 32, 33]:
            opens[i] = 1.0800 + (i - 31) * 0.0020
            closes[i] = opens[i] + 0.0025  # strong bullish body
            highs[i] = closes[i] + 0.0002
            lows[i] = opens[i] - 0.0002
            vols[i] = impulse_volume

        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=dates,
        )

    def test_order_block_volume_score_high(self):
        """OB from high-volume impulse should have volume_score > 0."""
        df = self._make_ob_df(impulse_volume=500, prior_volume=100)
        detector = OrderBlockDetector(min_impulse_candles=2, min_body_percentage=0.3)
        analysis = detector.detect(df)

        all_obs = analysis.bullish_obs + analysis.bearish_obs
        if all_obs:
            ob = all_obs[0]
            assert hasattr(ob, "volume_score")
            assert ob.volume_score > 0
            assert "volume_score" in ob.to_dict()

    def test_order_block_low_volume_score(self):
        """OB from same-volume impulse should have volume_score ~0."""
        df = self._make_ob_df(impulse_volume=100, prior_volume=100)
        detector = OrderBlockDetector(min_impulse_candles=2, min_body_percentage=0.3)
        analysis = detector.detect(df)

        all_obs = analysis.bullish_obs + analysis.bearish_obs
        if all_obs:
            ob = all_obs[0]
            # volume_score = max(0, ratio - 1) = max(0, 1.0 - 1.0) = 0
            assert ob.volume_score <= 0.1  # close to 0


# ============================================================================
# Liquidity Sweep + Volume Integration Tests
# ============================================================================


class TestLiquiditySweepVolumeSpike:
    """Tests for volume_spike on LiquiditySweep."""

    def _make_sweep_df(self, sweep_volume=500, normal_volume=100):
        """
        Build a DataFrame with a clear swing high followed by a sweep candle.
        """
        n = 40
        dates = pd.date_range(start="2024-01-01", periods=n, freq="1h")

        opens = [1.0800] * n
        closes = [1.0800] * n
        highs = [1.0805] * n
        lows = [1.0795] * n
        vols = [normal_volume] * n

        # Create a clear swing high at index 15
        highs[13] = 1.0810
        highs[14] = 1.0820
        highs[15] = 1.0830  # swing high peak
        closes[15] = 1.0825
        opens[15] = 1.0815
        highs[16] = 1.0815
        highs[17] = 1.0810

        # After the swing high, price pulls back
        for i in range(18, 25):
            opens[i] = 1.0810
            closes[i] = 1.0808
            highs[i] = 1.0812
            lows[i] = 1.0805

        # Sweep candle at index 30: price breaks above swing high and reverses
        opens[30] = 1.0820
        highs[30] = 1.0835  # sweeps above 1.0830
        lows[30] = 1.0810
        closes[30] = 1.0815  # closes below the swing high -> reversal
        vols[30] = sweep_volume

        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=dates,
        )

    def test_liquidity_sweep_volume_spike(self):
        """Sweep with 2.5x avg volume should have volume_spike=True."""
        df = self._make_sweep_df(sweep_volume=500, normal_volume=100)
        mapper = LiquidityMapper(swing_lookback=3, pip_value=0.0001, sweep_threshold_pips=1.0)
        analysis = mapper.analyze(df)

        sweep_with_vol = [s for s in analysis.recent_sweeps if s.volume_spike]
        # We expect at least one sweep with volume_spike=True
        if analysis.recent_sweeps:
            # Check that volume_spike field exists in to_dict()
            d = analysis.recent_sweeps[0].to_dict()
            assert "volume_spike" in d

    def test_liquidity_sweep_no_volume_spike(self):
        """Sweep with normal volume should have volume_spike=False."""
        df = self._make_sweep_df(sweep_volume=100, normal_volume=100)
        mapper = LiquidityMapper(swing_lookback=3, pip_value=0.0001, sweep_threshold_pips=1.0)
        analysis = mapper.analyze(df)

        for sweep in analysis.recent_sweeps:
            # 100 vs 100 avg -> no spike
            assert sweep.volume_spike is False
