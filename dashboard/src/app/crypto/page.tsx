'use client'

import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { 
  Coins, 
  TrendingUp, 
  TrendingDown,
  AlertTriangle,
  Shield,
  Zap,
  RefreshCw,
  Clock,
  Scale,
  ArrowUpRight,
  ArrowDownRight
} from 'lucide-react'

interface CryptoLevels {
  support_1: number
  support_2: number
  resistance_1: number
  resistance_2: number
  recent_low: number
  recent_high: number
  all_time_high: number
}

interface CryptoAnalysis {
  symbol: string
  name: string
  current_price: number
  recommendation: string
  reasoning: string
  technical: {
    rsi: number
    volatility: number
    is_high_volatility: boolean
  }
  levels: CryptoLevels & { distance_to_ath_percent: number }
  position_near: {
    support: boolean
    support_level: string
    resistance: boolean
    resistance_level: string
  }
  risk: {
    factors: string[]
    level: string
    regulatory: {
      risk: string
      details: string
      recommendation: string
    }
  }
  position_size_adjustment: number
  use_case: string
  is_24_7: boolean
}

const CRYPTOS = [
  { symbol: 'XRPUSD', name: 'Ripple (XRP)', icon: '💧', color: 'text-blue-400' },
  { symbol: 'ADAUSD', name: 'Cardano (ADA)', icon: '🔷', color: 'text-cyan-400' },
]

