'use client'

import { useEffect, useState } from 'react'
import { api, PerformanceStats, ICTConceptStats, SymbolStats, EdgeTrackerResponse } from '@/lib/api'
import { cn } from '@/lib/utils'
import { TrendingUp, TrendingDown, Target, Award, Shield } from 'lucide-react'

export default function PerformancePage() {
  const [stats, setStats] = useState<PerformanceStats | null>(null)
  const [ictStats, setIctStats] = useState<ICTConceptStats[]>([])
  const [symbolStats, setSymbolStats] = useState<SymbolStats[]>([])
  const [edge, setEdge] = useState<EdgeTrackerResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState<number | undefined>(undefined)

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true)
      try {
        const [statsData, ictData, symbolData, edgeData] = await Promise.all([
          api.getPerformanceStats(period),
          api.getICTConceptStats(),
          api.getPerformanceBySymbol(),
          api.getEdgeTracker(50).catch(() => null),
        ])
        setStats(statsData)
        setIctStats(ictData)
        setSymbolStats(symbolData)
        if (edgeData) setEdge(edgeData)
      } catch (error) {
        console.error('Error fetching performance data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [period])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Performance Analytics</h1>
        <div className="flex gap-2 p-1 bg-slate-800 rounded-lg">
          {[
            { label: 'All Time', value: undefined },
            { label: '30 Days', value: 30 },
            { label: '7 Days', value: 7 },
          ].map(({ label, value }) => (
            <button
              key={label}
              onClick={() => setPeriod(value)}
              className={cn(
                'px-4 py-2 text-sm font-medium rounded-md transition-colors',
                period === value
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-slate-700'
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card">
          <div className="card-body">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-blue-500/20 rounded-lg">
                <Target className="w-5 h-5 text-blue-400" />
              </div>
              <span className="text-slate-400">Total Trades</span>
            </div>
            <p className="text-3xl font-bold">{stats?.total_trades || 0}</p>
            <p className="text-sm text-slate-400 mt-1">
              {stats?.wins || 0}W / {stats?.losses || 0}L
            </p>
          </div>
        </div>

        <div className="card">
          <div className="card-body">
            <div className="flex items-center gap-3 mb-2">
              <div className={cn(
                'p-2 rounded-lg',
                (stats?.win_rate || 0) >= 0.5 ? 'bg-green-500/20' : 'bg-red-500/20'
              )}>
                {(stats?.win_rate || 0) >= 0.5 ? (
                  <TrendingUp className="w-5 h-5 text-green-400" />
                ) : (
                  <TrendingDown className="w-5 h-5 text-red-400" />
                )}
              </div>
              <span className="text-slate-400">Win Rate</span>
            </div>
            <p className={cn(
              'text-3xl font-bold',
              (stats?.win_rate || 0) >= 0.5 ? 'text-green-400' : 'text-red-400'
            )}>
              {((stats?.win_rate || 0) * 100).toFixed(1)}%
            </p>
          </div>
        </div>

        <div className="card">
          <div className="card-body">
            <div className="flex items-center gap-3 mb-2">
              <div className={cn(
                'p-2 rounded-lg',
                (stats?.total_profit || 0) >= 0 ? 'bg-green-500/20' : 'bg-red-500/20'
              )}>
                {(stats?.total_profit || 0) >= 0 ? (
                  <TrendingUp className="w-5 h-5 text-green-400" />
                ) : (
                  <TrendingDown className="w-5 h-5 text-red-400" />
                )}
              </div>
              <span className="text-slate-400">Total P/L</span>
            </div>
            <p className={cn(
              'text-3xl font-bold',
              (stats?.total_profit || 0) >= 0 ? 'text-green-400' : 'text-red-400'
            )}>
              ${(stats?.total_profit || 0).toFixed(2)}
            </p>
          </div>
        </div>

        <div className="card">
          <div className="card-body">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-purple-500/20 rounded-lg">
                <Award className="w-5 h-5 text-purple-400" />
              </div>
              <span className="text-slate-400">Total R</span>
            </div>
            <p className={cn(
              'text-3xl font-bold',
              (stats?.total_r || 0) >= 0 ? 'text-green-400' : 'text-red-400'
            )}>
              {(stats?.total_r || 0).toFixed(2)}R
            </p>
            <p className="text-sm text-slate-400 mt-1">
              Avg: {(stats?.avg_r || 0).toFixed(2)}R
            </p>
          </div>
        </div>
      </div>

      {/* Detailed Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Advanced Metrics */}
        <div className="card">
          <div className="card-header">
            <h2 className="font-semibold">Advanced Metrics</h2>
          </div>
          <div className="card-body space-y-4">
            <div className="flex justify-between">
              <span className="text-slate-400">Profit Factor</span>
              <span className={cn(
                'font-medium',
                (stats?.profit_factor || 0) >= 1 ? 'text-green-400' : 'text-red-400'
              )}>
                {(stats?.profit_factor || 0).toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Avg Win</span>
              <span className="font-medium text-green-400">
                ${(stats?.avg_win || 0).toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Avg Loss</span>
              <span className="font-medium text-red-400">
                ${(stats?.avg_loss || 0).toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Largest Win</span>
              <span className="font-medium text-green-400">
                ${(stats?.largest_win || 0).toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Largest Loss</span>
              <span className="font-medium text-red-400">
                ${(stats?.largest_loss || 0).toFixed(2)}
              </span>
            </div>
          </div>
        </div>

        {/* ICT Concept Performance */}
        <div className="card">
          <div className="card-header">
            <h2 className="font-semibold">ICT Concept Performance</h2>
          </div>
          <div className="card-body p-0">
            {ictStats.length === 0 ? (
              <div className="p-4 text-center text-slate-400">No data available</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                  <tr>
                    <th className="px-4 py-2 text-left">Concept</th>
                    <th className="px-4 py-2 text-right">Trades</th>
                    <th className="px-4 py-2 text-right">Win %</th>
                    <th className="px-4 py-2 text-right">Avg R</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {ictStats.map((stat) => (
                    <tr key={stat.concept}>
                      <td className="px-4 py-2 capitalize">{stat.concept.replace('_', ' ')}</td>
                      <td className="px-4 py-2 text-right">{stat.trades}</td>
                      <td className={cn(
                        'px-4 py-2 text-right',
                        stat.win_rate >= 0.5 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {(stat.win_rate * 100).toFixed(0)}%
                      </td>
                      <td className={cn(
                        'px-4 py-2 text-right',
                        stat.avg_r >= 0 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {stat.avg_r.toFixed(2)}R
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Performance by Symbol */}
        <div className="card">
          <div className="card-header">
            <h2 className="font-semibold">Performance by Symbol</h2>
          </div>
          <div className="card-body p-0">
            {symbolStats.length === 0 ? (
              <div className="p-4 text-center text-slate-400">No data available</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                  <tr>
                    <th className="px-4 py-2 text-left">Symbol</th>
                    <th className="px-4 py-2 text-right">Trades</th>
                    <th className="px-4 py-2 text-right">Win %</th>
                    <th className="px-4 py-2 text-right">P/L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {symbolStats.map((stat) => (
                    <tr key={stat.symbol}>
                      <td className="px-4 py-2 font-medium">{stat.symbol}</td>
                      <td className="px-4 py-2 text-right">{stat.trades}</td>
                      <td className={cn(
                        'px-4 py-2 text-right',
                        stat.win_rate >= 0.5 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {(stat.win_rate * 100).toFixed(0)}%
                      </td>
                      <td className={cn(
                        'px-4 py-2 text-right font-medium',
                        stat.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                      )}>
                        ${stat.total_pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* Edge Tracker Section */}
      {edge && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Shield className="h-5 w-5 text-blue-400" />
            <h2 className="text-lg font-bold">Edge Tracker</h2>
            <span className="text-xs text-slate-400 ml-2">{edge.window_label}</span>
          </div>

          {/* Overall stats */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
            <div>
              <p className="text-xs text-slate-400">Score</p>
              <p className={cn(
                'text-2xl font-bold tabular-nums',
                edge.overall_score >= 60 ? 'text-green-400' :
                edge.overall_score >= 40 ? 'text-yellow-400' : 'text-red-400'
              )}>
                {edge.overall_score.toFixed(0)}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Win Rate</p>
              <p className="text-2xl font-bold tabular-nums">{(edge.rolling_win_rate * 100).toFixed(1)}%</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Avg R</p>
              <p className="text-2xl font-bold tabular-nums">{edge.rolling_avg_r.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Total R</p>
              <p className={cn(
                'text-2xl font-bold tabular-nums',
                edge.rolling_total_r >= 0 ? 'text-green-400' : 'text-red-400'
              )}>
                {edge.rolling_total_r >= 0 ? '+' : ''}{edge.rolling_total_r.toFixed(1)}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Trades</p>
              <p className="text-2xl font-bold tabular-nums">{edge.rolling_trades}</p>
            </div>
          </div>

          {/* WR Trend bars */}
          {edge.recent_wr_trend.length > 1 && (
            <div className="mb-6">
              <p className="text-xs text-slate-400 mb-2">Win Rate Trend (newest left, batches of 10)</p>
              <div className="flex items-end gap-1 h-16">
                {edge.recent_wr_trend.map((wr, i) => (
                  <div key={i} className="flex flex-col items-center flex-1 gap-0.5">
                    <span className="text-[10px] text-slate-400">{(wr * 100).toFixed(0)}%</span>
                    <div
                      className={cn(
                        'w-full rounded-t',
                        wr >= 0.55 ? 'bg-green-500/70' : wr >= 0.45 ? 'bg-yellow-500/70' : 'bg-red-500/70',
                      )}
                      style={{ height: `${Math.max(wr * 100, 5)}%` }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Per-symbol breakdown table */}
          {edge.symbols.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-slate-400 mb-2">Per-Symbol Breakdown</p>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-slate-400">
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-right">Trades</th>
                    <th className="px-3 py-2 text-right">Win %</th>
                    <th className="px-3 py-2 text-right">Avg R</th>
                    <th className="px-3 py-2 text-right">Total R</th>
                    <th className="px-3 py-2 text-right">Score</th>
                    <th className="px-3 py-2 text-right">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {edge.symbols.map((s) => (
                    <tr key={s.symbol}>
                      <td className="px-3 py-2 font-medium">{s.symbol}</td>
                      <td className="px-3 py-2 text-right">{s.trades}</td>
                      <td className={cn(
                        'px-3 py-2 text-right',
                        s.win_rate >= 0.55 ? 'text-green-400' : s.win_rate >= 0.45 ? 'text-yellow-400' : 'text-red-400'
                      )}>
                        {(s.win_rate * 100).toFixed(0)}%
                      </td>
                      <td className="px-3 py-2 text-right">{s.avg_r.toFixed(2)}</td>
                      <td className={cn(
                        'px-3 py-2 text-right',
                        s.total_r >= 0 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {s.total_r >= 0 ? '+' : ''}{s.total_r.toFixed(1)}
                      </td>
                      <td className={cn(
                        'px-3 py-2 text-right font-bold',
                        s.score >= 60 ? 'text-green-400' : s.score >= 40 ? 'text-yellow-400' : 'text-red-400'
                      )}>
                        {s.score.toFixed(0)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <span className={cn(
                          'text-xs px-2 py-0.5 rounded-full',
                          s.status === 'healthy' ? 'bg-green-500/10 text-green-400' :
                          s.status === 'warning' ? 'bg-yellow-500/10 text-yellow-400' :
                          'bg-red-500/10 text-red-400'
                        )}>
                          {s.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Alerts */}
          {edge.alerts.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-slate-400 mb-1">Active Alerts</p>
              {edge.alerts.map((a, i) => (
                <div
                  key={i}
                  className={cn(
                    'text-sm px-3 py-2 rounded',
                    a.level === 'critical' ? 'bg-red-500/10 text-red-400' :
                    a.level === 'warning' ? 'bg-yellow-500/10 text-yellow-400' :
                    'bg-blue-500/10 text-blue-400'
                  )}
                >
                  {a.symbol && <span className="font-bold mr-1">[{a.symbol}]</span>}
                  {a.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
