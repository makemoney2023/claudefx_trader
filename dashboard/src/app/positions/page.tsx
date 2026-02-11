'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, ManagedPosition, ManagedPositionsResponse, BotStatus, AccountInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { 
  AlertTriangle, 
  ArrowUpRight, 
  ArrowDownRight, 
  Shield, 
  Target, 
  TrendingUp,
  X,
  Edit2,
  RefreshCw,
  Power,
  Zap,
  AlertCircle,
  Clock,
  Wallet,
  Percent,
} from 'lucide-react'
import { PendingOrdersTable } from '@/components/PendingOrdersTable'

export default function PositionsPage() {
  const [positions, setPositions] = useState<ManagedPosition[]>([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [loading, setLoading] = useState(true)
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [accountInfo, setAccountInfo] = useState<AccountInfo | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [emergencyLoading, setEmergencyLoading] = useState(false)
  const [editingPosition, setEditingPosition] = useState<number | null>(null)
  const [editValues, setEditValues] = useState<{ sl: string; tp: string }>({ sl: '', tp: '' })
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchPositions = useCallback(async () => {
    try {
      const data = await api.getManagedPositions()
      setPositions(data.positions || [])
      setTotalPnl(data.total_pnl ?? 0)
    } catch (error) {
      console.error('Error fetching positions:', error)
    }
  }, [])

  const fetchBotStatus = useCallback(async () => {
    try {
      const status = await api.getBotStatus()
      setBotStatus(status)
    } catch (error) {
      console.error('Error fetching bot status:', error)
    }
  }, [])

  const fetchAccountInfo = useCallback(async () => {
    try {
      const info = await api.getAccountInfo()
      setAccountInfo(info)
    } catch (error) {
      console.error('Error fetching account info:', error)
    }
  }, [])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await Promise.all([fetchPositions(), fetchBotStatus(), fetchAccountInfo()])
      setLoading(false)
    }
    loadData()

    // Auto-refresh every 5 seconds
    const interval = setInterval(() => {
      fetchPositions()
      fetchBotStatus()
      fetchAccountInfo()
    }, 5000)

    return () => clearInterval(interval)
  }, [fetchPositions, fetchBotStatus, fetchAccountInfo])

  const handleRefresh = async () => {
    setRefreshing(true)
    await Promise.all([fetchPositions(), fetchBotStatus()])
    setRefreshing(false)
  }

  const handleEmergencyClose = async () => {
    if (!confirm('⚠️ EMERGENCY CLOSE: This will close ALL open positions immediately!\n\nAre you sure?')) {
      return
    }
    
    setEmergencyLoading(true)
    try {
      const result = await api.emergencyCloseAll('Dashboard emergency close')
      setActionMessage({ 
        type: result.status === 'success' ? 'success' : 'error', 
        text: result.message 
      })
      await fetchPositions()
    } catch (error) {
      setActionMessage({ type: 'error', text: 'Failed to execute emergency close' })
    } finally {
      setEmergencyLoading(false)
    }
    
    setTimeout(() => setActionMessage(null), 5000)
  }

  const handleClosePosition = async (ticket: number, symbol: string) => {
    if (!confirm(`Close ${symbol} position #${ticket}?`)) return
    
    try {
      const result = await api.closePosition(ticket, 'Manual close from dashboard')
      setActionMessage({ 
        type: result.status === 'success' ? 'success' : 'error', 
        text: result.message 
      })
      await fetchPositions()
    } catch (error) {
      setActionMessage({ type: 'error', text: `Failed to close position ${ticket}` })
    }
    
    setTimeout(() => setActionMessage(null), 5000)
  }

  const handleModifyPosition = async (ticket: number) => {
    const sl = editValues.sl ? parseFloat(editValues.sl) : undefined
    const tp = editValues.tp ? parseFloat(editValues.tp) : undefined
    
    if (!sl && !tp) {
      setActionMessage({ type: 'error', text: 'Please enter a new SL or TP value' })
      return
    }
    
    try {
      const result = await api.modifyPosition(ticket, sl, tp)
      setActionMessage({ 
        type: result.status === 'success' ? 'success' : 'error', 
        text: result.message 
      })
      setEditingPosition(null)
      setEditValues({ sl: '', tp: '' })
      await fetchPositions()
    } catch (error) {
      setActionMessage({ type: 'error', text: `Failed to modify position ${ticket}` })
    }
    
    setTimeout(() => setActionMessage(null), 5000)
  }

  const startEditing = (position: ManagedPosition) => {
    setEditingPosition(position.ticket)
    setEditValues({
      sl: position.stop_loss.toString(),
      tp: position.take_profit.toString()
    })
  }

  const getStatusBadge = (position: ManagedPosition) => {
    if (position.trailing_active) {
      return { label: 'TRAILING', color: 'bg-purple-500/20 text-purple-400' }
    }
    if (position.be_triggered) {
      return { label: 'BREAK-EVEN', color: 'bg-blue-500/20 text-blue-400' }
    }
    if (position.partial_closed) {
      return { label: 'PARTIAL', color: 'bg-yellow-500/20 text-yellow-400' }
    }
    return { label: 'OPEN', color: 'bg-slate-500/20 text-slate-400' }
  }

  const getManagementDetail = (position: ManagedPosition): string => {
    const parts: string[] = []
    if (position.initial_sl && position.initial_sl !== position.stop_loss) {
      parts.push(`Initial SL: ${position.initial_sl.toFixed(5)}`)
    }
    if (position.trailing_active) {
      parts.push(`Trailing SL: ${position.stop_loss.toFixed(5)}`)
    }
    if (position.be_triggered && !position.trailing_active) {
      parts.push(`BE at entry: ${position.entry_price.toFixed(5)}`)
    }
    if (position.tp1_hit) parts.push('TP1 hit')
    if (position.tp2_hit) parts.push('TP2 hit')
    if (position.tp1 && position.tp1 > 0 && !position.tp1_hit) {
      parts.push(`TP1: ${position.tp1.toFixed(5)}`)
    }
    if (position.initial_volume && position.initial_volume !== position.volume) {
      parts.push(`Vol: ${position.volume.toFixed(2)}/${position.initial_volume.toFixed(2)}`)
    }
    return parts.join(' | ')
  }

  const getRMultipleColor = (r: number) => {
    if (r >= 2) return 'text-green-400'
    if (r >= 1) return 'text-emerald-400'
    if (r >= 0) return 'text-yellow-400'
    if (r >= -0.5) return 'text-orange-400'
    return 'text-red-400'
  }

  return (
    <div className="space-y-6">
      {/* Header with Emergency Button */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Shield className="w-7 h-7 text-blue-400" />
            Position Management
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Real-time position monitoring and control
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          {/* Refresh Button */}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Refresh
          </button>
          
          {/* Emergency Close Button */}
          <button
            onClick={handleEmergencyClose}
            disabled={emergencyLoading || positions.length === 0}
            className={cn(
              "flex items-center gap-2 px-6 py-3 rounded-lg font-bold text-lg transition-all",
              "bg-red-600 hover:bg-red-500 shadow-lg shadow-red-500/30",
              "disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none",
              "animate-pulse hover:animate-none"
            )}
          >
            <Zap className="w-5 h-5" />
            {emergencyLoading ? 'CLOSING...' : 'EMERGENCY CLOSE ALL'}
          </button>
        </div>
      </div>

      {/* Action Message */}
      {actionMessage && (
        <div className={cn(
          "p-4 rounded-lg flex items-center gap-2",
          actionMessage.type === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        )}>
          {actionMessage.type === 'success' ? (
            <Target className="w-5 h-5" />
          ) : (
            <AlertCircle className="w-5 h-5" />
          )}
          {actionMessage.text}
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Total Positions */}
        <div className="card p-4">
          <div className="text-slate-400 text-sm">Open Positions</div>
          <div className="text-3xl font-bold mt-1">{positions.length}</div>
        </div>
        
        {/* Total P&L */}
        <div className="card p-4">
          <div className="text-slate-400 text-sm">Total P&L</div>
          <div className={cn(
            "text-3xl font-bold mt-1",
            (totalPnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
          )}>
            ${(totalPnl ?? 0).toFixed(2)}
          </div>
        </div>
        
        {/* Bot Status */}
        <div className="card p-4">
          <div className="text-slate-400 text-sm">Bot Status</div>
          <div className="flex items-center gap-2 mt-1">
            <div className={cn(
              "w-3 h-3 rounded-full",
              botStatus?.is_running ? 'bg-green-400 animate-pulse' : 'bg-red-400'
            )} />
            <span className="text-xl font-bold">
              {botStatus?.is_running ? 'Running' : 'Stopped'}
            </span>
          </div>
        </div>
        
        {/* Current Action */}
        <div className="card p-4">
          <div className="text-slate-400 text-sm">Current Action</div>
          <div className="text-lg font-medium mt-1 truncate">
            {botStatus?.current_action || 'Idle'}
          </div>
        </div>
      </div>

      {/* Margin Health Section */}
      {accountInfo && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="card p-4">
            <div className="flex items-center gap-2 text-slate-400 text-sm mb-1">
              <Wallet className="w-4 h-4" />
              Free Margin
            </div>
            <div className={cn(
              "text-2xl font-bold",
              (accountInfo.free_margin ?? 0) > 1000 ? 'text-green-400' : 
              (accountInfo.free_margin ?? 0) > 500 ? 'text-yellow-400' : 'text-red-400'
            )}>
              ${(accountInfo.free_margin ?? 0).toFixed(2)}
            </div>
          </div>
          
          <div className="card p-4">
            <div className="flex items-center gap-2 text-slate-400 text-sm mb-1">
              <Percent className="w-4 h-4" />
              Margin Level
            </div>
            <div className={cn(
              "text-2xl font-bold",
              (accountInfo.margin_level ?? 0) > 300 ? 'text-green-400' : 
              (accountInfo.margin_level ?? 0) > 150 ? 'text-yellow-400' : 'text-red-400'
            )}>
              {(accountInfo.margin_level ?? 0).toFixed(0)}%
            </div>
            {(accountInfo.margin_level ?? 0) < 300 && (accountInfo.margin_level ?? 0) > 0 && (
              <div className="flex items-center gap-1 mt-1 text-xs text-yellow-400">
                <AlertTriangle className="w-3 h-3" />
                <span>Below safe threshold</span>
              </div>
            )}
          </div>
          
          <div className="card p-4">
            <div className="text-slate-400 text-sm mb-1">Used Margin</div>
            <div className="text-2xl font-bold text-slate-300">
              ${(accountInfo.margin ?? 0).toFixed(2)}
            </div>
          </div>
          
          <div className="card p-4">
            <div className="text-slate-400 text-sm mb-1">Equity</div>
            <div className={cn(
              "text-2xl font-bold",
              (accountInfo.equity ?? 0) >= (accountInfo.balance ?? 0) ? 'text-green-400' : 'text-red-400'
            )}>
              ${(accountInfo.equity ?? 0).toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Pending Orders Section */}
      <PendingOrdersTable />

      {/* Positions Table */}
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <h2 className="card-title flex items-center gap-2">
            <TrendingUp className="w-5 h-5" />
            Active Positions
          </h2>
          {positions.length > 0 && (
            <span className="text-sm text-slate-400">
              Auto-refreshing every 5 seconds
            </span>
          )}
        </div>
        
        <div className="card-body p-0">
          {loading ? (
            <div className="p-12 text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
              <p className="text-slate-400 mt-4">Loading positions...</p>
            </div>
          ) : positions.length === 0 ? (
            <div className="p-12 text-center">
              <Shield className="w-16 h-16 text-slate-600 mx-auto mb-4" />
              <p className="text-slate-400 text-lg">No open positions</p>
              <p className="text-slate-500 text-sm mt-2">
                Positions will appear here when the bot opens trades
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="text-xs text-slate-400 uppercase bg-slate-700/50">
                  <tr>
                    <th className="px-4 py-3 text-left">Ticket</th>
                    <th className="px-4 py-3 text-left">Symbol</th>
                    <th className="px-4 py-3 text-left">Direction</th>
                    <th className="px-4 py-3 text-right">Volume</th>
                    <th className="px-4 py-3 text-right">Entry</th>
                    <th className="px-4 py-3 text-right">Current</th>
                    <th className="px-4 py-3 text-right">SL</th>
                    <th className="px-4 py-3 text-right">TP</th>
                    <th className="px-4 py-3 text-right">P&L</th>
                    <th className="px-4 py-3 text-center">R-Multiple</th>
                    <th className="px-4 py-3 text-center">Status</th>
                    <th className="px-4 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {positions.map((position) => {
                    const status = getStatusBadge(position)
                    const isEditing = editingPosition === position.ticket
                    
                    return (
                      <tr key={position.ticket} className="hover:bg-slate-700/30">
                        <td className="px-4 py-3 font-mono text-sm text-slate-400">
                          #{position.ticket}
                        </td>
                        <td className="px-4 py-3 font-medium">
                          {position.symbol}
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn(
                            'flex items-center gap-1 font-medium',
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
                        <td className="px-4 py-3 text-right font-mono">
                          {(position.volume ?? 0).toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">
                          {(position.entry_price ?? 0).toFixed(5)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">
                          {(position.current_price ?? 0).toFixed(5)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {isEditing ? (
                            <input
                              type="number"
                              step="0.00001"
                              value={editValues.sl}
                              onChange={(e) => setEditValues({ ...editValues, sl: e.target.value })}
                              className="w-24 px-2 py-1 bg-slate-600 border border-slate-500 rounded text-right font-mono text-sm"
                            />
                          ) : (
                            <span className="font-mono text-red-400">
                              {position.stop_loss?.toFixed(5) ?? '-'}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {isEditing ? (
                            <input
                              type="number"
                              step="0.00001"
                              value={editValues.tp}
                              onChange={(e) => setEditValues({ ...editValues, tp: e.target.value })}
                              className="w-24 px-2 py-1 bg-slate-600 border border-slate-500 rounded text-right font-mono text-sm"
                            />
                          ) : (
                            <span className="font-mono text-green-400">
                              {position.take_profit?.toFixed(5) ?? '-'}
                            </span>
                          )}
                        </td>
                        <td className={cn(
                          "px-4 py-3 text-right font-bold",
                          (position.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                        )}>
                          ${(position.unrealized_pnl ?? 0).toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className={cn(
                            "font-bold text-lg",
                            getRMultipleColor(position.r_multiple ?? 0)
                          )}>
                            {(position.r_multiple ?? 0) >= 0 ? '+' : ''}{(position.r_multiple ?? 0).toFixed(2)}R
                          </span>
                        </td>
                        <td className="px-4 py-3 text-center">
                          <div className="flex flex-col items-center gap-1">
                            <span className={cn(
                              "px-2 py-1 text-xs font-medium rounded-full",
                              status.color
                            )}>
                              {status.label}
                            </span>
                            {getManagementDetail(position) && (
                              <span className="text-[10px] text-slate-500 max-w-[160px] text-center leading-tight">
                                {getManagementDetail(position)}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-center gap-2">
                            {isEditing ? (
                              <>
                                <button
                                  onClick={() => handleModifyPosition(position.ticket)}
                                  className="p-2 bg-green-500/20 hover:bg-green-500/30 text-green-400 rounded-lg transition-colors"
                                  title="Save changes"
                                >
                                  <Target className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => {
                                    setEditingPosition(null)
                                    setEditValues({ sl: '', tp: '' })
                                  }}
                                  className="p-2 bg-slate-500/20 hover:bg-slate-500/30 text-slate-400 rounded-lg transition-colors"
                                  title="Cancel"
                                >
                                  <X className="w-4 h-4" />
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  onClick={() => startEditing(position)}
                                  className="p-2 bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded-lg transition-colors"
                                  title="Modify SL/TP"
                                >
                                  <Edit2 className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => handleClosePosition(position.ticket, position.symbol)}
                                  className="p-2 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-lg transition-colors"
                                  title="Close position"
                                >
                                  <X className="w-4 h-4" />
                                </button>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      {positions.length > 0 && (
        <div className="card p-4">
          <h3 className="font-medium mb-4 flex items-center gap-2">
            <Zap className="w-4 h-4 text-yellow-400" />
            Quick Actions
          </h3>
          <div className="flex flex-wrap gap-3">
            <button
              onClick={async () => {
                for (const pos of positions.filter(p => !p.be_triggered && p.r_multiple >= 1)) {
                  await api.modifyPosition(pos.ticket, pos.entry_price, undefined)
                }
                await fetchPositions()
                setActionMessage({ type: 'success', text: 'Moved profitable positions to break-even' })
                setTimeout(() => setActionMessage(null), 5000)
              }}
              disabled={!positions.some(p => !p.be_triggered && p.r_multiple >= 1)}
              className="px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Move All to Break-Even
            </button>
            <button
              onClick={async () => {
                for (const pos of positions.filter(p => p.r_multiple <= -0.5)) {
                  await api.closePosition(pos.ticket, 'Close losing positions')
                }
                await fetchPositions()
                setActionMessage({ type: 'success', text: 'Closed losing positions' })
                setTimeout(() => setActionMessage(null), 5000)
              }}
              disabled={!positions.some(p => p.r_multiple <= -0.5)}
              className="px-4 py-2 bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Close Losing Positions (&lt;-0.5R)
            </button>
            <button
              onClick={async () => {
                const silverPositions = positions.filter(p => p.symbol.includes('XAG') || p.symbol.includes('SILVER'))
                for (const pos of silverPositions) {
                  const newTp = pos.direction === 'long' 
                    ? Math.max(pos.take_profit, 150) 
                    : Math.min(pos.take_profit, 80)
                  await api.modifyPosition(pos.ticket, undefined, newTp)
                }
                await fetchPositions()
                setActionMessage({ type: 'success', text: 'Extended silver targets' })
                setTimeout(() => setActionMessage(null), 5000)
              }}
              disabled={!positions.some(p => p.symbol.includes('XAG') || p.symbol.includes('SILVER'))}
              className="px-4 py-2 bg-purple-500/20 hover:bg-purple-500/30 text-purple-400 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Extend Silver Targets
            </button>
          </div>
        </div>
      )}

      {/* Bot Control */}
      <div className="card p-4">
        <h3 className="font-medium mb-4 flex items-center gap-2">
          <Power className="w-4 h-4 text-green-400" />
          Bot Control
        </h3>
        <div className="flex gap-4">
          <button
            onClick={async () => {
              try {
                await api.startBot()
                await fetchBotStatus()
                setActionMessage({ type: 'success', text: 'Bot started' })
              } catch (error) {
                setActionMessage({ type: 'error', text: 'Failed to start bot' })
              }
              setTimeout(() => setActionMessage(null), 5000)
            }}
            disabled={botStatus?.is_running}
            className="px-6 py-3 bg-green-600 hover:bg-green-500 rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Start Bot
          </button>
          <button
            onClick={async () => {
              try {
                await api.stopBot()
                await fetchBotStatus()
                setActionMessage({ type: 'success', text: 'Bot stopped' })
              } catch (error) {
                setActionMessage({ type: 'error', text: 'Failed to stop bot' })
              }
              setTimeout(() => setActionMessage(null), 5000)
            }}
            disabled={!botStatus?.is_running}
            className="px-6 py-3 bg-slate-600 hover:bg-slate-500 rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Stop Bot
          </button>
        </div>
      </div>
    </div>
  )
}
