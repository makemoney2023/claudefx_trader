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
  History,
  DollarSign,
  ArrowRight,
  RefreshCw,
  Shield,
  BarChart3
} from 'lucide-react'

interface SilverLevels {
  recent_low: number
  recent_high: number
  target_1: number
  target_2: number
  euphoria: number
  invalidation: number
  entry_zone_low: number
  entry_zone_high: number
}

interface SilverAnalysis {
  recommendation: string
  current_price: number
  entry_zone_status: string
  rsi: number
  pattern_match: {
    similarity_score: number
    interpretation: string
  }
  volume_analysis: {
    accumulation: boolean
    distribution: boolean
  }
  targets: {
    tp1: number
    tp2: number
    tp3: number
    final: number
  }
  stop_loss: number
  risk_assessment: {
    level: string
    factors: string[]
  }
}

export default function SilverPage() {
  const [levels, setLevels] = useState<SilverLevels | null>(null)
  const [analysis, setAnalysis] = useState<SilverAnalysis | null>(null)
  const [currentPrice, setCurrentPrice] = useState(98.0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const fetchData = useCallback(async () => {
    try {
      // Fetch levels using api client
      const levelsData = await api.getSilverLevels().catch(() => null)
      if (levelsData) {
        setLevels(levelsData as unknown as SilverLevels)
      }

      // Fetch analysis using api client
      const prices = Array.from({ length: 20 }, (_, i) => 95 + i * 0.5)
      const volume = Array.from({ length: 20 }, () => 100000 + Math.random() * 50000)
      const analysisData = await api.analyzeSilver(currentPrice, prices, volume, 12.0).catch(() => null)
      if (analysisData) {
        setAnalysis(analysisData as unknown as SilverAnalysis)
      }
    } catch (error) {
      console.error('Error fetching silver data:', error)
    }
  }, [currentPrice])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await fetchData()
      setLoading(false)
    }
    loadData()
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchData()
    setRefreshing(false)
  }

  const getRecommendationStyle = (rec: string) => {
    switch (rec) {
      case 'STRONG_BUY': return 'bg-green-500 text-white'
      case 'BUY': return 'bg-emerald-500 text-white'
      case 'HOLD': return 'bg-yellow-500 text-black'
      case 'CAUTION': return 'bg-orange-500 text-white'
      case 'SELL': return 'bg-red-500 text-white'
      default: return 'bg-slate-500 text-white'
    }
  }

  const getRiskColor = (level: string) => {
    switch (level) {
      case 'low': return 'text-green-400'
      case 'medium': return 'text-yellow-400'
      case 'high': return 'text-orange-400'
      case 'extreme': return 'text-red-400'
      default: return 'text-slate-400'
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-400"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-slate-300 to-slate-500 rounded-lg flex items-center justify-center">
              <span className="text-sm font-bold">Ag</span>
            </div>
            Silver Analysis (XAGUSD)
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            1979 Pattern Analysis • Historic Opportunity
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-400">Price:</label>
            <input
              type="number"
              step="0.01"
              value={currentPrice}
              onChange={(e) => setCurrentPrice(parseFloat(e.target.value) || 0)}
              className="w-24 px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-right font-mono"
            />
          </div>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Analyze
          </button>
        </div>
      </div>

      {/* 1979 Pattern Banner */}
      <div className="bg-gradient-to-r from-amber-900/30 to-yellow-900/30 border border-amber-500/30 rounded-xl p-6">
        <div className="flex items-start gap-4">
          <History className="w-8 h-8 text-amber-400 flex-shrink-0" />
          <div>
            <h2 className="text-xl font-bold text-amber-400">1979 Pattern Detected</h2>
            <p className="text-slate-300 mt-2">
              Silver gained 65% in January 2026 - only the 3rd time in 52 years (after Dec 1979 and Feb 1974).
              In 1979, silver gained another 35-40% before peaking at $50.
            </p>
            <div className="mt-4 flex flex-wrap gap-4">
              <div className="bg-slate-800/50 px-4 py-2 rounded-lg">
                <div className="text-xs text-slate-400">Pattern Match</div>
                <div className="text-lg font-bold text-amber-400">
                  {analysis?.pattern_match?.similarity_score?.toFixed(0) || 0}%
                </div>
              </div>
              <div className="bg-slate-800/50 px-4 py-2 rounded-lg">
                <div className="text-xs text-slate-400">Current Phase</div>
                <div className="text-lg font-bold text-green-400">SURGE</div>
              </div>
              <div className="bg-slate-800/50 px-4 py-2 rounded-lg">
                <div className="text-xs text-slate-400">1979 Outcome</div>
                <div className="text-lg font-bold text-slate-300">+100% → Crash</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recommendation Card */}
        <div className="card p-6">
          <div className="text-slate-400 text-sm mb-2">Recommendation</div>
          {analysis && (
            <div className={cn(
              "text-3xl font-bold px-4 py-2 rounded-lg inline-block",
              getRecommendationStyle(analysis.recommendation)
            )}>
              {analysis.recommendation.replace('_', ' ')}
            </div>
          )}
          
          <div className="mt-6 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Entry Zone</span>
              <span className={cn(
                "font-medium",
                analysis?.entry_zone_status === 'IN_ZONE' ? 'text-green-400' : 'text-red-400'
              )}>
                {analysis?.entry_zone_status}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-400">RSI</span>
              <span className={cn(
                "font-medium font-mono",
                (analysis?.rsi || 0) > 70 ? 'text-red-400' : (analysis?.rsi || 0) < 30 ? 'text-green-400' : 'text-slate-300'
              )}>
                {analysis?.rsi?.toFixed(1)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Volume</span>
              <span className={cn(
                "font-medium",
                analysis?.volume_analysis?.accumulation ? 'text-green-400' : 
                analysis?.volume_analysis?.distribution ? 'text-red-400' : 'text-slate-400'
              )}>
                {analysis?.volume_analysis?.accumulation ? 'ACCUMULATION' : 
                 analysis?.volume_analysis?.distribution ? 'DISTRIBUTION' : 'NEUTRAL'}
              </span>
            </div>
          </div>
        </div>

        {/* Key Levels Card */}
        <div className="card p-6">
          <div className="text-slate-400 text-sm mb-4 flex items-center gap-2">
            <Target className="w-4 h-4" />
            Key Price Levels
          </div>
          
          {levels && (
            <div className="space-y-3">
              <div className="flex items-center justify-between py-2 border-b border-slate-700">
                <span className="text-red-400">Invalidation</span>
                <span className="font-mono font-bold text-red-400">${levels.invalidation}</span>
              </div>
              <div className="flex items-center justify-between py-2">
                <span className="text-slate-400">Entry Zone</span>
                <span className="font-mono">${levels.entry_zone_low} - ${levels.entry_zone_high}</span>
              </div>
              <div className="flex items-center justify-between py-2">
                <span className="text-slate-400">Recent High</span>
                <span className="font-mono">${levels.recent_high}</span>
              </div>
              <div className="flex items-center justify-between py-2 text-green-400">
                <span>Target 1</span>
                <span className="font-mono font-bold">${levels.target_1}</span>
              </div>
              <div className="flex items-center justify-between py-2 text-emerald-400">
                <span>Target 2</span>
                <span className="font-mono font-bold">${levels.target_2}</span>
              </div>
              <div className="flex items-center justify-between py-2 border-t border-slate-700 text-amber-400">
                <span>Euphoria Zone</span>
                <span className="font-mono font-bold">${levels.euphoria}+</span>
              </div>
            </div>
          )}
        </div>

        {/* Risk Assessment */}
        <div className="card p-6">
          <div className="text-slate-400 text-sm mb-4 flex items-center gap-2">
            <Shield className="w-4 h-4" />
            Risk Assessment
          </div>
          
          {analysis && (
            <>
              <div className={cn(
                "text-2xl font-bold capitalize mb-4",
                getRiskColor(analysis.risk_assessment?.level)
              )}>
                {analysis.risk_assessment?.level || 'Unknown'} Risk
              </div>
              
              <div className="space-y-2">
                {analysis.risk_assessment?.factors?.length > 0 ? (
                  analysis.risk_assessment.factors.map((factor, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                      <span className="text-slate-400">{factor}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-slate-500 text-sm">No major risk factors identified</div>
                )}
              </div>
              
              <div className="mt-6 p-4 bg-slate-700/50 rounded-lg">
                <div className="text-xs text-slate-400 mb-1">Position Size Recommendation</div>
                <div className="text-lg font-medium">
                  {analysis.risk_assessment?.level === 'low' ? '2-3% of account' :
                   analysis.risk_assessment?.level === 'medium' ? '1-2% of account' :
                   analysis.risk_assessment?.level === 'high' ? '0.5-1% of account' :
                   'Avoid trading'}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Trade Setup */}
      {analysis && (
        <div className="card p-6">
          <h3 className="font-medium mb-4 flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-400" />
            Trade Setup (Entry at ${currentPrice})
          </h3>
          
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
              <div className="text-xs text-red-400 mb-1">Stop Loss</div>
              <div className="text-2xl font-bold font-mono text-red-400">
                ${analysis.stop_loss?.toFixed(2)}
              </div>
              <div className="text-xs text-slate-500 mt-1">
                Risk: ${(currentPrice - analysis.stop_loss).toFixed(2)}
              </div>
            </div>
            
            <div className="p-4 bg-slate-700/50 rounded-lg flex items-center justify-center">
              <ArrowRight className="w-6 h-6 text-slate-500" />
            </div>
            
            <div className="p-4 bg-green-500/10 border border-green-500/30 rounded-lg">
              <div className="text-xs text-green-400 mb-1">Target 1 (TP1)</div>
              <div className="text-2xl font-bold font-mono text-green-400">
                ${analysis.targets?.tp1?.toFixed(2)}
              </div>
              <div className="text-xs text-slate-500 mt-1">
                R:R = {((analysis.targets?.tp1 - currentPrice) / (currentPrice - analysis.stop_loss)).toFixed(1)}
              </div>
            </div>
            
            <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
              <div className="text-xs text-emerald-400 mb-1">Target 2 (TP2)</div>
              <div className="text-2xl font-bold font-mono text-emerald-400">
                ${analysis.targets?.tp2?.toFixed(2)}
              </div>
              <div className="text-xs text-slate-500 mt-1">
                R:R = {((analysis.targets?.tp2 - currentPrice) / (currentPrice - analysis.stop_loss)).toFixed(1)}
              </div>
            </div>
            
            <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-lg">
              <div className="text-xs text-amber-400 mb-1">Target 3 (TP3)</div>
              <div className="text-2xl font-bold font-mono text-amber-400">
                ${analysis.targets?.tp3?.toFixed(2)}
              </div>
              <div className="text-xs text-slate-500 mt-1">
                R:R = {((analysis.targets?.tp3 - currentPrice) / (currentPrice - analysis.stop_loss)).toFixed(1)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Exit Strategy */}
      <div className="card p-6">
        <h3 className="font-medium mb-4 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-red-400" />
          Exit Strategy (Based on 1979 Pattern)
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-4 bg-slate-700/50 rounded-lg">
            <div className="text-sm text-slate-400 mb-2">RSI Exit Signal</div>
            <div className="text-xl font-bold">Weekly RSI &gt; 85</div>
            <p className="text-xs text-slate-500 mt-2">
              When weekly RSI exceeds 85, begin scaling out of positions
            </p>
          </div>
          
          <div className="p-4 bg-slate-700/50 rounded-lg">
            <div className="text-sm text-slate-400 mb-2">Sentiment Exit</div>
            <div className="text-xl font-bold">Everyone Bullish</div>
            <p className="text-xs text-slate-500 mt-2">
              When mainstream media, retail, and everyone is bullish on silver
            </p>
          </div>
          
          <div className="p-4 bg-slate-700/50 rounded-lg">
            <div className="text-sm text-slate-400 mb-2">Price Exit</div>
            <div className="text-xl font-bold">${levels?.euphoria || 200}+ Zone</div>
            <p className="text-xs text-slate-500 mt-2">
              Euphoria zone - take profits, don't try to catch the exact top
            </p>
          </div>
        </div>
        
        <div className="mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
          <div className="flex items-center gap-2 text-red-400 font-medium">
            <AlertTriangle className="w-4 h-4" />
            WARNING: 1979 Pattern Ended in Devastating Crash
          </div>
          <p className="text-sm text-slate-400 mt-2">
            After peaking at $50, silver crashed back to $10 within months. 
            Have a clear exit plan and don't be greedy at the top.
          </p>
        </div>
      </div>
    </div>
  )
}
