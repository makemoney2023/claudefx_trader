"""Analyze-and-trade pipeline runner — extracted from TradingBot._analyze_and_trade."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from ..config import settings
from ..utils.logging import get_logger
from ..utils.notifications import notify, NotificationType
from ..utils.win_optimization import apply_demote_policy, build_confidence_decision, classify_a_plus
from ..execution.scaling_position_sizer import verify_post_sizing_risk
from ..services.confidence_modifiers import (
    SecondaryModifierContext,
    apply_secondary_modifiers,
    confidence_decision_to_dict,
)
from ..services.gate_pipeline import (
    count_confluence,
    evaluate_entry_gates,
    evaluate_trade_permission_gates,
)
from ..services.signal_normalizer import normalize_signal_prices
from ..api.websocket import broadcast_trade_update, broadcast_analysis_update

try:
    from ..api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ..main import TradingBot


async def run_analyze_and_trade(bot: "TradingBot", symbol: str, is_crypto: bool = False) -> None:
    """Run full analyze-and-trade pipeline for one symbol."""
    from ..main import save_signal_to_db, save_trade_to_db

    """
    Analyze a symbol and execute trade if valid setup found.
    
    Args:
        symbol: Trading symbol to analyze
        is_crypto: Whether this is a crypto symbol (24/7 trading)
    """
    _trade_reservation = None
    _trade_reservation_context = None
    if not hasattr(bot, "_trade_pipeline"):
        from .trade_pipeline import TradePipeline
        bot._trade_pipeline = TradePipeline(bot)
    try:
        logger.info(f"Analyzing {symbol}...")
        
        # Update bot state
        if bot_state:
            bot_state.analyzing_symbol(symbol)
        
        # POST-LOSS COOLDOWN: Prevent revenge trading
        cooldown_expiry = bot._symbol_loss_cooldowns.get(symbol)
        if cooldown_expiry:
            if datetime.now(timezone.utc) < cooldown_expiry:
                remaining = (cooldown_expiry - datetime.now(timezone.utc)).total_seconds() / 60
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
        if not hasattr(bot, "_analysis_orchestrator"):
            from .analysis_orchestrator import AnalysisOrchestrator
            bot._analysis_orchestrator = AnalysisOrchestrator()
        analysis_results = bot._analysis_orchestrator.run_core_analysis(symbol, df)
        
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
        # NEW: 100-PIP EXPANSION ANALYSIS
        # =============================================
        
        print(f"[ANALYSIS] Core ICT done for {symbol}. Running expanded analysis...", flush=True)
        
        # Update pip_value on all analyzers for this symbol
        from ..config import get_symbol_spec
        _sym_spec = get_symbol_spec(symbol)
        _sym_pip = _sym_spec.pip_size
        if bot.amd_analyzer:
            bot.amd_analyzer.pip_value = _sym_pip
        if bot.displacement_detector:
            bot.displacement_detector.pip_value = _sym_pip
        if bot.ipda_tracker:
            bot.ipda_tracker.pip_value = _sym_pip
        if bot.nwog_tracker:
            bot.nwog_tracker.pip_value = _sym_pip
        
        # 1. AMD Cycle Analysis - Power of Three
        amd_state = None
        if bot.amd_analyzer:
            try:
                amd_state = bot.amd_analyzer.analyze(df)
                logger.info(
                    f"AMD Phase: {amd_state.phase.value}, "
                    f"Expected Direction: {amd_state.expected_direction or 'Unknown'}, "
                    f"Confidence: {amd_state.confidence:.0%}"
                )
                analysis_results["amd_cycle"] = {
                    "phase": amd_state.phase.value,
                    "expected_direction": amd_state.expected_direction,
                    "manipulation_extreme": amd_state.manipulation_extreme,
                    "confidence": amd_state.confidence
                }
            except Exception as e:
                logger.warning(f"AMD analysis error: {e}")
        
        # 2. Displacement Detection - Distribution confirmation
        displacement_analysis = None
        if bot.displacement_detector:
            try:
                expected_dir = amd_state.expected_direction if amd_state else None
                displacement_analysis = bot.displacement_detector.detect(df, expected_dir)
                
                if displacement_analysis.distribution_confirmed:
                    logger.info(
                        f"🚀 DISPLACEMENT CONFIRMED: {displacement_analysis.distribution_direction} "
                        f"(Strong candle + FVG)"
                    )
                
                analysis_results["displacement"] = displacement_analysis.to_dict()
            except Exception as e:
                logger.warning(f"Displacement analysis error: {e}")
        
        # 3. Breaker Block Prioritization - Check for A+ setups
        breaker_blocks = analysis_results["order_blocks"].breaker_blocks if analysis_results["order_blocks"].breaker_blocks else []
        if breaker_blocks:
            logger.info(f"🔄 Found {len(breaker_blocks)} Breaker Blocks - HIGH PRIORITY ENTRY ZONES")
            analysis_results["breaker_blocks"] = {
                "count": len(breaker_blocks),
                "bullish": [bb.to_dict() for bb in breaker_blocks if bb.type.value == "breaker_bullish"],
                "bearish": [bb.to_dict() for bb in breaker_blocks if bb.type.value == "breaker_bearish"]
            }
        
        # 4. Premium/Discount Zone Analysis
        pd_analysis = None
        if bot.premium_discount_analyzer:
            try:
                current_price_pd = float(df['close'].iloc[-1])
                pd_analysis = bot.premium_discount_analyzer.analyze(df, current_price_pd)
                
                logger.info(
                    f"📊 Price Zone: {pd_analysis.current_zone.value} "
                    f"({pd_analysis.retracement_percent:.0%}), "
                    f"OTE: {'YES' if pd_analysis.in_ote else 'NO'}"
                )
                
                analysis_results["premium_discount"] = pd_analysis.to_dict()
            except Exception as e:
                logger.warning(f"Premium/Discount analysis error: {e}")
        
        # 5. IPDA Levels - Draw on Liquidity targets for 100-pip moves
        ipda_analysis = None
        if bot.ipda_tracker:
            try:
                ipda_analysis = bot.ipda_tracker.update(df)
                if ipda_analysis.pdh or ipda_analysis.pdl:
                    pdh_str = f"{ipda_analysis.pdh.price:.5f}" if ipda_analysis.pdh else "N/A"
                    pdl_str = f"{ipda_analysis.pdl.price:.5f}" if ipda_analysis.pdl else "N/A"
                    logger.info(f"📍 IPDA Levels: PDH={pdh_str}, PDL={pdl_str}")
                analysis_results["ipda_levels"] = ipda_analysis.to_dict()
            except Exception as e:
                logger.warning(f"IPDA analysis error: {e}")
        
        # 6. NWOG Check - Weekend gaps as targets
        nwog_target = None
        if bot.nwog_tracker and hasattr(bot.nwog_tracker, 'gaps') and bot.nwog_tracker.gaps:
            try:
                nearest_nwog = bot.nwog_tracker.get_nearest_nwog(float(df['close'].iloc[-1]))
                if nearest_nwog:
                    nwog_target = nearest_nwog.ce_level
                    logger.info(f"🎯 NWOG Target: {nwog_target:.5f} ({nearest_nwog.gap_size_pips:.0f} pip gap)")
                    analysis_results["nwog_target"] = {
                        "ce_level": nwog_target,
                        "gap_size_pips": nearest_nwog.gap_size_pips,
                        "filled": nearest_nwog.filled
                    }
            except Exception as e:
                logger.warning(f"NWOG analysis error: {e}")
        
        # 7. Silver Bullet + Displacement Check
        silver_bullet_ready = False
        if hasattr(bot, 'silver_bullet_detector') and bot.silver_bullet_detector:
            sb_status = bot.silver_bullet_detector.is_in_silver_bullet_window()
            if sb_status.get('active', False):
                # Silver Bullet requires displacement in the window
                if displacement_analysis and displacement_analysis.distribution_confirmed:
                    silver_bullet_ready = True
                    logger.info(f"🔫⚡ SILVER BULLET READY: Displacement confirmed in {sb_status['window']} window!")
                else:
                    logger.info(f"🔫 Silver Bullet window active but waiting for displacement...")
                analysis_results["silver_bullet_status"] = {
                    "window_active": True,
                    "window": sb_status.get('window'),
                    "displacement_confirmed": silver_bullet_ready,
                    "time_remaining": sb_status.get('time_remaining_minutes', 0)
                }
        
        print(f"[ANALYSIS] Running MTF analysis for {symbol} (D1->H4->H1->M15->M5->M1)...", flush=True)
        # 8. MULTI-TIMEFRAME ANALYSIS - HTF bias confirmation
        mtf_result = None
        if bot.mtf_analyzer:
            try:
                mtf_result = await bot.mtf_analyzer.analyze(symbol)
                if mtf_result:
                    logger.info(
                        f"📊 MTF Bias: {mtf_result.overall_bias.value}, "
                        f"Alignment: {mtf_result.alignment}, "
                        f"Can Long: {mtf_result.can_trade_long}, "
                        f"Can Short: {mtf_result.can_trade_short}"
                    )
                    analysis_results["mtf_analysis"] = mtf_result.to_dict()
                    
                    # Log MTF results to bot activity dashboard
                    if bot_state:
                        mtf_details = {
                            "d1_bias": mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else "N/A",
                            "h4_bias": mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else "N/A",
                            "h4_structure": mtf_result.h4_analysis.structure if mtf_result.h4_analysis else "N/A",
                            "h1_bias": mtf_result.h1_analysis.bias.value if mtf_result.h1_analysis else "N/A",
                            "h1_structure": mtf_result.h1_analysis.structure if mtf_result.h1_analysis else "N/A",
                            "m15_bias": mtf_result.m15_analysis.bias.value if mtf_result.m15_analysis else "N/A",
                            "m5_bias": mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else "N/A",
                            "m5_structure": mtf_result.m5_analysis.structure if mtf_result.m5_analysis else "N/A",
                            "m1_bias": mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else "N/A",
                            "m1_structure": mtf_result.m1_analysis.structure if mtf_result.m1_analysis else "N/A",
                            "alignment": mtf_result.alignment,
                            "key_levels": mtf_result.htf_key_levels[:5] if mtf_result.htf_key_levels else [],
                        }
                        bot_state.mtf_analysis_complete(
                            symbol,
                            bias=mtf_result.overall_bias.value,
                            alignment=mtf_result.alignment,
                            can_long=mtf_result.can_trade_long,
                            can_short=mtf_result.can_trade_short,
                            details=mtf_details
                        )
                else:
                    logger.warning(f"MTF analysis returned no result for {symbol}")
                    if bot_state:
                        bot_state.error(symbol, f"MTF analysis: no D1/H4/H1 data available")
            except Exception as e:
                logger.warning(f"MTF analysis error: {e}")
                if bot_state:
                    bot_state.error(symbol, f"MTF analysis error: {e}")
        
        print(f"[ANALYSIS] Running Fibonacci/OTE analysis for {symbol}...", flush=True)
        # 9. FIBONACCI / OTE ANALYSIS
        fib_analysis = None
        if bot.fibonacci_analyzer:
            try:
                # Determine direction from market structure
                ms_trend = analysis_results["market_structure"].trend.value if analysis_results.get("market_structure") else 'bullish'
                fib_direction = 'bullish' if ms_trend == 'bullish' else 'bearish'
                fib_analysis = bot.fibonacci_analyzer.analyze_ote(df, fib_direction, lookback=50)
                if fib_analysis:
                    logger.info(
                        f"📐 Fibonacci: Zone={fib_analysis.price_zone.value}, "
                        f"In OTE: {fib_analysis.in_ote}, "
                        f"Optimal Entry: {fib_analysis.optimal_entry}"
                    )
                    analysis_results["fibonacci"] = fib_analysis.to_dict()
                    
                    # Log Fibonacci results to bot activity dashboard
                    if bot_state:
                        fib_details = {
                            "zone": fib_analysis.price_zone.value,
                            "in_ote": fib_analysis.in_ote,
                            "optimal_entry": fib_analysis.optimal_entry,
                            "direction": fib_direction,
                            "levels": fib_analysis.to_dict().get('levels', {}) if hasattr(fib_analysis, 'to_dict') else {},
                        }
                        bot_state.fibonacci_analysis_complete(
                            symbol,
                            zone=fib_analysis.price_zone.value,
                            in_ote=fib_analysis.in_ote,
                            optimal_entry=fib_analysis.optimal_entry,
                            details=fib_details
                        )
            except Exception as e:
                logger.warning(f"Fibonacci analysis error: {e}")
        
        # 10. ENHANCED FIRECRAWL INTELLIGENCE
        dxy_confirmation = None
        retail_contrarian = None
        vix_risk_mode = None
        currency_strength_recommendation = None
        
        if bot.firecrawl_service:
            try:
                # 8a. DXY Correlation for FX pairs
                if symbol in ['EURUSD', 'GBPUSD', 'USDCHF', 'USDJPY', 'AUDUSD', 'NZDUSD']:
                    dxy_data = await bot.firecrawl_service.get_dxy_analysis()
                    dxy_trend = dxy_data.get('trend', 'unknown')
                    
                    if symbol in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD']:
                        if dxy_trend == 'bullish':
                            dxy_confirmation = 'short'
                            logger.info(f"💵 DXY BULLISH: Confirms SHORT bias for {symbol}")
                        elif dxy_trend == 'bearish':
                            dxy_confirmation = 'long'
                            logger.info(f"💵 DXY BEARISH: Confirms LONG bias for {symbol}")
                    elif symbol in ['USDCHF', 'USDJPY']:
                        if dxy_trend == 'bullish':
                            dxy_confirmation = 'long'
                            logger.info(f"💵 DXY BULLISH: Confirms LONG bias for {symbol}")
                        elif dxy_trend == 'bearish':
                            dxy_confirmation = 'short'
                            logger.info(f"💵 DXY BEARISH: Confirms SHORT bias for {symbol}")
                    
                    analysis_results["dxy_correlation"] = {
                        "dxy_trend": dxy_trend,
                        "confirmed_direction": dxy_confirmation
                    }
                
                # 8b. RETAIL SENTIMENT (Contrarian Indicator)
                retail_data = await bot.firecrawl_service.get_retail_sentiment(symbol)
                if retail_data.get('contrarian_signal') != 'unknown':
                    retail_contrarian = retail_data.get('contrarian_signal')
                    logger.info(
                        f"🔄 RETAIL CONTRARIAN: {retail_data.get('bias', 'N/A')} bias, "
                        f"Signal: {retail_contrarian.upper()}"
                    )
                    if retail_data.get('note'):
                        logger.info(f"   {retail_data.get('note')}")
                    analysis_results["retail_sentiment"] = retail_data
                
                # 8c. VIX Risk Sentiment
                vix_data = await bot.firecrawl_service.get_vix_sentiment()
                if vix_data.get('risk_mode'):
                    vix_risk_mode = vix_data.get('risk_mode')
                    logger.info(
                        f"📊 VIX RISK MODE: {vix_risk_mode.upper()} "
                        f"(Level: {vix_data.get('level', 'N/A')})"
                    )
                    analysis_results["vix_sentiment"] = vix_data
                    
                    # Adjust for risk-off (favor JPY, CHF, Gold)
                    if vix_risk_mode == 'risk_off':
                        if symbol in ['USDJPY', 'USDCHF']:
                            logger.info(f"⚠️ RISK-OFF: Consider SHORT {symbol} (safe-haven flows)")
                        elif symbol == 'XAUUSD':
                            logger.info(f"✅ RISK-OFF: Supports LONG GOLD")
                
                # 8d. Currency Strength Meter
                strength_data = await bot.firecrawl_service.get_currency_strength()
                if strength_data.get('strongest') or strength_data.get('weakest'):
                    logger.info(
                        f"💪 CURRENCY STRENGTH: Strongest={strength_data.get('strongest')}, "
                        f"Weakest={strength_data.get('weakest')}"
                    )
                    if strength_data.get('recommendation'):
                        currency_strength_recommendation = strength_data.get('recommendation')
                        logger.info(f"   💡 {currency_strength_recommendation}")
                    analysis_results["currency_strength"] = strength_data
                
                # 8e. TradingView Technical Consensus
                tv_tech = await bot.firecrawl_service.get_tradingview_technical(symbol)
                if tv_tech.get('signal') != 'neutral':
                    logger.info(
                        f"📈 TV TECHNICAL: {tv_tech.get('consensus', 'N/A').upper()} "
                        f"({tv_tech.get('signal', 'neutral').upper()})"
                    )
                    analysis_results["tv_technical"] = tv_tech
                
                # 8f. Commodity Correlations (for CAD and AUD)
                if symbol in ['USDCAD', 'CADJPY']:
                    oil_data = await bot.firecrawl_service.get_commodity_correlation("oil")
                    if oil_data.get('trend') != 'unknown':
                        logger.info(f"🛢️ OIL: {oil_data.get('trend').upper()} - {oil_data.get('currency_implication', {}).get('pair_recommendation', '')}")
                        analysis_results["oil_correlation"] = oil_data
                
                if symbol in ['AUDUSD', 'AUDJPY', 'XAUUSD']:
                    gold_data = await bot.firecrawl_service.get_commodity_correlation("gold")
                    if gold_data.get('trend') != 'unknown':
                        logger.info(f"🥇 GOLD: {gold_data.get('trend').upper()} - {gold_data.get('currency_implication', {}).get('pair_recommendation', '')}")
                        analysis_results["gold_correlation"] = gold_data
                
                # 8g. SOCIAL SENTIMENT (Twitter/X - Contrarian)
                social_data = await bot.firecrawl_service.get_twitter_forex_sentiment(symbol)
                if social_data.get('sentiment') != 'unknown':
                    logger.info(
                        f"🐦 SOCIAL: {social_data.get('sentiment', 'N/A').upper()} "
                        f"(Volume: {social_data.get('volume', 'N/A')})"
                    )
                    analysis_results["social_sentiment"] = social_data
                
                # 8h. OPTIONS FLOW (Magnet Levels)
                options_data = await bot.firecrawl_service.get_options_flow(symbol)
                if options_data.get('flow') != 'neutral':
                    logger.info(
                        f"📊 OPTIONS FLOW: {options_data.get('flow', 'N/A').upper()}"
                    )
                    if options_data.get('magnet_levels'):
                        logger.info(f"   Magnet Levels: {options_data.get('magnet_levels')}")
                    analysis_results["options_flow"] = options_data
                
                # 8i. BOND YIELD SPREAD (EUR/USD bias)
                if symbol in ['EURUSD', 'EURGBP', 'EURJPY']:
                    yield_data = await bot.firecrawl_service.get_bond_yield_spread()
                    if yield_data.get('spread') is not None:
                        logger.info(
                            f"📈 YIELD SPREAD: US-DE = {yield_data.get('spread', 0):.2f}% "
                            f"-> EUR/USD bias: {yield_data.get('eurusd_bias', 'neutral').upper()}"
                        )
                        analysis_results["bond_yields"] = yield_data
                
                # 8j. INTERMARKET RISK ENVIRONMENT
                intermarket_data = await bot.firecrawl_service.get_intermarket_analysis()
                if intermarket_data.get('risk_environment') != 'unknown':
                    risk_env = intermarket_data.get('risk_environment', 'unknown')
                    logger.info(
                        f"🌐 INTERMARKET: {risk_env.upper().replace('_', ' ')} "
                        f"(SPX: {intermarket_data.get('spx_trend', 'N/A').upper()})"
                    )
                    analysis_results["intermarket"] = intermarket_data
                
                # 8k. SEASONAL PATTERN
                seasonal_data = await bot.firecrawl_service.get_seasonal_pattern(symbol)
                if seasonal_data.get('current_month_bias') != 'unknown':
                    logger.info(
                        f"📅 SEASONAL: {seasonal_data.get('current_month', 'N/A')} "
                        f"bias = {seasonal_data.get('current_month_bias', 'N/A').upper()} "
                        f"({seasonal_data.get('historical_accuracy', 0)}% accuracy)"
                    )
                    analysis_results["seasonal_pattern"] = seasonal_data
                
                # 8l. ECONOMIC SURPRISE INDEX
                surprise_data = await bot.firecrawl_service.get_economic_surprise_index()
                if surprise_data.get('us') != 'unknown' or surprise_data.get('eu') != 'unknown':
                    logger.info(
                        f"📰 ECONOMIC SURPRISE: US={surprise_data.get('us', 'N/A').upper()}, "
                        f"EU={surprise_data.get('eu', 'N/A').upper()}"
                    )
                    analysis_results["economic_surprise"] = surprise_data
                
                # 8l2. RATE EXPECTATIONS (Critical for currency bias)
                rate_data = await bot.firecrawl_service.get_rate_expectations()
                if rate_data.get('fed', {}).get('next_move') not in ['unknown', None]:
                    logger.info(
                        f"💰 FED RATE: Expected {rate_data['fed'].get('next_move', 'N/A').upper()} "
                        f"-> USD {rate_data['fed'].get('usd_impact', 'N/A').upper()}"
                    )
                    analysis_results["rate_expectations"] = rate_data
                
                # 8l3. ECONOMIC CALENDAR TODAY
                calendar_events = await bot.firecrawl_service.get_economic_calendar_today()
                if calendar_events:
                    high_impact = [e for e in calendar_events if e.get('impact') == 'high']
                    if high_impact:
                        logger.info(f"📅 {len(high_impact)} HIGH IMPACT events today")
                    analysis_results["economic_calendar"] = calendar_events
                
                # 8m. BTC DOMINANCE (for crypto pairs)
                if symbol in ['BTCUSD', 'ETHUSD', 'XRPUSD', 'SOLUSD', 'ADAUSD']:
                    btc_dom = await bot.firecrawl_service.get_btc_dominance()
                    if btc_dom.get('dominance') is not None:
                        logger.info(
                            f"₿ BTC DOMINANCE: {btc_dom.get('dominance', 'N/A')}% "
                            f"({btc_dom.get('trend', 'N/A')}) "
                            f"-> Altcoins: {btc_dom.get('altcoin_sentiment', 'N/A').upper()}"
                        )
                        analysis_results["btc_dominance"] = btc_dom
                
            except Exception as e:
                logger.debug(f"Firecrawl intelligence error: {e}")
        
        # Get current price
        current_price = float(df['close'].iloc[-1])
        
        # Skip Claude analysis if not configured
        if not bot.claude_client or not bot.claude_client.api_key:
            logger.debug(f"Claude not configured, using technical analysis only for {symbol}")
            return
        
        # Generate initial chart (will be regenerated with overlays after analysis)
        chart_base64 = await bot._generate_chart_image(df, symbol)
        if not chart_base64:
            logger.warning(f"Failed to generate chart for {symbol}")
            return
        
        _chart_pkg = await bot._analysis_orchestrator.build_chart_package(
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
        
        # Build strategy context
        strategy_context = bot.context_builder.get_ict_context()
        
        # Get account info for enhanced context
        account_info = await bot.mt5_client.get_account_info()
        current_equity = account_info.equity if account_info else 1000.0
        
        # Prepare market data for Claude (ENHANCED with all integrated services)
        from ..config import get_symbol_spec as _gss
        _sym_spec = _gss(symbol)
        # Fetch real bid/ask/spread from MT5
        _real_bid = current_price
        _real_ask = current_price
        _real_spread = 0.0
        _spread_pct = 0.0
        try:
            _sym_info = await bot.mt5_client.get_symbol_info(symbol)
            if _sym_info and getattr(_sym_info, 'ask', 0) > 0:
                _real_bid = _sym_info.bid
                _real_ask = _sym_info.ask
                _real_spread = _real_ask - _real_bid
                _spread_pct = _real_spread / ((_real_ask + _real_bid) / 2) if (_real_ask + _real_bid) > 0 else 0
        except Exception as e:
            logger.debug(f"Could not get real spread: {e}")
            pass
        
        market_data = {
            "current_price": current_price,
            "bid": _real_bid,
            "ask": _real_ask,
            "spread": round(_real_spread, 6),
            "spread_pct": f"{_spread_pct:.4%}",
            # Account & Goal Context
            "account_equity": current_equity,
            "scaling_tier": bot.position_sizer.get_tier_name(current_equity) if bot.position_sizer else "Unknown",
            "trading_mode": bot.scaling_manager.current_mode.value if bot.scaling_manager else "normal",
            "goal_progress": bot.scaling_manager.calculate_goal_progress(current_equity) if bot.scaling_manager else 0,
            # Swap/overnight cost awareness
            "swap_long": _sym_spec.swap_long,
            "swap_short": _sym_spec.swap_short,
            "swap_info": (
                f"Overnight swap costs per lot: Long={_sym_spec.swap_long}, Short={_sym_spec.swap_short}. "
                f"{'Positive swap favors longs.' if _sym_spec.swap_long > 0 and _sym_spec.swap_short < 0 else ''}"
                f"{'Positive swap favors shorts.' if _sym_spec.swap_short > 0 and _sym_spec.swap_long < 0 else ''}"
                f"{'Both directions have negative swap (costly to hold overnight).' if _sym_spec.swap_long < 0 and _sym_spec.swap_short < 0 else ''}"
            ) if (_sym_spec.swap_long != 0 or _sym_spec.swap_short != 0) else "Swap data not available.",
            # Volume constraints for position sizing
            "volume_min": _sym_spec.volume_min,
            "volume_max": _sym_spec.volume_max,
            "volume_step": _sym_spec.volume_step,
        }

        # Assign pre-computed volume profile and bar extreme data
        if _vp_data:
            market_data["volume_profile_levels"] = {
                'poc': _vp_data['poc'],
                'vah': _vp_data['vah'],
                'val': _vp_data['val'],
            }
        for _be_key, _be_val in _bar_extreme_results.items():
            market_data[_be_key] = _be_val
        
        # Add ATR context for Claude's SL placement awareness
        try:
            from ..utils.candle_utils import calculate_atr as _calc_atr_ctx
            _atr_ctx = _calc_atr_ctx(df, period=14)
            _atr_current = float(_atr_ctx.iloc[-1]) if not _atr_ctx.empty and not np.isnan(_atr_ctx.iloc[-1]) else None
            if _atr_current:
                market_data["atr_14"] = round(_atr_current, 6)
                market_data["atr_min_sl"] = round(_atr_current * 1.5, 6)
        except Exception as e:
            logger.debug(f"ATR calculation failed for context: {e}")
        
        # Add market regime classification
        if bot.regime_classifier:
            try:
                _amd_phase = 'unknown'
                if 'amd_cycle' in analysis_results:
                    _amd_phase = analysis_results['amd_cycle'].get('phase', 'unknown')
                _regime_result = bot.regime_classifier.classify(df, market_phase=_amd_phase)
                if _regime_result:
                    market_data["regime"] = _regime_result.to_dict()
                    logger.info(
                        f"[REGIME] {symbol}: {_regime_result.regime.value} "
                        f"(ADX={_regime_result.adx:.1f}, vol={_regime_result.volatility_ratio:.2f}x)"
                    )
            except Exception as _reg_err:
                logger.debug(f"[REGIME] Classification error for {symbol}: {_reg_err}")
        
        # Add recent performance context
        if bot.scaling_manager:
            market_data["recent_performance"] = bot.scaling_manager.get_recent_performance()
        
        # Add session performance context
        if bot.session_analytics:
            current_session = bot.session_analytics.get_current_session()
            session_stats = bot.session_analytics.get_session_stats(current_session)
            market_data["session"] = current_session.value
            market_data["session_performance"] = {
                "win_rate": session_stats.win_rate,
                "avg_r": session_stats.avg_r,
                "total_trades": session_stats.total_trades
            }
        
        # Add news/blackout context
        if bot.news_service:
            is_blackout, blackout_reason = bot.news_service.is_blackout_period()
            market_data["news_status"] = {
                "is_blackout": is_blackout,
                "reason": blackout_reason
            }
            if not is_blackout:
                countdown = bot.news_service.get_countdown_to_next_event()
                if countdown and countdown.get('minutes_until', 999) < 60:
                    market_data["news_status"]["next_event"] = {
                        "title": countdown.get('event', {}).get('title', 'Unknown'),
                        "minutes_until": countdown.get('minutes_until', 999),
                        "impact": countdown.get('event', {}).get('impact', 'medium')
                    }
        
        # Add correlation context
        if bot.correlation_service:
            should_block, reason = bot.correlation_service.should_block_trade(symbol)
            if should_block or bot.correlation_service.get_position_size_multiplier(symbol) < 1.0:
                market_data["correlation_exposure"] = {
                    "warning": reason if should_block else f"Reduced size recommended for {symbol}",
                    "blocked": should_block,
                    "multiplier": bot.correlation_service.get_position_size_multiplier(symbol)
                }
        
        # Add Silver Bullet window context
        if hasattr(bot, 'silver_bullet_detector') and bot.silver_bullet_detector:
            sb_status = bot.silver_bullet_detector.is_in_silver_bullet_window()
            market_data["silver_bullet"] = {
                "active": sb_status.get('active', False),
                "window": sb_status.get('window'),
                "time_remaining_minutes": sb_status.get('time_remaining_minutes', 0),
                "displacement_confirmed": silver_bullet_ready if 'silver_bullet_ready' in dir() else False
            }
            if sb_status.get('active'):
                logger.info(f"🔫 Silver Bullet window active for {symbol}: {sb_status['window']}")
        
        # =============================================
        # NEW: ADD 100-PIP EXPANSION CONTEXT FOR CLAUDE
        # =============================================
        
        # Mechanical ICT baseline (rule-based ICTStrategy advisory)
        if 'mechanical_setup' in analysis_results:
            market_data["mechanical_ict_setup"] = analysis_results["mechanical_setup"]
        
        # AMD Cycle context
        if 'amd_cycle' in analysis_results:
            market_data["amd_cycle"] = analysis_results["amd_cycle"]
        
        # Displacement context
        if 'displacement' in analysis_results:
            market_data["displacement"] = analysis_results["displacement"]
        
        # Breaker blocks for A+ entry prioritization
        if 'breaker_blocks' in analysis_results:
            market_data["breaker_blocks"] = analysis_results["breaker_blocks"]
        
        # Premium/Discount zone
        if 'premium_discount' in analysis_results:
            market_data["premium_discount"] = analysis_results["premium_discount"]
        
        # IPDA levels for 100-pip targets
        if 'ipda_levels' in analysis_results:
            market_data["ipda_levels"] = analysis_results["ipda_levels"]
        
        # NWOG targets
        if 'nwog_target' in analysis_results:
            market_data["nwog_target"] = analysis_results["nwog_target"]
        
        # Volume analysis context
        if 'volume' in analysis_results:
            market_data["volume_profile"] = analysis_results["volume"]
        
        # DXY correlation for FX pairs
        if 'dxy_correlation' in analysis_results:
            market_data["dxy_correlation"] = analysis_results["dxy_correlation"]
        
        # Silver Bullet + displacement status
        if 'silver_bullet_status' in analysis_results:
            market_data["silver_bullet_setup"] = analysis_results["silver_bullet_status"]
        
        # =============================================
        # ADDITIONAL FIRECRAWL INTELLIGENCE FOR CLAUDE
        # =============================================
        
        # Retail sentiment (contrarian indicator)
        if 'retail_sentiment' in analysis_results:
            market_data["retail_sentiment"] = analysis_results["retail_sentiment"]
        
        # VIX risk mode
        if 'vix_sentiment' in analysis_results:
            market_data["vix_sentiment"] = analysis_results["vix_sentiment"]
        
        # Currency strength
        if 'currency_strength' in analysis_results:
            market_data["currency_strength"] = analysis_results["currency_strength"]
        
        # TradingView technical
        if 'tv_technical' in analysis_results:
            market_data["tradingview_technical"] = analysis_results["tv_technical"]
        
        # Rate expectations
        if 'rate_expectations' in analysis_results:
            market_data["rate_expectations"] = analysis_results["rate_expectations"]
        
        # Economic calendar
        if 'economic_calendar' in analysis_results:
            market_data["economic_calendar"] = analysis_results["economic_calendar"]
        
        # Social sentiment
        if 'social_sentiment' in analysis_results:
            market_data["social_sentiment"] = analysis_results["social_sentiment"]
        
        # Options flow
        if 'options_flow' in analysis_results:
            market_data["options_flow"] = analysis_results["options_flow"]
        
        # Bond yields
        if 'bond_yields' in analysis_results:
            market_data["bond_yields"] = analysis_results["bond_yields"]
        
        # Intermarket analysis
        if 'intermarket' in analysis_results:
            market_data["intermarket"] = analysis_results["intermarket"]
        
        # Seasonal pattern
        if 'seasonal_pattern' in analysis_results:
            market_data["seasonal_pattern"] = analysis_results["seasonal_pattern"]
        
        # Economic surprise
        if 'economic_surprise' in analysis_results:
            market_data["economic_surprise"] = analysis_results["economic_surprise"]
        
        # BTC dominance (crypto)
        if 'btc_dominance' in analysis_results:
            market_data["btc_dominance"] = analysis_results["btc_dominance"]
        
        # Commodity correlations
        if 'oil_correlation' in analysis_results:
            market_data["oil_correlation"] = analysis_results["oil_correlation"]
        if 'gold_correlation' in analysis_results:
            market_data["gold_correlation"] = analysis_results["gold_correlation"]
        
        # Add Firecrawl real-time intelligence context (ENHANCED with Deep Research)
        if hasattr(bot, 'firecrawl_service') and bot.firecrawl_service:
            # Get comprehensive context including retail sentiment, VIX, etc.
            firecrawl_context = bot.firecrawl_service.get_market_context_for_claude(symbol)
            if firecrawl_context:
                market_data["firecrawl_intelligence"] = firecrawl_context
            
            # === DEEP RESEARCH INTELLIGENCE (AI-POWERED) ===
            # Get structured deep research data from Agent and Extract methods
            comprehensive_intel = bot.firecrawl_service.get_comprehensive_intelligence(symbol)
            
            # Add deep research context for Claude
            if comprehensive_intel:
                # Convert to formatted string for Claude's prompt
                deep_research_context = comprehensive_intel.to_claude_context()
                if deep_research_context:
                    market_data["deep_research_intelligence"] = deep_research_context
                
                # Add risk warnings and adjustments
                if comprehensive_intel.warnings:
                    market_data["intelligence_warnings"] = comprehensive_intel.warnings
                
                # Specific risk adjustments based on geopolitical analysis
                if comprehensive_intel.geopolitical:
                    geo = comprehensive_intel.geopolitical
                    market_data["geopolitical_risk_level"] = geo.risk_level
                    if geo.risk_level in ["high", "extreme"]:
                        market_data["risk_warning"] = f"⚠️ HIGH GEOPOLITICAL RISK ({geo.risk_level.upper()}) - REDUCE POSITION SIZES BY 25-50%"
                        market_data["confidence_adjustment"] = -25 if geo.risk_level == "high" else -40
                
                # Central bank policy context
                if comprehensive_intel.central_banks:
                    cb = comprehensive_intel.central_banks
                    if cb.divergence_plays:
                        market_data["cb_divergence_plays"] = cb.divergence_plays
                    market_data["cb_overall_bias"] = cb.overall_bias
                
                # Intermarket risk environment
                if comprehensive_intel.intermarket:
                    im = comprehensive_intel.intermarket
                    market_data["risk_environment"] = im.risk_environment
                    if im.trading_implications:
                        market_data["intermarket_implications"] = im.trading_implications
                    
                    # Risk-off environment adjustments
                    if im.risk_environment in ["strong_risk_off", "risk_off"]:
                        # Favor safe havens
                        if symbol in ["USDJPY", "EURJPY", "GBPJPY"]:
                            market_data["risk_adjustment_note"] = "Risk-off favors JPY strength (bearish bias)"
                        elif symbol in ["USDCHF", "EURCHF"]:
                            market_data["risk_adjustment_note"] = "Risk-off favors CHF strength (bearish bias)"
                        elif symbol == "XAUUSD":
                            market_data["risk_adjustment_note"] = "Risk-off favors Gold (bullish bias)"
            
            # Add symbol-specific fundamentals if available
            symbol_fundamentals = bot.firecrawl_service.get_cached_symbol_fundamentals(symbol)
            if symbol_fundamentals:
                market_data["symbol_fundamentals"] = {
                    "bias": symbol_fundamentals.fundamental_bias,
                    "key_drivers": symbol_fundamentals.key_drivers[:3],
                    "confidence": symbol_fundamentals.confidence,
                    "trade_recommendation": symbol_fundamentals.trade_recommendation
                }
            
            # Add specific intelligence for trade validation
            if retail_contrarian:
                market_data["retail_contrarian_signal"] = retail_contrarian
            if vix_risk_mode:
                market_data["vix_risk_mode"] = vix_risk_mode
            if currency_strength_recommendation:
                market_data["currency_strength_tip"] = currency_strength_recommendation
        
        # Add learning context from Claude's trade reviews
        if bot.learning_service:
            try:
                session_name = market_data.get("session", "")
                learning_context = await bot.learning_service.build_context_for_claude(
                    symbol=symbol,
                    session=session_name
                )
                if learning_context:
                    market_data["learning_context"] = learning_context
                    logger.debug(f"Added learning context for {symbol}")
            except Exception as e:
                logger.warning(f"Could not add learning context: {e}")
        
        # Add MFE/MAE excursion data for SL/TP validation
        try:
            from ..analysis.excursion_analysis import ExcursionAnalyzer
            _excursion = ExcursionAnalyzer()
            _exc_result = await _excursion.compute(symbol, direction='all', lookback_days=90)
            if _exc_result and _exc_result.sample_size >= 5:
                market_data["excursion_data"] = _exc_result.to_dict()
                logger.debug(f"Added MFE/MAE data for {symbol}: opt_SL={_exc_result.optimal_sl:.5f}, opt_TP={_exc_result.optimal_tp:.5f}")
        except Exception as _exc_err:
            logger.debug(f"Could not compute excursion data for {symbol}: {_exc_err}")
        
        # Add setup playbook from historical trade data
        if bot.learning_service:
            try:
                if not hasattr(bot, '_playbook_cache') or not bot._playbook_cache:
                    bot._playbook_cache = await bot.learning_service.build_setup_playbook()
                    bot._playbook_cache_time = datetime.now(timezone.utc)
                elif hasattr(bot, '_playbook_cache_time') and (datetime.now(timezone.utc) - bot._playbook_cache_time).total_seconds() > 86400:
                    bot._playbook_cache = await bot.learning_service.build_setup_playbook()
                    bot._playbook_cache_time = datetime.now(timezone.utc)
                if bot._playbook_cache:
                    market_data["setup_playbook"] = bot._playbook_cache
            except Exception as e:
                logger.debug(f"Could not build setup playbook: {e}")
        
        # Add precious metals context for gold/silver
        if symbol in bot.PRECIOUS_METALS and bot.precious_metals_analyzer:
            try:
                # Get prices for both metals
                gold_price = market_data.get('current_price', 0) if symbol == 'XAUUSD' else 0
                silver_price = market_data.get('current_price', 0) if symbol == 'XAGUSD' else 0
                
                # Try to get the other metal's price
                other_symbol = 'XAGUSD' if symbol == 'XAUUSD' else 'XAUUSD'
                other_data = await bot.data_fetcher.get_ohlcv(other_symbol, settings.timeframes.execution_tf)
                if other_data and 'close' in other_data and len(other_data['close']) > 0:
                    if symbol == 'XAUUSD':
                        silver_price = float(other_data['close'].iloc[-1])
                    else:
                        gold_price = float(other_data['close'].iloc[-1])
                
                # Generate precious metals context
                if gold_price > 0 and silver_price > 0:
                    geopolitical = 'normal'
                    if bot.news_service:
                        geo_level = bot.news_service.get_geopolitical_risk_level()
                        geopolitical = geo_level if geo_level else 'normal'
                    
                    market_data["precious_metals_context"] = bot.precious_metals_analyzer.get_context_for_claude(
                        gold_price=gold_price,
                        silver_price=silver_price,
                        geopolitical_risk=geopolitical
                    )
                    logger.debug(f"Added precious metals context for {symbol}")
            except Exception as e:
                logger.warning(f"Could not add precious metals context: {e}")
        
        # Prepare ENRICHED analysis data for Claude (full price levels, not just counts)
        # Use .get() to avoid KeyError if any analyzer failed
        ms_obj = analysis_results.get("market_structure")
        fvg_obj = analysis_results.get("fvg")
        ob_obj = analysis_results.get("order_blocks")
        liq_obj = analysis_results.get("liquidity")
        
        analysis_data = {
            "market_structure": {
                "trend": ms_obj.trend.value if ms_obj and hasattr(ms_obj, 'trend') else "unknown",
                "structure_breaks": len(ms_obj.structure_breaks) if ms_obj and hasattr(ms_obj, 'structure_breaks') else 0,
                "break_details": [
                    {"type": sb.type if hasattr(sb, 'type') else str(sb), 
                     "price": sb.price if hasattr(sb, 'price') else 0}
                    for sb in ms_obj.structure_breaks[-5:]
                ] if ms_obj and hasattr(ms_obj, 'structure_breaks') and ms_obj.structure_breaks else [],
                "swing_highs": [float(sh.price) if hasattr(sh, 'price') else float(sh) 
                                for sh in (ms_obj.swing_highs[-5:] if hasattr(ms_obj, 'swing_highs') and ms_obj.swing_highs else [])] if ms_obj else [],
                "swing_lows": [float(sl.price) if hasattr(sl, 'price') else float(sl) 
                               for sl in (ms_obj.swing_lows[-5:] if hasattr(ms_obj, 'swing_lows') and ms_obj.swing_lows else [])] if ms_obj else [],
            },
            "fvg": {
                "bullish": len(fvg_obj.bullish_fvgs) if fvg_obj and hasattr(fvg_obj, 'bullish_fvgs') else 0,
                "bearish": len(fvg_obj.bearish_fvgs) if fvg_obj and hasattr(fvg_obj, 'bearish_fvgs') else 0,
                "active": len(fvg_obj.active_fvgs) if fvg_obj and hasattr(fvg_obj, 'active_fvgs') else 0,
                "bullish_zones": [
                    {"high": float(f.top), "low": float(f.bottom)} 
                    for f in fvg_obj.bullish_fvgs[-3:]
                ] if fvg_obj and hasattr(fvg_obj, 'bullish_fvgs') and fvg_obj.bullish_fvgs else [],
                "bearish_zones": [
                    {"high": float(f.top), "low": float(f.bottom)} 
                    for f in fvg_obj.bearish_fvgs[-3:]
                ] if fvg_obj and hasattr(fvg_obj, 'bearish_fvgs') and fvg_obj.bearish_fvgs else [],
            },
            "order_blocks": {
                "bullish": len(ob_obj.bullish_obs) if ob_obj and hasattr(ob_obj, 'bullish_obs') else 0,
                "bearish": len(ob_obj.bearish_obs) if ob_obj and hasattr(ob_obj, 'bearish_obs') else 0,
                "bullish_zones": [
                    {"high": float(ob.high), "low": float(ob.low)} 
                    for ob in ob_obj.bullish_obs[-3:]
                ] if ob_obj and hasattr(ob_obj, 'bullish_obs') and ob_obj.bullish_obs else [],
                "bearish_zones": [
                    {"high": float(ob.high), "low": float(ob.low)} 
                    for ob in ob_obj.bearish_obs[-3:]
                ] if ob_obj and hasattr(ob_obj, 'bearish_obs') and ob_obj.bearish_obs else [],
            },
            "liquidity": {
                "nearest_bsl": float(liq_obj.nearest_bsl) if liq_obj and liq_obj.nearest_bsl else None,
                "nearest_ssl": float(liq_obj.nearest_ssl) if liq_obj and liq_obj.nearest_ssl else None,
                "all_bsl": [float(p.price) if hasattr(p, 'price') else float(p) 
                            for p in (liq_obj.bsl_pools[-5:] if hasattr(liq_obj, 'bsl_pools') and liq_obj.bsl_pools else [])] if liq_obj else [],
                "all_ssl": [float(p.price) if hasattr(p, 'price') else float(p) 
                            for p in (liq_obj.ssl_pools[-5:] if hasattr(liq_obj, 'ssl_pools') and liq_obj.ssl_pools else [])] if liq_obj else [],
                "equal_highs": [float(eh.price) if hasattr(eh, 'price') else float(eh) 
                                for eh in (liq_obj.equal_highs[-3:] if hasattr(liq_obj, 'equal_highs') and liq_obj.equal_highs else [])] if liq_obj else [],
                "equal_lows": [float(el.price) if hasattr(el, 'price') else float(el) 
                               for el in (liq_obj.equal_lows[-3:] if hasattr(liq_obj, 'equal_lows') and liq_obj.equal_lows else [])] if liq_obj else [],
            },
            "volume": analysis_results.get("volume", {})
        }
        
        # Add MTF context to market_data for Claude (and cache for position re-eval)
        if mtf_result:
            market_data["htf_bias"] = mtf_result.overall_bias.value
            market_data["htf_alignment"] = mtf_result.alignment
            market_data["htf_can_trade_long"] = mtf_result.can_trade_long
            market_data["htf_can_trade_short"] = mtf_result.can_trade_short
            bot._last_mtf_results[symbol] = {
                "d1_bias": mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else "unknown",
                "h4_bias": mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else "unknown",
                "alignment": mtf_result.alignment,
            }
            # D1 context (top-down starting point)
            market_data["d1_bias"] = mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else None
            market_data["d1_structure"] = mtf_result.daily_analysis.structure if mtf_result.daily_analysis else None
            market_data["d1_trend"] = mtf_result.daily_analysis.trend if mtf_result.daily_analysis else None
            # H4 context
            market_data["h4_bias"] = mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else None
            market_data["h4_structure"] = mtf_result.h4_analysis.structure if mtf_result.h4_analysis else None
            market_data["h4_trend"] = mtf_result.h4_analysis.trend if mtf_result.h4_analysis else None
            # H1 context
            market_data["h1_bias"] = mtf_result.h1_analysis.bias.value if mtf_result.h1_analysis else None
            market_data["h1_structure"] = mtf_result.h1_analysis.structure if mtf_result.h1_analysis else None
            market_data["h1_trend"] = mtf_result.h1_analysis.trend if mtf_result.h1_analysis else None
            market_data["htf_key_levels"] = mtf_result.htf_key_levels
            # M15 context (execution timeframe)
            market_data["m15_bias"] = mtf_result.m15_analysis.bias.value if mtf_result.m15_analysis else None
            market_data["m15_structure"] = mtf_result.m15_analysis.structure if mtf_result.m15_analysis else None
            market_data["m15_trend"] = mtf_result.m15_analysis.trend if mtf_result.m15_analysis else None
            # M5/M1 context (precision entry)
            market_data["m5_bias"] = mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else None
            market_data["m5_structure"] = mtf_result.m5_analysis.structure if mtf_result.m5_analysis else None
            market_data["m5_trend"] = mtf_result.m5_analysis.trend if mtf_result.m5_analysis else None
            market_data["m1_bias"] = mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else None
            market_data["m1_structure"] = mtf_result.m1_analysis.structure if mtf_result.m1_analysis else None
            market_data["m1_trend"] = mtf_result.m1_analysis.trend if mtf_result.m1_analysis else None
        
        # Add Fibonacci/OTE context to market_data for Claude
        if fib_analysis:
            market_data["fibonacci_zone"] = fib_analysis.price_zone.value
            market_data["in_ote"] = fib_analysis.in_ote
            market_data["optimal_entry"] = fib_analysis.optimal_entry
            market_data["fib_levels"] = fib_analysis.fib_levels.to_dict() if fib_analysis.fib_levels else None
        
        # Inject last signal memory so Claude knows what it said last cycle
        if symbol in bot._last_signal_per_symbol:
            market_data["last_signal"] = bot._last_signal_per_symbol[symbol]
        
        # Regenerate M15 chart WITH ICT overlays now that analysis is complete
        print(f"[CHART-DEBUG] {symbol}: ob_obj={type(ob_obj).__name__ if ob_obj else None}, fvg_obj={type(fvg_obj).__name__ if fvg_obj else None}, liq_obj={type(liq_obj).__name__ if liq_obj else None}, ms_obj={type(ms_obj).__name__ if ms_obj else None}", flush=True)
        try:
            _chart_obs = []
            _chart_fvgs = []
            _chart_liq = []
            _chart_swings = []
            if ob_obj:
                for ob in (getattr(ob_obj, 'bullish_obs', []) or [])[-5:]:
                    _chart_obs.append({"top": float(ob.high), "bottom": float(ob.low), "type": "bullish"})
                for ob in (getattr(ob_obj, 'bearish_obs', []) or [])[-5:]:
                    _chart_obs.append({"top": float(ob.high), "bottom": float(ob.low), "type": "bearish"})
            if fvg_obj:
                for f in (getattr(fvg_obj, 'bullish_fvgs', []) or [])[-5:]:
                    _chart_fvgs.append({"top": float(f.top), "bottom": float(f.bottom), "type": "bullish"})
                for f in (getattr(fvg_obj, 'bearish_fvgs', []) or [])[-5:]:
                    _chart_fvgs.append({"top": float(f.top), "bottom": float(f.bottom), "type": "bearish"})
            if liq_obj:
                for p in (getattr(liq_obj, 'bsl_pools', []) or [])[-5:]:
                    _price = float(p.price) if hasattr(p, 'price') else float(p)
                    _chart_liq.append({"price": _price, "label": "BSL", "color": "purple"})
                for p in (getattr(liq_obj, 'ssl_pools', []) or [])[-5:]:
                    _price = float(p.price) if hasattr(p, 'price') else float(p)
                    _chart_liq.append({"price": _price, "label": "SSL", "color": "purple"})
            if ms_obj:
                for sh in (getattr(ms_obj, 'swing_highs', []) or [])[-8:]:
                    _p = float(sh.price) if hasattr(sh, 'price') else float(sh)
                    _idx = getattr(sh, 'index', None) or getattr(sh, 'bar_index', None)
                    _chart_swings.append({"price": _p, "type": "high", "index": _idx})
                for sl_pt in (getattr(ms_obj, 'swing_lows', []) or [])[-8:]:
                    _p = float(sl_pt.price) if hasattr(sl_pt, 'price') else float(sl_pt)
                    _idx = getattr(sl_pt, 'index', None) or getattr(sl_pt, 'bar_index', None)
                    _chart_swings.append({"price": _p, "type": "low", "index": _idx})
            if _chart_obs or _chart_fvgs or _chart_liq or _chart_swings:
                _enhanced_chart = await bot._generate_chart_image(
                    df, symbol,
                    order_blocks=_chart_obs if _chart_obs else None,
                    fvg_zones=_chart_fvgs if _chart_fvgs else None,
                    liquidity_levels=_chart_liq if _chart_liq else None,
                    swing_points=_chart_swings if _chart_swings else None,
                )
                if _enhanced_chart:
                    chart_base64 = _enhanced_chart
                    print(f"[CHART] {symbol}: Enhanced M15 chart with {len(_chart_obs)} OBs, {len(_chart_fvgs)} FVGs, {len(_chart_liq)} liq levels, {len(_chart_swings)} swings", flush=True)
            else:
                print(f"[CHART] {symbol}: No ICT overlays found (OBs={len(_chart_obs)}, FVGs={len(_chart_fvgs)}, liq={len(_chart_liq)}, swings={len(_chart_swings)})", flush=True)
        except Exception as overlay_err:
            print(f"[CHART] {symbol}: Overlay error: {overlay_err}", flush=True)
        
        # Get Claude's analysis
        logger.info(f"Requesting Claude analysis for {symbol}...")
        if bot_state:
            bot_state.calling_claude(symbol)
        
        claude_result = await bot.claude_client.analyze_chart_async(
            chart_image_base64=chart_base64,
            symbol=symbol,
            timeframe=settings.timeframes.execution_tf,
            strategy_context=strategy_context,
            market_data=market_data,
            analysis_data=analysis_data,
            additional_charts=additional_charts if additional_charts else None
        )
        
        # Extract trade signal from result
        trade_signal = claude_result.signal
        
        # Print detailed analysis block to terminal
        bot._print_analysis_summary(symbol, trade_signal, claude_result, market_data)
        
        # Update cycle-to-cycle signal memory
        bot._last_signal_per_symbol[symbol] = {
            "direction": trade_signal.direction,
            "confidence": trade_signal.confidence,
            "trade_type": getattr(trade_signal, 'trade_type', 'intraday'),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reasoning": trade_signal.reasoning or "",
        }
        
        # Mechanical-vs-Claude agreement telemetry (measures LLM value-add
        # over the pure rule-based baseline)
        _mech_baseline = analysis_results.get("mechanical_setup")
        if _mech_baseline:
            _mech_dir = _mech_baseline.get("direction", "?")
            if trade_signal.direction in ("long", "short"):
                _mech_agree = "AGREE" if _mech_dir == trade_signal.direction else "DISAGREE"
            else:
                _mech_agree = "CLAUDE_PASSED"
            print(
                f"[MECH-VS-CLAUDE] {symbol}: mechanical={_mech_dir.upper()} "
                f"({_mech_baseline.get('confidence', 0):.0%}) vs "
                f"claude={trade_signal.direction.upper()} "
                f"({trade_signal.confidence:.0%}) -> {_mech_agree}",
                flush=True,
            )
            logger.info(
                f"[MECH-VS-CLAUDE] {symbol}: {_mech_agree} "
                f"(mech={_mech_dir}, claude={trade_signal.direction})"
            )
        
        # Log Claude's response
        if bot_state:
            bot_state.claude_response(
                symbol, 
                trade_signal.direction, 
                trade_signal.confidence,
                trade_signal.reasoning or ""
            )
        
        # Save signal to the signals store (for dashboard display)
        bot._save_signal(symbol, trade_signal, analysis_results)
        asyncio.create_task(broadcast_analysis_update(symbol, {
            "direction": trade_signal.direction,
            "confidence": trade_signal.confidence,
            "rr_ratio": trade_signal.risk_reward,
            "entry_price": trade_signal.entry_price,
            "stop_loss": trade_signal.stop_loss,
            "take_profit": trade_signal.take_profit,
            "market_structure": trade_signal.market_structure
        }))
        
        # Add signal to activity feed
        from ..api.routes.activity import add_activity
        add_activity(
            "signal_generated",
            f"Signal: {trade_signal.direction.upper()} {symbol} ({trade_signal.confidence:.0%} confidence)",
            symbol,
            {
                "direction": trade_signal.direction,
                "confidence": trade_signal.confidence,
                "entry_price": trade_signal.entry_price,
                "market_structure": trade_signal.market_structure
            }
        )
        
        # Telegram notifications for signals disabled — only notify on executed trades, TP/SL hits
        
        # Check if we have a valid trade signal
        if trade_signal.direction == "no_trade":
            logger.info(f"No trade signal for {symbol}: {trade_signal.reasoning[:100] if trade_signal.reasoning else 'No reason given'}")
            await bot._record_terminal_decision(
                "no_trade",
                symbol,
                direction="no_trade",
                entry=trade_signal.entry_price or current_price,
                sl=trade_signal.stop_loss or 0.0,
                tp=trade_signal.take_profit or 0.0,
                confidence=trade_signal.confidence,
                reason=(
                    trade_signal.reasoning[:200]
                    if trade_signal.reasoning
                    else "No setup"
                ),
            )
            if bot_state:
                bot_state.trade_decision(symbol, "no_trade", trade_signal.reasoning[:100] if trade_signal.reasoning else "No setup")
                bot_state.symbol_complete(symbol, "no_trade")
            return
        
        # ============================================
        # SIGNAL PRICE SANITY CHECKS (A5) — shared normalizer
        # ============================================
        from .signal_normalizer import normalize_signal_prices
        _norm = normalize_signal_prices(
            trade_signal, claude_result, current_price, symbol
        )
        if _norm.rejected:
            logger.warning(f"SIGNAL REJECTED for {symbol}: {_norm.reject_reason}")
            if bot_state:
                bot_state.trade_decision(symbol, "rejected", _norm.reject_reason)
                bot_state.symbol_complete(symbol, "invalid_signal")
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
        # ATR-BASED MINIMUM SL DISTANCE (v3)
        # Ensure SL is at least 1.5x ATR(14) from entry
        # to avoid stop-outs from normal price noise.
        # ============================================
        try:
            from ..utils.candle_utils import calculate_atr as _calc_atr
            _atr_series = _calc_atr(df, period=14)
            _atr_val = float(_atr_series.iloc[-1]) if not _atr_series.empty and not np.isnan(_atr_series.iloc[-1]) else None
            if _atr_val and _atr_val > 0 and _sl and _entry:
                _min_sl_dist = _atr_val * 1.5
                _current_sl_dist = abs(_entry - _sl)
                if _current_sl_dist < _min_sl_dist:
                    _old_sl = _sl
                    if _dir == 'long':
                        _sl = _entry - _min_sl_dist
                    else:
                        _sl = _entry + _min_sl_dist
                    trade_signal.stop_loss = _sl
                    _new_tp_dist = abs(_tp - _entry) if _tp else 0
                    _new_rr = _new_tp_dist / _min_sl_dist if _min_sl_dist > 0 else 0
                    logger.info(
                        f"[ATR-SL-ADJUST] {symbol}: SL widened from {_old_sl:.5f} to {_sl:.5f} "
                        f"(ATR={_atr_val:.5f}, min_dist={_min_sl_dist:.5f}, new R:R={_new_rr:.2f})"
                    )
                    print(
                        f"[ATR-SL-ADJUST] {symbol}: SL {_old_sl:.5f} -> {_sl:.5f} "
                        f"(1.5x ATR={_min_sl_dist:.5f}), R:R now {_new_rr:.2f}:1",
                        flush=True
                    )
                    if _new_rr < 1.5:
                        logger.warning(
                            f"[ATR-SL-BLOCK] {symbol}: After ATR SL widen, R:R={_new_rr:.2f} < 1.5. "
                            f"SL too wide for TP target. Blocking trade."
                        )
                        print(
                            f"[ATR-SL-BLOCK] {symbol}: R:R {_new_rr:.2f}:1 after ATR widen — trade blocked.",
                            flush=True
                        )
                        return
        except Exception as _atr_err:
            logger.debug(f"[ATR-SL] Could not apply ATR SL check for {symbol}: {_atr_err}")
        
        # ============================================
        # R:R ENFORCEMENT (A6)
        # Ensure TP distance >= min_rr * SL distance
        # If Claude gives bad R:R, auto-correct the TP
        # ============================================
        # Adjust min R:R based on trade type AND asset category
        # Crypto assets need higher R:R because even minimum lot sizes carry
        # significant dollar risk — a 1.5:1 R:R on ETH/BTC is not worth it.
        _trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
        from ..config import get_symbol_spec as _get_spec_rr
        _spec_rr = _get_spec_rr(symbol)
        
        if _spec_rr.category == 'crypto':
            _rr_by_type = {'scalp': 2.0, 'intraday': 2.5, 'swing': 3.5}
        else:
            _rr_by_type = {'scalp': 1.5, 'intraday': 2.0, 'swing': 3.0}
        
        min_rr = _rr_by_type.get(_trade_type, settings.trading.min_risk_reward)
        logger.info(f"R:R threshold for {symbol} ({_trade_type}, {_spec_rr.category}): {min_rr:.1f}:1")
        sl_distance = abs(_entry - _sl)
        tp_distance = abs(_tp - _entry)
        
        if sl_distance > 0:
            actual_rr = tp_distance / sl_distance
        else:
            actual_rr = 0.0
        
        if actual_rr < min_rr and sl_distance > 0:
            # R:R is below minimum. Two tiers:
            # 1) If R:R >= hard floor, let Trade Judge decide.
            # 2) If R:R < hard floor, hard-reject.
            # In AGGRESSIVE mode (demo data collection), lower floor to 1.0
            # to collect more trade outcomes for learning.
            _is_aggressive = (bot.scaling_manager and 
                              bot.scaling_manager.current_mode.value == 'aggressive')
            _hard_floor_rr = 1.0 if _is_aggressive else 1.5
            
            if actual_rr < _hard_floor_rr:
                # Reward < risk — hard reject regardless of setup quality
                logger.warning(
                    f"[BLOCKED] {symbol}: R:R {actual_rr:.2f}:1 below hard floor {_hard_floor_rr:.1f}:1 "
                    f"(risk ${sl_distance:.2f} > reward ${tp_distance:.2f}). Rejected."
                )
                print(
                    f"[BLOCKED] {symbol}: R:R {actual_rr:.2f}:1 — risking ${sl_distance:.2f} "
                    f"for only ${tp_distance:.2f} reward. Not worth it.",
                    flush=True
                )
                return
            else:
                # R:R is between 1.0 and min_rr — borderline.
                # Let the Trade Judge decide with full context.
                logger.info(
                    f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 below target {min_rr:.1f}:1 "
                    f"but above 1.0 floor. Passing to Trade Judge for evaluation."
                )
                print(
                    f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 (target {min_rr:.1f}:1) — "
                    f"borderline, letting Trade Judge decide.",
                    flush=True
                )
        else:
            logger.info(f"R:R OK for {symbol}: {actual_rr:.2f} (min {min_rr:.1f})")
        
        # Claude's TP is trusted — based on structure, liquidity, IPDA levels.
        # No hardcoded TP floors or ceilings. R:R enforcement above handles rejection.
        
        # ============================================
        # COUNTER-TREND SCALP CAP
        # If scalp direction opposes the D1 bias, enforce stricter limits
        # ============================================
        _trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
        _d1_bias = market_data.get('d1_bias', '').lower() if market_data else ''
        _is_counter_trend_scalp = (
            _trade_type == 'scalp'
            and _d1_bias in ('bullish', 'bearish')
            and (
                (_d1_bias == 'bullish' and _dir == 'short')
                or (_d1_bias == 'bearish' and _dir == 'long')
            )
        )
        if _is_counter_trend_scalp:
            # Cap confidence at 70%
            if trade_signal.confidence > 0.70:
                logger.info(
                    f"[COUNTER-SCALP] {symbol}: Counter-D1-trend scalp confidence "
                    f"{trade_signal.confidence:.0%} -> capped at 70%"
                )
                trade_signal.confidence = 0.70
            # Enforce 2.0:1 R:R minimum
            if actual_rr < settings.trading.gate_counter_trend_rr_floor:
                logger.warning(
                    f"[BLOCKED] {symbol}: Counter-D1-trend scalp R:R {actual_rr:.2f}:1 "
                    f"below {settings.trading.gate_counter_trend_rr_floor:.1f}:1 minimum. "
                    f"D1={_d1_bias}, dir={_dir}. Rejected."
                )
                print(
                    f"[BLOCKED] {symbol}: Counter-trend scalp needs "
                    f"{settings.trading.gate_counter_trend_rr_floor:.1f}:1 R:R, "
                    f"got {actual_rr:.2f}:1. Skipping.",
                    flush=True
                )
                return
        
        # ============================================
        # PIPELINE ENTRY GATES (shared with replay)
        # ============================================
        _pipeline_ctx, _zone_settings, _use_zone_gate_live = bot._build_pipeline_context(
            symbol=symbol,
            trade_signal=trade_signal,
            market_data=market_data,
            analysis_results=analysis_results,
            pd_analysis=pd_analysis,
            current_price=current_price,
            actual_rr=actual_rr,
            is_crypto=is_crypto,
            is_counter_trend_scalp=_is_counter_trend_scalp,
        )
        _session_name, _is_kill = bot._session_for_gates()
        _entry_gate = evaluate_entry_gates(
            _pipeline_ctx,
            zone_settings=_zone_settings,
            use_zone_gate=_use_zone_gate_live,
            session_name=_session_name,
            is_kill_zone=_is_kill,
            asian_penalty=settings.trading.gate_session_penalty_asian,
        )
        if _entry_gate.blocked:
            await bot._handle_pipeline_gate_block(symbol, _entry_gate, ctx=_pipeline_ctx)
            return
        trade_signal.confidence = _pipeline_ctx.confidence
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
        # PIPELINE PERMISSION GATES (scaling + correlation)
        # ============================================
        _pipeline_ctx.scaling_aggressive = (
            bot.scaling_manager is not None
            and bot.scaling_manager.current_mode.value == "aggressive"
        )
        _pipeline_ctx.confidence = trade_signal.confidence

        def _correlation_check():
            if bot.correlation_service:
                return bot.correlation_service.should_block_trade(
                    symbol, direction=trade_signal.direction
                )
            return False, ""

        _perm_gate = evaluate_trade_permission_gates(
            _pipeline_ctx,
            scaling_manager=bot.scaling_manager,
            daily_trades=bot.daily_trades,
            gate_min_confidence=settings.trading.gate_min_confidence,
            correlation_check=_correlation_check if bot.correlation_service else None,
        )
        if _perm_gate.blocked:
            await bot._handle_pipeline_gate_block(symbol, _perm_gate, ctx=_pipeline_ctx)
            return
        trade_signal.confidence = _pipeline_ctx.confidence
        if (
            hasattr(bot, "_post_cooldown_symbols")
            and symbol in bot._post_cooldown_symbols
        ):
            bot._post_cooldown_symbols.discard(symbol)

        # R:R was already validated in the A6 block above (rejected if below 1.0,
        # borderline passed to Trade Judge). Claude's TP/SL are never modified.
        # We do NOT reject based on Claude's self-reported risk_reward field here.
        # The final validate_trade() call will do the definitive R:R check.
        
        # =============================================
        # DIRECTION-FLIP COOLDOWN (shared flip guard)
        # =============================================
        from .scaling_gates import evaluate_flip_guard

        _flip_outcome = evaluate_flip_guard(
            symbol=symbol,
            direction=trade_signal.direction,
            confidence=trade_signal.confidence,
            last_signal_direction=bot._last_signal_direction,
            direction_flipped=_direction_flipped,
            reversal_reentry=getattr(trade_signal, "reversal_reentry", False),
        )
        _pipeline_ctx.gate_path.extend(_flip_outcome.gate_path)
        if _flip_outcome.blocked:
            logger.warning(f"[FLIP-GUARD] {symbol}: {_flip_outcome.reason}")
            from ..api.routes.activity import add_activity

            last_dir = bot._last_signal_direction.get(symbol, ("", datetime.now(timezone.utc)))[0]
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
                f"[BLOCKED] ║  Direction flip {symbol}: {_flip_outcome.reason}",
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
                gate_id=_flip_outcome.gate_id,
                direction=trade_signal.direction,
                entry=trade_signal.entry_price or current_price,
                sl=trade_signal.stop_loss or 0.0,
                tp=trade_signal.take_profit or 0.0,
                confidence=trade_signal.confidence,
                reason=_flip_outcome.reason,
                details={"gate_path": list(_pipeline_ctx.gate_path)},
            )
            return
        if _flip_outcome.gate_path and _flip_outcome.gate_path[0].startswith("flip_guard_bypass"):
            logger.info(f"[FLIP-GUARD] {symbol}: Bypassing cooldown ({_flip_outcome.gate_path[0]})")
        elif _flip_outcome.gate_path == ["flip_guard_high_confidence"]:
            logger.info(
                f"[FLIP-GUARD] {symbol}: Allowing high-confidence flip "
                f"({trade_signal.confidence:.0%} >= 80%)"
            )

        bot._last_signal_direction[symbol] = (
            trade_signal.direction,
            datetime.now(timezone.utc),
        )

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
                        timeout=15.0  # Don't wait more than 15s for size rec
                    )
                    if rec.get('recommended_lots'):
                        claude_size_rec = rec['recommended_lots']
                        logger.info(f"Claude size recommendation: {claude_size_rec} lots ({rec.get('reasoning', '')})")
            except asyncio.TimeoutError:
                logger.warning("Claude position size recommendation timed out (15s)")
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
            if bot.news_service:
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
                    bot.lots = lots
            
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
                    # Don't block entirely - switch to pending order for better entry
                    if trade_signal.order_type == 'market':
                        # Switch to limit order at OTE zone
                        if trade_signal.direction == 'long':
                            trade_signal.order_type = 'buy_limit'
                            trade_signal.entry_price = pd_analysis.ote_low  # 79% retracement
                            logger.info(f"🔄 Converted to BUY LIMIT @ {trade_signal.entry_price:.5f} (OTE zone)")
                        else:
                            trade_signal.order_type = 'sell_limit'
                            trade_signal.entry_price = pd_analysis.ote_high  # 62% retracement
                            logger.info(f"🔄 Converted to SELL LIMIT @ {trade_signal.entry_price:.5f} (OTE zone)")
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
            if trade_signal.order_type == 'market':
                if displacement_analysis and not displacement_analysis.distribution_confirmed:
                    # Convert to pending order - wait for displacement
                    if amd_state and amd_state.phase.value in ['manipulation', 'accumulation']:
                        logger.warning(
                            f"⚠️ NO DISPLACEMENT: Converting market order to pending "
                            f"(AMD Phase: {amd_state.phase.value})"
                        )
                        if trade_signal.direction == 'long':
                            trade_signal.order_type = 'buy_limit'
                            # Entry at FVG or OB if available
                            entry_zone = analysis_results.get("fvg", {})
                            if hasattr(entry_zone, 'bullish_fvgs') and entry_zone.bullish_fvgs:
                                nearest_fvg = min(entry_zone.bullish_fvgs, key=lambda x: abs(x.midpoint - current_price))
                                trade_signal.entry_price = nearest_fvg.midpoint
                            elif pd_analysis:
                                trade_signal.entry_price = pd_analysis.ote_low
                            logger.info(f"🔄 Converted to BUY LIMIT @ {trade_signal.entry_price:.5f}")
                        else:
                            trade_signal.order_type = 'sell_limit'
                            entry_zone = analysis_results.get("fvg", {})
                            if hasattr(entry_zone, 'bearish_fvgs') and entry_zone.bearish_fvgs:
                                nearest_fvg = min(entry_zone.bearish_fvgs, key=lambda x: abs(x.midpoint - current_price))
                                trade_signal.entry_price = nearest_fvg.midpoint
                            elif pd_analysis:
                                trade_signal.entry_price = pd_analysis.ote_high
                            logger.info(f"🔄 Converted to SELL LIMIT @ {trade_signal.entry_price:.5f}")
                    else:
                        logger.info("✅ Displacement confirmed - proceeding with market order")
            
            # GATE 3: Displacement Check for Market Orders
            if abs(trade_signal.confidence - _conf_used_for_sizing) > 0.001:
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
                if bot.news_service:
                    try:
                        _news_mult, _ = bot.news_service.should_reduce_size(symbol)
                        if _news_mult < 1.0:
                            from ..config import normalize_lots as _norm_news2
                            final_lots = _norm_news2(symbol, final_lots * _news_mult)
                    except Exception:
                        pass
                position_size.lots = final_lots
                logger.info(
                    f"[CONF-RESIZE] {symbol}: lots re-sized after confidence "
                    f"{_conf_used_for_sizing:.0%} -> {trade_signal.confidence:.0%} "
                    f"({final_lots} lots)"
                )
            
            # FINAL SAFETY CHECK before execution
            # Block if position size is 0 (blocked pair) or symbol is dangerous
            if position_size.lots <= 0:
                logger.error(f"🚫 BLOCKED: Position size is 0 for {symbol} - trade not executed")
                if bot_state:
                    bot_state.error(symbol, "Position size 0 - blocked pair")
                return
            
            if symbol.upper().endswith('BTC') or symbol.upper().endswith('BIT'):
                logger.error(f"🚫 FINAL BLOCK: {symbol} is BTC/BIT pair - REFUSING to execute!")
                if bot_state:
                    bot_state.error(symbol, "BTC/BIT pair blocked at execution")
                return
            
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

                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  {verdict_label} {symbol} — \"{reason}\"  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )

                await save_signal_to_db(
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
                        )
                        return
                
                reason = judge_outcome.reason or 'Judge demoted'
                flags = judge_outcome.risk_flags
                logger.info(
                    f"[JUDGE] Demoted {symbol} {trade_signal.direction} market -> "
                    f"{trade_signal.order_type} @ {demoted_entry:.5f} (reason: {reason})"
                )
                
                # Log to activity feed
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
                
                # Print DEMOTE to terminal
                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  DEMOTE {symbol} — \"{reason}\" → limit @ {demoted_entry:.5f}  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )
                
                # Save demoted signal to DB for correlation
                await save_signal_to_db(
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
                
                # Print APPROVE to terminal
                flags_str = ", ".join(flags) if flags else "none"
                print(
                    f"[JUDGE] ║  APPROVE {symbol} — \"{reason}\"  "
                    f"| flags: [{flags_str}]",
                    flush=True
                )
                
                # Save approved signal to DB for correlation
                await save_signal_to_db(
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

            if result.success:
                _risk_pct = compute_booked_risk_percent(
                    position_size.lots,
                    _final_entry,
                    _final_sl,
                    symbol,
                    account_info.balance,
                )
                if _risk_pct <= 0:
                    _risk_pct = (
                        size_result.risk_percent
                        if hasattr(size_result, 'risk_percent')
                        else bot.risk_manager.risk_per_trade
                    )
                if _trade_reservation:
                    _trade_reservation.risk_percent = _risk_pct
                    bot.reservation_ledger.commit_risk(_trade_reservation)
                print(
                    f"[RISK] {symbol}: Daily risk +{_risk_pct*100:.1f}%, "
                    f"total: {bot.risk_manager.daily_risk_used*100:.1f}%/"
                    f"{bot.risk_manager.max_daily_risk*100:.0f}%",
                    flush=True,
                )
                
                # Gap 21: Track signal hash to prevent duplicates
                bot._recent_signal_hashes.add(signal_hash)
                bot._signal_hash_expiry[signal_hash] = datetime.now(timezone.utc)
                
                logger.info(f"✓ Trade executed: {trade_signal.direction.upper()} {symbol}")
                logger.info(f"  Ticket: {result.ticket}, Fill Price: {result.fill_price}")
                
                # Gap 57: Verify order actually exists in MT5
                # Only verify for market orders — pending orders won't appear in positions yet
                is_pending_order = order_type in ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
                if result.ticket and not bot.mt5_client.is_simulation and not is_pending_order:
                    await asyncio.sleep(0.5)  # Brief delay for MT5 to process
                    positions = await bot.mt5_client.get_positions(symbol=symbol)
                    
                    # MT5 Position is a dataclass, access attributes directly
                    position_exists = any(
                        p.ticket == result.ticket for p in positions
                    )
                    
                    if not position_exists:
                        logger.error(
                            f"⚠ Order reported success but position {result.ticket} not found in MT5! "
                            f"Manual verification required."
                        )
                        # Don't track the position if it doesn't exist
                        return
                    
                    logger.info(f"  ✓ Position verified in MT5")
                elif is_pending_order:
                    logger.info(f"  ⏳ Pending order placed — will verify when filled")
                
                # Track position — but ONLY for market orders (immediately filled)
                # Pending orders (buy_limit, sell_limit, etc.) are tracked by pending_order_manager
                # and will be picked up by sync_with_mt5 when they fill
                if result.ticket:
                    # Validate SL/TP are real values before tracking
                    tracked_sl = trade_signal.stop_loss if trade_signal.stop_loss and trade_signal.stop_loss > 0 else None
                    tracked_tp = trade_signal.take_profit if trade_signal.take_profit and trade_signal.take_profit > 0 else None
                    if not tracked_sl:
                        logger.error(f"CRITICAL: Position {result.ticket} has no valid SL! trade_signal.stop_loss={trade_signal.stop_loss}")
                    if not tracked_tp:
                        logger.warning(f"Position {result.ticket} has no TP set: trade_signal.take_profit={trade_signal.take_profit}")
                    
                    if is_pending_order:
                        # =============================================
                        # PENDING ORDER: Do NOT add to position_manager!
                        # MT5's get_positions() doesn't return pending orders,
                        # so sync_with_mt5 would falsely detect them as "closed".
                        # They're already tracked by pending_order_manager.
                        # When they fill, sync_with_mt5 will pick them up as new positions.
                        # =============================================
                        print(f"[PENDING] {symbol}: Pending {order_type} placed (ticket={result.ticket}, entry={entry_price:.5f}, SL={trade_signal.stop_loss}, TP={trade_signal.take_profit})", flush=True)
                        logger.info(f"Pending order {result.ticket} tracked by pending_order_manager (NOT position_manager)")
                        await bot._record_terminal_decision(
                            "pending_placed",
                            symbol,
                            direction=trade_signal.direction,
                            entry=entry_price,
                            sl=trade_signal.stop_loss or 0.0,
                            tp=trade_signal.take_profit or 0.0,
                            confidence=trade_signal.confidence,
                            reason=f"Pending {order_type} placed",
                            details={"ticket": result.ticket, "order_type": order_type},
                        )
                        
                        # Add to activity feed as pending order (not "trade opened")
                        from ..api.routes.activity import add_activity
                        add_activity(
                            "pending_order_placed",
                            f"Pending {order_type.upper()} {trade_signal.direction.upper()} {symbol} @ {entry_price:.5f}",
                            symbol,
                            {
                                "ticket": result.ticket,
                                "order_type": order_type,
                                "direction": trade_signal.direction,
                                "entry_price": entry_price,
                                "stop_loss": trade_signal.stop_loss,
                                "take_profit": trade_signal.take_profit,
                                "lots": position_size.lots,
                                "confidence": trade_signal.confidence
                            }
                        )
                        asyncio.create_task(broadcast_trade_update({
                            "event": "pending_order_placed",
                            "ticket": result.ticket,
                            "symbol": symbol,
                            "order_type": order_type,
                            "direction": trade_signal.direction,
                            "entry_price": entry_price,
                            "stop_loss": trade_signal.stop_loss,
                            "take_profit": trade_signal.take_profit,
                            "lots": position_size.lots,
                            "confidence": trade_signal.confidence
                        }))
                        
                        # Save to database with full analysis context
                        await save_trade_to_db(
                            ticket=result.ticket,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            entry_price=entry_price,
                            stop_loss=trade_signal.stop_loss or 0.0,
                            take_profit=trade_signal.take_profit or 0.0,
                            position_size=position_size.lots,
                            confidence=trade_signal.confidence,
                            reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                            judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                            judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                            judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                            trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                            order_type=order_type,
                            amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                            market_structure=getattr(trade_signal, 'market_structure', ''),
                            confluence_factors=confluence_factors if confluence_factors else None,
                            confluence_count=confluence_count if confluence_count else None,
                            ict_concepts={
                                'order_blocks': getattr(trade_signal, 'order_blocks', []),
                                'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                                'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                                'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                            },
                            timeframe="M15",
                            session_name=bot.kill_zone_checker.get_current_session().session_name if bot.kill_zone_checker else "",
                            risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else bot.risk_manager.risk_per_trade,
                        )
                        
                        # Pending orders: do NOT send Telegram notification.
                        # These get cancelled/replaced frequently and would spam
                        # the user. Only notify when a trade actually fills.
                        logger.info(f"Pending order placed for {symbol} — Telegram notification deferred until fill")
                    else:
                        # =============================================
                        # MARKET ORDER: Immediately filled, add to position_manager
                        # =============================================
                        position = Position(
                            ticket=result.ticket,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            volume=result.fill_volume or position_size.lots,
                            entry_price=result.fill_price or current_price,
                            stop_loss=tracked_sl or (result.fill_price or current_price),
                            take_profit=tracked_tp or 0.0,
                            open_time=datetime.now(timezone.utc),
                            trade_type=getattr(trade_signal, 'trade_type', 'intraday') or 'intraday',
                            reservation_id=_trade_reservation.reservation_id if _trade_reservation else None,
                            a_plus=classify_a_plus(
                                setup_grade.value if hasattr(setup_grade, 'value') else str(setup_grade),
                                confluence_count or 0,
                            ),
                        )
                        if _trade_reservation:
                            bot.reservation_ledger.transfer_to_position(
                                _trade_reservation,
                                result.ticket,
                            )
                        
                        # Set multi-TP levels for partial close management
                        # Scalps: single TP — close full position, no partials
                        # Intraday/Swing: multi-TP with partial close management
                        _pos_trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
                        
                        if _pos_trade_type == 'scalp':
                            # Scalps: single TP, full close. No multi-TP complexity.
                            position.tp1 = position.take_profit
                            position.tp2 = 0.0
                            position.tp3 = 0.0
                            logger.info(f"  SCALP: Single TP at {position.tp1:.5f} (full close)")
                        elif take_profit_levels:
                            position.tp1 = take_profit_levels.get('tp1', 0.0) or 0.0
                            position.tp2 = take_profit_levels.get('tp2', 0.0) or 0.0
                            position.tp3 = take_profit_levels.get('tp3', 0.0) or 0.0
                            logger.info(
                                f"  Multi-TP set: TP1={position.tp1}, TP2={position.tp2}, TP3={position.tp3}"
                            )
                        elif trade_signal.stop_loss and trade_signal.take_profit:
                            # Fallback: auto-calculate TP levels from SL/TP
                            _entry = result.fill_price or current_price
                            _sl_dist = abs(_entry - trade_signal.stop_loss)
                            if trade_signal.direction == 'long':
                                position.tp1 = _entry + (_sl_dist * 1.0)   # 1R
                                position.tp2 = _entry + (_sl_dist * 2.0)   # 2R
                                position.tp3 = _entry + (_sl_dist * 3.0)   # 3R
                            else:
                                position.tp1 = _entry - (_sl_dist * 1.0)   # 1R
                                position.tp2 = _entry - (_sl_dist * 2.0)   # 2R
                                position.tp3 = _entry - (_sl_dist * 3.0)   # 3R
                            logger.info(
                                f"  Multi-TP (auto): TP1={position.tp1:.5f}, TP2={position.tp2:.5f}, TP3={position.tp3:.5f}"
                            )
                        
                        bot.position_manager.add_position(position)
                        print(f"[TRADE] {symbol}: Market order filled (ticket={result.ticket}, fill={result.fill_price})", flush=True)
                        await bot._record_terminal_decision(
                            "market_filled",
                            symbol,
                            direction=trade_signal.direction,
                            entry=result.fill_price or current_price,
                            sl=tracked_sl or 0.0,
                            tp=tracked_tp or 0.0,
                            confidence=trade_signal.confidence,
                            reason="Market order filled",
                            details={"ticket": result.ticket},
                        )
                        
                        # Track in correlation service
                        if bot.correlation_service:
                            bot.correlation_service.set_open_position(
                                symbol, position_size.lots, trade_signal.direction
                            )
                        
                        # Add to activity feed
                        from ..api.routes.activity import add_activity
                        add_activity(
                            "trade_opened",
                            f"Opened {trade_signal.direction.upper()} {symbol} @ {result.fill_price or current_price:.5f}",
                            symbol,
                            {
                                "ticket": result.ticket,
                                "direction": trade_signal.direction,
                                "entry_price": result.fill_price or current_price,
                                "stop_loss": trade_signal.stop_loss,
                                "take_profit": trade_signal.take_profit,
                                "lots": position_size.lots,
                                "confidence": trade_signal.confidence
                            }
                        )
                        asyncio.create_task(broadcast_trade_update({
                            "event": "trade_opened",
                            "ticket": result.ticket,
                            "symbol": symbol,
                            "direction": trade_signal.direction,
                            "entry_price": result.fill_price or current_price,
                            "stop_loss": trade_signal.stop_loss,
                            "take_profit": trade_signal.take_profit,
                            "lots": position_size.lots,
                            "confidence": trade_signal.confidence
                        }))
                        
                        # Save trade to database with full analysis context
                        await save_trade_to_db(
                            ticket=result.ticket,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            entry_price=result.fill_price or current_price,
                            stop_loss=trade_signal.stop_loss or 0.0,
                            take_profit=trade_signal.take_profit or 0.0,
                            position_size=position_size.lots,
                            confidence=trade_signal.confidence,
                            reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                            judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                            judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                            judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                            trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                            order_type=order_type,
                            amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                            market_structure=getattr(trade_signal, 'market_structure', ''),
                            confluence_factors=confluence_factors if confluence_factors else None,
                            confluence_count=confluence_count if confluence_count else None,
                            ict_concepts={
                                'order_blocks': getattr(trade_signal, 'order_blocks', []),
                                'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                                'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                                'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                            },
                            timeframe="M15",
                            session_name=bot.kill_zone_checker.get_current_session().session_name if bot.kill_zone_checker else "",
                            risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else bot.risk_manager.risk_per_trade,
                        )
                        
                        # Send Telegram notification
                        await notify(
                            NotificationType.TRADE_OPENED,
                            f"Trade opened: {symbol}",
                            symbol=symbol,
                            direction=trade_signal.direction,
                            entry_price=result.fill_price or current_price,
                            stop_loss=trade_signal.stop_loss or 0.0,
                            take_profit=trade_signal.take_profit or 0.0,
                            lots=position_size.lots,
                            confidence=trade_signal.confidence,
                            ticket=result.ticket
                        )
            else:
                logger.error(f"✗ Trade execution failed for {symbol}: {result.message}")
                reconciled_ticket = await bot._reconcile_fill_after_ambiguous_order(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    lots=position_size.lots,
                    reservation=_trade_reservation,
                    stop_loss=_final_sl or 0.0,
                    take_profit=_final_tp or 0.0,
                )
                if reconciled_ticket:
                    logger.warning(
                        f"[RECONCILE] {symbol}: execution reported failure but position "
                        f"{reconciled_ticket} found in MT5 — reservation retained"
                    )
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "reconcile_fill",
                        f"Recovered ambiguous fill for {symbol} (ticket={reconciled_ticket})",
                        symbol,
                        {"ticket": reconciled_ticket, "reason": result.message},
                    )
                else:
                    await bot._record_terminal_decision(
                        "execution_failure",
                        symbol,
                        direction=trade_signal.direction,
                        entry=trade_signal.entry_price or current_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                        reason=result.message or "broker rejected order",
                    )
                    bot._release_trade_reservation(_trade_reservation)
                
                # Log error to activity feed
                from ..api.routes.activity import add_activity
                add_activity(
                    "error",
                    f"Trade execution failed for {symbol}: {result.message}",
                    symbol,
                    {"error": result.message}
                )
            
    except Exception as e:
        print(f"[ERROR] _analyze_and_trade({symbol}) CRASHED: {e}", flush=True)
        logger.error(f"Error analyzing {symbol}: {e}")
        import traceback
        traceback.print_exc()
        # Release trade slot if it was reserved before the crash
        bot._release_trade_reservation(_trade_reservation)
    finally:
        if _trade_reservation_context is not None:
            await _trade_reservation_context.__aexit__(None, None, None)
    

