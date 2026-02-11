'use client'

import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { 
  TrendingUp, 
  TrendingDown,
  Target, 
  AlertTriangle,
  Zap,
  Shield,
  Scale,
  RefreshCw,
  ArrowUpRight,
  ArrowDownRight,
  Coins
} from 'lucide-react'

interface PreciousMetalsSummary {
  timestamp: string
  gold_price: number
  silver_price: number
  gold_recommendation: string
  silver_recommendation: string
  ratio: number
  ratio_interpretation: string
  primary_metal: string
  primary_reasoning: string
  safe_haven_level: string
}

interface GoldLevels {
  recent_low: number
  recent_high: number
  all_time_high: number
  support_1: number
  support_2: number
  resistance_1: number
  resistance_2: number
  invalidation: number
  entry_zone_low: number
  entry_zone_high: number
}

interface RatioData {
  current_ratio: number
  historical_avg: number
  normal_low: number
  normal_high: number
  interpretation: string
  trade_bias: string
}

export default function PreciousMetalsPage() {
  const [goldPrice, setGoldPrice] = useState(2950)
  const [silverPrice, setSilverPrice] = useState(32.50)
  const [summary, setSummary] = useState<PreciousMetalsSummary | null>(null)
  const [goldLevels, setGoldLevels] = useState<GoldLevels | null>(null)
  const [ratioData, setRatioData] = useState<RatioData | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const fetchData = useCallback(async (gold: number, silver: number) => {
    try {
      const [summaryData, levelsData, ratioResult] = await Promise.all([
        api.getPreciousMetalsSummary(gold, silver).catch(() => null),
        api.getGoldLevels().catch(() => null),
        api.getGoldSilverRatio(gold, silver).catch(() => null)
      ])
      
      if (summaryData) {
        setSummary(summaryData)
        // Update prices from API response if available
        if (summaryData.gold_price && summaryData.gold_price > 0) setGoldPrice(summaryData.gold_price)
        if (summaryData.silver_price && summaryData.silver_price > 0) setSilverPrice(summaryData.silver_price)
      }
      if (levelsData) setGoldLevels(levelsData)
      if (ratioResult) setRatioData(ratioResult)
    } catch (error) {
      console.error('Error fetching precious metals data:', error)
    }
  }, [])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      // Try to get live prices from MT5 first
      try {
        const livePrices = await api.getLiveMetalsPrices()
        if (livePrices) {
          const gold = livePrices.gold_price > 0 ? livePrices.gold_price : goldPrice
          const silver = livePrices.silver_price > 0 ? livePrices.silver_price : silverPrice
          setGoldPrice(gold)
          setSilverPrice(silver)
          await fetchData(gold, silver)
          setLoading(false)
          return
        }
      } catch {
        // MT5 prices unavailable, use defaults
      }
      await fetchData(goldPrice, silverPrice)
      setLoading(false)
    }
    loadData()
    // Only run on mount, not on every price change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchData(goldPrice, silverPrice)
    setRefreshing(false)
  }

  const getRecommendationStyle = (rec: string) => {
    switch (rec) {
      case 'STRONG_BUY': return 'bg-green-500 text-white'
      case 'BUY': return 'bg-emerald-500 text-white'
      case 'HOLD': return 'bg-slate-500 text-white'
      case 'CAUTION': return 'bg-orange-500 text-white'
      case 'SELL': return 'bg-red-500 text-white'
      default: return 'bg-slate-500 text-white'
    }
  }

  const getSafeHavenStyle = (level: string) => {
    switch (level) {
      case 'very_high': return 'bg-red-500/20 border-red-500/50 text-red-400'
      case 'elevated': return 'bg-orange-500/20 border-orange-500/50 text-orange-400'
      case 'normal': return 'bg-green-500/20 border-green-500/50 text-green-400'
      default: return 'bg-slate-500/20 border-slate-500/50 text-slate-400'
    }
  }

  const getRatioColor = (ratio: number) => {
    if (ratio >= 80) return 'text-amber-400'
    if (ratio <= 60) return 'text-cyan-400'
    return 'text-slate-300'
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Coins className="w-7 h-7 text-amber-400" />
            Precious Metals Analysis
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Gold & Silver Combined Dashboard
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-400">Gold:</label>
            <input
              type="number"
              step="1"
              value={goldPrice}
              onChange={(e) => setGoldPrice(parseFloat(e.target.value) || 0)}
              className="w-24 px-3 py-2 bg-slate-800 border border-amber-500/30 rounded-lg text-right font-mono text-amber-400"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-400">Silver:</label>
            <input
              type="number"
              step="0.01"
              value={silverPrice}
              onChange={(e) => setSilverPrice(parseFloat(e.target.value) || 0)}
              className="w-24 px-3 py-2 bg-slate-800 border border-slate-500/30 rounded-lg text-right font-mono"
            />
          </div>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded-lg"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Analyze
          </button>
        </div>
      </div>

      {/* Safe Haven Banner */}
      {summary && (
        <div className={cn(
          "rounded-xl p-4 border flex items-center justify-between",
          getSafeHavenStyle(summary.safe_haven_level)
        )}>
          <div className="flex items-center gap-3">
            <Shield className="w-6 h-6" />
            <div>
              <span className="font-medium">Safe Haven Demand: </span>
              <span className="capitalize">{summary.safe_haven_level.replace('_', ' ')}</span>
            </div>
          </div>
          <div className="text-sm">
            Primary: <span className="font-bold">{summary.primary_metal}</span>
          </div>
        </div>
      )}

      {/* Price Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Gold Card */}
        <div className="card p-6 border-amber-500/30">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 bg-gradient-to-br from-amber-400 to-yellow-600 rounded-xl flex items-center justify-center">
                <span className="text-xl font-bold text-black">Au</span>
              </div>
              <div>
                <div className="text-xl font-bold text-amber-400">GOLD</div>
                <div className="text-slate-400 text-sm">XAUUSD</div>
              </div>
            </div>
            {summary && (
              <div className={cn(
                "px-3 py-1 rounded-lg font-medium",
                getRecommendationStyle(summary.gold_recommendation)
              )}>
                {summary.gold_recommendation}
              </div>
            )}
          </div>
          
          <div className="text-3xl font-bold text-amber-400 mb-4">
            ${goldPrice.toLocaleString()}
          </div>
          
          {goldLevels && (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between py-1 border-b border-slate-700">
                <span className="text-red-400">Support</span>
                <span className="font-mono">${goldLevels.support_1.toLocaleString()} / ${goldLevels.support_2.toLocaleString()}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-slate-700">
                <span className="text-green-400">Resistance</span>
                <span className="font-mono">${goldLevels.resistance_1.toLocaleString()} / ${goldLevels.resistance_2.toLocaleString()}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-slate-700">
                <span className="text-amber-400">Entry Zone</span>
                <span className="font-mono">${goldLevels.entry_zone_low.toLocaleString()} - ${goldLevels.entry_zone_high.toLocaleString()}</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-purple-400">All-Time High</span>
                <span className="font-mono font-bold">${goldLevels.all_time_high.toLocaleString()}</span>
              </div>
            </div>
          )}
        </div>

        {/* Silver Card */}
        <div className="card p-6 border-slate-400/30">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 bg-gradient-to-br from-slate-300 to-slate-500 rounded-xl flex items-center justify-center">
                <span className="text-xl font-bold text-black">Ag</span>
              </div>
              <div>
                <div className="text-xl font-bold text-slate-300">SILVER</div>
                <div className="text-slate-400 text-sm">XAGUSD</div>
              </div>
            </div>
            {summary && (
              <div className={cn(
                "px-3 py-1 rounded-lg font-medium",
                getRecommendationStyle(summary.silver_recommendation)
              )}>
                {summary.silver_recommendation}
              </div>
            )}
          </div>
          
          <div className="text-3xl font-bold text-slate-300 mb-4">
            ${silverPrice.toFixed(2)}
          </div>
          
          <div className="space-y-2 text-sm">
            <div className="flex justify-between py-1 border-b border-slate-700">
              <span className="text-red-400">Entry Zone</span>
              <span className="font-mono">$95 - $105</span>
            </div>
            <div className="flex justify-between py-1 border-b border-slate-700">
              <span className="text-green-400">Target 1</span>
              <span className="font-mono">$150</span>
            </div>
            <div className="flex justify-between py-1 border-b border-slate-700">
              <span className="text-emerald-400">Target 2</span>
              <span className="font-mono">$160</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-amber-400">Euphoria Exit</span>
              <span className="font-mono font-bold">$200+</span>
            </div>
          </div>
          
          {/* 1979 Pattern Badge */}
          <div className="mt-4 p-2 bg-amber-500/10 border border-amber-500/30 rounded-lg text-center">
            <span className="text-xs text-amber-400">1979 Pattern Active</span>
          </div>
        </div>
      </div>

      {/* Gold/Silver Ratio */}
      {ratioData && (
        <div className="card p-6">
          <h3 className="font-medium mb-4 flex items-center gap-2">
            <Scale className="w-5 h-5 text-amber-400" />
            Gold/Silver Ratio
          </h3>
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="text-center">
              <div className={cn("text-4xl font-bold mb-1", getRatioColor(ratioData.current_ratio))}>
                {ratioData.current_ratio.toFixed(1)}
              </div>
              <div className="text-slate-400 text-sm">Current Ratio</div>
            </div>
            
            <div className="text-center">
              <div className="text-2xl font-bold text-slate-400 mb-1">
                {ratioData.normal_low} - {ratioData.normal_high}
              </div>
              <div className="text-slate-400 text-sm">Normal Range</div>
            </div>
            
            <div className="text-center">
              <div className="text-2xl font-bold text-slate-300 mb-1">
                {ratioData.historical_avg}
              </div>
              <div className="text-slate-400 text-sm">Historical Avg</div>
            </div>
          </div>
          
          {/* Ratio Bar */}
          <div className="mt-6">
            <div className="relative h-4 bg-slate-700 rounded-full overflow-hidden">
              <div 
                className="absolute h-full bg-gradient-to-r from-cyan-500 via-slate-500 to-amber-500"
                style={{ width: '100%' }}
              />
              {/* Position marker */}
              <div 
                className="absolute top-0 h-full w-1 bg-white"
                style={{ 
                  left: `${Math.min(Math.max((ratioData.current_ratio - 40) / 80 * 100, 0), 100)}%`,
                  transform: 'translateX(-50%)'
                }}
              />
            </div>
            <div className="flex justify-between mt-1 text-xs text-slate-500">
              <span>40 (Silver overvalued)</span>
              <span>120 (Silver undervalued)</span>
            </div>
          </div>
          
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div className="p-3 bg-slate-700/50 rounded-lg">
              <div className="text-sm text-slate-400">Interpretation</div>
              <div className="font-medium">{ratioData.interpretation}</div>
            </div>
            <div className="p-3 bg-slate-700/50 rounded-lg">
              <div className="text-sm text-slate-400">Trade Bias</div>
              <div className="font-medium text-amber-400">{ratioData.trade_bias}</div>
            </div>
          </div>
        </div>
      )}

      {/* Primary Recommendation */}
      {summary && (
        <div className="card p-6">
          <h3 className="font-medium mb-4 flex items-center gap-2">
            <Target className="w-5 h-5 text-green-400" />
            Primary Recommendation
          </h3>
          
          <div className="flex items-start gap-4">
            <div className={cn(
              "px-4 py-2 rounded-lg text-xl font-bold",
              summary.primary_metal === 'GOLD' ? 'bg-amber-500/20 text-amber-400' :
              summary.primary_metal === 'SILVER' ? 'bg-slate-500/20 text-slate-300' :
              'bg-green-500/20 text-green-400'
            )}>
              {summary.primary_metal}
            </div>
            <div className="flex-1">
              <p className="text-slate-400">{summary.primary_reasoning}</p>
            </div>
          </div>
        </div>
      )}

      {/* Trading Notes */}
      <div className="card p-6">
        <h3 className="font-medium mb-4 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-amber-400" />
          Trading Notes
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2 text-sm">
            <div className="flex items-start gap-2">
              <ArrowUpRight className="w-4 h-4 text-green-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">Gold is less volatile, preferred in risk-off</span>
            </div>
            <div className="flex items-start gap-2">
              <Zap className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">Silver has 2x gold volatility - adjust size accordingly</span>
            </div>
            <div className="flex items-start gap-2">
              <TrendingDown className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">USD inverse correlation - watch DXY</span>
            </div>
          </div>
          
          <div className="space-y-2 text-sm">
            <div className="flex items-start gap-2">
              <Scale className="w-4 h-4 text-cyan-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">High ratio (&gt;80) favors silver longs</span>
            </div>
            <div className="flex items-start gap-2">
              <Shield className="w-4 h-4 text-purple-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">Geopolitical events boost both metals</span>
            </div>
            <div className="flex items-start gap-2">
              <TrendingUp className="w-4 h-4 text-green-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">Both rising = strong precious metals bid</span>
            </div>
          </div>
        </div>
      </div>

      {/* Correlation Warning */}
      <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
        <div className="flex items-center gap-2 text-red-400 font-medium mb-2">
          <AlertTriangle className="w-4 h-4" />
          Position Sizing Note
        </div>
        <p className="text-sm text-slate-400">
          Gold and Silver have ~90% correlation. If holding positions in both, consider them as 
          <span className="text-red-400 font-medium"> one combined position </span>
          for risk management. Avoid full-size positions in both simultaneously.
        </p>
      </div>
    </div>
  )
}
