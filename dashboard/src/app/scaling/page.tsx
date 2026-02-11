'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Scale, TrendingUp, Target, AlertTriangle, CheckCircle } from 'lucide-react'

interface ScalingStatus {
  current_mode: string
  mode_description: string
  risk_multiplier: number
  confidence_threshold: number
  setup_filter: string
  max_daily_trades: number
  daily_drawdown: number
  weekly_drawdown: number
  goal_progress: number
  recent_performance: {
    win_rate: number
    avg_r: number
    current_streak: string
    trades_count: number
  }
  daily_pnl: number
  weekly_pnl: number
}

interface ScalingTier {
  current_tier: string
  progress_percent: number
  base_lots: number
  max_lots: number
  risk_percent: number
  max_exposure_percent: number
  next_tier?: {
    name: string
    equity_needed: number
  }
}

interface ScalingTierInfo {
  name: string
  base_lots: number
  max_lots: number
  risk_percent: number
}

export default function ScalingPage() {
  const [status, setStatus] = useState<ScalingStatus | null>(null)
  const [tier, setTier] = useState<ScalingTier | null>(null)
  const [allTiers, setAllTiers] = useState<ScalingTierInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchData() {
      try {
        // Try to get scaling status with default equity
        const statusData = await api.getScalingStatus(10000).catch(() => ({
          current_mode: 'normal',
          mode_description: 'Standard trading mode',
          risk_multiplier: 1.0,
          confidence_threshold: 0.7,
          setup_filter: 'grade_b',
          max_daily_trades: 5,
          daily_drawdown: 0,
          weekly_drawdown: 0,
          goal_progress: 10,
          recent_performance: { win_rate: 50, avg_r: 0, current_streak: 'None', trades_count: 0 },
          daily_pnl: 0,
          weekly_pnl: 0
        }))
        
        const tierData = await api.getScalingTier(10000).catch(() => ({
          current_tier: '$1,000-$2,500',
          progress_percent: 0,
          base_lots: 0.01,
          max_lots: 0.02,
          risk_percent: 5,
          max_exposure_percent: 10,
          next_tier: { name: '$2,500-$5,000', equity_needed: 2500 }
        }))
        
        const tiersData = await api.getScalingTiers().catch(() => ({
          tiers: [
            { name: '$1,000-$2,500', base_lots: 0.01, max_lots: 0.02, risk_percent: 5 },
            { name: '$2,500-$5,000', base_lots: 0.02, max_lots: 0.05, risk_percent: 4 },
            { name: '$5,000-$10,000', base_lots: 0.05, max_lots: 0.10, risk_percent: 3 },
            { name: '$10,000-$25,000', base_lots: 0.10, max_lots: 0.25, risk_percent: 2.5 },
            { name: '$25,000-$50,000', base_lots: 0.25, max_lots: 0.50, risk_percent: 2 },
            { name: '$50,000-$100,000', base_lots: 0.50, max_lots: 1.00, risk_percent: 1.5 },
            { name: '$100,000+', base_lots: 1.00, max_lots: 2.00, risk_percent: 1 }
          ]
        }))
        
        setStatus(statusData as ScalingStatus)
        setTier(tierData as ScalingTier)
        setAllTiers(tiersData.tiers)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load scaling data')
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-500/10 border border-red-500 rounded-lg p-4 text-red-500">
          {error}
        </div>
      </div>
    )
  }

  const getModeColor = (mode: string) => {
    switch (mode) {
      case 'aggressive': return 'bg-green-500'
      case 'normal': return 'bg-blue-500'
      case 'conservative': return 'bg-yellow-500'
      case 'defensive': return 'bg-red-500'
      default: return 'bg-gray-500'
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Scaling & Position Sizing</h1>
          <p className="text-slate-400">Dynamic risk management for $1K → $100K journey</p>
        </div>
        <span className={`${getModeColor(status?.current_mode || '')} text-white px-4 py-2 rounded-lg font-medium`}>
          {status?.current_mode?.toUpperCase()} MODE
        </span>
      </div>

      {/* Current Mode Card */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg">
        <div className="p-4 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <Scale className="h-5 w-5 text-blue-500" />
            <h2 className="font-semibold">Current Trading Mode</h2>
          </div>
          <p className="text-sm text-slate-400 mt-1">{status?.mode_description}</p>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-slate-700/50 rounded-lg p-4">
              <p className="text-sm text-slate-400">Risk Multiplier</p>
              <p className="text-2xl font-bold">{status?.risk_multiplier}x</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4">
              <p className="text-sm text-slate-400">Confidence Threshold</p>
              <p className="text-2xl font-bold">{((status?.confidence_threshold || 0) * 100).toFixed(0)}%</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4">
              <p className="text-sm text-slate-400">Setup Filter</p>
              <p className="text-2xl font-bold">{status?.setup_filter?.replace('_', ' ')}</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4">
              <p className="text-sm text-slate-400">Max Daily Trades</p>
              <p className="text-2xl font-bold">{status?.max_daily_trades}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Current Tier */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg">
        <div className="p-4 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <Target className="h-5 w-5 text-green-500" />
            <h2 className="font-semibold">Current Scaling Tier</h2>
          </div>
          <p className="text-sm text-slate-400 mt-1">Progress within {tier?.current_tier}</p>
        </div>
        <div className="p-4 space-y-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-slate-400">Tier Progress</span>
            <span className="text-sm font-medium">{tier?.progress_percent?.toFixed(1)}%</span>
          </div>
          <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
            <div 
              className="h-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all"
              style={{ width: `${tier?.progress_percent || 0}%` }}
            />
          </div>
          
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mt-4">
            <div className="bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-400">Base Lots</p>
              <p className="text-lg font-bold text-blue-400">{tier?.base_lots}</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-400">Max Lots</p>
              <p className="text-lg font-bold text-green-400">{tier?.max_lots}</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-400">Risk %</p>
              <p className="text-lg font-bold text-yellow-400">{tier?.risk_percent}%</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-400">Max Exposure</p>
              <p className="text-lg font-bold text-purple-400">{tier?.max_exposure_percent}%</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-xs text-slate-400">To Next Tier</p>
              <p className="text-lg font-bold text-orange-400">
                ${tier?.next_tier?.equity_needed?.toLocaleString() || '—'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Drawdown Status */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-800 border border-slate-700 rounded-lg">
          <div className="p-4 border-b border-slate-700">
            <div className="flex items-center gap-2">
              {(status?.daily_drawdown || 0) > 3 ? (
                <AlertTriangle className="h-5 w-5 text-yellow-500" />
              ) : (
                <CheckCircle className="h-5 w-5 text-green-500" />
              )}
              <h2 className="font-semibold">Daily Drawdown</h2>
            </div>
          </div>
          <div className="p-4">
            <div className="flex items-end gap-2">
              <p className="text-4xl font-bold">{status?.daily_drawdown?.toFixed(2)}%</p>
              <p className="text-slate-400 mb-1">of 5% limit</p>
            </div>
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden mt-2">
              <div 
                className={`h-full ${(status?.daily_drawdown || 0) > 3 ? 'bg-yellow-500' : 'bg-green-500'}`}
                style={{ width: `${Math.min((status?.daily_drawdown || 0) / 5 * 100, 100)}%` }}
              />
            </div>
          </div>
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-lg">
          <div className="p-4 border-b border-slate-700">
            <div className="flex items-center gap-2">
              {(status?.weekly_drawdown || 0) > 7 ? (
                <AlertTriangle className="h-5 w-5 text-yellow-500" />
              ) : (
                <CheckCircle className="h-5 w-5 text-green-500" />
              )}
              <h2 className="font-semibold">Weekly Drawdown</h2>
            </div>
          </div>
          <div className="p-4">
            <div className="flex items-end gap-2">
              <p className="text-4xl font-bold">{status?.weekly_drawdown?.toFixed(2)}%</p>
              <p className="text-slate-400 mb-1">of 10% limit</p>
            </div>
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden mt-2">
              <div 
                className={`h-full ${(status?.weekly_drawdown || 0) > 7 ? 'bg-yellow-500' : 'bg-green-500'}`}
                style={{ width: `${Math.min((status?.weekly_drawdown || 0) / 10 * 100, 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* All Tiers */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg">
        <div className="p-4 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-purple-500" />
            <h2 className="font-semibold">Scaling Tiers Overview</h2>
          </div>
          <p className="text-sm text-slate-400 mt-1">Your journey from $1K to $100K+</p>
        </div>
        <div className="p-4 space-y-2">
          {allTiers.map((t, i) => {
            const isCurrentTier = t.name === tier?.current_tier
            return (
              <div 
                key={i} 
                className={`flex items-center justify-between p-3 rounded-lg ${
                  isCurrentTier ? 'bg-blue-500/20 border border-blue-500' : 'bg-slate-700/30'
                }`}
              >
                <div className="flex items-center gap-3">
                  {isCurrentTier && (
                    <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                  )}
                  <span className="font-medium">{t.name}</span>
                </div>
                <div className="flex items-center gap-6 text-sm">
                  <span className="text-slate-400">
                    Base: <span className="text-white">{t.base_lots} lots</span>
                  </span>
                  <span className="text-slate-400">
                    Max: <span className="text-white">{t.max_lots} lots</span>
                  </span>
                  <span className="text-slate-400">
                    Risk: <span className="text-white">{t.risk_percent}%</span>
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Recent Performance */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg">
        <div className="p-4 border-b border-slate-700">
          <h2 className="font-semibold">Recent Performance (Last 20 Trades)</h2>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-slate-700/50 rounded-lg p-4 text-center">
              <p className="text-sm text-slate-400">Win Rate</p>
              <p className="text-2xl font-bold">
                {status?.recent_performance?.win_rate?.toFixed(0) || 0}%
              </p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4 text-center">
              <p className="text-sm text-slate-400">Avg R</p>
              <p className="text-2xl font-bold">
                {status?.recent_performance?.avg_r?.toFixed(2) || 0}
              </p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4 text-center">
              <p className="text-sm text-slate-400">Current Streak</p>
              <p className="text-2xl font-bold">
                {status?.recent_performance?.current_streak || 'None'}
              </p>
            </div>
            <div className="bg-slate-700/50 rounded-lg p-4 text-center">
              <p className="text-sm text-slate-400">Trades</p>
              <p className="text-2xl font-bold">
                {status?.recent_performance?.trades_count || 0}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
