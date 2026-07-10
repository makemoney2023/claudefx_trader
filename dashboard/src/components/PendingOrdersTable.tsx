'use client'

import { useState, useCallback } from 'react'
import { api, PendingOrder, OrderResult } from '@/lib/api'
import { useWebSocketWithPolling } from '@/hooks/useWebSocketWithPolling'
import { cn } from '@/lib/utils'
import {
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  X,
  RefreshCw,
  Target,
  AlertCircle,
  Timer,
  TrendingUp,
  TrendingDown,
} from 'lucide-react'

interface PendingOrdersTableProps {
  compact?: boolean
  showTitle?: boolean
  maxOrders?: number
}

export function PendingOrdersTable({ 
  compact = false, 
  showTitle = true,
  maxOrders 
}: PendingOrdersTableProps) {
  const [refreshing, setRefreshing] = useState(false)
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchOrders = useCallback(async (): Promise<PendingOrder[]> => {
    const ordersArray = await api.getPendingOrders()
    return maxOrders ? ordersArray.slice(0, maxOrders) : ordersArray
  }, [maxOrders])

  const { data: ordersData, refresh } = useWebSocketWithPolling<PendingOrder[]>({
    channel: 'trades',
    fetchFn: fetchOrders,
    fastInterval: 10000,
    slowInterval: 60000,
  })

  const orders = ordersData ?? []
  const loading = ordersData === null

  const handleRefresh = async () => {
    setRefreshing(true)
    await refresh()
    setRefreshing(false)
  }

  const handleCancelOrder = async (ticket: number, symbol: string) => {
    if (!confirm(`Cancel pending order #${ticket} for ${symbol}?`)) return

    try {
      const result = await api.cancelPendingOrder(ticket)
      setActionMessage({
        type: result.success ? 'success' : 'error',
        text: result.message,
      })
      refresh()
    } catch (error) {
      setActionMessage({ type: 'error', text: `Failed to cancel order ${ticket}` })
    }

    setTimeout(() => setActionMessage(null), 5000)
  }

  const getOrderTypeColor = (orderType: string) => {
    switch (orderType) {
      case 'buy_limit':
        return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
      case 'sell_limit':
        return 'bg-rose-500/20 text-rose-400 border-rose-500/30'
      case 'buy_stop':
        return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30'
      case 'sell_stop':
        return 'bg-orange-500/20 text-orange-400 border-orange-500/30'
      default:
        return 'bg-slate-500/20 text-slate-400 border-slate-500/30'
    }
  }

  const getOrderTypeIcon = (orderType: string) => {
    if (orderType.includes('buy')) {
      return <TrendingUp className="w-3.5 h-3.5" />
    }
    return <TrendingDown className="w-3.5 h-3.5" />
  }

  const formatExpiration = (expiration?: string) => {
    if (!expiration) return 'GTC'
    const date = new Date(expiration)
    const now = new Date()
    const diff = date.getTime() - now.getTime()
    
    if (diff <= 0) return 'Expired'
    
    const hours = Math.floor(diff / (1000 * 60 * 60))
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))
    
    if (hours > 0) return `${hours}h ${minutes}m`
    return `${minutes}m`
  }

  if (loading) {
    return (
      <div className="card p-6">
        <div className="flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      </div>
    )
  }

  return (
    <div className={cn("card", compact && "p-0")}>
      {showTitle && (
        <div className="card-header flex items-center justify-between">
          <h2 className="card-title flex items-center gap-2">
            <Clock className="w-5 h-5 text-amber-400" />
            Pending Orders
            {orders.length > 0 && (
              <span className="ml-2 px-2 py-0.5 text-xs font-medium bg-amber-500/20 text-amber-400 rounded-full">
                {orders.length}
              </span>
            )}
          </h2>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
          </button>
        </div>
      )}

      {actionMessage && (
        <div
          className={cn(
            "mx-4 mt-2 p-3 rounded-lg flex items-center gap-2 text-sm",
            actionMessage.type === 'success'
              ? 'bg-green-500/20 text-green-400'
              : 'bg-red-500/20 text-red-400'
          )}
        >
          {actionMessage.type === 'success' ? (
            <Target className="w-4 h-4" />
          ) : (
            <AlertCircle className="w-4 h-4" />
          )}
          {actionMessage.text}
        </div>
      )}

      <div className={cn("card-body", compact && "p-3")}>
        {orders.length === 0 ? (
          <div className="text-center py-8">
            <Clock className="w-12 h-12 text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400">No pending orders</p>
            <p className="text-slate-500 text-sm mt-1">
              Limit and stop orders will appear here
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {orders.map((order) => (
              <div
                key={order.ticket}
                className="bg-slate-700/50 rounded-lg p-3 border border-slate-600/50 hover:border-slate-500/50 transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <span className="font-semibold text-white">{order.symbol}</span>
                    <span
                      className={cn(
                        "px-2 py-0.5 text-xs font-medium rounded border flex items-center gap-1",
                        getOrderTypeColor(order.order_type)
                      )}
                    >
                      {getOrderTypeIcon(order.order_type)}
                      {order.order_type.replace('_', ' ').toUpperCase()}
                    </span>
                  </div>
                  <button
                    onClick={() => handleCancelOrder(order.ticket, order.symbol)}
                    className="p-1.5 hover:bg-red-500/20 text-slate-400 hover:text-red-400 rounded transition-colors"
                    title="Cancel order"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div className="grid grid-cols-4 gap-2 text-sm">
                  <div>
                    <span className="text-slate-400 text-xs">Entry</span>
                    <div className="font-mono text-white">{order.price?.toFixed(5) ?? '—'}</div>
                  </div>
                  <div>
                    <span className="text-slate-400 text-xs">Volume</span>
                    <div className="font-mono text-white">{order.volume?.toFixed(2) ?? '—'}</div>
                  </div>
                  <div>
                    <span className="text-slate-400 text-xs">SL</span>
                    <div className="font-mono text-red-400">{order.stop_loss?.toFixed(5) ?? '—'}</div>
                  </div>
                  <div>
                    <span className="text-slate-400 text-xs">TP</span>
                    <div className="font-mono text-green-400">{order.take_profit?.toFixed(5) ?? '—'}</div>
                  </div>
                </div>

                <div className="flex items-center justify-between mt-2 pt-2 border-t border-slate-600/50">
                  <div className="flex items-center gap-1 text-xs text-slate-400">
                    <Timer className="w-3 h-3" />
                    <span>Expires: {formatExpiration(order.expiration)}</span>
                  </div>
                  <span className="text-xs text-slate-500">#{order.ticket}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