export default function CryptoPage() {
  const [selectedCrypto, setSelectedCrypto] = useState('XRPUSD')
  const [currentPrice, setCurrentPrice] = useState(2.50)
  const [analysis, setAnalysis] = useState<CryptoAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const fetchAnalysis = useCallback(async () => {
    try {
      const prices = Array.from({ length: 20 }, (_, i) => currentPrice * (0.95 + i * 0.005))
      const data = await api.analyzeCrypto(selectedCrypto, currentPrice, prices).catch(() => null)
      
      if (data) {
        setAnalysis(data as unknown as CryptoAnalysis)
      }
    } catch (error) {
      console.error('Error fetching crypto analysis:', error)
    }
  }, [selectedCrypto, currentPrice])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await fetchAnalysis()
      setLoading(false)
    }
    loadData()
  }, [fetchAnalysis])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchAnalysis()
    setRefreshing(false)
  }

  const getRecommendationStyle = (rec: string) => {
    switch (rec) {
      case 'BUY': return 'bg-green-500 text-white'
      case 'SELL': return 'bg-red-500 text-white'
      case 'HOLD': return 'bg-slate-500 text-white'
      case 'CAUTION': return 'bg-orange-500 text-white'
      default: return 'bg-slate-500 text-white'
    }
  }

  const getRiskColor = (level: string) => {
    switch (level) {
      case 'low': return 'text-green-400'
      case 'medium': return 'text-yellow-400'
      case 'high': return 'text-red-400'
      default: return 'text-slate-400'
    }
  }

  // Set default price based on selected crypto
  useEffect(() => {
    if (selectedCrypto === 'XRPUSD') {
      setCurrentPrice(2.50)
    } else if (selectedCrypto === 'ADAUSD') {
      setCurrentPrice(0.95)
    }
  }, [selectedCrypto])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Coins className="w-7 h-7 text-cyan-400" />
            Crypto Analysis
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            XRP & ADA Trading Focus • 24/7 Markets
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          {/* Crypto Selector */}
          <div className="flex bg-slate-800 rounded-lg p-1">
            {CRYPTOS.map((crypto) => (
              <button
                key={crypto.symbol}
                onClick={() => setSelectedCrypto(crypto.symbol)}
                className={cn(
                  "px-4 py-2 rounded-md transition-colors flex items-center gap-2",
                  selectedCrypto === crypto.symbol 
                    ? "bg-cyan-600 text-white" 
                    : "text-slate-400 hover:text-white"
                )}
              >
                <span>{crypto.icon}</span>
                <span>{crypto.symbol.replace('USD', '')}</span>
              </button>
            ))}
          </div>
          
          {/* Price Input */}
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
            className="flex items-center gap-2 px-4 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Analyze
          </button>
        </div>
      </div>

      {/* 24/7 Trading Banner */}
      <div className="bg-gradient-to-r from-cyan-900/30 to-blue-900/30 border border-cyan-500/30 rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Clock className="w-6 h-6 text-cyan-400" />
          <div>
            <span className="font-medium text-cyan-400">24/7 Trading</span>
            <span className="text-slate-400 ml-2">Crypto markets never close</span>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
            <span className="text-slate-400">Markets Open</span>
          </div>
        </div>
      </div>

      {analysis && (
        <>
          {/* Main Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Recommendation Card */}
            <div className="card p-6">
              <div className="flex items-center gap-3 mb-4">
                <span className="text-3xl">
                  {CRYPTOS.find(c => c.symbol === selectedCrypto)?.icon}
                </span>
                <div>
                  <div className="text-2xl font-bold">{analysis.name}</div>
                  <div className="text-slate-400">{analysis.use_case}</div>
                </div>
              </div>
              
              <div className={cn(
                "text-3xl font-bold px-4 py-2 rounded-lg inline-block mb-4",
                getRecommendationStyle(analysis.recommendation)
              )}>
                {analysis.recommendation}
              </div>
              
              <p className="text-slate-400 text-sm">{analysis.reasoning}</p>
              
              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">RSI</span>
                  <span className={cn(
                    "font-mono font-medium",
                    analysis.technical.rsi > 70 ? 'text-red-400' : 
                    analysis.technical.rsi < 30 ? 'text-green-400' : 'text-slate-300'
                  )}>
                    {analysis.technical.rsi.toFixed(1)}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Volatility</span>
                  <span className={cn(
                    "font-medium",
                    analysis.technical.is_high_volatility ? 'text-orange-400' : 'text-slate-300'
                  )}>
                    {analysis.technical.volatility.toFixed(1)}%
                    {analysis.technical.is_high_volatility && ' ⚡'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Position Adj.</span>
                  <span className="text-amber-400 font-medium">
                    {(analysis.position_size_adjustment * 100).toFixed(0)}% of base
                  </span>
                </div>
              </div>
            </div>

            {/* Key Levels */}
            <div className="card p-6">
              <h3 className="font-medium mb-4 flex items-center gap-2">
                <Scale className="w-5 h-5 text-cyan-400" />
                Key Levels
              </h3>
              
              <div className="space-y-3">
                <div className="flex items-center justify-between py-2 border-b border-slate-700">
                  <span className="text-red-400 flex items-center gap-1">
                    <ArrowDownRight className="w-4 h-4" />
                    Support 1
                  </span>
                  <span className="font-mono font-bold">${analysis.levels.support_1}</span>
                </div>
                <div className="flex items-center justify-between py-2 border-b border-slate-700">
                  <span className="text-red-400 flex items-center gap-1">
                    <ArrowDownRight className="w-4 h-4" />
                    Support 2
                  </span>
                  <span className="font-mono">${analysis.levels.support_2}</span>
                </div>
                <div className="flex items-center justify-between py-2 border-b border-slate-700 bg-slate-700/30 -mx-2 px-2">
                  <span className="text-cyan-400 font-medium">Current Price</span>
                  <span className="font-mono font-bold text-cyan-400">${analysis.current_price}</span>
                </div>
                <div className="flex items-center justify-between py-2 border-b border-slate-700">
                  <span className="text-green-400 flex items-center gap-1">
                    <ArrowUpRight className="w-4 h-4" />
                    Resistance 1
                  </span>
                  <span className="font-mono font-bold">${analysis.levels.resistance_1}</span>
                </div>
                <div className="flex items-center justify-between py-2 border-b border-slate-700">
                  <span className="text-green-400 flex items-center gap-1">
                    <ArrowUpRight className="w-4 h-4" />
                    Resistance 2
                  </span>
                  <span className="font-mono">${analysis.levels.resistance_2}</span>
                </div>
                <div className="flex items-center justify-between py-2 text-amber-400">
                  <span className="flex items-center gap-1">
                    <Zap className="w-4 h-4" />
                    All-Time High
                  </span>
                  <span className="font-mono font-bold">${analysis.levels.all_time_high}</span>
                </div>
                <div className="text-xs text-slate-500 text-center mt-2">
                  {analysis.levels.distance_to_ath_percent.toFixed(0)}% below ATH
                </div>
              </div>
            </div>

            {/* Risk Assessment */}
            <div className="card p-6">
              <h3 className="font-medium mb-4 flex items-center gap-2">
                <Shield className="w-5 h-5 text-amber-400" />
                Risk Assessment
              </h3>
              
              <div className={cn(
                "text-2xl font-bold mb-4 capitalize",
                getRiskColor(analysis.risk.level)
              )}>
                {analysis.risk.level} Risk
              </div>
              
              {analysis.risk.factors.length > 0 && (
                <div className="space-y-2 mb-4">
                  {analysis.risk.factors.map((factor, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                      <span className="text-slate-400">{factor}</span>
                    </div>
                  ))}
                </div>
              )}
              
              {/* Regulatory Risk */}
              <div className={cn(
                "p-4 rounded-lg border",
                analysis.risk.regulatory.risk === 'elevated' 
                  ? "bg-orange-500/10 border-orange-500/30"
                  : "bg-green-500/10 border-green-500/30"
              )}>
                <div className="font-medium mb-1">
                  Regulatory: <span className={cn(
                    "capitalize",
                    analysis.risk.regulatory.risk === 'elevated' ? 'text-orange-400' : 'text-green-400'
                  )}>
                    {analysis.risk.regulatory.risk}
                  </span>
                </div>
                <p className="text-xs text-slate-400">{analysis.risk.regulatory.details}</p>
                <p className="text-xs text-slate-500 mt-1">{analysis.risk.regulatory.recommendation}</p>
              </div>
            </div>
          </div>

          {/* Position Near Levels Alert */}
          {(analysis.position_near.support || analysis.position_near.resistance) && (
            <div className={cn(
              "p-4 rounded-lg border flex items-center gap-4",
              analysis.position_near.support 
                ? "bg-green-500/10 border-green-500/30"
                : "bg-red-500/10 border-red-500/30"
            )}>
              <Zap className={cn(
                "w-6 h-6",
                analysis.position_near.support ? "text-green-400" : "text-red-400"
              )} />
              <div>
                <div className="font-bold">
                  {analysis.position_near.support 
                    ? `Near Support: ${analysis.position_near.support_level}`
                    : `Near Resistance: ${analysis.position_near.resistance_level}`
                  }
                </div>
                <p className="text-sm text-slate-400">
                  {analysis.position_near.support 
                    ? "Potential buying opportunity - watch for bounce confirmation"
                    : "Potential selling pressure - watch for rejection"
                  }
                </p>
              </div>
            </div>
          )}

          {/* Crypto Comparison */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">XRP vs ADA Comparison</h2>
            </div>
            <div className="card-body">
              <div className="grid grid-cols-2 gap-6">
                {/* XRP */}
                <div className="p-4 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-2xl">💧</span>
                    <div>
                      <div className="font-bold text-blue-400">XRP (Ripple)</div>
                      <div className="text-xs text-slate-400">Cross-border payments</div>
                    </div>
                  </div>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Key Support</span>
                      <span className="font-mono">$2.00</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Key Resistance</span>
                      <span className="font-mono">$3.00</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">ATH</span>
                      <span className="font-mono">$3.84</span>
                    </div>
                    <div className="flex justify-between text-orange-400">
                      <span>Regulatory</span>
                      <span>⚠️ Elevated</span>
                    </div>
                  </div>
                </div>
                
                {/* ADA */}
                <div className="p-4 bg-cyan-500/10 border border-cyan-500/30 rounded-lg">
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-2xl">🔷</span>
                    <div>
                      <div className="font-bold text-cyan-400">ADA (Cardano)</div>
                      <div className="text-xs text-slate-400">Smart contracts, DeFi</div>
                    </div>
                  </div>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Key Support</span>
                      <span className="font-mono">$0.80</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Key Resistance</span>
                      <span className="font-mono">$1.20</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">ATH</span>
                      <span className="font-mono">$3.10</span>
                    </div>
                    <div className="flex justify-between text-green-400">
                      <span>Regulatory</span>
                      <span>✓ Normal</span>
                    </div>
                  </div>
                </div>
              </div>
              
              <div className="mt-4 p-3 bg-slate-700/50 rounded-lg text-sm text-slate-400">
                <strong className="text-slate-300">Trading Note:</strong> Crypto is more volatile than forex. 
                Position sizes are automatically reduced by 33-45% compared to standard forex trades. 
                Both XRP and ADA trade 24/7 with no session restrictions.
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
