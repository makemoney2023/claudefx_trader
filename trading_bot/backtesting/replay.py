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
from typing import Optional, List, Dict, Any, Callable, Tuple

import pandas as pd
import numpy as np

from ..utils.logging import get_logger
from ..utils.candle_utils import calculate_atr
from ..utils.json_helpers import sanitize_for_json
from .replay_simulation import ReplaySignal, ReplayTrade, simulate_raw_trade as _simulate_trade

logger = get_logger(__name__)

DEFAULT_CRYPTO_SYMBOLS = (
    "BTCUSD",
    "ETHUSD",
    "XRPUSD",
    "ADAUSD",
    "LTCUSD",
    "DOGEUSD",
    "SOLUSD",
    "DOTUSD",
    "EOSUSD",
    "NEOUSD",
    "ETCUSD",
    "XMRUSD",
    "ZECUSD",
    "DASHUSD",
    "IOTAUSD",
    "BITUSD",
    "USDTUSD",
)


def align_timestamp_to_index(value: datetime, index) -> pd.Timestamp:
    """Align a datetime to a DatetimeIndex timezone for safe comparisons.

    MT5 history frames are often UTC-aware; dashboard/API start/end dates are
    usually tz-naive. Comparing them directly raises TypeError on modern pandas.
    """
    ts = pd.Timestamp(value)
    idx_tz = getattr(index, "tz", None)
    if idx_tz is not None:
        if ts.tzinfo is None:
            return ts.tz_localize(idx_tz)
        return ts.tz_convert(idx_tz)
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC").tz_localize(None)
    return ts


@dataclass
class GateFixtureComparison:
    """Live phased vs replay one-shot gate outcome for one fixture."""

    name: str
    live_blocked: bool
    replay_blocked: bool
    live_gate_id: str
    replay_gate_id: str
    live_gate_path: List[str]
    replay_gate_path: List[str]

    @property
    def paths_match(self) -> bool:
        return (
            self.live_blocked == self.replay_blocked
            and self.live_gate_id == self.replay_gate_id
            and self.live_gate_path == self.replay_gate_path
        )


def run_phased_live_gates(inp) -> Any:
    """Mirror live analyze_and_trade_runner phased post-Claude gate calls."""
    from ..services.post_claude_gates import run_post_claude_gates

    price = run_post_claude_gates(inp, stop_after="price")
    if price.blocked:
        return price
    entry = run_post_claude_gates(
        inp,
        start_at="entry",
        stop_after="entry",
        gate_path=price.gate_path,
        carry=price,
    )
    if entry.blocked:
        return entry
    return run_post_claude_gates(
        inp,
        start_at="permission",
        stop_after="complete",
        ctx=entry.pipeline_ctx,
        gate_path=entry.gate_path,
        carry=entry,
    )


def compare_gate_fixture_batch(
    fixtures: List[Tuple[str, Any]],
    *,
    kill_zone_checker=None,
) -> Dict[str, Any]:
    """
    Run a batch of PostClaudeGateInput fixtures through live-phased and replay paths.

    Returns aggregate block rates and per-fixture parity for replay validation.
    """
    from ..services.post_claude_gates import run_post_claude_gates

    comparisons: List[GateFixtureComparison] = []
    for name, inp in fixtures:
        replay = run_post_claude_gates(
            inp, kill_zone_checker=kill_zone_checker, stop_after="complete"
        )
        live = run_phased_live_gates(inp)
        comparisons.append(
            GateFixtureComparison(
                name=name,
                live_blocked=live.blocked,
                replay_blocked=replay.blocked,
                live_gate_id=live.gate_id,
                replay_gate_id=replay.gate_id,
                live_gate_path=list(live.gate_path),
                replay_gate_path=list(replay.gate_path),
            )
        )

    total = len(comparisons)
    live_blocks = sum(1 for c in comparisons if c.live_blocked)
    replay_blocks = sum(1 for c in comparisons if c.replay_blocked)
    mismatches = [c for c in comparisons if not c.paths_match]
    return {
        "total": total,
        "live_block_rate": live_blocks / total if total else 0.0,
        "replay_block_rate": replay_blocks / total if total else 0.0,
        "parity_matches": total - len(mismatches),
        "mismatches": mismatches,
        "comparisons": comparisons,
    }


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
    execution_policy_trades: int = 0
    execution_policy_total_r: float = 0.0
    strategy_total_r: float = 0.0

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
            'execution_policy_trades': self.execution_policy_trades,
            'execution_policy_total_r': round(self.execution_policy_total_r, 2),
            'strategy_total_r': round(self.strategy_total_r, 2),
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


