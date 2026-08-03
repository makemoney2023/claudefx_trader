'use client'

import { useCallback, useEffect, useState } from 'react'
import { Radar, RefreshCw, Flame, Plus, Trash2 } from 'lucide-react'
import { api, OpportunityHotEntry, OpportunityRow } from '@/lib/api'
import { cn } from '@/lib/utils'

export default function OpportunitiesPage() {
  const [results, setResults] = useState<OpportunityRow[]>([])
  const [hot, setHot] = useState<OpportunityHotEntry[]>([])
  const [enabled, setEnabled] = useState(false)
  const [lastScan, setLastScan] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const [ops, hotRes] = await Promise.all([
        api.getOpportunities(),
        api.getOpportunityHotList(),
      ])
      setResults(ops.results || [])
      setEnabled(ops.enabled)
      setLastScan(ops.last_scan_at)
      setHot(hotRes.hot || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load opportunities')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 20000)
    return () => clearInterval(id)
  }, [load])

  const forceScan = async () => {
    setScanning(true)
    setError(null)
    try {
      const started = await api.forceOpportunityScan()
      // Scan runs in the background (can take 30–90s). Poll until done.
      const startedAt = Date.now()
      while (Date.now() - startedAt < 120000) {
        await new Promise((r) => setTimeout(r, 3000))
        const ops = await api.getOpportunities()
        setResults(ops.results || [])
        setEnabled(ops.enabled)
        setLastScan(ops.last_scan_at)
        if (!ops.scanning) {
          const hotRes = await api.getOpportunityHotList()
          setHot(hotRes.hot || [])
          break
        }
      }
      if (started.status === 'already_running') {
        setError(null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setScanning(false)
      await load()
    }
  }

  const promote = async (symbol: string) => {
    try {
      await api.promoteOpportunity(symbol)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Promote failed')
    }
  }

  const removeHot = async (symbol: string) => {
    try {
      await api.removeOpportunityHot(symbol)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Remove failed')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Radar className="w-6 h-6 text-cyan-400" />
          <div>
            <h1 className="text-2xl font-bold">Opportunities</h1>
            <p className="text-sm text-slate-400">
              Mechanical scan (no Claude) ·{' '}
              {enabled ? (
                <span className="text-green-400">scanner enabled</span>
              ) : (
                <span className="text-amber-400">
                  scanner disabled — set TRADING_OPPORTUNITY_SCANNER_ENABLED=true
                </span>
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={forceScan}
            disabled={scanning}
            className="px-3 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 rounded-lg text-sm flex items-center gap-2"
          >
            <Radar className={cn('w-4 h-4', scanning && 'animate-spin')} />
            Scan now
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {lastScan && (
        <p className="text-xs text-slate-500">
          Last scan: {new Date(lastScan).toLocaleString()}
        </p>
      )}

      <div className="card">
        <div className="card-body">
          <div className="flex items-center gap-2 mb-4">
            <Flame className="w-4 h-4 text-orange-400" />
            <h2 className="font-semibold">Hot list</h2>
          </div>
          {hot.length === 0 ? (
            <p className="text-sm text-slate-500">No hot symbols promoted yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-700">
                    <th className="py-2 pr-3">Symbol</th>
                    <th className="py-2 pr-3">Dir</th>
                    <th className="py-2 pr-3">Score</th>
                    <th className="py-2 pr-3">TTL</th>
                    <th className="py-2">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {hot.map((h) => (
                    <tr key={h.symbol} className="border-b border-slate-800">
                      <td className="py-2 pr-3 font-medium">{h.symbol}</td>
                      <td className="py-2 pr-3 uppercase">{h.direction || '—'}</td>
                      <td className="py-2 pr-3">{h.score.toFixed(2)}</td>
                      <td className="py-2 pr-3">{h.ttl_minutes_remaining}m</td>
                      <td className="py-2">
                        <button
                          onClick={() => removeHot(h.symbol)}
                          className="text-red-400 hover:text-red-300 flex items-center gap-1"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h2 className="font-semibold mb-4">Latest scan rankings</h2>
          {results.length === 0 ? (
            <p className="text-sm text-slate-500">
              No scan results yet. Enable the scanner and/or run Scan now.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-700">
                    <th className="py-2 pr-2">#</th>
                    <th className="py-2 pr-2">Symbol</th>
                    <th className="py-2 pr-2">Dir</th>
                    <th className="py-2 pr-2">Score</th>
                    <th className="py-2 pr-2">Conf</th>
                    <th className="py-2 pr-2">R:R</th>
                    <th className="py-2 pr-2">Zone</th>
                    <th className="py-2 pr-2">KZ</th>
                    <th className="py-2 pr-2">Reason</th>
                    <th className="py-2">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr
                      key={`${r.symbol}-${i}`}
                      className={cn(
                        'border-b border-slate-800',
                        r.promotable && 'bg-cyan-500/5'
                      )}
                    >
                      <td className="py-2 pr-2 text-slate-500">{i + 1}</td>
                      <td className="py-2 pr-2 font-medium">{r.symbol}</td>
                      <td className="py-2 pr-2 uppercase">{r.direction || '—'}</td>
                      <td className="py-2 pr-2">{r.score.toFixed(2)}</td>
                      <td className="py-2 pr-2">
                        {r.has_setup ? `${(r.confidence * 100).toFixed(0)}%` : '—'}
                      </td>
                      <td className="py-2 pr-2">
                        {r.has_setup ? r.risk_reward.toFixed(2) : '—'}
                      </td>
                      <td className="py-2 pr-2">
                        {r.zone_ok ? (
                          <span className="text-green-400">ok</span>
                        ) : (
                          <span className="text-amber-400">no</span>
                        )}
                      </td>
                      <td className="py-2 pr-2">
                        {r.in_kill_zone || r.is_crypto ? (
                          <span className="text-green-400">yes</span>
                        ) : (
                          <span className="text-slate-500">no</span>
                        )}
                      </td>
                      <td className="py-2 pr-2 text-slate-400 max-w-[180px] truncate">
                        {r.reason}
                      </td>
                      <td className="py-2">
                        <button
                          onClick={() => promote(r.symbol)}
                          className="text-cyan-400 hover:text-cyan-300 flex items-center gap-1"
                        >
                          <Plus className="w-3.5 h-3.5" />
                          Promote
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
