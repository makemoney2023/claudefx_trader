"""Expanded ICT + intelligence analysis for the live pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from ..utils.logging import get_logger

logger = get_logger(__name__)

try:
    from ..api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None


@dataclass
class ExpandedAnalysisResult:
    pd_analysis: Any = None
    mtf_result: Any = None
    dxy_confirmation: Optional[str] = None
    retail_contrarian: Optional[str] = None
    vix_risk_mode: Optional[str] = None
    currency_strength_recommendation: Optional[str] = None
    amd_state: Any = None
    displacement_analysis: Any = None
    breaker_blocks: Any = None
    silver_bullet_ready: bool = False
    ipda_analysis: Any = None
    nwog_target: Optional[float] = None


async def run_expanded_analysis(
    bot: Any,
    *,
    symbol: str,
    df: pd.DataFrame,
    analysis_results: Dict[str, Any],
) -> ExpandedAnalysisResult:
    """Run AMD, displacement, MTF, Fib, and Firecrawl intelligence."""
    result = ExpandedAnalysisResult()

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
    if bot.premium_discount_analyzer:
        try:
            current_price_pd = float(df['close'].iloc[-1])
            result.pd_analysis = bot.premium_discount_analyzer.analyze(df, current_price_pd)
            
            logger.info(
                f"📊 Price Zone: {result.pd_analysis.current_zone.value} "
                f"({result.pd_analysis.retracement_percent:.0%}), "
                f"OTE: {'YES' if result.pd_analysis.in_ote else 'NO'}"
            )
            
            analysis_results["premium_discount"] = result.pd_analysis.to_dict()
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
    result.mtf_result = None
    if bot.mtf_analyzer:
        try:
            result.mtf_result = await bot.mtf_analyzer.analyze(symbol)
            if result.mtf_result:
                logger.info(
                    f"📊 MTF Bias: {result.mtf_result.overall_bias.value}, "
                    f"Alignment: {result.mtf_result.alignment}, "
                    f"Can Long: {result.mtf_result.can_trade_long}, "
                    f"Can Short: {result.mtf_result.can_trade_short}"
                )
                analysis_results["mtf_analysis"] = result.mtf_result.to_dict()
                
                # Log MTF results to bot activity dashboard
                if bot_state:
                    mtf_details = {
                        "d1_bias": result.mtf_result.daily_analysis.bias.value if result.mtf_result.daily_analysis else "N/A",
                        "h4_bias": result.mtf_result.h4_analysis.bias.value if result.mtf_result.h4_analysis else "N/A",
                        "h4_structure": result.mtf_result.h4_analysis.structure if result.mtf_result.h4_analysis else "N/A",
                        "h1_bias": result.mtf_result.h1_analysis.bias.value if result.mtf_result.h1_analysis else "N/A",
                        "h1_structure": result.mtf_result.h1_analysis.structure if result.mtf_result.h1_analysis else "N/A",
                        "m15_bias": result.mtf_result.m15_analysis.bias.value if result.mtf_result.m15_analysis else "N/A",
                        "m5_bias": result.mtf_result.m5_analysis.bias.value if result.mtf_result.m5_analysis else "N/A",
                        "m5_structure": result.mtf_result.m5_analysis.structure if result.mtf_result.m5_analysis else "N/A",
                        "m1_bias": result.mtf_result.m1_analysis.bias.value if result.mtf_result.m1_analysis else "N/A",
                        "m1_structure": result.mtf_result.m1_analysis.structure if result.mtf_result.m1_analysis else "N/A",
                        "alignment": result.mtf_result.alignment,
                        "key_levels": result.mtf_result.htf_key_levels[:5] if result.mtf_result.htf_key_levels else [],
                    }
                    bot_state.mtf_analysis_complete(
                        symbol,
                        bias=result.mtf_result.overall_bias.value,
                        alignment=result.mtf_result.alignment,
                        can_long=result.mtf_result.can_trade_long,
                        can_short=result.mtf_result.can_trade_short,
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
    result.dxy_confirmation = None
    result.retail_contrarian = None
    result.vix_risk_mode = None
    result.currency_strength_recommendation = None
    
    if bot.firecrawl_service:
        try:
            # 8a. DXY Correlation for FX pairs
            if symbol in ['EURUSD', 'GBPUSD', 'USDCHF', 'USDJPY', 'AUDUSD', 'NZDUSD']:
                dxy_data = await bot.firecrawl_service.get_dxy_analysis()
                dxy_trend = dxy_data.get('trend', 'unknown')
                
                if symbol in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD']:
                    if dxy_trend == 'bullish':
                        result.dxy_confirmation = 'short'
                        logger.info(f"💵 DXY BULLISH: Confirms SHORT bias for {symbol}")
                    elif dxy_trend == 'bearish':
                        result.dxy_confirmation = 'long'
                        logger.info(f"💵 DXY BEARISH: Confirms LONG bias for {symbol}")
                elif symbol in ['USDCHF', 'USDJPY']:
                    if dxy_trend == 'bullish':
                        result.dxy_confirmation = 'long'
                        logger.info(f"💵 DXY BULLISH: Confirms LONG bias for {symbol}")
                    elif dxy_trend == 'bearish':
                        result.dxy_confirmation = 'short'
                        logger.info(f"💵 DXY BEARISH: Confirms SHORT bias for {symbol}")
                
                analysis_results["dxy_correlation"] = {
                    "dxy_trend": dxy_trend,
                    "confirmed_direction": result.dxy_confirmation
                }
            
            # 8b. RETAIL SENTIMENT (Contrarian Indicator)
            retail_data = await bot.firecrawl_service.get_retail_sentiment(symbol)
            if retail_data.get('contrarian_signal') != 'unknown':
                result.retail_contrarian = retail_data.get('contrarian_signal')
                logger.info(
                    f"🔄 RETAIL CONTRARIAN: {retail_data.get('bias', 'N/A')} bias, "
                    f"Signal: {result.retail_contrarian.upper()}"
                )
                if retail_data.get('note'):
                    logger.info(f"   {retail_data.get('note')}")
                analysis_results["retail_sentiment"] = retail_data
            
            # 8c. VIX Risk Sentiment
            vix_data = await bot.firecrawl_service.get_vix_sentiment()
            if vix_data.get('risk_mode'):
                result.vix_risk_mode = vix_data.get('risk_mode')
                logger.info(
                    f"📊 VIX RISK MODE: {result.vix_risk_mode.upper()} "
                    f"(Level: {vix_data.get('level', 'N/A')})"
                )
                analysis_results["vix_sentiment"] = vix_data
                
                # Adjust for risk-off (favor JPY, CHF, Gold)
                if result.vix_risk_mode == 'risk_off':
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
                    result.currency_strength_recommendation = strength_data.get('recommendation')
                    logger.info(f"   💡 {result.currency_strength_recommendation}")
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

    result.amd_state = amd_state
    result.displacement_analysis = displacement_analysis
    result.breaker_blocks = breaker_blocks
    result.silver_bullet_ready = silver_bullet_ready
    result.ipda_analysis = ipda_analysis
    result.nwog_target = nwog_target
    return result