class ClaudeReplayBacktester:
    """
    Replay historical charts through Claude's analysis pipeline.

    Usage:
        bt = ClaudeReplayBacktester(claude_client, mt5_client)
        result = await bt.run("XAUUSD", start, end, interval_hours=1)
    """

    # Approximate cost per API call (Opus 5 with images)
    # Opus 5 medium analysis with prompt cache: ~$0.11–0.35 healthy, up to ~$0.90 if
    # thinking fills the output budget. Midpoint for UI estimate (not a hard cap).
    COST_PER_CALL = 0.25

    def __init__(
        self,
        claude_client=None,
        mt5_client=None,
        trade_learning_service=None,
        *,
        auto_approve_judge: bool = False,
        invoke_judge: bool = False,
        replay_account_equity: float = 2000.0,
        scaling_manager=None,
        correlation_service=None,
        news_service=None,
        replay_daily_trades: int = 0,
        crypto_symbols: Optional[Tuple[str, ...]] = None,
    ):
        self._claude = claude_client
        self._data_loader = HistoricalDataLoader(mt5_client)
        self._chart_gen = None
        self._learning_service = trade_learning_service
        self._auto_approve_judge = auto_approve_judge
        self._invoke_judge = invoke_judge
        self._replay_account_equity = replay_account_equity
        self._scaling_manager = scaling_manager
        self._correlation_service = correlation_service
        self._news_service = news_service
        self._replay_daily_trades = replay_daily_trades
        self._crypto_symbols = crypto_symbols or DEFAULT_CRYPTO_SYMBOLS
        from ..services.trade_judge import JudgeOutcome, JudgeVerdict

        if auto_approve_judge:
            self._default_judge_outcome = JudgeOutcome(
                verdict=JudgeVerdict.APPROVE,
                reason="replay baseline auto-approve (explicit opt-in)",
            )
        else:
            self._default_judge_outcome = JudgeOutcome(
                verdict=JudgeVerdict.REJECT,
                reason="replay requires judge invocation (no auto-approve)",
            )

    def _replay_correlation_check(
        self, symbol: str, direction: str
    ) -> Tuple[bool, str]:
        if self._correlation_service is None:
            return False, ""
        return self._correlation_service.should_block_trade(
            symbol, direction=direction
        )

    def should_skip_for_news(self, symbol: str) -> Tuple[bool, str]:
        """Mirror live cycle news blackout / fail-closed calendar checks."""
        if self._news_service is None:
            return False, ""
        is_blackout, reason = self._news_service.is_blackout_period()
        if is_blackout and symbol not in self._crypto_symbols:
            return True, f"news_blackout:{reason}"
        if getattr(self._news_service, "is_calendar_unreliable", lambda: False)():
            return True, "news_calendar_stale"
        from ..services.live_trade_gates import news_allows_trading

        allowed, fail_reason = news_allows_trading(self._news_service)
        if not allowed:
            return True, fail_reason
        return False, ""

    def build_post_claude_gate_input(
        self,
        *,
        symbol: str,
        trade_signal,
        norm,
        market_data: Dict[str, Any],
        analysis_results: Dict[str, Any],
        pd_analysis,
        current_price: float,
        df: pd.DataFrame,
        snapshot_time: datetime,
        zone_settings,
        use_zone_gate: bool,
        last_signal_direction: Dict[str, Dict[str, Any]],
        direction_flipped: bool,
        session_name: str = "",
        is_kill_zone: bool = False,
    ):
        """Build PostClaudeGateInput with optional live parity services."""
        from ..services.post_claude_gates import (
            PostClaudeGateInput,
            SecondaryModifierInput,
        )

        correlation_check: Optional[Callable[[], Tuple[bool, str]]] = None
        if self._correlation_service is not None:
            direction = getattr(trade_signal, "direction", "")

            def _correlation_check(
                _symbol=symbol, _direction=direction
            ) -> Tuple[bool, str]:
                return self._replay_correlation_check(_symbol, _direction)

            correlation_check = _correlation_check

        scaling_aggressive = (
            self._scaling_manager is not None
            and getattr(self._scaling_manager.current_mode, "value", "") == "aggressive"
        )
        return PostClaudeGateInput(
            symbol=symbol,
            trade_signal=trade_signal,
            norm=norm,
            market_data=market_data,
            analysis_results=analysis_results or {},
            pd_analysis=pd_analysis,
            current_price=current_price,
            df=df,
            snapshot_time=snapshot_time,
            zone_settings=zone_settings,
            use_zone_gate=use_zone_gate,
            last_signal_direction=last_signal_direction,
            direction_flipped=direction_flipped,
            apply_secondary_modifiers=True,
            modifier_input=SecondaryModifierInput(),
            scaling_manager=self._scaling_manager,
            daily_trades=self._replay_daily_trades,
            scaling_aggressive=scaling_aggressive,
            correlation_check=correlation_check,
            session_name=session_name,
            is_kill_zone=is_kill_zone,
        )

    async def invoke_judge_for_signal(
        self,
        symbol: str,
        trade_signal,
        *,
        current_price: float,
        session_name: str = "",
    ):
        """Run the live fail-closed judge adapter during replay."""
        from ..services.trade_judge import run_replay_trade_judge

        if not self._invoke_judge:
            return self._default_judge_outcome

        return await run_replay_trade_judge(
            self._claude,
            symbol,
            trade_signal,
            current_price,
            session_name=session_name,
            account_equity=self._replay_account_equity,
            learning_service=self._learning_service,
        )

    def _evaluate_replay_signal(
        self,
        replay_sig: ReplaySignal,
        future_data: pd.DataFrame,
        *,
        current_price: float,
        pip_size: float = 0.0001,
        judge_outcome=None,
    ):
        """Run one replay signal through raw strategy + execution policy pipelines."""
        from .execution_policy import run_policy_replay

        strategy_trade = _simulate_trade(replay_sig, future_data, pip_size=pip_size)
        policy_result = run_policy_replay(
            replay_sig,
            future_data,
            judge_outcome or self._default_judge_outcome,
            current_price=current_price,
            pip_size=pip_size,
        )
        return strategy_trade, policy_result

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
        _last_signal_for_symbol: Dict[str, Dict[str, Any]] = {}
        self._replay_last_signal_direction: Dict[str, Any] = {}

        while current <= end_date and signals_processed < max_signals:
            if current.weekday() >= 5 or (current.weekday() == 4 and current.hour >= 19):
                current += timedelta(hours=interval_hours)
                step_idx += 1
                continue

            window_end = align_timestamp_to_index(current, m15_data.index)
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
                _pip = 0.0001
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
                    _last_m15_break = ms.structure_breaks[-1] if ms.structure_breaks else None
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
                    analysis_data["m15_structure"] = (
                        f"{_last_m15_break.type.value.upper()} at {float(_last_m15_break.price):.5f}"
                        if _last_m15_break else "No recent breaks"
                    )
                    analysis_data["m15_trend"] = ms.trend.value

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
                    h1_cut = align_timestamp_to_index(current, h1_data.index)
                    h1_window = h1_data[h1_data.index <= h1_cut].tail(100)
                    if len(h1_window) >= 20:
                        try:
                            from ..analysis.market_structure import MarketStructureAnalyzer as _MSA
                            from ..analysis.bar_extreme_zones import BarExtremeZoneDetector as _BED
                            h1_ms = _MSA().analyze(h1_window)
                            analysis_data["h1_bias"] = h1_ms.trend.value
                            _last_h1_break = h1_ms.structure_breaks[-1] if h1_ms.structure_breaks else None
                            analysis_data["h1_structure"] = (
                                f"{_last_h1_break.type.value.upper()} at {float(_last_h1_break.price):.5f}"
                                if _last_h1_break else "No recent breaks"
                            )
                            analysis_data["h1_trend"] = h1_ms.trend.value
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
                    d1_cut = align_timestamp_to_index(current, d1_data.index)
                    d1_window = d1_data[d1_data.index <= d1_cut].tail(60)
                    if len(d1_window) >= 10:
                        try:
                            from ..analysis.market_structure import MarketStructureAnalyzer as _MSA2
                            d1_ms = _MSA2().analyze(d1_window)
                            analysis_data["d1_bias"] = d1_ms.trend.value
                            _last_d1_break = d1_ms.structure_breaks[-1] if d1_ms.structure_breaks else None
                            analysis_data["d1_structure"] = (
                                f"{_last_d1_break.type.value.upper()} at {float(_last_d1_break.price):.5f}"
                                if _last_d1_break else "No recent breaks"
                            )
                            analysis_data["d1_trend"] = d1_ms.trend.value
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
                    h1_chart_win = h1_data[
                        h1_data.index <= align_timestamp_to_index(current, h1_data.index)
                    ].tail(100)
                    if len(h1_chart_win) >= 10:
                        chart_panels.insert(0, {"timeframe": "H1", "df": h1_chart_win})

                logger.info(
                    f"[REPLAY] {current.strftime('%m/%d %H:%M')} rendering chart "
                    f"({len(chart_panels)} panels)..."
                )
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
                    logger.warning(
                        f"[REPLAY] {current.strftime('%m/%d %H:%M')} chart render returned empty — skipping"
                    )
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

                # Enrich market_data with HTF/LTF context so Claude prompt
                # includes the Multi-Timeframe Analysis and LTF Execution sections
                _d1_b = analysis_data.get('d1_bias', '')
                _h1_b = analysis_data.get('h1_bias', '')
                _m15_b = analysis_data.get('market_structure', {}).get('trend', '')
                if _d1_b:
                    market_data['d1_bias'] = _d1_b
                    market_data['htf_bias'] = _d1_b
                    market_data['d1_structure'] = analysis_data.get('d1_structure')
                    market_data['d1_trend'] = analysis_data.get('d1_trend')
                if _h1_b:
                    market_data['h1_bias'] = _h1_b
                    market_data['h1_structure'] = analysis_data.get('h1_structure')
                    market_data['h1_trend'] = analysis_data.get('h1_trend')
                if _m15_b:
                    market_data['m15_bias'] = _m15_b
                    market_data['m15_structure'] = analysis_data.get('m15_structure')
                    market_data['m15_trend'] = analysis_data.get('m15_trend')
                if _d1_b and _h1_b:
                    market_data['htf_alignment'] = (_d1_b == _h1_b)
                    market_data['htf_can_trade_long'] = (
                        'preferred' if _d1_b == 'bullish'
                        else ('counter_trend' if _d1_b == 'bearish' else 'no_data')
                    )
                    market_data['htf_can_trade_short'] = (
                        'preferred' if _d1_b == 'bearish'
                        else ('counter_trend' if _d1_b == 'bullish' else 'no_data')
                    )

                if symbol in _last_signal_for_symbol:
                    market_data['last_signal'] = _last_signal_for_symbol[symbol]

                logger.info(
                    f"[REPLAY] {current.strftime('%m/%d %H:%M')} calling Claude "
                    f"(session={snapshot_session}, price={current_price:.5f})..."
                )
                claude_result = await self._claude.analyze_chart_async(
                    chart_image_base64=chart_b64,
                    symbol=symbol,
                    timeframe='M15',
                    strategy_context=strategy_ctx,
                    market_data=market_data,
                    analysis_data=analysis_data if analysis_data else None,
                )
                logger.info(
                    f"[REPLAY] {current.strftime('%m/%d %H:%M')} Claude returned "
                    f"{claude_result.signal.direction} conf={claude_result.signal.confidence:.0%}"
                )

                sig = claude_result.signal
                from ..services.signal_normalizer import normalize_signal_prices

                _norm = normalize_signal_prices(
                    sig,
                    claude_result,
                    current_price,
                    symbol,
                )
                if _norm.rejected:
                    logger.info(
                        f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                        f"NORMALIZER rejected {sig.direction.upper()}: {_norm.reject_reason}"
                    )
                    signals_processed += 1
                    current += timedelta(hours=interval_hours)
                    step_idx += 1
                    continue
                _news_skip, _news_reason = self.should_skip_for_news(symbol)
                if _news_skip:
                    logger.info(
                        f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                        f"NEWS blocked {symbol}: {_news_reason}"
                    )
                    signals_processed += 1
                    current += timedelta(hours=interval_hours)
                    step_idx += 1
                    continue
                _zone_gate_decision = "no_gate"
                if sig.direction != 'no_trade' and sig.entry_price and sig.stop_loss and sig.take_profit:
                    from ..config import settings as _bt_settings
                    from ..services.entry_gates import ZoneGateSettings, should_use_zone_gate
                    from ..services.post_claude_gates import run_post_claude_gates
                    from ..analysis.kill_zones import KillZoneChecker

                    _zone_settings = ZoneGateSettings(
                        gate_mode=_bt_settings.trading.zone_gate_mode,
                        misaligned_min_confidence=_bt_settings.trading.zone_misaligned_min_confidence,
                        misaligned_min_rr=_bt_settings.trading.zone_misaligned_min_rr,
                        equilibrium_min_confidence=_bt_settings.trading.zone_equilibrium_min_confidence,
                        disabled_symbols=tuple(_bt_settings.trading.zone_gate_disabled_symbols),
                    )
                    _use_zone = should_use_zone_gate(
                        _pd_d1_result is not None,
                        _zone_settings.gate_mode,
                        symbol,
                        _zone_settings.disabled_symbols,
                    )
                    _replay_last_dir: Dict[str, Dict[str, Any]] = getattr(
                        self, "_replay_last_signal_direction", {}
                    )
                    _kz = KillZoneChecker()
                    _session_name = _detect_session(current)
                    _pc_inp = self.build_post_claude_gate_input(
                        symbol=symbol,
                        trade_signal=sig,
                        norm=_norm,
                        market_data=market_data,
                        analysis_results=analysis_data or {},
                        pd_analysis=_pd_d1_result,
                        current_price=current_price,
                        df=m15_window,
                        snapshot_time=current,
                        zone_settings=_zone_settings,
                        use_zone_gate=_use_zone,
                        last_signal_direction=_replay_last_dir,
                        direction_flipped=_norm.direction_flipped,
                        session_name=_session_name,
                        is_kill_zone=_kz.is_kill_zone(current),
                    )
                    _gate_result = run_post_claude_gates(
                        _pc_inp,
                        kill_zone_checker=_kz,
                        stop_after="complete",
                    )
                    sig.confidence = _gate_result.confidence
                    _zone_gate_decision = (
                        _gate_result.gate_id if _gate_result.blocked else "allowed"
                    )
                    if _gate_result.blocked:
                        logger.info(
                            f"[REPLAY] {current.strftime('%m/%d %H:%M')} "
                            f"GATE blocked {sig.direction.upper()}: {_gate_result.reason}"
                        )
                        if progress_callback:
                            try:
                                await progress_callback(
                                    pct,
                                    f"Gate blocked {sig.direction} @ {current.strftime('%m/%d %H:%M')}"
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

                    _last_signal_for_symbol[symbol] = {
                        'direction': sig.direction,
                        'confidence': sig.confidence,
                        'entry_price': sig.entry_price,
                        'timestamp': current.isoformat(),
                    }
                    self._replay_last_signal_direction[symbol] = (
                        sig.direction,
                        current,
                    )

                    future = m15_data[m15_data.index > window_end].head(200)
                    _judge_outcome = await self.invoke_judge_for_signal(
                        symbol,
                        sig,
                        current_price=sig.entry_price or current_price,
                        session_name=snapshot_session,
                    )
                    if self._invoke_judge:
                        result.api_calls += 1
                    trade, policy_result = self._evaluate_replay_signal(
                        replay_sig,
                        future,
                        current_price=sig.entry_price or current_price,
                        pip_size=_pip,
                        judge_outcome=_judge_outcome,
                    )
                    result.trades.append(trade)
                    result.total_trades += 1
                    result.strategy_total_r += trade.r_multiple

                    if policy_result.execution_trade:
                        result.execution_policy_trades += 1
                        result.execution_policy_total_r += (
                            policy_result.execution_trade.r_multiple
                        )

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
                        "judge_verdict": policy_result.judge_verdict,
                        "gate_path": _gate_result.gate_path if _gate_result else [],
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


def replay_signal_with_policy(
    signal: ReplaySignal,
    future_data: pd.DataFrame,
    judge_outcome,
    *,
    current_price: float,
    pip_size: float = 0.0001,
):
    """Run a single signal through the shared execution policy pipeline."""
    from .execution_policy import run_policy_replay

    return run_policy_replay(
        signal,
        future_data,
        judge_outcome,
        current_price=current_price,
        pip_size=pip_size,
    )
