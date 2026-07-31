'use client'

import { useState, useCallback } from 'react'
import { api, Position, Trade } from '@/lib/api'
import { useWebSocketWithPolling } from '@/hooks/useWebSocketWithPolling'
import { cn } from '@/lib/utils'
import { ArrowUpRight, ArrowDownRight, Clock } from 'lucide-react'

interface TradeData {
  positions: Position[]
  trades: Trade[]
}

export function TradeMonitor() {
  const [tab, setTab] = useState<'open' | 'recent'>('open')

  const fetchData = useCallback(async (): Promise<TradeData> => {
    const [positionsData, tradesData] = await Promise.all([
      api.getOpenPositions(),
      api.getTrades({ page_size: 10 }),
    ])
    return { positions: positionsData, trades: tradesData.trades }
  }, [])

  const { data } = useWebSocketWithPolling<TradeData>({
    channel: 'trades',
    fetchFn: fetchData,
    fastInterval: 10000,
    slowInterval: 60000,
  })

  const positions = data?.positions ?? []
  const recentTrades = data?.trades ?? []
  const loading = data === null

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h2 className="font-semibold">Trade Monitor</h2>
        <div className="flex gap-1 p-1 bg-slate-700 rounded-lg">
          <button
            onClick={() => setTab('open')}
            className={cn(
              'px-3 py-1 text-sm rounded-md transition-colors',
              tab === 'open' ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-white'
            )}
          >
            Open ({positions.length})
          </button>
          <button
            onClick={() => setTab('recent')}
            className={cn(
              'px-3 py-1 text-sm rounded-md transition-colors',
              tab === 'recent' ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-white'
            )}
          >
            Recent
          </button>
        </div>
      </div>
      <div className="card-body p-0">
        {loading ? (
          <div className="p-8 text-center text-slate-400">Loading...</div>
        ) : tab === 'open' ? (
          positions.length === 0 ? (
            <div className="p-8 text-center text-slate-400">
              No open positions
            </div>
          ) : (
            <table className="w-full">
              <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                <tr>
                  <th className="px-4 py-3 text-left">Symbol</th>
                  <th className="px-4 py-3 text-left">Direction</th>
                  <th className="px-4 py-3 text-right">Size</th>
                  <th className="px-4 py-3 text-right">Entry</th>
                  <th className="px-4 py-3 text-right">Current</th>
                  <th className="px-4 py-3 text-right">P/L</th>
                  <th className="px-4 py-3 text-right">R</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {positions.map((position) => (
                  <tr key={position.ticket} className="hover:bg-slate-700/30">
                    <td className="px-4 py-3 font-medium">{position.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        'flex items-center gap-1',
                        position.direction === 'long' ? 'text-green-400' : 'text-red-400'
                      )}>
                        {position.direction === 'long' ? (
                          <ArrowUpRight className="w-4 h-4" />
                        ) : (
                          <ArrowDownRight className="w-4 h-4" />
                        )}
                        {position.direction.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">{position.volume}</td>
                    <td className="px-4 py-3 text-right font-mono">{position.entry_price.toFixed(5)}</td>
                    <td className="px-4 py-3 text-right font-mono">{position.current_price.toFixed(5)}</td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      position.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                    )}>
                      ${position.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      position.r_multiple >= 0 ? 'text-green-400' : 'text-red-400'
                    )}>
                      {position.r_multiple.toFixed(2)}R
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          recentTrades.length === 0 ? (
            <div className="p-8 text-center text-slate-400">
              No recent trades
            </div>
          ) : (
            <table className="w-full">
              <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                <tr>
                  <th className="px-4 py-3 text-left">Time</th>
                  <th className="px-4 py-3 text-left">Symbol</th>
                  <th className="px-4 py-3 text-left">Direction</th>
                  <th className="px-4 py-3 text-right">Entry</th>
                  <th className="px-4 py-3 text-right">Exit</th>
                  <th className="px-4 py-3 text-right">P/L</th>
                  <th className="px-4 py-3 text-right">R</th>
                  <th className="px-4 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {recentTrades.map((trade) => (
                  <tr key={trade.trade_id} className="hover:bg-slate-700/30">
                    <td className="px-4 py-3 text-sm text-slate-400">
                      {new Date(trade.entry_time).toLocaleString()}
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
                    <td className="px-4 py-3 text-right font-mono">
                      {trade.exit_price ? trade.exit_price.toFixed(5) : '-'}
                    </td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      trade.status === 'cancelled'
                        ? 'text-slate-400'
                        : trade.profit_loss !== undefined
                          ? trade.profit_loss > 0 ? 'text-green-400' : trade.profit_loss < 0 ? 'text-red-400' : 'text-slate-400'
                          : 'text-slate-400'
                    )}>
                      {trade.profit_loss !== undefined ? `$${trade.profit_loss.toFixed(2)}` : '-'}
                    </td>
                    <td className={cn(
                      'px-4 py-3 text-right font-medium',
                      trade.status === 'cancelled'
                        ? 'text-slate-400'
                        : trade.r_multiple !== undefined
                          ? trade.r_multiple > 0 ? 'text-green-400' : trade.r_multiple < 0 ? 'text-red-400' : 'text-slate-400'
                          : 'text-slate-400'
                    )}>
                      {trade.r_multiple !== undefined ? `${trade.r_multiple.toFixed(2)}R` : '-'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={cn(
                        'px-2 py-1 text-xs rounded-full',
                        trade.status === 'open'
                          ? 'bg-blue-500/20 text-blue-400'
                          : trade.status === 'cancelled'
                            ? 'bg-slate-500/20 text-slate-300'
                            : trade.profit_loss !== undefined && trade.profit_loss > 0
                              ? 'bg-green-500/20 text-green-400'
                              : trade.profit_loss !== undefined && trade.profit_loss < 0
                                ? 'bg-red-500/20 text-red-400'
                                : 'bg-slate-500/20 text-slate-300'
                      )}>
                        {trade.status === 'open'
                          ? 'Open'
                          : trade.status === 'cancelled'
                            ? 'Cancelled'
                            : trade.profit_loss !== undefined && trade.profit_loss > 0
                              ? 'Win'
                              : trade.profit_loss !== undefined && trade.profit_loss < 0
                                ? 'Loss'
                                : 'Flat'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  )
}
