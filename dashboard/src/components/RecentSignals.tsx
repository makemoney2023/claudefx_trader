'use client'

import { useEffect, useState, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { WebSocketMessage } from '@/lib/wsTypes'
import { ArrowUpRight, ArrowDownRight, Minus, AlertTriangle, RefreshCw } from 'lucide-react'

interface Signal {
  id: string
  timestamp: string
  symbol: string
  direction: 'long' | 'short' | 'no_trade'
  confidence: number
  reasoning: string
  market_structure: string
  entry_price?: number
  stop_loss?: number
  take_profit?: number
  risk_reward?: number
}

export function RecentSignals() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

  const { lastMessage, isConnected } = useWebSocket('analysis')

  const fetchSignals = useCallback(async () => {
    try {
      setError(null)
      const response = await fetch(`${apiBase}/api/analysis/signals?limit=10`)
      if (!response.ok) {
        throw new Error(`Failed to fetch signals: ${response.status}`)
      }
      const data = await response.json()
      setSignals(data)
    } catch (err) {
      console.error('Error fetching signals:', err)
      setError('Could not fetch signals')
    } finally {
      setLoading(false)
    }
  }, [apiBase])

  useEffect(() => {
    if (lastMessage && lastMessage.type === 'analysis_update') {
      fetchSignals()
    }
  }, [lastMessage, fetchSignals])

  useEffect(() => {
    fetchSignals()
    const interval = setInterval(fetchSignals, isConnected ? 120000 : 30000)
    return () => clearInterval(interval)
  }, [fetchSignals, isConnected])

  const getDirectionIcon = (direction: string) => {
    switch (direction) {
      case 'long':
        return <ArrowUpRight className="w-4 h-4 text-green-400" />
      case 'short':
        return <ArrowDownRight className="w-4 h-4 text-red-400" />
      default:
        return <Minus className="w-4 h-4 text-slate-400" />
    }
  }

  const getDirectionColor = (direction: string) => {
    switch (direction) {
      case 'long':
        return 'text-green-400'
      case 'short':
        return 'text-red-400'
      default:
        return 'text-slate-400'
    }
  }

  return (
    <div className="card h-full">
      <div className="card-header flex items-center justify-between">
        <h2 className="font-semibold">Recent Signals</h2>
        <button
          onClick={fetchSignals}
          className="p-1 hover:bg-slate-700 rounded transition-colors"
          title="Refresh signals"
        >
          <RefreshCw className="w-4 h-4 text-slate-400" />
        </button>
      </div>
      <div className="card-body p-0">
        {loading ? (
          <div className="p-8 text-center text-slate-400">Loading...</div>
        ) : error ? (
          <div className="p-8 text-center text-slate-400">
            <AlertTriangle className="w-6 h-6 mx-auto mb-2 text-yellow-500" />
            <p>{error}</p>
            <button
              onClick={fetchSignals}
              className="mt-2 text-sm text-blue-400 hover:text-blue-300"
            >
              Retry
            </button>
          </div>
        ) : signals.length === 0 ? (
          <div className="p-8 text-center text-slate-400">
            <p>No signals yet</p>
            <p className="text-sm mt-2">
              Signals appear when the trading bot analyzes charts
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-700">
            {signals.map((signal) => (
              <div key={signal.id} className="p-4 hover:bg-slate-700/30 transition-colors">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{signal.symbol}</span>
                    {getDirectionIcon(signal.direction)}
                    <span className={cn('text-sm font-medium', getDirectionColor(signal.direction))}>
                      {signal.direction === 'no_trade' ? 'No Trade' : signal.direction.toUpperCase()}
                    </span>
                  </div>
                  <div className={cn(
                    'px-2 py-0.5 text-xs rounded-full',
                    signal.confidence >= 0.7
                      ? 'bg-green-500/20 text-green-400'
                      : signal.confidence >= 0.5
                        ? 'bg-yellow-500/20 text-yellow-400'
                        : 'bg-slate-500/20 text-slate-400'
                  )}>
                    {(signal.confidence * 100).toFixed(0)}%
                  </div>
                </div>
                <p className="text-sm text-slate-400 line-clamp-2">{signal.reasoning || 'No reasoning provided'}</p>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs text-slate-500">
                    {new Date(signal.timestamp).toLocaleTimeString()}
                  </span>
                  <div className="flex items-center gap-3">
                    {signal.risk_reward && (
                      <span className="text-xs text-slate-400">
                        R:R {signal.risk_reward.toFixed(1)}
                      </span>
                    )}
                    <span className={cn(
                      'text-xs',
                      signal.market_structure === 'bullish' && 'text-green-400',
                      signal.market_structure === 'bearish' && 'text-red-400',
                      signal.market_structure === 'ranging' && 'text-slate-400'
                    )}>
                      {signal.market_structure}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
