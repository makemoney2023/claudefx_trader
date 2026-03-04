"""
Claude Replay Backtester.

Feeds historical chart data through Claude's full analysis pipeline
to statistically validate its edge. Simulates trade outcomes using
subsequent price data.

NOTE: This is API-intensive (~$50-100 per symbol per month of data).
Run on weekends with sampled subsets.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import pandas as pd
import numpy as np

from ..utils.logging import get_logger
from ..utils.candle_utils import calculate_atr
from ..utils.json_helpers import sanitize_for_json

logger = get_logger(__name__)


def _detect_session(ts: datetime) -> str:
    """Derive trading session from UTC timestamp."""
    h = ts.hour
    if 0 <= h < 7:
        return "asian"
    if 7 <= h < 13:
        return "london"
    if 13 <= h < 17:
        return "new_york"
    if 17 <= h < 22:
        return "new_york_pm"
    return "asian"


@dataclass
class ReplaySignal:
    """A signal generated during replay."""
    timestamp: datetime
    symbol: str
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reasoning: str = ""
    trade_type: str = "intraday"
    market_structure: str = "unknown"


@dataclass
class ReplayTrade:
    """A simulated trade from replay."""
    signal: ReplaySignal
    outcome: str  # 'win', 'loss', 'timeout'
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl_pips: float = 0.0
    r_multiple: float = 0.0
    mfe_pips: float = 0.0  # Max Favorable Excursion
    mae_pips: float = 0.0  # Max Adverse Excursion
    bars_held: int = 0


@dataclass
class ReplayResult:
    """Aggregate results from a replay backtest."""
    symbol: str
    start_date: datetime
    end_date: datetime
    total_signals: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    max_drawdown_r: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    trades: List[ReplayTrade] = field(default_factory=list)
    api_calls: int = 0
    estimated_cost: float = 0.0
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'total_signals': self.total_signals,
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'timeouts': self.timeouts,
            'win_rate': round(self.win_rate, 2),
            'avg_r': round(self.avg_r, 2),
            'total_r': round(self.total_r, 2),
            'max_drawdown_r': round(self.max_drawdown_r, 2),
            'sharpe_ratio': round(self.sharpe_ratio, 2),
            'profit_factor': round(self.profit_factor, 2),
            'api_calls': self.api_calls,
            'estimated_cost': round(self.estimated_cost, 2),
            'duration_seconds': round(self.duration_seconds, 1),
        }


class HistoricalDataLoader:
    """Loads and caches historical OHLCV data from MT5."""

    def __init__(self, mt5_client=None):
        self._mt5 = mt5_client
        self._cache: Dict[str, pd.DataFrame] = {}

    async def load(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> Optional[pd.DataFrame]:
        """Load historical data, using cache when available."""
        cache_key = f"{symbol}_{timeframe}_{start.date()}_{end.date()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._mt5 is None:
            logger.warning("No MT5 client available for historical data")
            return None

        try:
            from ..mt5.data_fetcher import DataFetcher
            fetcher = DataFetcher(self._mt5)
            df = await fetcher.get_ohlcv_range(symbol, timeframe, start, end)
            if df is not None and not df.empty:
                self._cache[cache_key] = df
            return df
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
            return None


def _simulate_trade(
    signal: ReplaySignal,
    future_data: pd.DataFrame,
    max_bars: int = 200,
) -> ReplayTrade:
    """
    Simulate a trade outcome using price data after the signal.

    Checks each bar to see if SL or TP is hit first, tracking MFE/MAE.
    When both SL and TP are hit on the same bar, uses the bar's open to
    infer which level was reached first.
    """
    if future_data is None or future_data.empty:
        return ReplayTrade(signal=signal, outcome='timeout')

    entry = signal.entry_price
    sl = signal.stop_loss
    tp = signal.take_profit
    is_long = signal.direction == 'long'

    sl_dist = abs(entry - sl) if sl is not None else 1.0
    mfe = 0.0
    mae = 0.0

    for i, (_, bar) in enumerate(future_data.iterrows()):
        if i >= max_bars:
            break

        bar_open = float(bar['open'])
        high = float(bar['high'])
        low = float(bar['low'])
        close = float(bar['close'])

        if is_long:
            fav = high - entry
            adv = entry - low
        else:
            fav = entry - low
            adv = high - entry

        mfe = max(mfe, fav)
        mae = max(mae, adv)

        sl_hit = (is_long and low <= sl) or (not is_long and high >= sl)
        tp_hit = (is_long and high >= tp) or (not is_long and low <= tp)

        if sl_hit and tp_hit:
            dist_to_sl = abs(bar_open - sl)
            dist_to_tp = abs(bar_open - tp)
            if dist_to_sl <= dist_to_tp:
                sl_hit, tp_hit = True, False
            else:
                sl_hit, tp_hit = False, True

        if sl_hit:
            pnl = (sl - entry) if is_long else (entry - sl)
            return ReplayTrade(
                signal=signal, outcome='loss', exit_price=sl,
                exit_time=bar.name if hasattr(bar, 'name') else None,
                pnl_pips=pnl, r_multiple=-1.0,
                mfe_pips=mfe, mae_pips=mae, bars_held=i + 1,
            )

        if tp_hit:
            pnl = (tp - entry) if is_long else (entry - tp)
            r = pnl / sl_dist if sl_dist > 0 else 0
            return ReplayTrade(
                signal=signal, outcome='win', exit_price=tp,
                exit_time=bar.name if hasattr(bar, 'name') else None,
                pnl_pips=pnl, r_multiple=r,
                mfe_pips=mfe, mae_pips=mae, bars_held=i + 1,
            )

    last_close = float(future_data.iloc[-1]['close'])
    pnl = (last_close - entry) if is_long else (entry - last_close)
    r = pnl / sl_dist if sl_dist > 0 else 0
    return ReplayTrade(
        signal=signal,
        outcome='timeout',
        exit_price=last_close,
        pnl_pips=pnl,
        r_multiple=r,
        mfe_pips=mfe,
        mae_pips=mae,
        bars_held=len(future_data),
    )


class ClaudeReplayBacktester:
    """
    Replay historical charts through Claude's analysis pipeline.

    Usage:
        bt = ClaudeReplayBacktester(claude_client, mt5_client)
        result = await bt.run("XAUUSD", start, end, interval_hours=1)
    """

    # Approximate cost per API call (Opus 4.5 with images)
    COST_PER_CALL = 0.08

    def __init__(self, claude_client=None, mt5_client=None, trade_learning_service=None):
        self._claude = claude_client
        self._data_loader = HistoricalDataLoader(mt5_client)
        self._chart_gen = None
        self._learning_service = trade_learning_service

    async def estimate_cost(
        self, symbol: str, start: datetime, end: datetime, interval_hours: float = 1.0
    ) -> Dict[str, Any]:
        """Estimate API cost without making calls."""
        total_hours = (end - start).total_seconds() / 3600
        num_calls = int(total_hours / interval_hours)
        cost = num_calls * self.COST_PER_CALL
        return {
            'symbol': symbol,
            'period': f"{start.date()} to {end.date()}",
            'estimated_api_calls': num_calls,
            'estimated_cost': f"${cost:.2f}",
            'interval_hours': interval_hours,
        }

    async def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval_hours: float = 1.0,
        max_signals: int = 500,
        dry_run: bool = False,
        progress_callback=None,
    ) -> ReplayResult:
        """
        Run the replay backtest.

        Args:
            symbol: Trading symbol
            start_date: Start of backtest period
            end_date: End of backtest period
            interval_hours: Hours between analysis snapshots
            max_signals: Maximum signals to process
            dry_run: If True, count calls without calling Claude

        Returns:
            ReplayResult with full statistics
        """
        start_time = time.time()
        result = ReplayResult(symbol=symbol, start_date=start_date, end_date=end_date)

        if not dry_run and (self._claude is None or not self._claude.api_key):
            logger.error("Claude client not available for replay")
            return result

        m15_data = await self._data_loader.load(symbol, 'M15', start_date, end_date + timedelta(days=5))
        if m15_data is None or m15_data.empty:
            logger.error(f"No historical M15 data for {symbol}")
            return result

        h1_data = None
        d1_data = None
        try:
            h1_data = await self._data_loader.load(symbol, 'H1', start_date, end_date + timedelta(days=5))
        except Exception as e:
            logger.warning(f"[REPLAY] H1 data unavailable (non-fatal): {e}")
        try:
            d1_data = await self._data_loader.load(symbol, 'D1', start_date - timedelta(days=60), end_date)
        except Exception as e:
            logger.warning(f"[REPLAY] D1 data unavailable (non-fatal): {e}")

        logger.info("=" * 70)
        logger.info(f"[REPLAY] {symbol} BACKTEST STARTING")
        logger.info(f"[REPLAY] Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"[REPLAY] Data: {len(m15_data)} M15 bars | H1: {'loaded' if h1_data is not None else 'N/A'} | D1: {'loaded' if d1_data is not None else 'N/A'}")
        logger.info(f"[REPLAY] Interval: {interval_hours}h | Max signals: {max_signals} | Dry run: {dry_run}")
        logger.info("=" * 70)

        setup_playbook = ""
        if self._learning_service:
            try:
                setup_playbook = await self._learning_service.build_setup_playbook(lookback_days=180, min_sample=3)
            except Exception as e:
                logger.debug(f"[REPLAY] Setup playbook unavailable: {e}")

        # Walk through the data at the specified interval
        current = start_date
        signals_processed = 0
        context_builder = None

        try:
            from ..llm.context_builder import ContextBuilder
            context_builder = ContextBuilder()
        except Exception as e:
            logger.debug(f"[REPLAY] ContextBuilder unavailable: {e}")

        total_steps = min(
            int((end_date - start_date).total_seconds() / 3600 / interval_hours) + 1,
            max_signals,
        )
        step_idx = 0

        while current <= end_date and signals_processed < max_signals:
            if current.weekday() >= 5 or (current.weekday() == 4 and current.hour >= 19):
                current += timedelta(hours=interval_hours)
                step_idx += 1
                continue

            window_end = current
            lookback_bars = 100
            m15_window = m15_data[m15_data.index <= window_end].tail(lookback_bars)

            if len(m15_window) < 20:
                current += timedelta(hours=interval_hours)
                step_idx += 1
                continue

            result.api_calls += 1

            if dry_run:
                current += timedelta(hours=interval_hours)
                signals_processed += 1
                step_idx += 1
                continue

            pct = min(int(step_idx / max(total_steps, 1) * 100), 99)
            step_desc = (
                f"Snapshot {step_idx}/{total_steps} | "
                f"{result.total_trades} trades, "
                f"{result.wins}W/{result.losses}L"
            )
            if progress_callback and step_idx % 3 == 0:
                try:
                    await progress_callback(pct, step_desc, None)
                except Exception as e:
                    logger.debug(f"[REPLAY] Progress callback error: {e}")

            try:
                current_price = float(m15_window['close'].iloc[-1])
                atr_s = calculate_atr(m15_window, period=14)
                atr_val = float(atr_s.iloc[-1]) if not atr_s.empty else 0
                snapshot_session = _detect_session(current)

                analysis_data: Dict[str, Any] = {}
                _bar_extreme_zones: list = []
                _be_m15 = None
                _m15_overlays: Dict[str, Any] = {}
                try:
                    from ..analysis.market_structure import MarketStructureAnalyzer
                    from ..analysis.fair_value_gap import FVGDetector
                    from ..analysis.order_blocks import OrderBlockDetector
                    from ..analysis.liquidity import LiquidityMapper
                    from ..analysis.bar_extreme_zones import BarExtremeZoneDetector
                    from ..config import get_symbol_spec

                    _spec = get_symbol_spec(symbol)
                    _pip = _spec.pip_size

                    ms = MarketStructureAnalyzer().analyze(m15_window)
                    analysis_data["market_structure"] = {
                        "trend": ms.trend.value,
                        "structure_breaks": len(ms.structure_breaks),
                        "break_details": [
                            {"type": sb.type.value, "price": float(sb.price)}
                            for sb in ms.structure_breaks[-5:]
                        ] if ms.structure_breaks else [],
                        "swing_highs": [float(sh.price) for sh in ms.swing_highs[-5:]],
                        "swing_lows": [float(sl_.price) for sl_ in ms.swing_lows[-5:]],
                    }

                    fvg = FVGDetector(pip_value=_pip).detect(m15_window)
                    analysis_data["fvg"] = {
                        "bullish": len(fvg.bullish_fvgs),
                        "bearish": len(fvg.bearish_fvgs),
                        "active": len(fvg.active_fvgs),
                        "bullish_zones": [{"high": float(f.top), "low": float(f.bottom)} for f in fvg.bullish_fvgs[-3:]],
                        "bearish_zones": [{"high": float(f.top), "low": float(f.bottom)} for f in fvg.bearish_fvgs[-3:]],
                    }

                    ob = OrderBlockDetector().detect(m15_window)
                    analysis_data["order_blocks"] = {
                        "bullish": len(ob.bullish_obs),
                        "bearish": len(ob.bearish_obs),
                        "bullish_zones": [{"high": float(o.high), "low": float(o.low)} for o in ob.bullish_obs[-3:]],
                        "bearish_zones": [{"high": float(o.high), "low": float(o.low)} for o in ob.bearish_obs[-3:]],
                    }

                    liq = LiquidityMapper(pip_value=_pip).analyze(m15_window, current_price)
                    analysis_data["liquidity"] = {
                        "nearest_bsl": float(liq.nearest_bsl) if liq.nearest_bsl else None,
                        "nearest_ssl": float(liq.nearest_ssl) if liq.nearest_ssl else None,
                        "all_bsl": [float(p.price) for p in liq.bsl_pools[-5:]],
                        "all_ssl": [float(p.price) for p in liq.ssl_pools[-5:]],
                        "equal_highs": [float(eh.price) for eh in liq.equal_highs[-3:]] if liq.equal_highs else [],
                        "equal_lows": [float(el.price) for el in liq.equal_lows[-3:]] if liq.equal_lows else [],
                    }

                    _be = BarExtremeZoneDetector()
                    _be_m15 = _be.detect(m15_window, current_price, 'M15')
                    if _be_m15.supply_zone:
                        _bar_extreme_zones.append({"top": _be_m15.supply_zone.top, "bottom": _be_m15.supply_zone.bottom, "type": "supply", "tf": "M15"})
                    if _be_m15.demand_zone:
                        _bar_extreme_zones.append({"top": _be_m15.demand_zone.top, "bottom": _be_m15.demand_zone.bottom, "type": "demand", "tf": "M15"})

                    # Chart overlays for M15 panel
                    _m15_overlays = sanitize_for_json({
                        "order_blocks": (
                            [{"top": float(o.high), "bottom": float(o.low), "type": "bullish"} for o in ob.bullish_obs[-5:]]
                            + [{"top": float(o.high), "bottom": float(o.low), "type": "bearish"} for o in ob.bearish_obs[-5:]]
                        ),
                        "fvg_zones": (
                            [{"top": float(f.top), "bottom": float(f.bottom), "type": "bullish"} for f in fvg.bullish_fvgs[-5:]]
                            + [{"top": float(f.top), "bottom": float(f.bottom), "type": "bearish"} for f in fvg.bearish_fvgs[-5:]]
                        ),
                        "liquidity_levels": (
                            [{"price": float(p.price), "label": "BSL", "color": "purple"} for p in liq.bsl_pools[-5:]]
                            + [{"price": float(p.price), "label": "SSL", "color": "purple"} for p in liq.ssl_pools[-5:]]
                        ),
                        "swing_points": (
                            [{"price": float(sh.price), "type": "high", "index": int(sh.index)} for sh in ms.swing_highs[-8:]]
                            + [{"price": float(sl_.price), "type": "low", "index": int(sl_.index)} for sl_ in ms.swing_lows[-8:]]
                        ),
                    })
                except Exception as _analysis_err:
                    logger.debug(f"[REPLAY] Analysis enrichment failed: {_analysis_err}")
                    _m15_overlays = {}

                _pd_h1_result = None
                if h1_data is not None and not h1_data.empty:
                    h1_window = h1_data[h1_data.index <= window_end].tail(100)
                    if len(h1_window) >= 20:
                        try:
                            from ..analysis.market_structure import MarketStructureAnalyzer as _MSA
                            from ..analysis.bar_extreme_zones import BarExtremeZoneDetector as _BED
                            h1_ms = _MSA().analyze(h1_window)
                            analysis_data["h1_bias"] = h1_ms.trend.value
                            h1_be = _BED().detect(h1_window, current_price, 'H1')
                            if h1_be.supply_zone:
                                _bar_extreme_zones.append({"top": h1_be.supply_zone.top, "bottom": h1_be.supply_zone.bottom, "type": "supply", "tf": "H1"})
                            if h1_be.demand_zone:
                                _bar_extreme_zones.append({"top": h1_be.demand_zone.top, "bottom": h1_be.demand_zone.bottom, "type": "demand", "tf": "H1"})
                        except Exception as e:
                            logger.debug(f"[REPLAY] H1 bar-extreme/structure analysis failed: {e}")
                        try:
                            from ..analysis.premium_discount import PremiumDiscountAnalyzer as _PDA_H1
                            _pd_h1_result = _PDA_H1(swing_lookback=20).analyze(h1_window, current_price=current_price)
                            analysis_data["h1_premium_discount"] = _pd_h1_result.to_dict()
                        except Exception as e:
                            logger.debug(f"[REPLAY] H1 premium/discount analysis failed: {e}")

                _pd_d1_result = None
                if d1_data is not None and not d1_data.empty:
                    d1_window = d1_data[d1_data.index <= window_end].tail(60)
                    if len(d1_window) >= 10:
                        try:
                            from ..analysis.market_structure import MarketStructureAnalyzer as _MSA2
                            d1_ms = _MSA2().analyze(d1_window)
                            analysis_data["d1_bias"] = d1_ms.trend.value
                        except Exception as e:
                            logger.debug(f"[REPLAY] D1 market structure analysis failed: {e}")
                        try:
                            from ..analysis.premium_discount import PremiumDiscountAnalyzer as _PDA
                            _pd_d1_result = _PDA(swing_lookback=20).analyze(d1_window, current_price=current_price)
                            analysis_data["premium_discount"] = _pd_d1_result.to_dict()
                        except Exception as e:
                            logger.debug(f"[REPLAY] D1 premium/discount analysis failed: {e}")

                # --- Generate composite chart (M15 + H1 if available) ---
                from ..utils.chart_screenshot import create_composite_chart, create_simple_chart
                chart_panels = [{"timeframe": "M15", "df": m15_window, "overlays": _m15_overlays}]
                if h1_data is not None and not h1_data.empty:
                    h1_chart_win = h1_data[h1_data.index <= window_end].tail(100)
                    if len(h1_chart_win) >= 10:
                        chart_panels.insert(0, {"timeframe": "H1", "df": h1_chart_win})

                try:
                    chart_b64 = await asyncio.to_thread(
                        create_composite_chart, chart_panels, symbol,
                        bar_extreme_zones=_bar_extreme_zones if _bar_extreme_zones else None,
                    )
                except Exception as e:
                    logger.debug(f"[REPLAY] Composite chart failed, falling back to simple: {e}")
                    chart_b64 = await asyncio.to_thread(
                        create_simple_chart, m15_window, symbol, 'M15'
                    )

                if not chart_b64:
                    current += timedelta(hours=interval_hours)
                    continue

                strategy_ctx = context_builder.get_ict_context() if context_builder else ""

                market_data: Dict[str, Any] = {
                    'current_price': current_price,
                    'session': snapshot_session,
                    'atr_14': round(atr_val, 6),
                    'atr_min_sl': round(atr_val * 1.5, 6),
                }
                if setup_playbook:
                    market_data['setup_playbook'] = setup_playbook
                if self._learning_service:
                    try:
                        lctx = await self._learning_service.build_context_for_claude(symbol, snapshot_session)
                        if lctx:
                            market_data['learning_context'] = lctx
                    except Exception as e:
                        logger.debug(f"[REPLAY] Learning context build failed: {e}")

                if _be_m15:
                    market_data['bar_extreme_m15'] = _be_m15.to_dict()

                if _pd_d1_result:
                    market_data['premium_discount'] = sanitize_for_json(_pd_d1_result.to_dict())
                    market_data['fibonacci_zone'] = _pd_d1_result.current_zone.value
                    market_data['in_ote'] = bool(_pd_d1_result.in_ote)
                if _pd_h1_result:
                    market_data['h1_premium_discount'] = sanitize_for_json(_pd_h1_result.to_dict())

                claude_result = await self._claude.analyze_chart_async(
                    chart_image_base64=chart_b64,
                    symbol=symbol,
                    timeframe='M15',
                    strategy_context=strategy_ctx,
                    market_data=market_data,
                    analysis_data=analysis_data if analysis_data else None,
                )

                sig = claude_result.signal
                _zone_gate_decision = "no_gate"
                if sig.direction != 'no_trade' and sig.entry_price and sig.stop_loss and sig.take_profit:
                    from ..config import settings as _bt_settings

                    _sl_dist_bt = abs(sig.entry_price - sig.stop_loss)
                    _tp_dist_bt = abs(sig.take_profit - sig.entry_price)
                    _rr_bt = _tp_dist_bt / _sl_dist_bt if _sl_dist_bt > 0 else 0
                    _d1_bias_bt = (analysis_data.get('d1_bias') or '').lower()
                    _gate_mode = _bt_settings.trading.zone_gate_mode
                    _gate_blocked = False

                    # --- Zone-aware gate (replaces legacy D1 direction gate) ---
                    _use_zone_gate = (
                        _pd_d1_result is not None
                        and _gate_mode in ('active', 'shadow')
                        and symbol not in _bt_settings.trading.zone_gate_disabled_symbols
                    )

                    if _use_zone_gate:
                        _zone_str = _pd_d1_result.current_zone.value
                        _retrace = _pd_d1_result.retracement_percent
                        _zone_aligned = (
                            (sig.direction == 'short' and _retrace >= 0.5)
                            or (sig.direction == 'long' and _retrace <= 0.5)
                        )
                        _zone_misaligned = (
                            (sig.direction == 'long' and _retrace >= 0.618)
                            or (sig.direction == 'short' and _retrace <= 0.382)
                        )

                        if _zone_misaligned:
                            _zm_conf = _bt_settings.trading.zone_misaligned_min_confidence
                            _zm_rr = _bt_settings.trading.zone_misaligned_min_rr
                            if sig.confidence < _zm_conf or _rr_bt < _zm_rr:
                                _gate_blocked = True
                                _zone_gate_decision = "blocked_misaligned"
                                _block_msg = (
                                    f"ZONE-GATE blocked {sig.direction.upper()} from {_zone_str} "
                                    f"(retrace={_retrace:.0%}, conf={sig.confidence:.0%}, RR={_rr_bt:.1f}, "
                                    f"need {_zm_conf:.0%}/{_zm_rr:.0f}:1)"
                                )
                            else:
                                _zone_gate_decision = "allowed_misaligned_high_conf"
                        elif not _zone_aligned:
                            _eq_conf = _bt_settings.trading.zone_equilibrium_min_confidence
                            if sig.confidence < _eq_conf:
                                _gate_blocked = True
                                _zone_gate_decision = "blocked_equilibrium"
                                _block_msg = (
                                    f"ZONE-GATE blocked {sig.direction.upper()} from {_zone_str} "
                                    f"(equilibrium, conf={sig.confidence:.0%}, need {_eq_conf:.0%})"
                                )
                            else:
                                _zone_gate_decision = "allowed_equilibrium"
                        else:
                            _zone_gate_decision = "allowed_zone_aligned"

                        if _gate_blocked and _gate_mode == 'shadow':
                            logger.info(
                                f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                                f"ZONE-GATE-SHADOW: would block — {_block_msg}"
                            )
                            _gate_blocked = False
                            _zone_gate_decision = f"shadow_{_zone_gate_decision}"

                        if _gate_blocked:
                            logger.info(f"[REPLAY] {current.strftime('%m/%d %H:%M')} {_block_msg}")
                            if progress_callback:
                                try:
                                    await progress_callback(
                                        pct,
                                        f"Zone-blocked {sig.direction} from {_zone_str} @ {current.strftime('%m/%d %H:%M')}"
                                    )
                                except Exception as e:
                                    logger.debug(f"[REPLAY] Progress callback error: {e}")
                            signals_processed += 1
                            current += timedelta(hours=interval_hours)
                            step_idx += 1
                            continue

                        if _zone_gate_decision.startswith("allowed"):
                            logger.info(
                                f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                                f"ZONE-GATE: {_zone_gate_decision} {sig.direction.upper()} "
                                f"from {_zone_str} (retrace={_retrace:.0%})"
                            )

                    elif _gate_mode != 'active' or not _use_zone_gate:
                        # Legacy D1 direction gate fallback
                        _is_counter_bt = (
                            _d1_bias_bt in ('bullish', 'bearish')
                            and (
                                (_d1_bias_bt == 'bullish' and sig.direction == 'short')
                                or (_d1_bias_bt == 'bearish' and sig.direction == 'long')
                            )
                        )
                        if _is_counter_bt and (sig.confidence < 0.70 or _rr_bt < 3.0):
                            logger.info(
                                f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                                f"DIRECTION-GATE blocked {sig.direction.upper()} "
                                f"(D1={_d1_bias_bt}, conf={sig.confidence:.0%}, RR={_rr_bt:.1f})"
                            )
                            _zone_gate_decision = "legacy_blocked"
                            if progress_callback:
                                try:
                                    await progress_callback(
                                        pct,
                                        f"Blocked counter-trend {sig.direction} @ {current.strftime('%m/%d %H:%M')}"
                                    )
                                except Exception as e:
                                    logger.debug(f"[REPLAY] Progress callback error: {e}")
                            signals_processed += 1
                            current += timedelta(hours=interval_hours)
                            step_idx += 1
                            continue
                        _zone_gate_decision = "legacy_allowed"

                    # --- Time-of-day gate: skip weak hours unless elevated confidence ---
                    _weak_hrs = _bt_settings.trading.weak_hours_by_symbol.get(symbol, [])
                    if current.hour in _weak_hrs and sig.confidence < 0.68:
                        logger.info(
                            f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                            f"TOD-GATE blocked {sig.direction.upper()} "
                            f"(weak hour {current.hour:02d}:00, conf={sig.confidence:.0%})"
                        )
                        if progress_callback:
                            try:
                                await progress_callback(
                                    pct,
                                    f"Blocked weak hour {sig.direction} @ {current.strftime('%m/%d %H:%M')}"
                                )
                            except Exception as e:
                                logger.debug(f"[REPLAY] Progress callback error: {e}")
                        signals_processed += 1
                        current += timedelta(hours=interval_hours)
                        step_idx += 1
                        continue

                    replay_sig = ReplaySignal(
                        timestamp=current,
                        symbol=symbol,
                        direction=sig.direction,
                        confidence=sig.confidence,
                        entry_price=sig.entry_price,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        reasoning=sig.reasoning or "",
                        trade_type=getattr(sig, 'trade_type', 'intraday'),
                        market_structure=sig.market_structure or 'unknown',
                    )
                    result.total_signals += 1

                    future = m15_data[m15_data.index > window_end].head(200)
                    trade = _simulate_trade(replay_sig, future)
                    result.trades.append(trade)
                    result.total_trades += 1

                    if trade.outcome == 'win':
                        result.wins += 1
                    elif trade.outcome == 'loss':
                        result.losses += 1
                    else:
                        result.timeouts += 1

                    wr = result.wins / result.total_trades * 100 if result.total_trades else 0
                    r_sum = sum(t.r_multiple for t in result.trades)
                    outcome_icon = "W" if trade.outcome == "win" else ("L" if trade.outcome == "loss" else "T")

                    logger.info(
                        f"[REPLAY] #{result.total_trades} {current.strftime('%m/%d %H:%M')} "
                        f"{sig.direction.upper()} {symbol} @ {sig.entry_price:.5f} "
                        f"SL={sig.stop_loss:.5f} TP={sig.take_profit:.5f} "
                        f"conf={sig.confidence:.0%} | "
                        f"{outcome_icon} R={trade.r_multiple:+.2f} bars={trade.bars_held} | "
                        f"Running: {result.wins}W/{result.losses}L/{result.timeouts}T "
                        f"WR={wr:.0f}% totalR={r_sum:+.1f}"
                    )

                    log_entry = sanitize_for_json({
                        "type": "trade",
                        "time": current.strftime("%m/%d %H:%M"),
                        "direction": sig.direction,
                        "confidence": round(float(sig.confidence), 2),
                        "entry": round(float(sig.entry_price), 5),
                        "sl": round(float(sig.stop_loss), 5),
                        "tp": round(float(sig.take_profit), 5),
                        "outcome": trade.outcome,
                        "r": round(float(trade.r_multiple), 2),
                        "bars": int(trade.bars_held),
                        "running_wr": round(float(wr), 1),
                        "running_r": round(float(r_sum), 2),
                        "trade_num": int(result.total_trades),
                        "zone": _pd_d1_result.current_zone.value if _pd_d1_result else "unknown",
                        "retracement_pct": round(float(_pd_d1_result.retracement_percent), 3) if _pd_d1_result else None,
                        "in_ote": bool(_pd_d1_result.in_ote) if _pd_d1_result else False,
                        "zone_gate_decision": _zone_gate_decision,
                    })
                    if progress_callback:
                        try:
                            await progress_callback(pct, step_desc, log_entry)
                        except Exception as e:
                            logger.debug(f"[REPLAY] Progress callback error: {e}")
                else:
                    logger.info(
                        f"[REPLAY] {current.strftime('%m/%d %H:%M')} No trade signal "
                        f"({snapshot_session} session, price={current_price:.5f})"
                    )
                    if progress_callback:
                        try:
                            await progress_callback(pct, f"No signal @ {current.strftime('%m/%d %H:%M')}")
                        except Exception as e:
                            logger.debug(f"[REPLAY] Progress callback error: {e}")

                signals_processed += 1

            except Exception as e:
                logger.warning(f"[REPLAY] Error at {current}: {e}")

            current += timedelta(hours=interval_hours)
            step_idx += 1

        # Compute aggregate metrics
        if result.total_trades > 0:
            result.win_rate = result.wins / result.total_trades * 100
            r_values = [t.r_multiple for t in result.trades]
            result.avg_r = float(np.mean(r_values))
            result.total_r = float(np.sum(r_values))

            # Drawdown
            cumulative = np.cumsum(r_values)
            peak = np.maximum.accumulate(cumulative)
            dd = cumulative - peak
            result.max_drawdown_r = float(np.min(dd)) if len(dd) > 0 else 0

            # Sharpe (R-based, annualized by actual trade frequency)
            if np.std(r_values, ddof=1) > 0:
                calendar_days = max((end_date - start_date).days, 1)
                trades_per_year = result.total_trades / calendar_days * 365
                annualization = np.sqrt(max(trades_per_year, 1))
                result.sharpe_ratio = float(
                    np.mean(r_values) / np.std(r_values, ddof=1) * annualization
                )

            # Profit factor
            gross_profit = sum(t.r_multiple for t in result.trades if t.r_multiple > 0)
            gross_loss = abs(sum(t.r_multiple for t in result.trades if t.r_multiple < 0))
            result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        result.estimated_cost = result.api_calls * self.COST_PER_CALL
        result.duration_seconds = time.time() - start_time

        logger.info("=" * 70)
        logger.info(f"[REPLAY] {symbol} BACKTEST COMPLETE")
        logger.info(
            f"[REPLAY] Trades: {result.total_trades} "
            f"({result.wins}W / {result.losses}L / {result.timeouts}T)"
        )
        logger.info(f"[REPLAY] Win Rate: {result.win_rate:.1f}%")
        logger.info(f"[REPLAY] Avg R: {result.avg_r:+.2f} | Total R: {result.total_r:+.1f}")
        logger.info(f"[REPLAY] Sharpe: {result.sharpe_ratio:.2f} | Profit Factor: {result.profit_factor:.2f}")
        logger.info(f"[REPLAY] Max Drawdown: {result.max_drawdown_r:.2f}R")
        logger.info(f"[REPLAY] API calls: {result.api_calls} | Cost: ${result.estimated_cost:.2f}")
        logger.info(f"[REPLAY] Duration: {result.duration_seconds:.0f}s")
        logger.info("=" * 70)

        return result
