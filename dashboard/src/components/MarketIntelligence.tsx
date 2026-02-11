'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, VIXSentiment, RetailSentiment, CurrencyStrength, IntermarketAnalysis, CompleteAnalysis } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  Globe,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Newspaper,
  DollarSign,
  BarChart3,
  AlertTriangle,
  Zap,
  Clock,
  ChevronDown,
  ChevronUp,
  Users,
  Activity,
  Gauge,
  Bitcoin,
  Scale,
  Calendar,
  LineChart,
} from 'lucide-react'

interface MarketIntelligenceProps {
  compact?: boolean
  symbol?: string
}

export function MarketIntelligence({ compact = false, symbol = 'EURUSD' }: MarketIntelligenceProps) {
  const [analysis, setAnalysis] = useState<CompleteAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [expanded, setExpanded] = useState(!compact)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const data = await api.getCompleteAnalysis(symbol)
      setAnalysis(data)
    } catch (err) {
      console.error('Error fetching intelligence:', err)
      setError('Failed to load intelligence data')
    } finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000) // Refresh every minute
    return () => clearInterval(interval)
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await api.refreshIntelligenceQuick(symbol)
      await fetchData()
    } catch (error) {
      console.error('Error refreshing intelligence:', error)
    }
    setRefreshing(false)
  }

  if (loading) {
    return (
      <div className="card p-6">
        <div className="flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500"></div>
        </div>
      </div>
    )
  }

  if (error || !analysis) {
    return (
      <div className="card">
        <div className="card-header">
          <h2 className="card-title flex items-center gap-2">
            <Globe className="w-5 h-5 text-purple-400" />
            Market Intelligence
          </h2>
        </div>
        <div className="card-body">
          <div className="text-center py-6">
            <AlertTriangle className="w-12 h-12 text-yellow-500 mx-auto mb-3" />
            <p className="text-slate-400">{error || 'Intelligence service not available'}</p>
            <p className="text-slate-500 text-sm mt-1">Configure Firecrawl API key in settings</p>
          </div>
        </div>
      </div>
    )
  }

  const BiasIndicator = ({ bias, size = 'md' }: { bias: string; size?: 'sm' | 'md' | 'lg' }) => {
    const colors = {
      strong_bullish: 'bg-green-500',
      bullish: 'bg-green-400/70',
      neutral: 'bg-slate-500',
      bearish: 'bg-red-400/70',
      strong_bearish: 'bg-red-500',
    }
    const textColors = {
      strong_bullish: 'text-green-400',
      bullish: 'text-green-400',
      neutral: 'text-slate-400',
      bearish: 'text-red-400',
      strong_bearish: 'text-red-400',
    }
    const sizeClasses = {
      sm: 'px-2 py-0.5 text-xs',
      md: 'px-3 py-1 text-sm',
      lg: 'px-4 py-2 text-base',
    }
    return (
      <span className={cn(
        colors[bias as keyof typeof colors] || 'bg-slate-500',
        sizeClasses[size],
        'rounded font-semibold text-white'
      )}>
        {bias.replace('_', ' ').toUpperCase()}
      </span>
    )
  }

  return (
    <div className="card">
      <div
        className={cn(
          "card-header flex items-center justify-between cursor-pointer",
          compact && "hover:bg-slate-700/50"
        )}
        onClick={() => compact && setExpanded(!expanded)}
      >
        <h2 className="card-title flex items-center gap-2">
          <Globe className="w-5 h-5 text-purple-400" />
          Market Intelligence
          <BiasIndicator bias={analysis.overall_bias} size="sm" />
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">
            {analysis.bullish_signals}↑ / {analysis.bearish_signals}↓
          </span>
          <button
            onClick={(e) => {
              e.stopPropagation()
              handleRefresh()
            }}
            disabled={refreshing}
            className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
          </button>
          {compact && (expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />)}
        </div>
      </div>

      {(!compact || expanded) && (
        <div className="card-body space-y-4">
          {/* Overall Signal Summary */}
          <div className="bg-gradient-to-r from-purple-500/20 to-blue-500/20 rounded-lg p-4">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-slate-400 text-sm">Overall Signal for {analysis.symbol}</span>
                <div className="flex items-center gap-3 mt-1">
                  <BiasIndicator bias={analysis.overall_bias} size="lg" />
                  <span className="text-slate-400 text-sm">
                    Strength: {(analysis.bias_strength * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold text-green-400">{analysis.bullish_signals}</div>
                <div className="text-xs text-slate-400">Bullish Signals</div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold text-red-400">{analysis.bearish_signals}</div>
                <div className="text-xs text-slate-400">Bearish Signals</div>
              </div>
            </div>
          </div>

          {/* Key Indicators Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {/* DXY */}
            <div className="bg-slate-700/50 rounded-lg p-3">
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <DollarSign className="w-3 h-3" />
                DXY
              </div>
              <div className={cn(
                "font-semibold",
                analysis.dxy.trend === 'bullish' ? 'text-green-400' :
                analysis.dxy.trend === 'bearish' ? 'text-red-400' : 'text-slate-400'
              )}>
                {analysis.dxy.trend?.toUpperCase() || 'N/A'}
              </div>
            </div>

            {/* VIX */}
            <div className="bg-slate-700/50 rounded-lg p-3">
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <Activity className="w-3 h-3" />
                VIX
              </div>
              <div className={cn(
                "font-semibold",
                analysis.vix.risk_mode === 'risk_on' ? 'text-green-400' :
                analysis.vix.risk_mode === 'risk_off' ? 'text-red-400' : 'text-slate-400'
              )}>
                {analysis.vix.level?.toFixed(1) || 'N/A'}
                <span className="text-xs ml-1 text-slate-500">
                  ({analysis.vix.risk_mode?.replace('_', ' ')})
                </span>
              </div>
            </div>

            {/* Retail Sentiment (Contrarian) */}
            <div className="bg-slate-700/50 rounded-lg p-3">
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <Users className="w-3 h-3" />
                Retail (Contrarian)
              </div>
              <div className={cn(
                "font-semibold",
                analysis.retail_sentiment.contrarian_signal === 'long' ? 'text-green-400' :
                analysis.retail_sentiment.contrarian_signal === 'short' ? 'text-red-400' : 'text-slate-400'
              )}>
                {analysis.retail_sentiment.contrarian_signal?.toUpperCase() || 'N/A'}
              </div>
              {analysis.retail_sentiment.bias !== 'unknown' && (
                <div className="text-[10px] text-slate-500">
                  Retail: {analysis.retail_sentiment.bias?.replace('_', ' ')}
                </div>
              )}
            </div>

            {/* Intermarket */}
            <div className="bg-slate-700/50 rounded-lg p-3">
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <LineChart className="w-3 h-3" />
                Risk Environment
              </div>
              <div className={cn(
                "font-semibold text-sm",
                analysis.intermarket.risk_environment?.includes('risk_on') ? 'text-green-400' :
                analysis.intermarket.risk_environment?.includes('risk_off') ? 'text-red-400' : 'text-slate-400'
              )}>
                {analysis.intermarket.risk_environment?.replace(/_/g, ' ').toUpperCase() || 'N/A'}
              </div>
            </div>
          </div>

          {/* Detailed Sections */}
          <div className="space-y-3">
            {/* Currency Strength */}
            {analysis.currency_strength.strongest && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center gap-2 text-sm font-medium mb-2">
                  <Scale className="w-4 h-4 text-blue-400" />
                  Currency Strength
                </div>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-slate-400">Strongest: </span>
                    <span className="text-green-400 font-semibold">{analysis.currency_strength.strongest}</span>
                  </div>
                  <div>
                    <span className="text-slate-400">Weakest: </span>
                    <span className="text-red-400 font-semibold">{analysis.currency_strength.weakest}</span>
                  </div>
                </div>
                {analysis.currency_strength.recommendation && (
                  <div className="mt-2 text-xs text-slate-400">
                    💡 {analysis.currency_strength.recommendation}
                  </div>
                )}
              </div>
            )}

            {/* TradingView Technical */}
            {analysis.tradingview_technical.signal !== 'neutral' && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <BarChart3 className="w-4 h-4 text-purple-400" />
                    TradingView Technical
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.tradingview_technical.signal === 'buy' ? 'bg-green-500/20 text-green-400' :
                    analysis.tradingview_technical.signal === 'sell' ? 'bg-red-500/20 text-red-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.tradingview_technical.consensus?.toUpperCase()}
                  </span>
                </div>
              </div>
            )}

            {/* Options Flow */}
            {analysis.options_flow.flow !== 'neutral' && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Gauge className="w-4 h-4 text-amber-400" />
                    Options Flow
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.options_flow.flow === 'bullish' ? 'bg-green-500/20 text-green-400' :
                    'bg-red-500/20 text-red-400'
                  )}>
                    {analysis.options_flow.flow?.toUpperCase()}
                  </span>
                </div>
                {analysis.options_flow.magnet_levels?.length > 0 && (
                  <div className="mt-2 text-xs text-slate-400">
                    Magnet Levels: {analysis.options_flow.magnet_levels.slice(0, 3).join(', ')}
                  </div>
                )}
              </div>
            )}

            {/* Bond Yields (EUR pairs) */}
            {analysis.bond_yields.spread !== null && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <TrendingUp className="w-4 h-4 text-cyan-400" />
                    Bond Yield Spread (US-DE)
                  </div>
                  <span className="font-mono text-sm">
                    {analysis.bond_yields.spread?.toFixed(2)}%
                  </span>
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  EUR/USD Bias: <span className={cn(
                    analysis.bond_yields.eurusd_bias === 'bullish' ? 'text-green-400' :
                    analysis.bond_yields.eurusd_bias === 'bearish' ? 'text-red-400' : 'text-slate-400'
                  )}>{analysis.bond_yields.eurusd_bias?.toUpperCase()}</span>
                </div>
              </div>
            )}

            {/* Seasonal Pattern */}
            {analysis.seasonal_pattern.current_month_bias !== 'unknown' && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Calendar className="w-4 h-4 text-orange-400" />
                    Seasonal: {analysis.seasonal_pattern.current_month}
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.seasonal_pattern.current_month_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                    analysis.seasonal_pattern.current_month_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.seasonal_pattern.current_month_bias?.toUpperCase()}
                    <span className="ml-1 opacity-70">
                      ({analysis.seasonal_pattern.historical_accuracy}%)
                    </span>
                  </span>
                </div>
              </div>
            )}

            {/* BTC Dominance (for crypto) */}
            {analysis.is_crypto && analysis.btc_dominance && (
              <div className="bg-slate-700/30 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Bitcoin className="w-4 h-4 text-orange-400" />
                    BTC Dominance
                  </div>
                  <span className="font-mono text-sm">
                    {analysis.btc_dominance.dominance?.toFixed(1)}%
                    <span className={cn(
                      "ml-2 text-xs",
                      analysis.btc_dominance.trend === 'rising' ? 'text-green-400' :
                      analysis.btc_dominance.trend === 'falling' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      ({analysis.btc_dominance.trend})
                    </span>
                  </span>
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  Altcoins: <span className={cn(
                    analysis.btc_dominance.altcoin_sentiment === 'bullish' ? 'text-green-400' :
                    analysis.btc_dominance.altcoin_sentiment === 'bearish' ? 'text-red-400' : 'text-slate-400'
                  )}>{analysis.btc_dominance.altcoin_sentiment?.toUpperCase()}</span>
                </div>
              </div>
            )}
          </div>

          {/* Last Updated */}
          <div className="flex items-center justify-center gap-2 text-xs text-slate-500 pt-2 border-t border-slate-700">
            <Clock className="w-3 h-3" />
            <span>
              Last updated: {new Date(analysis.timestamp).toLocaleTimeString()}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
