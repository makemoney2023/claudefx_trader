'use client'

import { useEffect, useState } from 'react'
import { Shield, AlertTriangle, XCircle, Activity } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { EdgeTrackerResponse } from '@/lib/api'

const STATUS_CONFIG = {
  healthy: { color: 'text-green-400', bg: 'bg-green-500/10', icon: Shield, label: 'Healthy' },
  warning: { color: 'text-yellow-400', bg: 'bg-yellow-500/10', icon: AlertTriangle, label: 'Warning' },
  critical: { color: 'text-red-400', bg: 'bg-red-500/10', icon: XCircle, label: 'Critical' },
  blocked: { color: 'text-red-500', bg: 'bg-red-500/20', icon: XCircle, label: 'Blocked' },
} as const

export function EdgeHealthCard() {
  const [data, setData] = useState<EdgeTrackerResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.getEdgeTracker(50)
        setData(res)
      } catch {
        // silent
      } finally {
        setLoading(false)
      }
    }
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [])

  if (loading || !data) {
    return (
      <div className="card">
        <div className="card-body">
          <p className="stat-label">Edge Health</p>
          <p className="text-sm text-slate-400 mt-2">Loading...</p>
        </div>
      </div>
    )
  }

  const cfg = STATUS_CONFIG[data.overall_status as keyof typeof STATUS_CONFIG] || STATUS_CONFIG.warning
  const Icon = cfg.icon

  return (
    <div className="card">
      <div className="card-body space-y-3">
        <div className="flex items-center justify-between">
          <p className="stat-label">Edge Health</p>
          <div className={cn('flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full', cfg.bg, cfg.color)}>
            <Icon className="w-3 h-3" />
            {cfg.label}
          </div>
        </div>

        <div className="flex items-end gap-3">
          <p className={cn('text-3xl font-bold tabular-nums', cfg.color)}>
            {data.overall_score.toFixed(0)}
          </p>
          <span className="text-xs text-slate-400 mb-1">/ 100</span>
        </div>

        <div className="flex gap-4 text-xs text-slate-400">
          <span>WR {(data.rolling_win_rate * 100).toFixed(0)}%</span>
          <span>Avg R {data.rolling_avg_r.toFixed(2)}</span>
          <span>{data.rolling_trades} trades</span>
        </div>

        {/* Mini sparkline */}
        {data.recent_wr_trend.length > 1 && (
          <div className="flex items-end gap-0.5 h-6">
            {data.recent_wr_trend.map((wr, i) => (
              <div
                key={i}
                className={cn(
                  'w-3 rounded-t',
                  wr >= 0.55 ? 'bg-green-500/60' : wr >= 0.45 ? 'bg-yellow-500/60' : 'bg-red-500/60',
                )}
                style={{ height: `${Math.max(wr * 100, 8)}%` }}
              />
            ))}
          </div>
        )}

        {/* Per-symbol dots */}
        {data.symbols.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-1 border-t border-slate-700">
            {data.symbols.map((s) => {
              const sc = STATUS_CONFIG[s.status as keyof typeof STATUS_CONFIG] || STATUS_CONFIG.warning
              return (
                <div key={s.symbol} className="flex items-center gap-1 text-xs">
                  <div className={cn('w-2 h-2 rounded-full', sc.color === 'text-green-400' ? 'bg-green-400' : sc.color === 'text-yellow-400' ? 'bg-yellow-400' : 'bg-red-400')} />
                  <span className="text-slate-300">{s.symbol}</span>
                  <span className={cn('font-mono', sc.color)}>{s.score.toFixed(0)}</span>
                </div>
              )
            })}
          </div>
        )}

        {/* Alerts */}
        {data.alerts.length > 0 && (
          <div className="space-y-1 pt-1">
            {data.alerts.slice(0, 3).map((a, i) => (
              <div
                key={i}
                className={cn(
                  'text-xs px-2 py-1 rounded',
                  a.level === 'critical' ? 'bg-red-500/10 text-red-400' :
                  a.level === 'warning' ? 'bg-yellow-500/10 text-yellow-400' :
                  'bg-blue-500/10 text-blue-400'
                )}
              >
                {a.message}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
