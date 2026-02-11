'use client'

import { useEffect, useState } from 'react'
import { api, Trade, MT5Symbol } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ArrowUpRight, ArrowDownRight, Filter, ChevronLeft, ChevronRight, Download } from 'lucide-react'

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [symbols, setSymbols] = useState<MT5Symbol[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [filter, setFilter] = useState<{ status?: string; symbol?: string }>({})
  
  // Fetch symbols for filter dropdown
  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const data = await api.getMarketWatchSymbols()
        setSymbols(data.symbols)
      } catch (error) {
        console.error('Error fetching symbols:', error)
        // Fallback
        try {
          const config = await api.getConfig()
          setSymbols(config.trading.symbols.map(s => ({ name: s, description: '', path: '', category: '', visible: true, tradeable: true })))
        } catch (e) {
          console.error('Error fetching fallback:', e)
        }
      }
    }
    fetchSymbols()
  }, [])

  useEffect(() => {
    const fetchTrades = async () => {
      setLoading(true)
      try {
        const data = await api.getTrades({
          page,
          page_size: 20,
          status: filter.status,
          symbol: filter.symbol,
        })
        setTrades(data.trades)
        setTotalPages(Math.ceil(data.total / 20))
      } catch (error) {
        console.error('Error fetching trades:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchTrades()
  }, [page, filter])

  const handleExport = (format: 'csv' | 'json') => {
    const url = api.exportTradesUrl(format)
    window.open(url, '_blank')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Trade History</h1>
        <div className="flex items-center gap-4">
          {/* Export Buttons */}
          <div className="flex gap-2">
            <button
              onClick={() => handleExport('csv')}
              className="flex items-center gap-2 px-3 py-2 bg-green-600 hover:bg-green-500 rounded-lg text-sm font-medium transition-colors"
            >
              <Download className="w-4 h-4" />
              CSV
            </button>
            <button
              onClick={() => handleExport('json')}
              className="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
            >
              <Download className="w-4 h-4" />
              JSON
            </button>
          </div>
          <select
            value={filter.status || ''}
            onChange={(e) => setFilter({ ...filter, status: e.target.value || undefined })}
            className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg focus:outline-none focus:border-blue-500"
          >
            <option value="">All Status</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </select>
          <select
            value={filter.symbol || ''}
            onChange={(e) => setFilter({ ...filter, symbol: e.target.value || undefined })}
            className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg focus:outline-none focus:border-blue-500"
          >
            <option value="">All Symbols ({symbols.length})</option>
            {symbols.map(s => (
              <option key={s.name} value={s.name}>{s.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        <div className="card-body p-0">
          {loading ? (
            <div className="p-12 text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
            </div>
          ) : trades.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              No trades found
            </div>
          ) : (
            <table className="w-full">
              <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                <tr>
                  <th className="px-4 py-3 text-left">Time</th>
                  <th className="px-4 py-3 text-left">Symbol</th>
                  <th className="px-4 py-3 text-left">Direction</th>
                  <th className="px-4 py-3 text-right">Entry</th>
                  <th className="px-4 py-3 text-right">SL</th>
                  <th className="px-4 py-3 text-right">TP</th>
                  <th className="px-4 py-3 text-right">Exit</th>
                  <th className="px-4 py-3 text-right">P/L</th>
                  <th className="px-4 py-3 text-right">R</th>
                  <th className="px-4 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {trades.map((trade) => (
                  <tr key={trade.trade_id} className="hover:bg-slate-700/30">
                    <td className="px-4 py-3 text-sm">
                      <div>{new Date(trade.entry_time).toLocaleDateString()}</div>
                      <div className="text-slate-400">{new Date(trade.entry_time).toLocaleTimeString()}</div>
                    </td>
                    <td className="px-4 py-3 font-medium">{trade.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        'flex items-center gap-1',
                        trade.direction === 'long' ? 'text-green-400' : 'text-red-400'
                      )}>
                        {trade.direction === 'long' ? (
                          <ArrowUpRight className="w-4 h-4" />
                        ) : (
                          <ArrowDownRight className="w-4 h-4" />
                        )}
                        {trade.direction.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{trade.entry_price.toFixed(5)}</td>
                    <td className="px-4 py-3 text-right font-mono text-red-400">{trade.stop_loss.toFixed(5)}</td>
                    <td className="px-4 py-3 text-right font-mono text-green-400">{trade.take_profit.toFixed(5)}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {trade.exit_price ? trade.exit_price.toFixed(5) : '-'}
                    </td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      trade.profit_loss != null
                        ? trade.profit_loss >= 0 ? 'text-green-400' : 'text-red-400'
                        : 'text-slate-400'
                    )}>
                      {trade.profit_loss != null ? `$${trade.profit_loss.toFixed(2)}` : '-'}
                    </td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      trade.r_multiple != null
                        ? trade.r_multiple >= 0 ? 'text-green-400' : 'text-red-400'
                        : 'text-slate-400'
                    )}>
                      {trade.r_multiple != null ? `${trade.r_multiple.toFixed(2)}R` : '-'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={cn(
                        'px-2 py-1 text-xs rounded-full',
                        trade.status === 'open'
                          ? 'bg-blue-500/20 text-blue-400'
                          : trade.profit_loss != null && trade.profit_loss >= 0
                            ? 'bg-green-500/20 text-green-400'
                            : 'bg-red-500/20 text-red-400'
                      )}>
                        {trade.status === 'open' ? 'Open' : trade.profit_loss != null && trade.profit_loss >= 0 ? 'Win' : 'Loss'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        
        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-slate-700">
            <div className="text-sm text-slate-400">
              Page {page} of {totalPages}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(page - 1)}
                disabled={page === 1}
                className="p-2 rounded-lg bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-600"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(page + 1)}
                disabled={page === totalPages}
                className="p-2 rounded-lg bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-600"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
