'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Clock, TrendingUp, TrendingDown, Sun, Moon, Sunrise } from 'lucide-react'

interface SessionStats {
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_pnl: number
  avg_r: number
  best_trade_r: number
  worst_trade_r: number
}

interface SessionSummary {
  total_trades: number
  total_pnl: number
  sessions: Record<string, SessionStats>
  best_session: string | null
  worst_session: string | null
  recommendations: string[]
}

interface CurrentSessionResponse {
  session: string
  is_overlap: boolean
  time_in_session: number
}

export default function SessionsPage() {
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [currentSession, setCurrentSession] = useState<CurrentSessionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchData() {
      try {
        const [currentData] = await Promise.all([
          api.getCurrentSession().catch(() => ({ session: 'off_hours', is_overlap: false, time_in_session: 0 }))
        ])
        
        // Try to get session stats
        try {
          const statsData = await api.getSessionStats()
          setSummary({
            total_trades: Object.values(statsData).reduce((sum: number, s: any) => sum + (s.total_trades || 0), 0),
            total_pnl: Object.values(statsData).reduce((sum: number, s: any) => sum + (s.total_pnl || 0), 0),
            sessions: statsData,
            best_session: null,
            worst_session: null,
            recommendations: []
          })
        } catch {
          // Use mock data if API fails
          setSummary({
            total_trades: 0,
            total_pnl: 0,
            sessions: {
              asian: { total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_r: 0, best_trade_r: 0, worst_trade_r: 0 },
              london: { total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_r: 0, best_trade_r: 0, worst_trade_r: 0 },
              new_york: { total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_r: 0, best_trade_r: 0, worst_trade_r: 0 },
              london_ny_overlap: { total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_r: 0, best_trade_r: 0, worst_trade_r: 0 }
            },
            best_session: null,
            worst_session: null,
            recommendations: []
          })
        }
        
        setCurrentSession(currentData)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load session data')
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 60000)
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

  const getSessionIcon = (session: string) => {
    switch (session) {
      case 'asian': return <Moon className="h-5 w-5" />
      case 'london': return <Sunrise className="h-5 w-5" />
      case 'new_york': return <Sun className="h-5 w-5" />
      case 'london_ny_overlap': return <TrendingUp className="h-5 w-5" />
      default: return <Clock className="h-5 w-5" />
    }
  }

  const getSessionColor = (session: string) => {
    switch (session) {
      case 'asian': return 'text-purple-400 bg-purple-500/20'
      case 'london': return 'text-blue-400 bg-blue-500/20'
      case 'new_york': return 'text-green-400 bg-green-500/20'
      case 'london_ny_overlap': return 'text-yellow-400 bg-yellow-500/20'
      default: return 'text-slate-400 bg-slate-500/20'
    }
  }

  const formatSessionName = (name: string) => {
    return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
  }

  const sessions = summary?.sessions ? Object.entries(summary.sessions) : []

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Session Analytics</h1>
          <p className="text-slate-400">Track performance across trading sessions</p>
        </div>
        {currentSession && (
          <div className={`flex items-center gap-2 px-4 py-2 rounded-lg ${getSessionColor(currentSession.session)}`}>
            {getSessionIcon(currentSession.session)}
            <span className="font-medium">{formatSessionName(currentSession.session)}</span>
            {currentSession.is_overlap && (
              <span className="ml-2 px-2 py-0.5 text-xs border border-yellow-500 text-yellow-500 rounded">
                OVERLAP
              </span>
            )}
          </div>
        )}
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
          <p className="text-sm text-slate-400">Total Trades</p>
          <p className="text-3xl font-bold">{summary?.total_trades || 0}</p>
        </div>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
          <p className="text-sm text-slate-400">Total P/L</p>
          <p className={`text-3xl font-bold ${(summary?.total_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            ${summary?.total_pnl?.toFixed(2) || '0.00'}
          </p>
        </div>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
          <p className="text-sm text-slate-400">Best Session</p>
          <p className="text-3xl font-bold text-green-500">
            {summary?.best_session ? formatSessionName(summary.best_session) : '—'}
          </p>
        </div>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
          <p className="text-sm text-slate-400">Worst Session</p>
          <p className="text-3xl font-bold text-red-500">
            {summary?.worst_session ? formatSessionName(summary.worst_session) : '—'}
          </p>
        </div>
      </div>

      {/* Session Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {sessions.map(([sessionKey, stats]) => {
          const typedStats = stats as SessionStats
          const isBest = sessionKey === summary?.best_session
          const isWorst = sessionKey === summary?.worst_session
          
          return (
            <div 
              key={sessionKey} 
              className={`bg-slate-800 border border-slate-700 rounded-lg ${
                isBest ? 'ring-2 ring-green-500' : isWorst ? 'ring-2 ring-red-500' : ''
              }`}
            >
              <div className="p-4 border-b border-slate-700">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`p-2 rounded-lg ${getSessionColor(sessionKey)}`}>
                      {getSessionIcon(sessionKey)}
                    </div>
                    <span className="font-semibold">{formatSessionName(sessionKey)}</span>
                  </div>
                  <div className="flex gap-2">
                    {isBest && (
                      <span className="flex items-center gap-1 px-2 py-1 bg-green-500 text-white text-xs rounded">
                        <TrendingUp className="h-3 w-3" />
                        BEST
                      </span>
                    )}
                    {isWorst && typedStats.total_trades >= 5 && (
                      <span className="flex items-center gap-1 px-2 py-1 bg-red-500 text-white text-xs rounded">
                        <TrendingDown className="h-3 w-3" />
                        AVOID
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="p-4">
                <div className="grid grid-cols-4 gap-4 text-center">
                  <div>
                    <p className="text-xs text-slate-400">Trades</p>
                    <p className="text-xl font-bold">{typedStats.total_trades}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Win Rate</p>
                    <p className={`text-xl font-bold ${(typedStats.win_rate || 0) >= 50 ? 'text-green-500' : 'text-red-500'}`}>
                      {typedStats.win_rate?.toFixed(0) || 0}%
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Avg R</p>
                    <p className={`text-xl font-bold ${(typedStats.avg_r || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                      {typedStats.avg_r?.toFixed(2) || '0.00'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">P/L</p>
                    <p className={`text-xl font-bold ${(typedStats.total_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                      ${typedStats.total_pnl?.toFixed(0) || 0}
                    </p>
                  </div>
                </div>
                
                {typedStats.total_trades > 0 && (
                  <div className="mt-4 pt-4 border-t border-slate-700">
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">
                        Best: <span className="text-green-500">{typedStats.best_trade_r?.toFixed(1) || 0}R</span>
                      </span>
                      <span className="text-slate-400">
                        Worst: <span className="text-red-500">{typedStats.worst_trade_r?.toFixed(1) || 0}R</span>
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Recommendations */}
      {summary?.recommendations && summary.recommendations.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg">
          <div className="p-4 border-b border-slate-700">
            <h2 className="font-semibold">AI Recommendations</h2>
            <p className="text-sm text-slate-400">Based on your session performance data</p>
          </div>
          <div className="p-4">
            <ul className="space-y-2">
              {summary.recommendations.map((rec, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-lg">{rec.charAt(0)}</span>
                  <span className="text-slate-300">{rec.slice(2)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Session Schedule */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg">
        <div className="p-4 border-b border-slate-700">
          <h2 className="font-semibold">Session Schedule (UTC)</h2>
        </div>
        <div className="p-4 space-y-2">
          <div className="flex items-center justify-between p-3 rounded-lg bg-purple-500/10">
            <div className="flex items-center gap-3">
              <Moon className="h-5 w-5 text-purple-400" />
              <span>Asian Session</span>
            </div>
            <span className="text-slate-400">00:00 - 08:00 UTC</span>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-blue-500/10">
            <div className="flex items-center gap-3">
              <Sunrise className="h-5 w-5 text-blue-400" />
              <span>London Session</span>
            </div>
            <span className="text-slate-400">07:00 - 16:00 UTC</span>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-yellow-500/10">
            <div className="flex items-center gap-3">
              <TrendingUp className="h-5 w-5 text-yellow-400" />
              <span>London/NY Overlap (Kill Zone)</span>
            </div>
            <span className="text-slate-400">12:00 - 16:00 UTC</span>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-green-500/10">
            <div className="flex items-center gap-3">
              <Sun className="h-5 w-5 text-green-400" />
              <span>New York Session</span>
            </div>
            <span className="text-slate-400">12:00 - 21:00 UTC</span>
          </div>
        </div>
      </div>
    </div>
  )
}
