'use client'

import { useEffect, useState, useRef, useCallback } from 'react'
import { api, BotStatus, BotLog } from '@/lib/api'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { WebSocketMessage } from '@/lib/wsTypes'
import { cn } from '@/lib/utils'
import { 
  Activity, 
  RefreshCw, 
  Zap, 
  Clock, 
  AlertCircle, 
  CheckCircle, 
  XCircle,
  TrendingUp,
  TrendingDown,
  Sparkles,
  BarChart3,
  Loader2,
  Wifi,
  WifiOff,
  RotateCw,
  Play,
  Square,
  AlertTriangle
} from 'lucide-react'

function ExpandableReasoning({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const isLong = text.length > 200
  
  return (
    <div className="mt-1">
      <p className={cn("text-slate-500 text-sm", !expanded && isLong && "line-clamp-3")}>
        {text}
      </p>
      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-blue-500 hover:text-blue-400 mt-1 font-medium"
        >
          {expanded ? 'Show less' : 'Show full analysis'}
        </button>
      )}
    </div>
  )
}

export default function BotActivityPage() {
  const [status, setStatus] = useState<BotStatus | null>(null)
  const [logs, setLogs] = useState<BotLog[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [filterType, setFilterType] = useState<string>('')
  const logsEndRef = useRef<HTMLDivElement>(null)

  const { lastMessage, isConnected } = useWebSocket('activity')

  const fetchData = useCallback(async () => {
    try {
      const [statusData, logsData] = await Promise.all([
        api.getBotStatus(),
        api.getBotLogs(100, undefined, filterType || undefined)
      ])
      setStatus(statusData)
      setLogs(logsData.logs)
    } catch (error) {
      console.error('Error fetching bot status:', error)
    } finally {
      setLoading(false)
    }
  }, [filterType])

  const syncSymbols = async () => {
    setSyncing(true)
    try {
      const result = await api.syncSymbolsFromMarketWatch()
      const message = `Synced ${result.synced.length} symbols!\n\n` +
        `Added: ${result.added.length > 0 ? result.added.join(', ') : 'none'}\n` +
        `Removed: ${result.removed.length > 0 ? result.removed.join(', ') : 'none'}\n\n` +
        `Current symbols: ${result.current_symbols.join(', ')}`
      alert(message)
      fetchData()
    } catch (error: any) {
      const errorMessage = error?.message || error?.toString() || 'Failed to sync symbols'
      alert(`Error syncing symbols: ${errorMessage}`)
      console.error('Sync error:', error)
    } finally {
      setSyncing(false)
    }
  }

  const startBot = async () => {
    setStarting(true)
    try {
      await api.startBot()
      fetchData()
    } catch (error: any) {
      const errorMessage = error?.message || error?.toString() || 'Failed to start bot'
      alert(`Error starting bot: ${errorMessage}`)
      console.error('Start error:', error)
    } finally {
      setStarting(false)
    }
  }

  const stopBot = async () => {
    setStopping(true)
    try {
      await api.stopBot()
      fetchData()
    } catch (error: any) {
      const errorMessage = error?.message || error?.toString() || 'Failed to stop bot'
      alert(`Error stopping bot: ${errorMessage}`)
      console.error('Stop error:', error)
    } finally {
      setStopping(false)
    }
  }

  useEffect(() => {
    if (lastMessage && (lastMessage.type === 'activity' || lastMessage.type === 'trade_update')) {
      fetchData()
    }
  }, [lastMessage, fetchData])

  useEffect(() => {
    fetchData()
    
    if (autoRefresh) {
      const interval = setInterval(fetchData, isConnected ? 15000 : 3000)
      return () => clearInterval(interval)
    }
  }, [autoRefresh, fetchData, isConnected])

  const getLogIcon = (type: string) => {
    switch (type) {
      case 'analyzing': return <BarChart3 className="w-4 h-4 text-blue-400" />
      case 'fetching': return <RefreshCw className="w-4 h-4 text-slate-400" />
      case 'technical': return <Activity className="w-4 h-4 text-purple-400" />
      case 'mtf': return <TrendingUp className="w-4 h-4 text-indigo-400" />
      case 'fibonacci': return <TrendingDown className="w-4 h-4 text-teal-400" />
      case 'claude': return <Sparkles className="w-4 h-4 text-amber-400" />
      case 'claude_reeval': return <Sparkles className="w-4 h-4 text-cyan-400" />
      case 'signal': return <Zap className="w-4 h-4 text-yellow-400" />
      case 'decision': return <CheckCircle className="w-4 h-4 text-cyan-400" />
      case 'trade': return <TrendingUp className="w-4 h-4 text-green-400" />
      case 'complete': return <CheckCircle className="w-4 h-4 text-green-400" />
      case 'cycle': return <RefreshCw className="w-4 h-4 text-blue-400" />
      case 'error': return <XCircle className="w-4 h-4 text-red-400" />
      default: return <Activity className="w-4 h-4 text-slate-400" />
    }
  }

  const getLogColor = (type: string) => {
    switch (type) {
      case 'analyzing': return 'border-blue-500/30 bg-blue-500/5'
      case 'mtf': return 'border-indigo-500/30 bg-indigo-500/5'
      case 'fibonacci': return 'border-teal-500/30 bg-teal-500/5'
      case 'claude': return 'border-amber-500/30 bg-amber-500/5'
      case 'claude_reeval': return 'border-cyan-500/30 bg-cyan-500/5'
      case 'signal': return 'border-yellow-500/30 bg-yellow-500/5'
      case 'trade': return 'border-green-500/30 bg-green-500/5'
      case 'error': return 'border-red-500/30 bg-red-500/5'
      case 'cycle': return 'border-blue-500/30 bg-blue-500/10'
      default: return 'border-slate-700 bg-slate-800/50'
    }
  }

  const getActionLabel = (action: string) => {
    if (action === 'idle') return 'Idle'
    if (action === 'waiting') return 'Waiting for next cycle'
    if (action === 'starting_cycle') return 'Starting new cycle'
    if (action.startsWith('analyzing_')) return `Analyzing ${action.replace('analyzing_', '')}`
    if (action.startsWith('fetching_data_')) return `Fetching data for ${action.replace('fetching_data_', '')}`
    if (action.startsWith('technical_analysis_')) return `Running ICT analysis on ${action.replace('technical_analysis_', '')}`
    if (action.startsWith('claude_analysis_')) return `Claude analyzing ${action.replace('claude_analysis_', '')}`
    return action
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">Bot Activity</h1>
          {status?.is_running ? (
            <span className="flex items-center gap-2 px-3 py-1 bg-green-500/20 text-green-400 rounded-full text-sm">
              <Wifi className="w-4 h-4" />
              Running
            </span>
          ) : (
            <span className="flex items-center gap-2 px-3 py-1 bg-slate-500/20 text-slate-400 rounded-full text-sm">
              <WifiOff className="w-4 h-4" />
              Stopped
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-400">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-slate-600"
            />
            Auto-refresh
          </label>
          <button
            onClick={fetchData}
            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg flex items-center gap-2 text-sm"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          {status?.is_running ? (
            <button
              onClick={stopBot}
              disabled={stopping}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg flex items-center gap-2 text-sm font-medium"
            >
              {stopping ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Square className="w-4 h-4" />
              )}
              Stop Bot
            </button>
          ) : (
            <button
              onClick={startBot}
              disabled={starting}
              className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded-lg flex items-center gap-2 text-sm font-medium"
            >
              {starting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              Start Bot
            </button>
          )}
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Current Action */}
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400 mb-1">Current Action</p>
            <div className="flex items-center gap-2">
              {status?.current_action !== 'idle' && status?.current_action !== 'waiting' && (
                <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
              )}
              <p className="font-medium text-lg">
                {status ? getActionLabel(status.current_action) : 'Unknown'}
              </p>
            </div>
            {status?.current_symbol && (
              <p className="text-sm text-blue-400 mt-1">Symbol: {status.current_symbol}</p>
            )}
          </div>
        </div>

        {/* Session */}
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400 mb-1">Trading Session</p>
            <p className="font-medium text-lg">{status?.session.name || 'Unknown'}</p>
            <p className={cn(
              'text-sm mt-1',
              status?.session.is_tradeable ? 'text-green-400' : 'text-red-400'
            )}>
              {status?.session.is_tradeable ? '✓ Tradeable' : '✗ Not Tradeable'}
            </p>
          </div>
        </div>

        {/* Cycle Info */}
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400 mb-1">Cycles Completed</p>
            <p className="font-medium text-lg">{status?.cycle_info.count || 0}</p>
            {status?.cycle_info.last_cycle_time && (
              <p className="text-sm text-slate-400 mt-1">
                Last: {new Date(status.cycle_info.last_cycle_time).toLocaleTimeString()}
              </p>
            )}
          </div>
        </div>

        {/* Trading Symbols */}
        <div className="card">
          <div className="card-body">
            <div className="flex items-center justify-between mb-1">
              <p className="text-sm text-slate-400">Trading Symbols</p>
              <button
                onClick={syncSymbols}
                disabled={syncing}
                className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
              >
                <RotateCw className={cn('w-3 h-3', syncing && 'animate-spin')} />
                Sync from MT5
              </button>
            </div>
            <p className="font-medium text-lg">{status?.config?.trading_symbols?.length || 0} symbols</p>
            <p className="text-xs text-slate-500 mt-1 truncate">
              {(status?.config?.trading_symbols ?? []).slice(0, 5).join(', ')}
              {(status?.config?.trading_symbols?.length || 0) > 5 && '...'}
            </p>
          </div>
        </div>
      </div>

      {/* Symbols Being Traded */}
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <h2 className="font-semibold">Symbols Being Traded</h2>
          <span className="text-sm text-slate-400">
            {status?.config?.trading_symbols?.length || 0} active
          </span>
        </div>
        <div className="card-body">
          <div className="flex flex-wrap gap-2">
            {(status?.config?.trading_symbols ?? []).map((symbol) => (
              <span
                key={symbol}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-sm font-medium',
                  status?.cycle_info?.symbols_this_cycle?.includes(symbol)
                    ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                    : status?.current_symbol === symbol
                    ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30 animate-pulse'
                    : 'bg-slate-700 text-slate-300'
                )}
              >
                {symbol}
                {status?.current_symbol === symbol && (
                  <span className="ml-2 text-xs">⟳</span>
                )}
                {status?.cycle_info?.symbols_this_cycle?.includes(symbol) && status?.current_symbol !== symbol && (
                  <span className="ml-2 text-xs">✓</span>
                )}
              </span>
            ))}
          </div>
          {status?.config?.trading_symbols?.length === 0 && (
            <p className="text-slate-400 text-center py-4">
              No symbols configured. Click "Sync from MT5" to add your Market Watch symbols.
            </p>
          )}
        </div>
      </div>

      {/* Activity Log */}
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <h2 className="font-semibold flex items-center gap-2">
            <Activity className="w-5 h-5" />
            Real-Time Activity Log
          </h2>
          <div className="flex items-center gap-3">
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="px-3 py-1 bg-slate-700 border border-slate-600 rounded-lg text-sm"
            >
              <option value="">All Events</option>
              <option value="analyzing">Analyzing</option>
              <option value="mtf">MTF Analysis (D1-M1)</option>
              <option value="fibonacci">Fibonacci/OTE</option>
              <option value="claude">Claude AI</option>
              <option value="claude_reeval">Claude Re-eval</option>
              <option value="signal">Signals</option>
              <option value="decision">Decisions</option>
              <option value="trade">Trades</option>
              <option value="error">Errors</option>
              <option value="cycle">Cycles</option>
            </select>
            <span className="text-sm text-slate-400">{logs.length} events</span>
          </div>
        </div>
        <div className="card-body p-0 max-h-[500px] overflow-auto">
          {logs.length === 0 ? (
            <div className="p-8 text-center text-slate-400">
              <Activity className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No activity logs yet</p>
              <p className="text-sm mt-1">Bot activity will appear here in real-time</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-700/50">
              {logs.map((log, index) => (
                <div
                  key={`${log.timestamp}-${index}`}
                  className={cn(
                    'px-4 py-3 border-l-2 transition-colors hover:bg-slate-800/50',
                    getLogColor(log.type)
                  )}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5">
                      {getLogIcon(log.type)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-slate-500">
                          {new Date(log.timestamp).toLocaleTimeString()}
                        </span>
                        {log.symbol && (
                          <span className="text-xs px-2 py-0.5 bg-slate-700 rounded text-slate-300">
                            {log.symbol}
                          </span>
                        )}
                        <span className="text-xs px-2 py-0.5 bg-slate-600/50 rounded text-slate-400 uppercase">
                          {log.type}
                        </span>
                      </div>
                      <p className="text-sm text-slate-200">{log.message}</p>
                      {log.details && Object.keys(log.details).length > 0 && (
                        <div className="mt-2 text-xs text-slate-400 bg-slate-900/50 rounded p-2">
                          {/* MTF Analysis details */}
                          {log.type === 'mtf' && (
                            <div className="space-y-1">
                              <div className="flex flex-wrap gap-2">
                                {log.details.d1_bias && (
                                  <span className={cn('px-2 py-0.5 rounded', 
                                    log.details.d1_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.d1_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>D1: {log.details.d1_bias?.toUpperCase()}</span>
                                )}
                                {log.details.h4_bias && (
                                  <span className={cn('px-2 py-0.5 rounded',
                                    log.details.h4_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.h4_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>H4: {log.details.h4_bias?.toUpperCase()}</span>
                                )}
                                {log.details.h1_bias && (
                                  <span className={cn('px-2 py-0.5 rounded',
                                    log.details.h1_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.h1_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>H1: {log.details.h1_bias?.toUpperCase()}</span>
                                )}
                                {log.details.m15_bias && (
                                  <span className={cn('px-2 py-0.5 rounded',
                                    log.details.m15_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.m15_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>M15: {log.details.m15_bias?.toUpperCase()}</span>
                                )}
                                {log.details.m5_bias && (
                                  <span className={cn('px-2 py-0.5 rounded',
                                    log.details.m5_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.m5_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>M5: {log.details.m5_bias?.toUpperCase()}</span>
                                )}
                                {log.details.m1_bias && (
                                  <span className={cn('px-2 py-0.5 rounded',
                                    log.details.m1_bias === 'bullish' ? 'bg-green-500/20 text-green-400' :
                                    log.details.m1_bias === 'bearish' ? 'bg-red-500/20 text-red-400' :
                                    'bg-slate-600/50 text-slate-400'
                                  )}>M1: {log.details.m1_bias?.toUpperCase()}</span>
                                )}
                              </div>
                              {log.details.h4_structure && log.details.h4_structure !== 'N/A' && (
                                <span className="text-slate-500">H4 Structure: {log.details.h4_structure}</span>
                              )}
                              {log.details.key_levels && log.details.key_levels.length > 0 && (
                                <span className="text-slate-500">
                                  HTF Levels: {log.details.key_levels.map((l: number) => l.toFixed(5)).join(', ')}
                                </span>
                              )}
                            </div>
                          )}
                          {/* Claude Re-evaluation details */}
                          {log.type === 'claude_reeval' && (
                            <div className="space-y-1">
                              <div className="flex flex-wrap gap-2 items-center">
                                <span className={cn('px-2 py-0.5 rounded font-bold text-sm',
                                  log.details.decision === 'HOLD' ? 'bg-green-500/20 text-green-400' :
                                  log.details.decision === 'CLOSE' ? 'bg-red-500/20 text-red-400' :
                                  log.details.decision === 'TIGHTEN' ? 'bg-yellow-500/20 text-yellow-400' :
                                  'bg-slate-600/50 text-slate-400'
                                )}>
                                  {log.details.decision}
                                </span>
                                <span className="text-slate-500">
                                  #{log.details.ticket} {log.details.direction?.toUpperCase()}
                                </span>
                                <span className={cn('font-mono text-sm',
                                  (log.details.r_multiple ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                                )}>
                                  {(log.details.r_multiple ?? 0) >= 0 ? '+' : ''}{(log.details.r_multiple ?? 0).toFixed(2)}R
                                </span>
                                <span className={cn('font-mono text-sm',
                                  (log.details.pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                                )}>
                                  ${(log.details.pnl ?? 0).toFixed(2)}
                                </span>
                                {log.details.hours_open !== undefined && (
                                  <span className="text-slate-500 text-xs">
                                    {log.details.hours_open.toFixed(1)}h open
                                  </span>
                                )}
                              </div>
                              {log.details.reasoning && (
                                <ExpandableReasoning text={log.details.reasoning} />
                              )}
                            </div>
                          )}
                          {/* Fibonacci details */}
                          {log.type === 'fibonacci' && (
                            <div className="flex flex-wrap gap-2">
                              <span className={cn('px-2 py-0.5 rounded',
                                log.details.in_ote ? 'bg-teal-500/20 text-teal-400' : 'bg-slate-600/50 text-slate-400'
                              )}>
                                {log.details.in_ote ? '✅ IN OTE ZONE' : 'Outside OTE'}
                              </span>
                              {log.details.zone && (
                                <span className="px-2 py-0.5 rounded bg-slate-600/50 text-slate-400">
                                  Zone: {log.details.zone.toUpperCase()}
                                </span>
                              )}
                              {log.details.direction && (
                                <span className={cn('px-2 py-0.5 rounded',
                                  log.details.direction === 'bullish' ? 'text-green-400' : 'text-red-400'
                                )}>
                                  {log.details.direction === 'bullish' ? '↑' : '↓'} {log.details.direction.toUpperCase()}
                                </span>
                              )}
                            </div>
                          )}
                          {/* Signal/trade details */}
                          {log.type !== 'mtf' && log.type !== 'fibonacci' && (
                            <>
                              {log.details.direction && (
                                <span className={cn(
                                  'mr-3',
                                  log.details.direction === 'long' ? 'text-green-400' : 
                                  log.details.direction === 'short' ? 'text-red-400' : 'text-slate-400'
                                )}>
                                  {log.details.direction === 'long' ? '↑ LONG' : 
                                   log.details.direction === 'short' ? '↓ SHORT' : log.details.direction}
                                </span>
                              )}
                              {log.details.confidence !== undefined && (
                                <span className="mr-3">Confidence: {(log.details.confidence * 100).toFixed(0)}%</span>
                              )}
                              {log.details.reasoning && (
                                <ExpandableReasoning text={log.details.reasoning} />
                              )}
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div ref={logsEndRef} />
        </div>
      </div>

      {/* Error Alert */}
      {status?.last_error && (
        <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm text-red-400 font-medium">Last Error</p>
            <p className="text-sm text-red-300 mt-1">{status.last_error}</p>
          </div>
        </div>
      )}
    </div>
  )
}
