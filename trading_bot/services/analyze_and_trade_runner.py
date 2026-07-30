"""Analyze-and-trade pipeline runner — extracted from TradingBot._analyze_and_trade."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ..config import settings
from ..utils.logging import get_logger
from ..utils.win_optimization import (
    apply_demote_policy,
    build_confidence_decision,
    classify_a_plus,
    displacement_gate_action,
    ote_pullback_entry,
    rebase_sl_tp_for_new_entry,
)
from ..execution.scaling_position_sizer import SetupGrade
from ..services.live_trade_gates import (
    compute_booked_risk_percent,
    symbol_edge_allows_trading,
)
from ..services.scaling_manager import TradingMode
from ..services.confidence_modifiers import (
    SecondaryModifierContext,
    apply_secondary_modifiers,
    confidence_decision_to_dict,
)
from ..services.gate_pipeline import count_confluence
from ..services.signal_normalizer import normalize_signal_prices

try:
    from ..api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ..main import TradingBot

_PLAYBOOK_STATS_TTL_SECONDS = 3600


async def safe_persist_judge_signal(save_fn, **kwargs) -> None:
    """Persist judge signal without allowing DB/IO failures to abort execution."""
    try:
        await save_fn(**kwargs)
    except Exception as exc:
        symbol = kwargs.get("symbol", "?")
        logger.warning(f"Signal DB persist failed for {symbol}: {exc}")


async def _get_playbook_stats(bot: "TradingBot") -> list:
    """Structured setup stats for the playbook gate, cached for 1 hour."""
    try:
        if not getattr(bot, "learning_service", None):
            return []
        now = datetime.now(timezone.utc)
        cached_at = getattr(bot, "_playbook_stats_time", None)
        if (
            cached_at is not None
            and isinstance(cached_at, datetime)
            and (now - cached_at).total_seconds() < _PLAYBOOK_STATS_TTL_SECONDS
        ):
            return getattr(bot, "_playbook_stats_cache", []) or []
        stats = await bot.learning_service.get_setup_stats()
        bot._playbook_stats_cache = stats if isinstance(stats, list) else []
        bot._playbook_stats_time = now
        return bot._playbook_stats_cache
    except Exception as exc:
        logger.debug(f"Playbook stats unavailable: {exc}")
        return []


async def run_analyze_and_trade(bot: "TradingBot", symbol: str, is_crypto: bool = False) -> None:
    """Run full analyze-and-trade pipeline for one symbol."""
    pipeline = bot._trade_pipeline
    _trade_reservation = None
    _trade_reservation_context = None
    try:
        logger.info(f"Analyzing {symbol}...")
        
        # Update bot state
        if bot_state:
            bot_state.analyzing_symbol(symbol)

        # Hard-skip Claude outside ICT kill zones (belt-and-suspenders vs cycle gate)
        from ..analysis.kill_zones import claude_analysis_allowed
        if bot.kill_zone_checker is not None:
            _kz_session = bot.kill_zone_checker.get_current_session()
            if not claude_analysis_allowed(
                bool(getattr(_kz_session, "is_tradeable", False)),
                claude_kill_zone_only=settings.trading.claude_kill_zone_only,
            ):
                next_kz = getattr(_kz_session, "next_kill_zone", None) or "next kill zone"
                mins = getattr(_kz_session, "next_kill_zone_in_minutes", None)
                eta = f" in {mins}min" if mins is not None else ""
                logger.info(
                    f"[KZ-GATE] {symbol}: Outside KZ "
                    f"({getattr(_kz_session, 'session_name', 'unknown')}) — "
                    f"Claude skipped until {next_kz}{eta}"
                )
                if bot_state:
                    bot_state.symbol_complete(symbol, "outside_kill_zone")
                return
        
        # POST-LOSS COOLDOWN: Prevent revenge trading
        from ..utils.datetime_utils import as_utc
        cooldown_expiry = bot._symbol_loss_cooldowns.get(symbol)
        if cooldown_expiry:
            _now_utc = datetime.now(timezone.utc)
            _cooldown_utc = as_utc(cooldown_expiry)
            if _now_utc < _cooldown_utc:
                remaining = (_cooldown_utc - _now_utc).total_seconds() / 60
                logger.info(f"[LOSS-COOLDOWN] {symbol}: Skipping — {remaining:.0f}min cooldown remaining")
                if bot_state:
                    bot_state.symbol_complete(symbol, "loss_cooldown")
                from ..api.routes.activity import add_activity
                add_activity("symbol_cooldown_skip", f"{symbol}: Skipped — {remaining:.0f}min loss cooldown remaining", symbol=symbol, details={"remaining_minutes": round(remaining)})
                return
            else:
                # Cooldown expired — flag this symbol for higher confidence bar
                del bot._symbol_loss_cooldowns[symbol]
                if not hasattr(bot, '_post_cooldown_symbols'):
                    bot._post_cooldown_symbols = set()
                bot._post_cooldown_symbols.add(symbol)
        
        # VOLATILITY PAUSE: Block new entries during an active spike window
        _vol_pause = getattr(bot, "_volatility_pause_until", None)
        if isinstance(_vol_pause, datetime) and datetime.now(timezone.utc) < as_utc(_vol_pause):
            _vol_remaining = (as_utc(_vol_pause) - datetime.now(timezone.utc)).total_seconds() / 60
            logger.warning(
                f"[VOLATILITY] {symbol}: New entries paused — "
                f"{_vol_remaining:.0f}min remaining in spike window"
            )
            if bot_state:
                bot_state.symbol_complete(symbol, "volatility_pause")
            from ..api.routes.activity import add_activity
            add_activity(
                "volatility_entry_pause",
                f"{symbol}: Entry skipped — volatility pause ({_vol_remaining:.0f}min left)",
                symbol=symbol,
                details={"remaining_minutes": round(_vol_remaining)},
            )
            return
        
        # CRITICAL: Block dangerous pairs (BTC-quoted pairs have wrong contract values)
        if symbol.upper() in bot.BLOCKED_PAIRS or symbol.upper().endswith('BTC') or symbol.upper().endswith('BIT'):
            logger.error(f"🚫 BLOCKED: {symbol} is a BTC/BIT pair - contract value issues cause massive losses!")
            if bot_state:
                bot_state.error(symbol, f"BLOCKED: {symbol} is a dangerous BTC pair")
                bot_state.symbol_complete(symbol, "blocked_btc_pair")
            return

        # Edge-health symbol blocking (per-symbol/session win-rate tracking)
        if bot.scaling_manager and bot.kill_zone_checker:
            _edge_session = bot.kill_zone_checker.get_current_session()
            _edge_key = bot._session_key_for_edge(
                _edge_session.session_name if _edge_session else ""
            )
            _edge_ok, _edge_reason, _edge_mult = symbol_edge_allows_trading(
                bot.scaling_manager, symbol, _edge_key
            )
            if not _edge_ok:
                logger.warning(f"[EDGE-HEALTH] {symbol}/{_edge_key}: {_edge_reason}")
                from ..api.routes.activity import add_activity
                add_activity(
                    "edge_health_blocked",
                    f"{symbol}/{_edge_key}: {_edge_reason}",
                    symbol=symbol,
                    details={"session": _edge_key, "reason": _edge_reason},
                )
                if bot_state:
                    bot_state.symbol_complete(symbol, "edge_health_blocked")
                return
            if _edge_mult != 1.0:
                bot._edge_size_multiplier = _edge_mult
            else:
                bot._edge_size_multiplier = 1.0
        
        # Gap 55: Block trading in simulation mode unless explicitly allowed
        if bot.mt5_client.is_simulation:
            if not settings.trading.allow_simulation_trades:
                logger.warning(
                    f"Skipping trade execution for {symbol} - MT5 in simulation mode. "
                    f"Set TRADING_ALLOW_SIMULATION_TRADES=true to enable simulation trading."
                )
                # Still run analysis for dashboard display, but don't execute trades
                await bot._run_analysis_only(symbol)
                return
            else:
                logger.warning(f"SIMULATION MODE: Trade for {symbol} will be simulated (not real)")
        
        # Fetch OHLCV data (using config candle counts)
        if bot_state:
            bot_state.fetching_data(symbol)
        
        df = await bot.data_fetcher.get_ohlcv(
            symbol=symbol,
            timeframe=settings.timeframes.execution_tf,
            count=settings.timeframes.execution_tf_candles
        )
        
        # Gap 56: Verify data is real before trading
        if df is None or df.empty:
            logger.error(f"No real market data for {symbol} - cannot trade without data")
            if bot_state:
                bot_state.symbol_complete(symbol, "no_data")
            return
        
        # Run technical analysis
        if bot_state:
            bot_state.running_technical_analysis(symbol)
        
        # Core ICT analysis via shared orchestrator
        analysis_results = pipeline.analysis.run_core_analysis(symbol, df)
        
        # Volume bot_state telemetry
        try:
            volume_analysis = analysis_results.get("volume", {})
            _rel = volume_analysis.get("relative_volume", 1.0) if isinstance(volume_analysis, dict) else 1.0
            _trend = volume_analysis.get("volume_trend", "") if isinstance(volume_analysis, dict) else ""
            _spikes = len(volume_analysis.get("spike_bars", [])) if isinstance(volume_analysis, dict) else 0
            logger.info(f"Volume: {_rel:.1f}x avg, Trend: {_trend}, Spikes: {_spikes}")
            if bot_state and isinstance(volume_analysis, dict):
                bot_state.volume_analysis_complete(
                    symbol, _rel, _trend, _spikes, _rel < 0.5
                )
        except Exception as e:
            logger.warning(f"Volume telemetry error: {e}")
        
        # =============================================
        # EXPANDED ICT + INTELLIGENCE ANALYSIS
        # =============================================
        from .expanded_analysis import run_expanded_analysis

        _expanded = await run_expanded_analysis(
            bot, symbol=symbol, df=df, analysis_results=analysis_results
        )
        pd_analysis = _expanded.pd_analysis
        mtf_result = _expanded.mtf_result
        dxy_confirmation = _expanded.dxy_confirmation
        retail_contrarian = _expanded.retail_contrarian
        vix_risk_mode = _expanded.vix_risk_mode
        currency_strength_recommendation = _expanded.currency_strength_recommendation
        amd_state = _expanded.amd_state
        displacement_analysis = _expanded.displacement_analysis
        breaker_blocks = _expanded.breaker_blocks or []
        silver_bullet_ready = _expanded.silver_bullet_ready
        ipda_analysis = _expanded.ipda_analysis
        nwog_target = _expanded.nwog_target

        # Get current price
        current_price = float(df['close'].iloc[-1])

        # PRE-CLAUDE VIABILITY: skip the LLM (and chart generation) when the
        # mechanical gate stack already guarantees rejection of every direction.
        from ..utils.win_optimization import pre_claude_viability

        def _bias_of(tf_analysis) -> str:
            bias = getattr(tf_analysis, "bias", None)
            return getattr(bias, "value", None) or "unknown"

        _in_kill_zone = False
        if bot.kill_zone_checker is not None:
            _viab_session = bot.kill_zone_checker.get_current_session()
            _in_kill_zone = bool(getattr(_viab_session, "is_kill_zone", False))
        _sb_window = bool(
            (analysis_results.get("silver_bullet") or {}).get("window_active", False)
        )
        _amd_phase = (
            (analysis_results.get("amd_cycle") or {}).get("phase") or "unknown"
        )
        _rel_volume = 1.0
        _vol = analysis_results.get("volume")
        if isinstance(_vol, dict):
            _rel_volume = float(_vol.get("relative_volume", 1.0) or 1.0)

        if mtf_result is not None:
            _viability = pre_claude_viability(
                d1_bias=_bias_of(getattr(mtf_result, "daily_analysis", None)),
                h4_bias=_bias_of(getattr(mtf_result, "h4_analysis", None)),
                m15_bias=_bias_of(getattr(mtf_result, "m15_analysis", None)),
                amd_phase=_amd_phase,
                relative_volume=_rel_volume,
                in_kill_zone=_in_kill_zone,
                silver_bullet_window=_sb_window,
            )
            if not _viability.proceed:
                _skip_reason = "; ".join(_viability.reasons)
                logger.info(
                    f"[PRE-CLAUDE] {symbol}: analysis skipped — {_skip_reason}"
                )
                from ..api.routes.activity import add_activity
                add_activity(
                    "analysis_skipped",
                    f"{symbol}: Claude skipped — gates guarantee rejection "
                    f"({_skip_reason})",
                    symbol=symbol,
                    details={"reasons": _viability.reasons},
                )
                if bot_state:
                    bot_state.symbol_complete(symbol, "pre_claude_skip")
                return

        # Skip Claude analysis if not configured
        if not bot.claude_client or not bot.claude_client.api_key:
            logger.debug(f"Claude not configured, using technical analysis only for {symbol}")
            return
        
        # Generate initial chart (will be regenerated with overlays after analysis)
        chart_base64 = await bot._generate_chart_image(df, symbol)
        if not chart_base64:
            logger.warning(f"Failed to generate chart for {symbol}")
            return
        
        _chart_pkg = await pipeline.analysis.build_chart_package(
            bot,
            symbol=symbol,
            df=df,
            generate_chart_image=bot._generate_chart_image,
        )
        _mtf_dfs = _chart_pkg.mtf_dfs
        additional_charts = _chart_pkg.additional_charts
        _vp_data = _chart_pkg.vp_data
        _bar_extreme_results = _chart_pkg.bar_extreme_results
        _bar_extreme_zones = _chart_pkg.bar_extreme_zones

        # Mechanical ICT advisory: rule-based baseline for Claude (never executes)
        _mech_setup = bot._mechanical_setup_advisory(symbol, _mtf_dfs.get('H4'), df)
        if _mech_setup:
            analysis_results["mechanical_setup"] = _mech_setup
            logger.info(
                f"[MECH] {symbol}: Rule-based ICT setup found — "
                f"{_mech_setup.get('direction', '?').upper()} "
                f"(conf {_mech_setup.get('confidence', 0):.0%}, "
                f"R:R {_mech_setup.get('risk_reward', 0):.2f})"
            )
        
        _claude_out = await pipeline.claude().run_stage(
            bot,
            symbol=symbol,
            df=df,
            analysis_results=analysis_results,
            chart_base64=chart_base64,
            additional_charts=additional_charts,
            vp_data=_vp_data,
            bar_extreme_results=_bar_extreme_results,
            mtf_dfs=_mtf_dfs,
            pd_analysis=pd_analysis,
            mtf_result=mtf_result,
            dxy_confirmation=dxy_confirmation,
            retail_contrarian=retail_contrarian,
            vix_risk_mode=vix_risk_mode,
            currency_strength_recommendation=currency_strength_recommendation,
            current_price=current_price,
        )
        if _claude_out is None or _claude_out.stop_pipeline:
            return
        trade_signal = _claude_out.trade_signal
        claude_result = _claude_out.claude_result
        market_data = _claude_out.market_data
        analysis_data = _claude_out.analysis_data
        chart_base64 = _claude_out.chart_base64
        strategy_context = _claude_out.strategy_context
        account_info = _claude_out.account_info
        current_price = _claude_out.current_price

        # SIGNAL PRICE SANITY CHECKS (A5) — shared normalizer
        # ============================================
        _norm = normalize_signal_prices(
            trade_signal, claude_result, current_price, symbol
        )
        if _norm.rejected:
            logger.warning(f"SIGNAL REJECTED for {symbol}: {_norm.reject_reason}")
            if bot_state:
                bot_state.trade_decision(symbol, "rejected", _norm.reject_reason)
                bot_state.symbol_complete(symbol, "invalid_signal")
            await bot._record_terminal_decision(
                "mechanical_reject",
                symbol,
                gate_id="normalizer_reject",
                direction=getattr(trade_signal, "direction", "") or "",
                entry=trade_signal.entry_price or current_price,
                sl=trade_signal.stop_loss or 0.0,
                tp=trade_signal.take_profit or 0.0,
                confidence=trade_signal.confidence,
                reason=_norm.reject_reason or "Signal price checks failed",
            )
            return
        _entry = _norm.entry
        _sl = _norm.sl
        _tp = _norm.tp
        _dir = _norm.direction
        _direction_flipped = _norm.direction_flipped
        for _audit in _norm.audit_log:
            logger.info(f"[A5] {symbol}: {_audit}")
        logger.info(f"Signal price checks passed for {symbol}: Entry={_entry}, SL={_sl}, TP={_tp}")

        # ============================================
        # SHARED POST-CLAUDE GATES (live/replay parity)
        # ============================================
        from .post_claude_gates import (
            PostClaudeGateInput,
            build_reject_details,
            run_post_claude_gates,
        )

        _is_aggressive = (
            bot.scaling_manager is not None
            and bot.scaling_manager.current_mode.value == "aggressive"
        )
        _session_name, _is_kill = bot._session_for_gates()
        _pc_inp = PostClaudeGateInput(
            symbol=symbol,
            trade_signal=trade_signal,
            norm=_norm,
            market_data=market_data,
            analysis_results=analysis_results,
            pd_analysis=pd_analysis,
            current_price=current_price,
            is_crypto=is_crypto,
            is_aggressive=_is_aggressive,
            df=df,
            session_name=_session_name,
            is_kill_zone=_is_kill,
            build_pipeline_context=bot._build_pipeline_context,
            last_signal_direction=bot._last_signal_direction,
            direction_flipped=_direction_flipped,
            direction_loss_streak=(
                bot._direction_loss_tracker.consecutive_losses(
                    symbol,
                    getattr(_norm, "direction", "") or "",
                    datetime.now(timezone.utc),
                )
                if getattr(bot, "_direction_loss_tracker", None) is not None
                else 0
            ),
        )

        _price_gate = run_post_claude_gates(_pc_inp, stop_after="price")
        if _price_gate.blocked:
            logger.warning(f"[BLOCKED] {symbol}: {_price_gate.reason}")
            print(f"[BLOCKED] {symbol}: {_price_gate.reason}", flush=True)
            await bot._record_terminal_decision(
                "mechanical_reject",
                symbol,
                gate_id=_price_gate.gate_id,
                direction=_price_gate.direction,
                entry=_price_gate.entry,
                sl=_price_gate.sl,
                tp=_price_gate.tp,
                confidence=_price_gate.confidence,
                reason=_price_gate.reason,
                details=build_reject_details(
                    gate_path=_price_gate.gate_path,
                    direction=_price_gate.direction,
                    entry=_price_gate.entry,
                    sl=_price_gate.sl,
                    tp=_price_gate.tp,
                    confidence=_price_gate.confidence,
                ),
            )
            return

        _entry = _price_gate.entry
        _sl = _price_gate.sl
        _tp = _price_gate.tp
        _dir = _price_gate.direction
        actual_rr = _price_gate.actual_rr
        min_rr = _price_gate.min_rr
        _is_counter_trend_scalp = _price_gate.is_counter_trend_scalp

        if actual_rr < min_rr:
            logger.info(
                f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 below target {min_rr:.1f}:1 "
                f"but above hard floor — Trade Judge will decide."
            )
            print(
                f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 (target {min_rr:.1f}:1) — "
                f"borderline, letting Trade Judge decide.",
                flush=True,
            )
        else:
            logger.info(f"R:R OK for {symbol}: {actual_rr:.2f} (min {min_rr:.1f})")

        _entry_gate_result = run_post_claude_gates(
            _pc_inp,
            start_at="entry",
            stop_after="entry",
            gate_path=_price_gate.gate_path,
            carry=_price_gate,
        )
        if _entry_gate_result.blocked:
            await bot._handle_pipeline_gate_block(
                symbol, _entry_gate_result, ctx=_entry_gate_result.pipeline_ctx
            )
            return
        _pipeline_ctx = _entry_gate_result.pipeline_ctx
        trade_signal.confidence = _entry_gate_result.confidence
        confluence_count, confluence_factors = count_confluence(_pipeline_ctx)
        logger.info(
            f"Confluence factors for {symbol}: {confluence_count} "
            f"({', '.join(confluence_factors) if confluence_factors else 'none'})"
        )
        print(
            f"[CONFLUENCE] {symbol}: {confluence_count} factors "
            f"({', '.join(confluence_factors) if confluence_factors else 'none'}), "
            f"confidence={trade_signal.confidence:.0%}",
            flush=True,
        )

        # ============================================
        # SCALING MANAGER: Check if trade is allowed
        # ============================================
        print(f"[SCALING] {symbol}: Checking scaling manager...", flush=True)
        if bot.scaling_manager:
            # Determine current mode based on performance
            # Use EQUITY (includes unrealized P/L) for drawdown-related mode decisions
            # Using balance alone ignores floating profits from open positions,
            # causing false DEFENSIVE downgrades when open trades dip temporarily.
            account_info = await bot.mt5_client.get_account_info()
            current_equity = account_info.equity if account_info else 1000.0
            
            # Skip per-symbol mode recalculation if day-of-week override is active
            # (Monday=CONSERVATIVE, Friday=CONSERVATIVE) — only drawdown can override
            if getattr(bot, '_day_of_week_mode_locked', False):
                # Still check drawdown even on locked days
                daily_dd = bot.scaling_manager.calculate_daily_drawdown(current_equity)
                weekly_dd = bot.scaling_manager.calculate_weekly_drawdown(current_equity)
                if weekly_dd >= bot.scaling_manager.max_weekly_drawdown or daily_dd >= bot.scaling_manager.max_daily_drawdown:
                    _prev_mode = bot.scaling_manager.current_mode
                    bot.scaling_manager.current_mode = TradingMode.DEFENSIVE
                    print(f"[SCALING] {symbol}: DEFENSIVE (drawdown override on locked day), equity={current_equity}", flush=True)
                    if TradingMode.DEFENSIVE != _prev_mode:
                        from ..api.routes.activity import add_activity
                        add_activity("mode_change", f"Trading mode changed to DEFENSIVE (drawdown override)", details={"mode": "DEFENSIVE", "previous": _prev_mode.value, "reason": "drawdown_override", "daily_dd": f"{daily_dd:.2%}", "weekly_dd": f"{weekly_dd:.2%}"})
                else:
                    print(f"[SCALING] {symbol}: Mode={bot.scaling_manager.current_mode.value} (day-of-week locked), equity={current_equity}", flush=True)
            else:
                # Let Claude help with mode decision if available
                # OVERRIDE: In AGGRESSIVE mode (data collection), ignore Claude's mode recommendation
                # to prevent it from downgrading to defensive/conservative during demo testing
                claude_mode = None
                if bot.scaling_manager.current_mode != TradingMode.AGGRESSIVE:
                    if bot.claude_client and bot.claude_client.api_key:
                        try:
                            scaling_decision = await bot.claude_client.assess_scaling_decision(
                                current_equity=current_equity,
                                current_tier=bot.position_sizer.get_tier_name(current_equity) if bot.position_sizer else "Unknown",
                                recent_performance=bot.scaling_manager.get_recent_performance(),
                                goal_progress=bot.scaling_manager.calculate_goal_progress(current_equity)
                            )
                            claude_mode = scaling_decision.get('recommended_mode')
                            print(f"[SCALING] {symbol}: Claude recommended mode: {claude_mode}", flush=True)
                        except Exception as e:
                            logger.debug(f"Could not get Claude scaling decision: {e}")
                else:
                    print(f"[SCALING] {symbol}: AGGRESSIVE mode locked (data collection) — skipping Claude mode assessment", flush=True)
                
                # Determine current trading mode (uses equity for drawdown watermarks)
                _prev_mode = bot.scaling_manager.current_mode
                mode = bot.scaling_manager.determine_mode(current_equity, claude_mode)
                print(f"[SCALING] {symbol}: Mode={mode.value}, equity={current_equity}", flush=True)
                if mode != bot.scaling_manager.current_mode:
                    bot.scaling_manager.current_mode = mode
                    logger.info(f"Trading mode changed to: {mode.value}")
                if bot.scaling_manager.current_mode != _prev_mode:
                    from ..api.routes.activity import add_activity
                    add_activity("mode_change", f"Trading mode changed to {bot.scaling_manager.current_mode.value}", details={"mode": bot.scaling_manager.current_mode.value, "previous": _prev_mode.value, "symbol": symbol, "equity": current_equity})
            
        # ============================================
        # PIPELINE PERMISSION + FLIP GATES (shared with replay)
        # ============================================
        _pc_inp.scaling_aggressive = (
            bot.scaling_manager is not None
            and bot.scaling_manager.current_mode.value == "aggressive"
        )
        _pc_inp.scaling_manager = bot.scaling_manager
        _pc_inp.daily_trades = bot.daily_trades

        def _correlation_check():
            if bot.correlation_service:
                return bot.correlation_service.should_block_trade(
                    symbol, direction=trade_signal.direction
                )
            return False, ""

        _pc_inp.correlation_check = (
            _correlation_check if bot.correlation_service else None
        )

        _perm_flip = run_post_claude_gates(
            _pc_inp,
            start_at="permission",
            stop_after="complete",
            ctx=_pipeline_ctx,
            gate_path=_entry_gate_result.gate_path,
            carry=_entry_gate_result,
        )
        if _perm_flip.blocked:
            if _perm_flip.gate_id == "direction_flip":
                logger.warning(f"[FLIP-GUARD] {symbol}: {_perm_flip.reason}")
                from ..api.routes.activity import add_activity

                last_dir = bot._last_signal_direction.get(
                    symbol, ("", datetime.now(timezone.utc))
                )[0]
                add_activity(
                    "direction_flip_blocked",
                    f"Blocked {symbol} flip: {last_dir.upper()} -> {trade_signal.direction.upper()}",
                    symbol,
                    {
                        "previous_direction": last_dir,
                        "new_direction": trade_signal.direction,
                        "confidence": trade_signal.confidence,
                        "required_confidence": 0.80,
                    },
                )
                print(
                    f"[BLOCKED] ║  Direction flip {symbol}: {_perm_flip.reason}",
                    flush=True,
                )
                if bot_state:
                    bot_state.trade_decision(
                        symbol,
                        "rejected",
                        f"Direction flip blocked ({last_dir} -> {trade_signal.direction})",
                    )
                await bot._record_terminal_decision(
                    "mechanical_reject",
                    symbol,
                    gate_id=_perm_flip.gate_id,
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=_perm_flip.reason,
                    details=build_reject_details(
                        gate_path=_perm_flip.gate_path,
                        direction=trade_signal.direction,
                        entry=trade_signal.entry_price or current_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                    ),
                )
                return
            await bot._handle_pipeline_gate_block(
                symbol, _perm_flip, ctx=_perm_flip.pipeline_ctx
            )
            return
        trade_signal.confidence = _perm_flip.confidence
        if (
            hasattr(bot, "_post_cooldown_symbols")
            and symbol in bot._post_cooldown_symbols
        ):
            bot._post_cooldown_symbols.discard(symbol)

        if any(p.startswith("flip_guard_bypass") for p in _perm_flip.gate_path):
            logger.info(f"[FLIP-GUARD] {symbol}: Bypassing cooldown")
        elif "flip_guard_high_confidence" in _perm_flip.gate_path:
            logger.info(
                f"[FLIP-GUARD] {symbol}: Allowing high-confidence flip "
                f"({trade_signal.confidence:.0%} >= 80%)"
            )

        bot._last_signal_direction[symbol] = (
            trade_signal.direction,
            datetime.now(timezone.utc),
        )

        # Stash regime for close/fill telemetry (used by TradeFillHandler)
        try:
            _regime_info = market_data.get("regime") or {}
            if not hasattr(bot, "_last_regime_by_symbol"):
                bot._last_regime_by_symbol = {}
            bot._last_regime_by_symbol[symbol] = (
                _regime_info.get("regime") if isinstance(_regime_info, dict) else None
            )
        except Exception:
            pass

        # ============================================
        # PLAYBOOK HARD GATE (proven negative expectancy combos)
        # ============================================
        from .edge_policies import evaluate_playbook_gate

        _playbook_stats = await _get_playbook_stats(bot)
        _pb_result = evaluate_playbook_gate(
            _playbook_stats,
            symbol,
            trade_signal.direction,
            _session_name,
            trade_type=getattr(trade_signal, "trade_type", None),
        )
        if _pb_result.blocked:
            logger.warning(f"[PLAYBOOK-GATE] {symbol}: {_pb_result.reason}")
            print(f"[BLOCKED] {symbol}: {_pb_result.reason}", flush=True)
            await bot._record_terminal_decision(
                "mechanical_reject",
                symbol,
                gate_id="playbook_block",
                direction=trade_signal.direction,
                entry=trade_signal.entry_price or current_price,
                sl=trade_signal.stop_loss or 0.0,
                tp=trade_signal.take_profit or 0.0,
                confidence=trade_signal.confidence,
                reason=_pb_result.reason,
                details={"playbook_stats": _pb_result.stats},
            )
            return

        # Gap 21: Track signal hashes for dedup, but DON'T hard-block.
        # Multiple trades per symbol are allowed if the analysis supports it.
        # The pending order replacement logic downstream already handles
        # cancelling old orders and placing new ones for the same symbol+direction.
        signal_hash = bot._get_signal_hash(symbol, trade_signal.direction, trade_signal.entry_price or current_price)
        if signal_hash in bot._recent_signal_hashes:
            # Same exact entry price + direction was placed recently.
            # Allow it to proceed — the downstream logic will either:
            # (a) replace the pending order with updated TP/SL, or
            # (b) open a second position if the first already filled.
            logger.info(f"[DEDUP] {symbol}: Repeat signal (same entry), allowing through for re-evaluation")
            print(f"[DEDUP] {symbol}: Repeat {trade_signal.direction} signal @ {trade_signal.entry_price or current_price:.2f} — allowing (may update pending order)", flush=True)
        
        # ============================================
        # CORRELATION SIZE ADJUSTMENT
        # ============================================
        if bot.correlation_service:
            size_multiplier = bot.correlation_service.get_position_size_multiplier(symbol)
            if size_multiplier < 1.0:
                logger.info(f"📊 Correlation adjustment: {symbol} size reduced to {size_multiplier*100:.0f}%")
        else:
            size_multiplier = 1.0
        _edge_mult = getattr(bot, "_edge_size_multiplier", 1.0)
        size_multiplier *= _edge_mult

        # ============================================
        # ENSEMBLE SIZING: mechanical baseline vs Claude agreement
        # ============================================
        from .edge_policies import mech_agreement_size_multiplier

        _agree = mech_agreement_size_multiplier(
            analysis_results.get("mechanical_setup"), trade_signal.direction
        )
        if _agree.multiplier != 1.0:
            size_multiplier *= _agree.multiplier
            logger.info(
                f"[ENSEMBLE] {symbol}: mech/Claude {_agree.label} — "
                f"size x{_agree.multiplier:.2f}"
            )
            print(
                f"[ENSEMBLE] {symbol}: Mechanical baseline {_agree.label.upper()} "
                f"with Claude — size multiplier {_agree.multiplier:.2f}",
                flush=True,
            )
        
        # ============================================
        # PRE-LOCK: Get Claude's position size recommendation OUTSIDE the trade lock
        # to avoid holding the lock during a slow API call (2-30s)
        # ============================================
        claude_size_rec = None
        
        # Determine setup grade from confidence (needed for Claude call)
        if trade_signal.confidence >= 0.85:
            setup_grade = SetupGrade.A_PLUS
        elif trade_signal.confidence >= 0.75:
            setup_grade = SetupGrade.A
        elif trade_signal.confidence >= 0.65:
            setup_grade = SetupGrade.B
        else:
            setup_grade = SetupGrade.C
        
        if bot.claude_client and bot.claude_client.api_key:
            try:
                # Pre-fetch account info for Claude call (non-critical, can be stale)
                _pre_account = await bot.mt5_client.get_account_info()
                if _pre_account:
                    tier = bot.position_sizer.get_tier(_pre_account.balance)
                    rec = await asyncio.wait_for(
                        bot.claude_client.recommend_position_size(
                            equity=_pre_account.balance,
                            setup_grade=setup_grade.value,
                            confidence=trade_signal.confidence,
                            symbol=symbol,
                            win_streak=bot.win_streak,
                            loss_streak=bot.loss_streak,
                            base_lots=tier.base_lots,
                            max_lots=tier.max_lots
                        ),
                        timeout=40.0  # Opus 5 + thinking needs more headroom than the old Sonnet 15s
                    )
                    if rec.get('recommended_lots'):
                        claude_size_rec = rec['recommended_lots']
                        logger.info(f"Claude size recommendation: {claude_size_rec} lots ({rec.get('reasoning', '')})")
            except asyncio.TimeoutError:
                logger.warning("Claude position size recommendation timed out (40s)")
            except Exception as e:
                logger.warning(f"Could not get Claude size recommendation: {e}")
        
        # Gap 20: Acquire trade lock to prevent race conditions
        async with bot._trade_lock:
            account_info = await bot.mt5_client.get_account_info()
            if not account_info:
                logger.error("Failed to get account info")
                return

            # Re-check daily trade limit under lock (tier + mode caps)
            _max_daily = bot._effective_max_daily_trades(account_info.balance)
            if bot.daily_trades >= _max_daily:
                logger.info(f"Daily trade limit reached ({bot.daily_trades}/{_max_daily})")
                return
            
            # Reserve this trade slot immediately to prevent over-execution
            _trade_reservation_context = bot._trade_reservation_scope(
                symbol=symbol,
                signal_id=signal_hash,
                risk_percent=0.0,
            )
            _trade_reservation = await _trade_reservation_context.__aenter__()
            logger.info(f"Trade slot reserved ({bot.daily_trades}/{_max_daily})")
            
            _conf_used_for_sizing = trade_signal.confidence
            _entry_used_for_sizing = trade_signal.entry_price or current_price
            # Calculate position size with scaling
            size_result = bot.position_sizer.calculate_position_size(
                equity=account_info.balance,
                entry_price=trade_signal.entry_price or current_price,
                stop_loss=trade_signal.stop_loss,
                symbol=symbol,
                confidence=trade_signal.confidence,
                setup_grade=setup_grade,
                win_streak=bot.win_streak,
                loss_streak=bot.loss_streak,
                current_exposure_lots=bot._get_current_exposure_lots(),
                correlation_multiplier=size_multiplier,
                claude_recommendation=claude_size_rec,
                confluence_count=confluence_count or 0,
            )
            
            # Apply crypto volatility adjustment if applicable
            final_lots = size_result.lots
            if is_crypto and bot.crypto_analyzer:
                crypto_adj = bot.crypto_analyzer.get_position_size_adjustment(symbol, final_lots)
                from ..config import normalize_lots
                final_lots = normalize_lots(symbol, crypto_adj)
                logger.info(f"🪙 Crypto volatility adjustment: {size_result.lots} -> {final_lots} lots")
            
            # Apply scaling manager risk multiplier (reduces lots during drawdowns)
            if bot.scaling_manager:
                mode_config = bot.scaling_manager.get_mode_config()
                risk_mult = getattr(mode_config, 'risk_multiplier', 1.0)
                if risk_mult != 1.0:
                    pre_scale_lots = final_lots
                    from ..config import normalize_lots as _norm_lots
                    final_lots = _norm_lots(symbol, final_lots * risk_mult)
                    logger.info(
                        f"[SCALING] {symbol}: Lots {pre_scale_lots} x {risk_mult:.2f} "
                        f"({bot.scaling_manager.current_mode.value}) = {final_lots}"
                    )
                    print(
                        f"[SCALING] {symbol}: Position size adjusted "
                        f"{pre_scale_lots} -> {final_lots} lots "
                        f"(mode={bot.scaling_manager.current_mode.value}, mult={risk_mult:.2f})",
                        flush=True
                    )
            
            # Apply news impact position size reduction
            if bot.news_service and settings.trading.news_gates_enabled:
                try:
                    _news_mult, _news_reason = bot.news_service.should_reduce_size(symbol)
                    if _news_mult < 1.0:
                        pre_news_lots = final_lots
                        from ..config import normalize_lots as _norm_news
                        final_lots = _norm_news(symbol, final_lots * _news_mult)
                        logger.info(
                            f"[NEWS-IMPACT] {symbol}: Lots {pre_news_lots} x {_news_mult:.2f} "
                            f"= {final_lots} ({_news_reason})"
                        )
                        print(
                            f"[NEWS-IMPACT] {symbol}: Size reduced {pre_news_lots} -> "
                            f"{final_lots} lots ({_news_reason})",
                            flush=True
                        )
                except Exception as _news_err:
                    logger.debug(f"[NEWS-IMPACT] Error checking news impact for {symbol}: {_news_err}")
            
            # Create position size object
            class SimplePositionSize:
                def __init__(self, lots):
                    self.lots = lots
            
            position_size = SimplePositionSize(final_lots)
            
            # Log position sizing details
            logger.info(f"📊 Position sizing: {final_lots} lots")
            logger.info(f"   Tier: {size_result.tier_name}, Risk: {size_result.risk_percent*100:.1f}%")
            for adj in size_result.adjustments:
                logger.info(f"   • {adj}")
            
            # Validate trade with risk manager
            _val_entry = trade_signal.entry_price or current_price
            _val_sl = trade_signal.stop_loss
            _val_tp = trade_signal.take_profit
            _val_dir = trade_signal.direction
            print(f"[VALIDATE] {symbol}: Running risk validation (entry={_val_entry}, SL={_val_sl}, TP={_val_tp}, dir={_val_dir})...", flush=True)
            validation = bot.risk_manager.validate_trade(
                entry_price=_val_entry,
                stop_loss=_val_sl,
                take_profit=_val_tp,
                direction=_val_dir,
                symbol=symbol,
                account_balance=account_info.balance,
                actual_risk_pct=compute_booked_risk_percent(
                    final_lots, _val_entry, _val_sl, symbol, account_info.balance
                ) or size_result.risk_percent,
                trade_type=getattr(trade_signal, 'trade_type', 'intraday')
            )
            
            if not validation.is_valid:
                print(f"[BLOCKED] {symbol}: Validation failed - {validation.errors}", flush=True)
                logger.warning(f"Trade validation failed for {symbol}: {validation.errors}")
                await bot._reject_and_record(
                    _trade_reservation,
                    "mechanical_reject",
                    symbol,
                    gate_id="risk_validation_fail",
                    direction=trade_signal.direction,
                    entry=_val_entry,
                    sl=_val_sl or 0.0,
                    tp=_val_tp or 0.0,
                    confidence=trade_signal.confidence,
                    reason="; ".join(validation.errors),
                    details=build_reject_details(
                        gate_path=["risk_validation"],
                        direction=trade_signal.direction,
                        entry=_val_entry,
                        sl=_val_sl or 0.0,
                        tp=_val_tp or 0.0,
                        confidence=trade_signal.confidence,
                    ),
                )
                return
            
            # =============================================
            # NEW: 100-PIP EXPANSION VALIDATION GATES
            # =============================================
            
            # GATE 1: Premium/Discount Zone Validation
            # Block longs in premium, shorts in discount
            if pd_analysis:
                zone_validation = bot.premium_discount_analyzer.validate_entry(
                    direction=trade_signal.direction,
                    current_price=current_price,
                    df=df
                )
                if not zone_validation["valid"]:
                    logger.warning(f"⚠️ ZONE BLOCK: {zone_validation['reason']}")
                    # Convert market to pending at OTE; reject if entry cannot be resolved
                    if trade_signal.order_type == 'market':
                        _ote_entry = ote_pullback_entry(
                            trade_signal.direction,
                            pd_analysis.swing_high,
                            pd_analysis.swing_low,
                        )
                        if _ote_entry > 0:
                            _old_entry = trade_signal.entry_price or current_price
                            trade_signal.order_type = (
                                'buy_limit' if trade_signal.direction == 'long'
                                else 'sell_limit'
                            )
                            trade_signal.entry_price = _ote_entry
                            trade_signal.stop_loss, trade_signal.take_profit = (
                                rebase_sl_tp_for_new_entry(
                                    stop_loss=trade_signal.stop_loss or 0.0,
                                    take_profit=trade_signal.take_profit or 0.0,
                                    old_entry=_old_entry,
                                    new_entry=_ote_entry,
                                )
                            )
                            logger.info(
                                f"🔄 Converted to {trade_signal.order_type.upper()} "
                                f"@ {_ote_entry:.5f} (OTE pullback, SL/TP rebased)"
                            )
                        else:
                            await bot._reject_and_record(
                                _trade_reservation,
                                "mechanical_reject",
                                symbol,
                                gate_id="zone_conversion_failed",
                                direction=trade_signal.direction,
                                entry=trade_signal.entry_price or current_price,
                                sl=trade_signal.stop_loss or 0.0,
                                tp=trade_signal.take_profit or 0.0,
                                confidence=trade_signal.confidence,
                                reason=(
                                    f"Zone invalid and no OTE entry available: "
                                    f"{zone_validation['reason']}"
                                ),
                            )
                            return
                else:
                    logger.info(f"✅ Zone check passed: {zone_validation['reason']}")
            
            # GATE 2: DXY Correlation Check for FX Pairs
            # Block trades that conflict with DXY direction
            if dxy_confirmation and symbol in ['EURUSD', 'GBPUSD', 'USDCHF', 'USDJPY', 'AUDUSD', 'NZDUSD']:
                if trade_signal.direction != dxy_confirmation:
                    logger.warning(
                        f"⚠️ DXY CONFLICT: {symbol} {trade_signal.direction.upper()} "
                        f"conflicts with DXY confirming {dxy_confirmation.upper()}"
                    )
                    # Reduce position size by 50% instead of blocking
                    original_lots = position_size.lots
                    from ..config import normalize_lots as _nl
                    position_size.lots = _nl(symbol, position_size.lots * 0.5)
                    logger.info(f"📉 Reduced size due to DXY conflict: {original_lots} -> {position_size.lots} lots")
                else:
                    logger.info(f"✅ DXY confirms {trade_signal.direction.upper()} bias for {symbol}")
            
            _conf_before_modifiers = trade_signal.confidence
            _at_breaker = False
            if breaker_blocks:
                _entry_chk = trade_signal.entry_price or current_price
                for bb in breaker_blocks:
                    if bb.bottom <= _entry_chk <= bb.top:
                        _at_breaker = True
                        break
            _conf_decision = apply_secondary_modifiers(
                _conf_before_modifiers,
                SecondaryModifierContext(
                    direction=trade_signal.direction,
                    symbol=symbol,
                    retail_contrarian=retail_contrarian,
                    vix_risk_mode=vix_risk_mode,
                    social_sentiment=analysis_results.get("social_sentiment"),
                    options_flow=analysis_results.get("options_flow"),
                    intermarket=analysis_results.get("intermarket"),
                    seasonal=analysis_results.get("seasonal_pattern"),
                    bond_yields=analysis_results.get("bond_yields"),
                    btc_dominance=analysis_results.get("btc_dominance"),
                    silver_bullet_ready=bool(silver_bullet_ready),
                    at_breaker_block=_at_breaker,
                    current_price=current_price,
                ),
            )
            trade_signal.confidence = _conf_decision.final
            bot._last_confidence_components = confidence_decision_to_dict(_conf_decision)
            logger.info(
                f"[CONF-PIPE] {symbol}: base={_conf_decision.base:.0%} -> "
                f"final={_conf_decision.final:.0%}"
            )
            
            # GATE 3: Displacement Check for Market Orders
            # Only allow immediate market execution if displacement is confirmed
            _disp_confirmed = (
                displacement_analysis.distribution_confirmed
                if displacement_analysis is not None
                else None
            )
            _amd_phase = (
                amd_state.phase.value
                if amd_state is not None and hasattr(amd_state, "phase")
                else None
            )
            _disp_action = displacement_gate_action(
                trade_signal.order_type,
                distribution_confirmed=_disp_confirmed,
                amd_phase=_amd_phase,
            )
            if _disp_action == "allow_market":
                logger.info("✅ Displacement confirmed - proceeding with market order")
            elif _disp_action == "reject":
                await bot._reject_and_record(
                    _trade_reservation,
                    "mechanical_reject",
                    symbol,
                    gate_id="no_displacement",
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=(
                        "Market order blocked: displacement not confirmed "
                        f"(AMD phase={_amd_phase or 'n/a'})"
                    ),
                )
                return
            elif _disp_action == "convert_pending":
                logger.warning(
                    f"⚠️ NO DISPLACEMENT: Converting market order to pending "
                    f"(AMD Phase: {_amd_phase})"
                )
                entry_zone = analysis_results.get("fvg", {})
                _new_entry = 0.0
                if trade_signal.direction == 'long':
                    if hasattr(entry_zone, 'bullish_fvgs') and entry_zone.bullish_fvgs:
                        nearest_fvg = min(
                            entry_zone.bullish_fvgs,
                            key=lambda x: abs(x.midpoint - current_price),
                        )
                        _new_entry = nearest_fvg.midpoint
                    elif pd_analysis:
                        _new_entry = ote_pullback_entry(
                            'long', pd_analysis.swing_high, pd_analysis.swing_low
                        )
                else:
                    if hasattr(entry_zone, 'bearish_fvgs') and entry_zone.bearish_fvgs:
                        nearest_fvg = min(
                            entry_zone.bearish_fvgs,
                            key=lambda x: abs(x.midpoint - current_price),
                        )
                        _new_entry = nearest_fvg.midpoint
                    elif pd_analysis:
                        _new_entry = ote_pullback_entry(
                            'short', pd_analysis.swing_high, pd_analysis.swing_low
                        )
                if _new_entry > 0:
                    _old_entry = trade_signal.entry_price or current_price
                    trade_signal.order_type = (
                        'buy_limit' if trade_signal.direction == 'long'
                        else 'sell_limit'
                    )
                    trade_signal.entry_price = _new_entry
                    trade_signal.stop_loss, trade_signal.take_profit = (
                        rebase_sl_tp_for_new_entry(
                            stop_loss=trade_signal.stop_loss or 0.0,
                            take_profit=trade_signal.take_profit or 0.0,
                            old_entry=_old_entry,
                            new_entry=_new_entry,
                        )
                    )
                    logger.info(
                        f"🔄 Converted to {trade_signal.order_type.upper()} "
                        f"@ {_new_entry:.5f} (no displacement, SL/TP rebased)"
                    )
                else:
                    await bot._reject_and_record(
                        _trade_reservation,
                        "mechanical_reject",
                        symbol,
                        gate_id="displacement_conversion_failed",
                        direction=trade_signal.direction,
                        entry=trade_signal.entry_price or current_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                        reason="Displacement missing and no FVG/OTE entry available",
                    )
                    return
            
            # RE-SIZE if confidence or entry changed after sizing
            _entry_now = trade_signal.entry_price or current_price
            _entry_drifted = (
                abs(_entry_now - _entry_used_for_sizing) / _entry_used_for_sizing > 0.0005
                if _entry_used_for_sizing else False
            )
            if abs(trade_signal.confidence - _conf_used_for_sizing) > 0.001 or _entry_drifted:
                if trade_signal.confidence >= 0.85:
                    setup_grade = SetupGrade.A_PLUS
                elif trade_signal.confidence >= 0.75:
                    setup_grade = SetupGrade.A
                elif trade_signal.confidence >= 0.65:
                    setup_grade = SetupGrade.B
                else:
                    setup_grade = SetupGrade.C
                size_result = bot.position_sizer.calculate_position_size(
                    equity=account_info.balance,
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    symbol=symbol,
                    confidence=trade_signal.confidence,
                    setup_grade=setup_grade,
                    win_streak=bot.win_streak,
                    loss_streak=bot.loss_streak,
                    current_exposure_lots=bot._get_current_exposure_lots(),
                    correlation_multiplier=size_multiplier,
                    claude_recommendation=claude_size_rec,
                    confluence_count=confluence_count or 0,
                )
                final_lots = size_result.lots
                if is_crypto and bot.crypto_analyzer:
                    crypto_adj = bot.crypto_analyzer.get_position_size_adjustment(symbol, final_lots)
                    from ..config import normalize_lots as _norm_crypto
                    final_lots = _norm_crypto(symbol, crypto_adj)
                if bot.scaling_manager:
                    mode_config = bot.scaling_manager.get_mode_config()
                    risk_mult = getattr(mode_config, 'risk_multiplier', 1.0)
                    if risk_mult != 1.0:
                        from ..config import normalize_lots as _norm_scale
                        final_lots = _norm_scale(symbol, final_lots * risk_mult)
                if bot.news_service and settings.trading.news_gates_enabled:
                    try:
                        _news_mult, _ = bot.news_service.should_reduce_size(symbol)
                        if _news_mult < 1.0:
                            from ..config import normalize_lots as _norm_news2
                            final_lots = _norm_news2(symbol, final_lots * _news_mult)
                    except Exception:
                        pass
                position_size.lots = final_lots
                logger.info(
                    f"[RE-SIZE] {symbol}: lots re-sized "
                    f"(confidence {_conf_used_for_sizing:.0%} -> {trade_signal.confidence:.0%}, "
                    f"entry {_entry_used_for_sizing:.5f} -> {_entry_now:.5f}) "
                    f"= {final_lots} lots"
                )
            
            # FINAL SAFETY CHECK before execution
            # Block if position size is 0 (blocked pair) or symbol is dangerous
            if position_size.lots <= 0:
                logger.error(f"🚫 BLOCKED: Position size is 0 for {symbol} - trade not executed")
                if bot_state:
                    bot_state.error(symbol, "Position size 0 - blocked pair")
                await bot._reject_and_record(
                    _trade_reservation,
                    "mechanical_reject",
                    symbol,
                    gate_id="zero_lots",
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason="Position size resolved to 0 lots",
                )
                return
            
            # (BTC/BIT-quoted pairs already blocked at analysis entry above;
            #  scaling_position_sizer refuses to size them as final backstop.)
            
            # =============================================
            # P0 CRITICAL: MARGIN VALIDATION BEFORE TRADE
            # =============================================
            signal_order_type = getattr(trade_signal, 'order_type', 'market') or 'market'
            signal_trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
            precheck = await bot.claude_trade_manager.precheck_trade(
                symbol=symbol,
                direction=trade_signal.direction,
                entry_price=trade_signal.entry_price or current_price,
                stop_loss=trade_signal.stop_loss,
                take_profit=trade_signal.take_profit,
                confidence=trade_signal.confidence,
                order_type=signal_order_type,
                trade_type=signal_trade_type
            )
            
            if not precheck.can_execute:
                # =============================================
                # SMART POSITION REPLACEMENT: If blocked by max
                # positions and new signal is strong, close the
                # weakest existing position to make room
                # =============================================
                is_position_limit_block = any("Max positions" in b for b in precheck.blockers)
                
                if is_position_limit_block and trade_signal.confidence >= 0.70:
                    replaced = await bot._try_replace_weakest_position(
                        new_symbol=symbol,
                        new_confidence=trade_signal.confidence,
                        new_direction=trade_signal.direction
                    )
                    if replaced:
                        logger.info(
                            f"♻️ Position replacement: closed weak trade to make room "
                            f"for {symbol} ({trade_signal.confidence:.0%} confidence)"
                        )
                        # Re-run precheck now that a slot is freed
                        precheck = await bot.claude_trade_manager.precheck_trade(
                            symbol=symbol,
                            direction=trade_signal.direction,
                            entry_price=trade_signal.entry_price or current_price,
                            stop_loss=trade_signal.stop_loss,
                            take_profit=trade_signal.take_profit,
                            confidence=trade_signal.confidence,
                            order_type=signal_order_type,
                            trade_type=signal_trade_type
                        )
                
                if not precheck.can_execute:
                    logger.warning(f"🚫 Trade blocked by pre-check: {precheck.blockers}")
                    if bot_state:
                        bot_state.error(symbol, f"Pre-check failed: {'; '.join(precheck.blockers)}")
                    await bot._reject_and_record(
                        _trade_reservation,
                        "mechanical_reject",
                        symbol,
                        gate_id="precheck_block",
                        direction=trade_signal.direction,
                        entry=trade_signal.entry_price or current_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                        reason="; ".join(precheck.blockers),
                        details={"blockers": list(precheck.blockers)},
                    )
                    # Blocked trade notifications disabled — only notify on executed trades/TP/SL
                    return
            
            # Log any warnings from precheck
            for warning in precheck.warnings:
                logger.warning(f"⚠️ Trade warning: {warning}")
            
            # Use pre-checked position size (respects margin/exposure limits)
            if precheck.recommended_lots < position_size.lots:
                logger.info(
                    f"📉 Position size adjusted by margin check: "
                    f"{position_size.lots} -> {precheck.recommended_lots} lots"
                )
                position_size.lots = precheck.recommended_lots
            
            # Log margin status
            logger.info(
                f"💰 Margin check passed: "
                f"Free margin: ${precheck.margin_check.free_margin:,.2f}, "
                f"Level: {precheck.margin_check.margin_level:.0f}%"
            )
            
            # =============================================
            # NEW: MULTI-TIER TAKE PROFIT FOR 100-PIP TARGETS
            # =============================================
            
            # Get enhanced TP levels from IPDA tracker
            take_profit_levels = {}
            original_tp = trade_signal.take_profit
            
            if ipda_analysis and trade_signal.stop_loss:
                try:
                    tp_levels = bot.ipda_tracker.get_take_profit_levels(
                        direction=trade_signal.direction,
                        current_price=trade_signal.entry_price or current_price,
                        stop_loss=trade_signal.stop_loss
                    )
                    
                    # SANITY CHECK: Validate IPDA TP levels are in a reasonable price range
                    # Max TP should be within 10% of current price for forex, 20% for crypto/metals
                    _entry_ref = trade_signal.entry_price or current_price
                    _max_tp_pct = 0.20 if symbol in bot.CRYPTO_SYMBOLS or symbol in bot.PRECIOUS_METALS or symbol in bot.INDEX_SYMBOLS or symbol in bot.OIL_SYMBOLS else 0.10
                    
                    def _is_sane_tp(tp_price):
                        """Check if a TP price is within reasonable range of entry."""
                        if tp_price is None or tp_price <= 0:
                            return False
                        deviation = abs(tp_price - _entry_ref) / _entry_ref
                        return deviation <= _max_tp_pct
                    
                    take_profit_levels = {
                        'tp1': tp_levels.get('tp1') if _is_sane_tp(tp_levels.get('tp1')) else None,
                        'tp2': tp_levels.get('tp2') if _is_sane_tp(tp_levels.get('tp2')) else None,
                        'tp3': tp_levels.get('tp3') if _is_sane_tp(tp_levels.get('tp3')) else None,
                    }
                    
                    # Log if any levels were filtered out as insane
                    for key in ('tp1', 'tp2', 'tp3'):
                        raw = tp_levels.get(key)
                        if raw is not None and take_profit_levels[key] is None:
                            logger.warning(
                                f"⚠️ IPDA {key} for {symbol} REJECTED: {raw:.5f} is too far "
                                f"from entry {_entry_ref:.5f} (>{_max_tp_pct:.0%})"
                            )
                    
                    # Add NWOG target if available and closer than IPDA
                    if nwog_target and _is_sane_tp(nwog_target):
                        nwog_distance = abs(nwog_target - _entry_ref)
                        ipda_tp3 = take_profit_levels.get('tp3')
                        if ipda_tp3:
                            ipda_distance = abs(ipda_tp3 - _entry_ref)
                            # Use NWOG if it's between TP2 and TP3
                            if nwog_distance < ipda_distance:
                                take_profit_levels['nwog_target'] = nwog_target
                                logger.info(f"🎯 Added NWOG target @ {nwog_target:.5f}")
                    
                    # IPDA levels are used for multi-TP management (partial closes),
                    # but NEVER override Claude's primary TP. Claude's TP is based on
                    # actual structure/liquidity analysis. IPDA snap was corrupting TP
                    # (e.g. XAUUSD R:R going from 2.37:1 to 0.02:1).
                    # Keep Claude's TP as the primary target.
                    logger.info(
                        f"IPDA TP levels calculated for {symbol} (multi-TP only, "
                        f"Claude's TP {trade_signal.take_profit:.5f} preserved as primary)"
                    )
                    
                    logger.info(f"📊 TP Levels: TP1={take_profit_levels.get('tp1')}, "
                               f"TP2={take_profit_levels.get('tp2')}, TP3={take_profit_levels.get('tp3')}")
                    
                except Exception as e:
                    logger.warning(f"Could not calculate IPDA TP levels: {e}")
            
            # Log trade signal
            logger.info(f"Valid trade signal for {symbol}:")
            logger.info(f"  Direction: {trade_signal.direction}")
            logger.info(f"  Confidence: {trade_signal.confidence:.2f}")
            logger.info(f"  Entry: {trade_signal.entry_price}")
            logger.info(f"  SL: {trade_signal.stop_loss}, TP: {trade_signal.take_profit}")
            if take_profit_levels:
                logger.info(f"  Multi-TP: {take_profit_levels}")
            logger.info(f"  R:R: {trade_signal.risk_reward}")
            logger.info(f"  Position Size: {position_size.lots} lots")
            logger.info(f"  Order Type: {trade_signal.order_type}")
            logger.info(f"  AMD Phase: {trade_signal.amd_phase}")
            logger.info(f"  Reasoning: {trade_signal.reasoning[:200] if trade_signal.reasoning else 'None'}...")
            
            # =============================================
            # FINAL R:R SAFETY NET (before execution)
            # Catches any R:R degradation from IPDA/OTE/zone adjustments
            # =============================================
            _final_entry = trade_signal.entry_price or current_price
            _final_sl = trade_signal.stop_loss
            _final_tp = trade_signal.take_profit
            _final_dir = trade_signal.direction
            
            if _final_sl and _final_tp:
                _final_sl_dist = abs(_final_entry - _final_sl)
                _final_tp_dist = abs(_final_tp - _final_entry)
                _final_rr = _final_tp_dist / _final_sl_dist if _final_sl_dist > 0 else 0
                
                if _final_rr < 1.0 and _final_sl_dist > 0:
                    # R:R below 1.0 after adjustments — hard reject
                    logger.warning(
                        f"[BLOCKED] FINAL R:R CHECK {symbol}: {_final_rr:.2f}:1 < 1.0:1 "
                        f"after adjustments. Reward < risk. Trade rejected. "
                        f"(entry={_final_entry}, SL={_final_sl}, TP={_final_tp})"
                    )
                    print(
                        f"[BLOCKED] {symbol}: Final R:R {_final_rr:.2f}:1 < 1.0 "
                        f"after price adjustments. Trade rejected. "
                        f"(entry={_final_entry}, SL={_final_sl}, TP={_final_tp})",
                        flush=True
                    )
                    await bot._reject_and_record(
                        _trade_reservation,
                        "mechanical_reject",
                        symbol,
                        gate_id="final_rr_below_1",
                        direction=_final_dir,
                        entry=_final_entry,
                        sl=_final_sl,
                        tp=_final_tp,
                        confidence=trade_signal.confidence,
                        reason=f"Final R:R {_final_rr:.2f}:1 below 1.0",
                        details=build_reject_details(
                            gate_path=["final_rr_check"],
                            direction=_final_dir,
                            entry=_final_entry,
                            sl=_final_sl,
                            tp=_final_tp,
                            confidence=trade_signal.confidence,
                        ),
                    )
                    return
                elif _final_rr < min_rr and _final_sl_dist > 0:
                    # Borderline — log warning, Trade Judge will evaluate
                    logger.info(
                        f"[R:R WARNING] FINAL CHECK {symbol}: R:R {_final_rr:.2f}:1 "
                        f"below target {min_rr:.1f}:1 — Trade Judge will decide."
                    )
            
            # No hardcoded TP floors or sanity gates — Claude's TP is final.
            # The FINAL R:R SAFETY NET above handles rejection of bad R:R.
            # No price fabrication. Accept or reject only.
            
            # =============================================
            # STALE ENTRY FIX: Rebase SL/TP for market orders
            # When Claude proposed entry differs from current price,
            # preserve the SL/TP distances so they stay valid
            # =============================================
            if (trade_signal.order_type in ('market', None) and 
                trade_signal.entry_price and trade_signal.stop_loss and trade_signal.take_profit and
                current_price > 0):
                proposed_entry = trade_signal.entry_price
                drift = abs(current_price - proposed_entry)
                drift_pct = drift / proposed_entry if proposed_entry > 0 else 0
                if drift_pct > 0.002:  # >0.2% drift triggers rebase
                    sl_offset = trade_signal.stop_loss - proposed_entry
                    tp_offset = trade_signal.take_profit - proposed_entry
                    new_sl = round(current_price + sl_offset, 5)
                    new_tp = round(current_price + tp_offset, 5)
                    print(
                        f"[REBASE] {symbol}: Price drifted {drift_pct:.2%} "
                        f"(proposed {proposed_entry:.5f} -> market {current_price:.5f}). "
                        f"SL {trade_signal.stop_loss:.5f}->{new_sl:.5f}, "
                        f"TP {trade_signal.take_profit:.5f}->{new_tp:.5f}",
                        flush=True
                    )
                    trade_signal.entry_price = current_price
                    trade_signal.stop_loss = new_sl
                    trade_signal.take_profit = new_tp
            
            # =============================================
            # TRADE JUDGE (pre-execution validation)
            # =============================================
            # Import once before REJECT/DEMOTE/APPROVE branches. A late import
            # inside only the REJECT path makes Python treat save_signal_to_db as
            # local for the whole function, so APPROVE crashes with UnboundLocalError
            # and never reaches order placement.
            from ..main import save_signal_to_db

            # A+ FAST PATH: pristine setups skip the judge (saves 10-45s and
            # one Opus call); anything with doubt still gets judged.
            from .trade_judge import (
                JudgeOutcome,
                JudgeVerdict,
                qualifies_for_judge_fast_path,
            )

            _j_entry = trade_signal.entry_price or current_price
            _j_sl = trade_signal.stop_loss or 0.0
            _j_sl_dist = abs(_j_entry - _j_sl) if _j_sl else 0.0
            _j_rr = (
                abs((trade_signal.take_profit or 0.0) - _j_entry) / _j_sl_dist
                if _j_sl_dist > 0
                else 0.0
            )
            _j_warnings = list(getattr(claude_result, "warnings", None) or [])
            _j_htf_aligned = bool(getattr(mtf_result, "alignment", False))

            if qualifies_for_judge_fast_path(
                confidence=trade_signal.confidence,
                risk_reward=_j_rr,
                htf_aligned=_j_htf_aligned,
                warnings=_j_warnings,
            ):
                logger.info(
                    f"[JUDGE] {symbol}: A+ fast path — judge skipped "
                    f"(conf={trade_signal.confidence:.0%}, RR={_j_rr:.2f}, "
                    f"HTF aligned, no warnings)"
                )
                judge_outcome = JudgeOutcome(
                    verdict=JudgeVerdict.APPROVE,
                    reason="A+ setup fast path — judge skipped",
                    risk_flags=["judge_skipped_a_plus"],
                )
            else:
                print(f"[JUDGE] {symbol}: Sending to trade judge (confidence={trade_signal.confidence:.0%}, dir={trade_signal.direction}, lots={position_size.lots})...", flush=True)
                judge_outcome = await bot._run_trade_judge(
                    symbol, trade_signal, position_size, current_price
                )
            judge_verdict = judge_outcome.to_dict()

            if judge_outcome.blocks_execution():
                verdict_label = judge_outcome.verdict.value
                reason = judge_outcome.reason or (
                    'Judge rejected' if verdict_label == 'REJECT' else 'Judge unavailable'
                )
                flags = judge_outcome.risk_flags
                logger.warning(
                    f"[JUDGE] {verdict_label} {symbol} {trade_signal.direction}: {reason}"
                )

                try:
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_reject" if verdict_label == "REJECT" else "trade_judge_unavailable",
                        f"Judge {verdict_label} {symbol} {trade_signal.direction}: {reason}",
                        symbol,
                        {
                            "verdict": verdict_label,
                            "reason": reason,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                except Exception as _act_err:
                    logger.warning(f"Activity feed failed after judge {verdict_label}: {_act_err}")

                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  {verdict_label} {symbol} — \"{reason}\"  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )

                await safe_persist_judge_signal(
                    save_signal_to_db,
                    symbol=symbol,
                    direction=trade_signal.direction,
                    confidence=trade_signal.confidence,
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                    judge_verdict=verdict_label,
                    judge_reason=reason,
                    judge_risk_flags=flags,
                    trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                    market_structure=getattr(trade_signal, 'market_structure', ''),
                    confluence_factors=confluence_factors if confluence_factors else None,
                    confluence_count=confluence_count if confluence_count else None,
                )

                outcome_type = (
                    "judge_reject" if verdict_label == "REJECT" else "judge_failure"
                )
                await bot._record_terminal_decision(
                    outcome_type,
                    symbol,
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=reason,
                    judge_verdict=verdict_label,
                    details={"risk_flags": flags},
                )

                bot._release_trade_reservation(_trade_reservation)
                return

            # Handle DEMOTE verdict — convert to pending limit order
            elif judge_outcome.allows_demote_execution():
                demote = apply_demote_policy(
                    trade_signal.direction,
                    current_price,
                    trade_signal.entry_price or current_price,
                    trade_signal.stop_loss,
                    trade_signal.take_profit,
                    getattr(trade_signal, "order_type", "market") or "market",
                    judge_outcome.suggested_entry,
                )
                demoted_entry = demote["demoted_entry"]
                trade_signal.order_type = demote["order_type"]
                trade_signal.stop_loss = demote["stop_loss"]
                trade_signal.take_profit = demote["take_profit"]
                trade_signal.entry_price = demoted_entry
                if demote.get("size_multiplier", 1.0) not in (None, 1.0):
                    from ..config import normalize_lots as _nd_demote
                    position_size.lots = _nd_demote(
                        symbol, position_size.lots * demote["size_multiplier"]
                    )

                _sl_for_check = trade_signal.stop_loss or 0
                _demote_sl_dist = abs(demoted_entry - _sl_for_check) if _sl_for_check else 0
                _demote_tp_dist = abs((trade_signal.take_profit or 0) - demoted_entry)
                _demote_rr = _demote_tp_dist / _demote_sl_dist if _demote_sl_dist > 0 else 0
                
                if _demote_rr < 1.0 and _demote_sl_dist > 0:
                    logger.warning(
                        f"[JUDGE] DEMOTE REJECTED {symbol}: R:R {_demote_rr:.2f}:1 < 1.0 "
                        f"after demotion to {demoted_entry:.5f}. "
                        f"SL dist=${_demote_sl_dist:.2f}, TP dist=${_demote_tp_dist:.2f}."
                    )
                    print(
                        f"[JUDGE] DEMOTE REJECTED {symbol}: R:R {_demote_rr:.2f}:1 < 1.0 "
                        f"after demotion (entry={demoted_entry:.5f}, SL={_sl_for_check:.5f}, "
                        f"TP={trade_signal.take_profit:.5f})",
                        flush=True
                    )
                    await bot._reject_and_record(
                        _trade_reservation,
                        "mechanical_reject",
                        symbol,
                        gate_id="demote_rr_below_1",
                        direction=trade_signal.direction,
                        entry=demoted_entry,
                        sl=_sl_for_check,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                        reason=f"DEMOTE R:R {_demote_rr:.2f}:1 below 1.0",
                        judge_verdict="DEMOTE",
                        details=build_reject_details(
                            gate_path=["judge_demote", "demote_rr_check"],
                            direction=trade_signal.direction,
                            entry=demoted_entry,
                            sl=_sl_for_check,
                            tp=trade_signal.take_profit or 0.0,
                            confidence=trade_signal.confidence,
                        ),
                    )
                    return
                
                reason = judge_outcome.reason or demote.get("reason", "Judge demoted")
                _sl_check = trade_signal.stop_loss or 0
                if _sl_check > 0:
                    _sl_wrong_side = False
                    if trade_signal.direction == 'long' and _sl_check >= demoted_entry:
                        _sl_wrong_side = True
                    elif trade_signal.direction == 'short' and _sl_check <= demoted_entry:
                        _sl_wrong_side = True
                    
                    if _sl_wrong_side:
                        logger.warning(
                            f"[JUDGE] DEMOTE REJECTED {symbol}: SL ({_sl_check:.5f}) is on wrong side "
                            f"of demoted entry ({demoted_entry:.5f}) for {trade_signal.direction}. "
                            f"Demotion pushed entry past the SL."
                        )
                        print(
                            f"[JUDGE] DEMOTE REJECTED {symbol}: SL {_sl_check:.5f} is on WRONG SIDE "
                            f"of entry {demoted_entry:.5f} ({trade_signal.direction}). Skipping.",
                            flush=True
                        )
                        await bot._reject_and_record(
                            _trade_reservation,
                            "mechanical_reject",
                            symbol,
                            gate_id="demote_sl_wrong_side",
                            direction=trade_signal.direction,
                            entry=demoted_entry,
                            sl=_sl_check,
                            tp=trade_signal.take_profit or 0.0,
                            confidence=trade_signal.confidence,
                            reason="DEMOTE SL on wrong side of entry",
                            judge_verdict="DEMOTE",
                            details=build_reject_details(
                                gate_path=["judge_demote", "demote_sl_check"],
                                direction=trade_signal.direction,
                                entry=demoted_entry,
                                sl=_sl_check,
                                tp=trade_signal.take_profit or 0.0,
                                confidence=trade_signal.confidence,
                            ),
                        )
                        return
                
                reason = judge_outcome.reason or 'Judge demoted'
                flags = judge_outcome.risk_flags
                logger.info(
                    f"[JUDGE] Demoted {symbol} {trade_signal.direction} market -> "
                    f"{trade_signal.order_type} @ {demoted_entry:.5f} (reason: {reason})"
                )
                
                # Log to activity feed (non-critical — must not abort execution)
                try:
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_demote",
                        f"Judge demoted {symbol} {trade_signal.direction}: {reason}",
                        symbol,
                        {
                            "verdict": "DEMOTE",
                            "reason": reason,
                            "original_entry": current_price,
                            "demoted_entry": demoted_entry,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                except Exception as _act_err:
                    logger.warning(f"Activity feed failed after judge DEMOTE: {_act_err}")
                
                # Print DEMOTE to terminal
                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  DEMOTE {symbol} — \"{reason}\" → limit @ {demoted_entry:.5f}  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )
                
                # Save demoted signal to DB for correlation (non-critical)
                await safe_persist_judge_signal(
                    save_signal_to_db,
                    symbol=symbol,
                    direction=trade_signal.direction,
                    confidence=trade_signal.confidence,
                    entry_price=demoted_entry,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                    judge_verdict="DEMOTE",
                    judge_reason=reason,
                    judge_risk_flags=flags,
                    trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                    market_structure=getattr(trade_signal, 'market_structure', ''),
                    confluence_factors=confluence_factors if confluence_factors else None,
                    confluence_count=confluence_count if confluence_count else None,
                )
                await bot._record_terminal_decision(
                    "judge_demote",
                    symbol,
                    direction=trade_signal.direction,
                    entry=demoted_entry,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=reason,
                    judge_verdict="DEMOTE",
                    details={"risk_flags": flags, "order_type": trade_signal.order_type},
                )
            else:
                # APPROVE — log for visibility
                reason = judge_outcome.reason or 'Approved'
                flags = judge_outcome.risk_flags
                if flags:
                    logger.info(f"[JUDGE] Approved {symbol} with flags: {flags}")
                
                # Non-critical I/O — must not abort order placement after APPROVE
                try:
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_approve",
                        f"Judge approved {symbol} {trade_signal.direction} ({trade_signal.confidence:.0%})",
                        symbol,
                        {
                            "verdict": "APPROVE",
                            "reason": reason,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                except Exception as _act_err:
                    logger.warning(f"Activity feed failed after judge APPROVE: {_act_err}")
                
                # Print APPROVE to terminal
                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  APPROVE {symbol} — \"{reason}\"  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )
                
                await safe_persist_judge_signal(
                    save_signal_to_db,
                    symbol=symbol,
                    direction=trade_signal.direction,
                    confidence=trade_signal.confidence,
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                    judge_verdict="APPROVE",
                    judge_reason=reason,
                    judge_risk_flags=flags,
                    trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                    market_structure=getattr(trade_signal, 'market_structure', ''),
                    confluence_factors=confluence_factors if confluence_factors else None,
                    confluence_count=confluence_count if confluence_count else None,
                )
            
            # =============================================
            # EXECUTION PREP (position conflicts + order normalization)
            # =============================================
            if not hasattr(bot, "_execution_coordinator"):
                from ..execution.trade_execution import ExecutionCoordinator
                bot._execution_coordinator = ExecutionCoordinator()
            _existing_positions = (
                bot.position_manager.get_positions_by_symbol(symbol)
                if bot.position_manager
                else []
            )
            _exec_prep = bot._execution_coordinator.prepare_order(
                trade_signal=trade_signal,
                current_price=current_price,
                existing_positions=_existing_positions,
                analysis_results=analysis_results,
            )
            if _exec_prep.blocked:
                print(f"[BLOCKED] {symbol}: {_exec_prep.reason}", flush=True)
                await bot._reject_and_record(
                    _trade_reservation,
                    "mechanical_reject",
                    symbol,
                    gate_id=_exec_prep.gate_id,
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=_exec_prep.reason,
                    details=build_reject_details(
                        gate_path=[_exec_prep.gate_id or "execution_prep"],
                        direction=trade_signal.direction,
                        entry=trade_signal.entry_price or current_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                    ),
                )
                return
            order_type = _exec_prep.order_type
            entry_price = _exec_prep.entry_price

            # Respect Claude's explicit pending order choice — do NOT override to market
            # even during distribution phase. Claude knows the entry model.
            
            _exec_result = await bot._execution_coordinator.execute(
                bot=bot,
                symbol=symbol,
                trade_signal=trade_signal,
                order_type=order_type,
                entry_price=entry_price,
                current_price=current_price,
                position_size=position_size,
                size_result=size_result,
                account_info=account_info,
                market_data=market_data,
                is_crypto=is_crypto,
                trade_reservation=_trade_reservation,
            )
            if _exec_result.blocked:
                await bot._reject_and_record(
                    _trade_reservation,
                    "mechanical_reject",
                    symbol,
                    gate_id=_exec_result.gate_id,
                    direction=trade_signal.direction,
                    entry=_exec_result.final_entry,
                    sl=_exec_result.final_sl,
                    tp=_exec_result.final_tp,
                    confidence=trade_signal.confidence,
                    reason=_exec_result.reason,
                    details=build_reject_details(
                        gate_path=[_exec_result.gate_id or "execution"],
                        direction=trade_signal.direction,
                        entry=_exec_result.final_entry,
                        sl=_exec_result.final_sl,
                        tp=_exec_result.final_tp,
                        confidence=trade_signal.confidence,
                    ),
                )
                return
            if _exec_result.dry_run:
                return None
            result = _exec_result.broker_result
            order_type = _exec_result.order_type
            entry_price = _exec_result.entry_price
            _final_sl = _exec_result.final_sl
            _final_tp = _exec_result.final_tp
            _final_entry = _exec_result.final_entry
            _final_spec = _exec_result.symbol_spec

            from ..execution.trade_fill_handler import TradeFillHandler
            from ..main import save_trade_to_db

            await TradeFillHandler.handle_result(
                bot,
                symbol=symbol,
                result=result,
                order_type=order_type,
                entry_price=entry_price,
                current_price=current_price,
                trade_signal=trade_signal,
                position_size=position_size,
                size_result=size_result,
                account_info=account_info,
                trade_reservation=_trade_reservation,
                signal_hash=signal_hash,
                final_sl=_final_sl,
                final_tp=_final_tp,
                final_entry=_final_entry,
                judge_verdict=judge_verdict,
                confluence_factors=confluence_factors,
                confluence_count=confluence_count,
                setup_grade=setup_grade,
                take_profit_levels=take_profit_levels,
                save_trade_to_db=save_trade_to_db,
            )

    except Exception as e:
        print(f"[ERROR] _analyze_and_trade({symbol}) CRASHED: {e}", flush=True)
        logger.error(f"Error analyzing {symbol}: {e}")
        import traceback
        traceback.print_exc()
        # Only release while still RESERVED — never undo a transferred fill/pending
        from ..services.trade_reservations import ReservationState
        if (
            _trade_reservation is not None
            and getattr(_trade_reservation, "state", None) == ReservationState.RESERVED
        ):
            bot._release_trade_reservation(_trade_reservation)
    finally:
        if _trade_reservation_context is not None:
            await _trade_reservation_context.__aexit__(None, None, None)
    

