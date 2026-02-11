'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, IntelligenceStatus, CompleteAnalysis, VIXSentiment, RetailSentiment, CurrencyStrength, IntermarketAnalysis, SeasonalPattern, BondYields, OptionsFlow, BTCDominance, ComprehensiveIntelligence, GeopoliticalAnalysis, CentralBankAnalysis, DeepIntermarketAnalysis } from '@/lib/api'
import { cn } from '@/lib/utils'
import { MarketIntelligence } from '@/components/MarketIntelligence'
import {
  Globe,
  RefreshCw,
  Zap,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  CheckCircle,
  Clock,
  BarChart3,
  DollarSign,
  Newspaper,
  Settings,
  Users,
  Activity,
  Scale,
  Bitcoin,
  Calendar,
  LineChart,
  Gauge,
  Thermometer,
  Target,
  ArrowUpDown,
  Brain,
  Shield,
  Landmark,
  AlertOctagon,
} from 'lucide-react'

export default function IntelligencePage() {
  const [status, setStatus] = useState<IntelligenceStatus | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState('EURUSD')
  const [analysis, setAnalysis] = useState<CompleteAnalysis | null>(null)
  const [deepResearch, setDeepResearch] = useState<ComprehensiveIntelligence | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [configuredSymbols, setConfiguredSymbols] = useState<string[]>([])

  // Default symbols fallback
  const symbols = configuredSymbols.length > 0 
    ? configuredSymbols 
    : ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'XAUUSD']

  const fetchData = useCallback(async () => {
    try {
      const statusData = await api.getIntelligenceStatus()
      setStatus(statusData)

      if (statusData.enabled && statusData.available) {
        const [analysisData, deepData] = await Promise.allSettled([
          api.getCompleteAnalysis(selectedSymbol),
          api.getComprehensiveIntelligence(selectedSymbol)
        ])
        
        if (analysisData.status === 'fulfilled') {
          setAnalysis(analysisData.value)
        }
        
        if (deepData.status === 'fulfilled') {
          setDeepResearch(deepData.value)
        }
      }
    } catch (error) {
      console.error('Error fetching intelligence:', error)
    } finally {
      setLoading(false)
    }
  }, [selectedSymbol])

  // Fetch symbols from MT5 MarketWatch
  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        // First try to get symbols from MT5 MarketWatch
        const marketWatch = await api.getMarketWatchSymbols()
        if (marketWatch.symbols && marketWatch.symbols.length > 0) {
          const symbolNames = marketWatch.symbols.map((s: any) => s.name || s)
          setConfiguredSymbols(symbolNames)
          console.log(`Loaded ${symbolNames.length} symbols from MarketWatch`)
          // Set first symbol as default if not already selected
          if (!selectedSymbol || !symbolNames.includes(selectedSymbol)) {
            setSelectedSymbol(symbolNames[0])
          }
          return
        }
      } catch (err) {
        console.warn('Could not fetch from MarketWatch, falling back to config:', err)
      }
      
      // Fallback to config symbols
      try {
        const config = await api.getConfig()
        if (config.trading?.symbols?.length > 0) {
          setConfiguredSymbols(config.trading.symbols)
          console.log(`Loaded ${config.trading.symbols.length} symbols from config`)
          if (!selectedSymbol || !config.trading.symbols.includes(selectedSymbol)) {
            setSelectedSymbol(config.trading.symbols[0])
          }
        }
      } catch (err) {
        console.error('Failed to fetch symbols:', err)
      }
    }
    fetchSymbols()
  }, [])
  
  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      // Pass configured symbols to refresh all
      const symbolsToRefresh = configuredSymbols.length > 0 
        ? configuredSymbols.join(',') 
        : 'EURUSD,GBPUSD,XAUUSD'
      await api.refreshIntelligence(symbolsToRefresh)
      await fetchData()
    } catch (error) {
      console.error('Error refreshing:', error)
      setError(error instanceof Error ? error.message : 'Refresh failed')
    }
    setRefreshing(false)
  }

  const handleQuickRefresh = async () => {
    setRefreshing(true)
    try {
      await api.refreshIntelligenceQuick(selectedSymbol)
      await fetchData()
    } catch (error) {
      console.error('Error refreshing:', error)
    }
    setRefreshing(false)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-500"></div>
      </div>
    )
  }

  const BiasIndicator = ({ bias, size = 'md' }: { bias: string; size?: 'sm' | 'md' | 'lg' }) => {
    const colors: Record<string, string> = {
      strong_bullish: 'bg-green-500',
      bullish: 'bg-green-400/70',
      neutral: 'bg-slate-500',
      bearish: 'bg-red-400/70',
      strong_bearish: 'bg-red-500',
      positive: 'bg-green-400',
      negative: 'bg-red-400',
      unknown: 'bg-slate-600',
    }
    const sizeClasses: Record<string, string> = {
      sm: 'px-2 py-0.5 text-xs',
      md: 'px-3 py-1 text-sm',
      lg: 'px-4 py-2 text-base',
    }
    return (
      <span className={cn(
        colors[bias] || 'bg-slate-500',
        sizeClasses[size],
        'rounded font-semibold text-white'
      )}>
        {bias?.replace(/_/g, ' ').toUpperCase() || 'N/A'}
      </span>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Globe className="w-7 h-7 text-purple-400" />
            Market Intelligence
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Comprehensive market data powered by Firecrawl
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Symbol Selector */}
          <select
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm"
          >
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          
          {status?.available ? (
            <span className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-lg text-sm">
              <CheckCircle className="w-4 h-4" />
              Active
            </span>
          ) : (
            <span className="flex items-center gap-2 px-3 py-1.5 bg-red-500/20 text-red-400 rounded-lg text-sm">
              <AlertTriangle className="w-4 h-4" />
              Not Configured
            </span>
          )}
          <button
            onClick={handleQuickRefresh}
            disabled={refreshing || !status?.available}
            className="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg transition-colors text-sm"
          >
            <Zap className="w-4 h-4" />
            Quick
          </button>
          <button
            onClick={handleRefresh}
            disabled={refreshing || !status?.available}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
          >
            <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
            Full Refresh
          </button>
        </div>
      </div>

      {!status?.available ? (
        <div className="card p-12 text-center">
          <Globe className="w-16 h-16 text-slate-600 mx-auto mb-4" />
          <h2 className="text-xl font-semibold mb-2">Intelligence Service Not Configured</h2>
          <p className="text-slate-400 mb-4">
            Add your Firecrawl API key in the settings to enable real-time market intelligence.
          </p>
          <a
            href="/settings"
            className="inline-flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded-lg transition-colors"
          >
            <Settings className="w-4 h-4" />
            Go to Settings
          </a>
        </div>
      ) : analysis ? (
        <>
          {/* AI Deep Research Banner */}
          {deepResearch?.available && (
            <div className="card bg-gradient-to-r from-indigo-900/40 via-purple-900/40 to-pink-900/40 border border-purple-500/30 p-6">
              <div className="flex items-center gap-2 mb-4">
                <Brain className="w-6 h-6 text-purple-400" />
                <h2 className="text-lg font-bold text-purple-300">AI Deep Research Intelligence</h2>
                <span className="text-xs bg-purple-500/30 text-purple-300 px-2 py-0.5 rounded-full ml-2">
                  Powered by Firecrawl Agent
                </span>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                {/* Overall Risk Level */}
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Shield className={cn(
                      "w-5 h-5",
                      deepResearch.overall_risk_level === 'high' ? 'text-red-400' :
                      deepResearch.overall_risk_level === 'elevated' ? 'text-orange-400' :
                      deepResearch.overall_risk_level === 'low' ? 'text-green-400' : 'text-slate-400'
                    )} />
                    <span className="text-sm text-slate-400">Overall Risk</span>
                  </div>
                  <div className={cn(
                    "text-xl font-bold",
                    deepResearch.overall_risk_level === 'high' ? 'text-red-400' :
                    deepResearch.overall_risk_level === 'elevated' ? 'text-orange-400' :
                    deepResearch.overall_risk_level === 'low' ? 'text-green-400' : 'text-slate-300'
                  )}>
                    {deepResearch.overall_risk_level?.toUpperCase() || 'NORMAL'}
                  </div>
                </div>

                {/* Trading Environment */}
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Target className={cn(
                      "w-5 h-5",
                      deepResearch.trading_environment === 'excellent' ? 'text-green-400' :
                      deepResearch.trading_environment === 'good' ? 'text-green-300' :
                      deepResearch.trading_environment === 'difficult' ? 'text-red-400' :
                      deepResearch.trading_environment === 'avoid' ? 'text-red-500' : 'text-slate-400'
                    )} />
                    <span className="text-sm text-slate-400">Environment</span>
                  </div>
                  <div className={cn(
                    "text-xl font-bold",
                    deepResearch.trading_environment === 'excellent' ? 'text-green-400' :
                    deepResearch.trading_environment === 'good' ? 'text-green-300' :
                    deepResearch.trading_environment === 'difficult' ? 'text-red-400' :
                    deepResearch.trading_environment === 'avoid' ? 'text-red-500' : 'text-slate-300'
                  )}>
                    {deepResearch.trading_environment?.toUpperCase() || 'NORMAL'}
                  </div>
                </div>

                {/* Geopolitical Risk */}
                {deepResearch.geopolitical && (
                  <div className="bg-slate-900/50 rounded-lg p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <Globe className={cn(
                        "w-5 h-5",
                        deepResearch.geopolitical.risk_level === 'extreme' ? 'text-red-500' :
                        deepResearch.geopolitical.risk_level === 'high' ? 'text-red-400' :
                        deepResearch.geopolitical.risk_level === 'medium' ? 'text-orange-400' : 'text-green-400'
                      )} />
                      <span className="text-sm text-slate-400">Geopolitical</span>
                    </div>
                    <div className={cn(
                      "text-xl font-bold",
                      deepResearch.geopolitical.risk_level === 'extreme' ? 'text-red-500' :
                      deepResearch.geopolitical.risk_level === 'high' ? 'text-red-400' :
                      deepResearch.geopolitical.risk_level === 'medium' ? 'text-orange-400' : 'text-green-400'
                    )}>
                      {deepResearch.geopolitical.risk_level?.toUpperCase()}
                    </div>
                    <div className="text-xs text-slate-400 mt-1">
                      {deepResearch.geopolitical.events?.length || 0} events tracked
                    </div>
                  </div>
                )}

                {/* Central Bank Bias */}
                {deepResearch.central_banks && (
                  <div className="bg-slate-900/50 rounded-lg p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <Landmark className="w-5 h-5 text-blue-400" />
                      <span className="text-sm text-slate-400">CB Policy</span>
                    </div>
                    <div className="text-xl font-bold text-blue-300">
                      {deepResearch.central_banks.overall_bias?.toUpperCase() || 'MIXED'}
                    </div>
                    {deepResearch.central_banks.divergence_plays?.length > 0 && (
                      <div className="text-xs text-blue-400 mt-1">
                        Divergence: {deepResearch.central_banks.divergence_plays.slice(0, 2).join(', ')}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Warnings */}
              {deepResearch.warnings && deepResearch.warnings.length > 0 && (
                <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
                  <div className="flex items-center gap-2 text-red-400 text-sm font-semibold mb-2">
                    <AlertOctagon className="w-4 h-4" />
                    Intelligence Warnings
                  </div>
                  <ul className="space-y-1">
                    {deepResearch.warnings.map((warning, i) => (
                      <li key={i} className="text-sm text-red-300">⚠️ {warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Key Themes */}
              {deepResearch.key_themes && deepResearch.key_themes.length > 0 && (
                <div className="mt-4">
                  <span className="text-sm text-slate-400">Key Market Themes:</span>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {deepResearch.key_themes.map((theme, i) => (
                      <span key={i} className="text-xs bg-slate-700/50 text-slate-300 px-2 py-1 rounded">
                        {theme}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Overall Signal Banner */}
          <div className="card bg-gradient-to-r from-purple-500/20 via-blue-500/20 to-cyan-500/20 p-6">
            <div className="grid grid-cols-1 md:grid-cols-5 gap-6">
              <div className="md:col-span-2">
                <span className="text-slate-400 text-sm">Overall Signal for {analysis.symbol}</span>
                <div className="flex items-center gap-3 mt-2">
                  <BiasIndicator bias={analysis.overall_bias} size="lg" />
                  <span className="text-slate-400">
                    Strength: <span className="text-white font-bold">{(analysis.bias_strength * 100).toFixed(0)}%</span>
                  </span>
                </div>
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-green-400">{analysis.bullish_signals}</div>
                <div className="text-sm text-slate-400">Bullish Signals</div>
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-red-400">{analysis.bearish_signals}</div>
                <div className="text-sm text-slate-400">Bearish Signals</div>
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-slate-300">{analysis.total_signals}</div>
                <div className="text-sm text-slate-400">Total Signals</div>
              </div>
            </div>
          </div>

          {/* Main Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Column 1: Core Sentiment */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <Thermometer className="w-5 h-5 text-orange-400" />
                Market Sentiment
              </h3>

              {/* DXY */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <DollarSign className="w-4 h-4 text-green-400" />
                    <span className="font-medium">DXY (Dollar Index)</span>
                  </div>
                  <BiasIndicator bias={analysis.dxy.trend || 'unknown'} size="sm" />
                </div>
                <p className="text-sm text-slate-400">{analysis.dxy.bias || 'No data'}</p>
              </div>

              {/* VIX */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-red-400" />
                    <span className="font-medium">VIX (Fear Index)</span>
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.vix.risk_mode === 'risk_on' ? 'bg-green-500/20 text-green-400' :
                    analysis.vix.risk_mode === 'risk_off' ? 'bg-red-500/20 text-red-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.vix.risk_mode?.replace('_', ' ').toUpperCase()}
                  </span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-2xl font-bold">{analysis.vix.level?.toFixed(1) || 'N/A'}</span>
                  <span className="text-sm text-slate-400">{analysis.vix.sentiment}</span>
                </div>
                {analysis.vix.note && (
                  <p className="text-xs text-slate-400 mt-2">{analysis.vix.note}</p>
                )}
              </div>

              {/* Retail Sentiment (Contrarian) */}
              <div className="card p-4 border-l-4 border-amber-500">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Users className="w-4 h-4 text-amber-400" />
                    <span className="font-medium">Retail Sentiment</span>
                    <span className="text-xs bg-amber-500/20 text-amber-400 px-1.5 py-0.5 rounded">CONTRARIAN</span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-xs text-slate-400">Retail Bias</span>
                    <div className={cn(
                      "font-semibold",
                      analysis.retail_sentiment.bias?.includes('long') ? 'text-green-400' :
                      analysis.retail_sentiment.bias?.includes('short') ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.retail_sentiment.bias?.replace(/_/g, ' ').toUpperCase() || 'N/A'}
                    </div>
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">Trade Against ↓</span>
                    <div className={cn(
                      "font-bold text-lg",
                      analysis.retail_sentiment.contrarian_signal === 'long' ? 'text-green-400' :
                      analysis.retail_sentiment.contrarian_signal === 'short' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.retail_sentiment.contrarian_signal?.toUpperCase() || 'N/A'}
                    </div>
                  </div>
                </div>
                {analysis.retail_sentiment.note && (
                  <p className="text-xs text-slate-400 mt-2">⚠️ {analysis.retail_sentiment.note}</p>
                )}
              </div>

              {/* Intermarket */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <LineChart className="w-4 h-4 text-cyan-400" />
                    <span className="font-medium">Intermarket</span>
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.intermarket.risk_environment?.includes('risk_on') ? 'bg-green-500/20 text-green-400' :
                    analysis.intermarket.risk_environment?.includes('risk_off') ? 'bg-red-500/20 text-red-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.intermarket.risk_environment?.replace(/_/g, ' ').toUpperCase()}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-slate-400">SPX: </span>
                    <span className={cn(
                      analysis.intermarket.spx_trend === 'bullish' ? 'text-green-400' :
                      analysis.intermarket.spx_trend === 'bearish' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.intermarket.spx_trend?.toUpperCase()}
                    </span>
                  </div>
                  {analysis.intermarket.gold_trend && (
                    <div>
                      <span className="text-slate-400">Gold: </span>
                      <span className={cn(
                        analysis.intermarket.gold_trend === 'bullish' ? 'text-green-400' :
                        analysis.intermarket.gold_trend === 'bearish' ? 'text-red-400' : 'text-slate-400'
                      )}>
                        {analysis.intermarket.gold_trend?.toUpperCase()}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Column 2: Technical & Fundamental */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <BarChart3 className="w-5 h-5 text-blue-400" />
                Technical & Fundamental
              </h3>

              {/* Currency Strength */}
              <div className="card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Scale className="w-4 h-4 text-blue-400" />
                  <span className="font-medium">Currency Strength</span>
                </div>
                <div className="grid grid-cols-2 gap-4 mb-3">
                  <div className="bg-green-500/10 rounded-lg p-3 text-center">
                    <span className="text-xs text-slate-400">Strongest</span>
                    <div className="text-xl font-bold text-green-400">
                      {analysis.currency_strength.strongest || 'N/A'}
                    </div>
                  </div>
                  <div className="bg-red-500/10 rounded-lg p-3 text-center">
                    <span className="text-xs text-slate-400">Weakest</span>
                    <div className="text-xl font-bold text-red-400">
                      {analysis.currency_strength.weakest || 'N/A'}
                    </div>
                  </div>
                </div>
                {analysis.currency_strength.recommendation && (
                  <p className="text-xs text-slate-400">💡 {analysis.currency_strength.recommendation}</p>
                )}
              </div>

              {/* TradingView Technical */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Target className="w-4 h-4 text-purple-400" />
                    <span className="font-medium">TradingView Technical</span>
                  </div>
                  <BiasIndicator bias={analysis.tradingview_technical.signal} size="sm" />
                </div>
                <div className="text-center">
                  <span className="text-2xl font-bold">
                    {analysis.tradingview_technical.consensus?.replace(/_/g, ' ').toUpperCase()}
                  </span>
                  <p className="text-xs text-slate-400 mt-1">
                    Strength: {analysis.tradingview_technical.strength}
                  </p>
                </div>
              </div>

              {/* Rate Expectations */}
              <div className="card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <ArrowUpDown className="w-4 h-4 text-indigo-400" />
                  <span className="font-medium">Fed Rate Expectations</span>
                </div>
                <div className="text-center">
                  <div className={cn(
                    "text-xl font-bold",
                    analysis.rate_expectations.fed.next_move === 'hike' ? 'text-green-400' :
                    analysis.rate_expectations.fed.next_move === 'cut' ? 'text-red-400' : 'text-slate-400'
                  )}>
                    {analysis.rate_expectations.fed.next_move?.toUpperCase()}
                  </div>
                  {analysis.rate_expectations.fed.usd_impact && (
                    <p className="text-xs text-slate-400 mt-1">
                      USD Impact: {analysis.rate_expectations.fed.usd_impact}
                    </p>
                  )}
                </div>
              </div>

              {/* Bond Yields */}
              {analysis.bond_yields.spread !== null && (
                <div className="card p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <TrendingUp className="w-4 h-4 text-cyan-400" />
                    <span className="font-medium">Bond Yield Spread (US-DE)</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <span className="text-xs text-slate-400">US 10Y</span>
                      <div className="font-mono font-bold">{analysis.bond_yields.us_10y?.toFixed(2)}%</div>
                    </div>
                    <div>
                      <span className="text-xs text-slate-400">DE 10Y</span>
                      <div className="font-mono font-bold">{analysis.bond_yields.de_10y?.toFixed(2)}%</div>
                    </div>
                    <div>
                      <span className="text-xs text-slate-400">Spread</span>
                      <div className="font-mono font-bold text-amber-400">{analysis.bond_yields.spread?.toFixed(2)}%</div>
                    </div>
                  </div>
                  <div className="mt-2 text-center">
                    <span className="text-xs text-slate-400">EUR/USD Bias: </span>
                    <span className={cn(
                      "text-sm font-semibold",
                      analysis.bond_yields.eurusd_bias === 'bullish' ? 'text-green-400' :
                      analysis.bond_yields.eurusd_bias === 'bearish' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.bond_yields.eurusd_bias?.toUpperCase()}
                    </span>
                  </div>
                </div>
              )}

              {/* Economic Surprise */}
              <div className="card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Newspaper className="w-4 h-4 text-amber-400" />
                  <span className="font-medium">Economic Surprises</span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div>
                    <span className="text-xs text-slate-400">US</span>
                    <BiasIndicator bias={analysis.economic_surprise.us} size="sm" />
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">EU</span>
                    <BiasIndicator bias={analysis.economic_surprise.eu} size="sm" />
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">UK</span>
                    <BiasIndicator bias={analysis.economic_surprise.uk} size="sm" />
                  </div>
                </div>
              </div>
            </div>

            {/* Column 3: Advanced Intelligence */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <Zap className="w-5 h-5 text-yellow-400" />
                Advanced Intelligence
              </h3>

              {/* Social Sentiment */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Users className="w-4 h-4 text-blue-400" />
                    <span className="font-medium">Social Sentiment (X/Twitter)</span>
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs rounded",
                    analysis.social_sentiment.volume === 'high' ? 'bg-green-500/20 text-green-400' :
                    analysis.social_sentiment.volume === 'medium' ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.social_sentiment.volume?.toUpperCase()} VOLUME
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-xs text-slate-400">Sentiment</span>
                    <div className={cn(
                      "font-semibold",
                      analysis.social_sentiment.sentiment?.includes('bullish') ? 'text-green-400' :
                      analysis.social_sentiment.sentiment?.includes('bearish') ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.social_sentiment.sentiment?.replace(/_/g, ' ').toUpperCase()}
                    </div>
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">Contrarian Signal</span>
                    <div className={cn(
                      "font-bold",
                      analysis.social_sentiment.contrarian_signal === 'long' ? 'text-green-400' :
                      analysis.social_sentiment.contrarian_signal === 'short' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.social_sentiment.contrarian_signal?.toUpperCase()}
                    </div>
                  </div>
                </div>
              </div>

              {/* Options Flow */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Gauge className="w-4 h-4 text-amber-400" />
                    <span className="font-medium">Options Flow</span>
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.options_flow.flow === 'bullish' ? 'bg-green-500/20 text-green-400' :
                    analysis.options_flow.flow === 'bearish' ? 'bg-red-500/20 text-red-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.options_flow.flow?.toUpperCase()}
                  </span>
                </div>
                {analysis.options_flow.magnet_levels?.length > 0 && (
                  <div>
                    <span className="text-xs text-slate-400">Magnet Levels (Price Attractors)</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {analysis.options_flow.magnet_levels.slice(0, 4).map((level, i) => (
                        <span key={i} className="px-2 py-1 bg-slate-700 rounded text-sm font-mono">
                          {level}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Seasonal Pattern */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-orange-400" />
                    <span className="font-medium">Seasonal Pattern</span>
                  </div>
                  <span className={cn(
                    "px-2 py-0.5 text-xs font-semibold rounded",
                    analysis.seasonal_pattern.confidence === 'high' ? 'bg-green-500/20 text-green-400' :
                    analysis.seasonal_pattern.confidence === 'moderate' ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-slate-500/20 text-slate-400'
                  )}>
                    {analysis.seasonal_pattern.historical_accuracy}% ACCURACY
                  </span>
                </div>
                <div className="text-center">
                  <span className="text-xs text-slate-400">{analysis.seasonal_pattern.current_month} Bias</span>
                  <div className={cn(
                    "text-xl font-bold",
                    analysis.seasonal_pattern.current_month_bias === 'bullish' ? 'text-green-400' :
                    analysis.seasonal_pattern.current_month_bias === 'bearish' ? 'text-red-400' : 'text-slate-400'
                  )}>
                    {analysis.seasonal_pattern.current_month_bias?.toUpperCase()}
                  </div>
                </div>
                {analysis.seasonal_pattern.note && (
                  <p className="text-xs text-slate-400 mt-2">📝 {analysis.seasonal_pattern.note}</p>
                )}
              </div>

              {/* BTC Dominance (for crypto) */}
              {analysis.is_crypto && analysis.btc_dominance && (
                <div className="card p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Bitcoin className="w-4 h-4 text-orange-400" />
                      <span className="font-medium">BTC Dominance</span>
                    </div>
                    <span className={cn(
                      "px-2 py-0.5 text-xs font-semibold rounded",
                      analysis.btc_dominance.trend === 'rising' ? 'bg-green-500/20 text-green-400' :
                      analysis.btc_dominance.trend === 'falling' ? 'bg-red-500/20 text-red-400' :
                      'bg-slate-500/20 text-slate-400'
                    )}>
                      {analysis.btc_dominance.trend?.toUpperCase()}
                    </span>
                  </div>
                  <div className="text-center">
                    <span className="text-3xl font-bold">{analysis.btc_dominance.dominance?.toFixed(1)}%</span>
                  </div>
                  <div className="mt-2 text-center">
                    <span className="text-xs text-slate-400">Altcoin Sentiment: </span>
                    <span className={cn(
                      "font-semibold",
                      analysis.btc_dominance.altcoin_sentiment === 'bullish' ? 'text-green-400' :
                      analysis.btc_dominance.altcoin_sentiment === 'bearish' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.btc_dominance.altcoin_sentiment?.toUpperCase()}
                    </span>
                  </div>
                </div>
              )}

              {/* Commodities */}
              <div className="card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <TrendingUp className="w-4 h-4 text-yellow-400" />
                  <span className="font-medium">Commodity Correlations</span>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-xs text-slate-400">Oil</span>
                    <div className={cn(
                      "font-semibold",
                      analysis.oil_correlation.trend === 'bullish' ? 'text-green-400' :
                      analysis.oil_correlation.trend === 'bearish' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.oil_correlation.trend?.toUpperCase()}
                    </div>
                    {analysis.oil_correlation.currency_implication?.pair_recommendation && (
                      <p className="text-[10px] text-slate-500">{analysis.oil_correlation.currency_implication.pair_recommendation}</p>
                    )}
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">Gold</span>
                    <div className={cn(
                      "font-semibold",
                      analysis.gold_correlation.trend === 'bullish' ? 'text-green-400' :
                      analysis.gold_correlation.trend === 'bearish' ? 'text-red-400' : 'text-slate-400'
                    )}>
                      {analysis.gold_correlation.trend?.toUpperCase()}
                    </div>
                    {analysis.gold_correlation.currency_implication?.pair_recommendation && (
                      <p className="text-[10px] text-slate-500">{analysis.gold_correlation.currency_implication.pair_recommendation}</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Cache Status */}
          <div className="card p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <Clock className="w-4 h-4" />
                <span>Last updated: {new Date(analysis.timestamp).toLocaleString()}</span>
              </div>
              <div className="flex items-center gap-4 text-xs text-slate-500">
                <span>Cached: {status?.cached_keys?.length || 0} items</span>
                <span>Refresh: {status?.refresh_minutes || 15} min</span>
              </div>
            </div>
          </div>
        </>
      ) : (
        <div className="card p-8 text-center">
          <AlertTriangle className="w-12 h-12 text-yellow-500 mx-auto mb-4" />
          <h2 className="text-lg font-semibold mb-2">No Analysis Available</h2>
          <p className="text-slate-400 mb-4">
            {error ? (
              <>
                {error.includes('API key') || error.includes('401') || error.includes('configured') ? (
                  <>
                    Firecrawl API key not configured. <a href="/settings" className="text-purple-400 hover:underline">Go to Settings → API Keys</a> to add your key.
                  </>
                ) : (
                  <>Click refresh to load intelligence data.</>
                )}
              </>
            ) : (
              <>Click refresh to load intelligence data. If you haven&apos;t configured your Firecrawl API key, <a href="/settings" className="text-purple-400 hover:underline">go to Settings</a>.</>
            )}
          </p>
          <button
            onClick={handleRefresh}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded-lg transition-colors"
          >
            <RefreshCw className="w-4 h-4 inline mr-2" />
            Load Data
          </button>
        </div>
      )}
    </div>
  )
}
