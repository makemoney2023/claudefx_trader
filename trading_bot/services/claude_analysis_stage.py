"""Claude analysis stage — context assembly and chart API call."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

from ..config import settings
from ..utils.logging import get_logger
from ..api.websocket import broadcast_analysis_update

logger = get_logger(__name__)

try:
    from ..api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None


@dataclass
class ClaudeStageResult:
    trade_signal: Any
    claude_result: Any
    market_data: dict
    analysis_data: dict
    chart_base64: str
    strategy_context: str
    account_info: Any
    current_price: float
    stop_pipeline: bool = False


class ClaudeAnalysisStage:
    """Builds Claude context, calls the API, and records signal telemetry."""

    def __init__(self, claude_client):
        self.claude_client = claude_client

    async def analyze(
        self,
        *,
        chart_image_base64: str,
        symbol: str,
        strategy_context: str,
        market_data: dict,
        analysis_data: Optional[dict] = None,
        timeframe: str = "M15",
        additional_charts: Optional[list] = None,
    ) -> Any:
        return await self.claude_client.analyze_chart_async(
            chart_image_base64=chart_image_base64,
            symbol=symbol,
            timeframe=timeframe,
            strategy_context=strategy_context,
            market_data=market_data,
            analysis_data=analysis_data,
            additional_charts=additional_charts,
        )

    async def run_stage(
        self,
        bot: Any,
        *,
        symbol: str,
        df,
        analysis_results: dict,
        chart_base64: str,
        additional_charts: list,
        vp_data,
        bar_extreme_results: dict,
        mtf_dfs: dict,
        pd_analysis,
        mtf_result,
        dxy_confirmation,
        retail_contrarian,
        vix_risk_mode,
        currency_strength_recommendation,
        current_price: float,
    ) -> ClaudeStageResult:
        """Assemble context, invoke Claude, and handle no-trade outcomes."""
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
        if vp_data:
            market_data["volume_profile_levels"] = {
                'poc': vp_data['poc'],
                'vah': vp_data['vah'],
                'val': vp_data['val'],
            }
        for _be_key, _be_val in bar_extreme_results.items():
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
                "displacement_confirmed": analysis_results.get(
                    "silver_bullet_status", {}
                ).get("displacement_confirmed", False),
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
        _fib = analysis_results.get("fibonacci") or {}
        if _fib:
            market_data["fibonacci_zone"] = _fib.get("price_zone")
            market_data["in_ote"] = _fib.get("in_ote")
            market_data["optimal_entry"] = _fib.get("optimal_entry")
            market_data["fib_levels"] = _fib.get("fib_levels")
        
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
        
        claude_result = await self.analyze(
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
            return ClaudeStageResult(
                trade_signal=trade_signal,
                claude_result=claude_result,
                market_data=market_data,
                analysis_data=analysis_data,
                chart_base64=chart_base64,
                strategy_context=strategy_context,
                account_info=account_info,
                current_price=current_price,
                stop_pipeline=True,
            )

        return ClaudeStageResult(
            trade_signal=trade_signal,
            claude_result=claude_result,
            market_data=market_data,
            analysis_data=analysis_data,
            chart_base64=chart_base64,
            strategy_context=strategy_context,
            account_info=account_info,
            current_price=current_price,
        )

