'use client'

import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  api,
  MT5Symbol,
  BacktestRun,
  IctBacktestConfig,
  ReplayBacktestConfig,
  OptimizerConfig,
} from '@/lib/api'
import {
  Play,
  Settings,
  BarChart3,
  Zap,
  Target,
  AlertCircle,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
} from 'lucide-react'

const POLL_INTERVAL_MS = 3000

export default function BacktestPage() {
  const [symbols, setSymbols] = useState<MT5Symbol[]>([])
  const [symbolsLoading, setSymbolsLoading] = useState(true)
  const [tab, setTab] = useState<'ict' | 'replay' | 'optimizer'>('ict')
  const [pastRuns, setPastRuns] = useState<BacktestRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [selectedRunDetail, setSelectedRunDetail] = useState<BacktestRun | null>(null)

  // ICT
  const [ictConfig, setIctConfig] = useState<IctBacktestConfig>({
    symbol: '',
    timeframe: 'H1',
    start_date: '2024-01-01',
    end_date: '2024-12-31',
    initial_balance: 10000,
    risk_per_trade: 0.01,
    min_risk_reward: 2,
  })
  const [ictRunning, setIctRunning] = useState(false)
  const [ictResult, setIctResult] = useState<BacktestRun | null>(null)

  // Replay
  const [replayConfig, setReplayConfig] = useState<ReplayBacktestConfig>({
    symbol: '',
    start_date: '2024-01-01',
    end_date: '2024-12-31',
    interval_hours: 4,
    max_signals: 100,
  })
  const [replayEstimate, setReplayEstimate] = useState<{ estimated_api_calls: number; estimated_cost: string } | null>(null)
  const [replayEstimating, setReplayEstimating] = useState(false)
  const [replayRunning, setReplayRunning] = useState(false)
  const [replayRunId, setReplayRunId] = useState<number | null>(null)
  const [replayResult, setReplayResult] = useState<BacktestRun | null>(null)
  const [replayProgress, setReplayProgress] = useState<{ pct: number; step: string }>({ pct: 0, step: '' })

  // Optimizer
  const [optConfig, setOptConfig] = useState<OptimizerConfig>({
    lookback_days: 180,
    n_folds: 3,
    train_ratio: 0.7,
  })
  const [optRunning, setOptRunning] = useState(false)
  const [optRunId, setOptRunId] = useState<number | null>(null)
  const [optResult, setOptResult] = useState<BacktestRun | null>(null)

  const fetchPastRuns = useCallback(async () => {
    try {
      const list = await api.listBacktestRuns({ limit: 30 })
      setPastRuns(list)
    } catch (e) {
      console.error('Failed to list backtest runs:', e)
    }
  }, [])

  useEffect(() => {
    fetchPastRuns()
  }, [fetchPastRuns])

  useEffect(() => {
    const fetchSymbols = async () => {
      let loaded = false
      try {
        const data = await api.getMarketWatchSymbols()
        if (data.symbols.length > 0) {
          setSymbols(data.symbols)
          if (!ictConfig.symbol) {
            setIctConfig((prev) => ({ ...prev, symbol: data.symbols[0].name }))
            setReplayConfig((prev) => ({ ...prev, symbol: data.symbols[0].name }))
          }
          loaded = true
        }
      } catch {}
      if (!loaded) {
        try {
          const configData = await api.getConfig()
          const fallback = configData.trading.symbols.map((s: string) => ({
            name: s,
            description: '',
            path: '',
            category: '',
            visible: true,
            tradeable: true,
          }))
          setSymbols(fallback)
          if (fallback.length > 0 && !ictConfig.symbol) {
            setIctConfig((prev) => ({ ...prev, symbol: fallback[0].name }))
            setReplayConfig((prev) => ({ ...prev, symbol: fallback[0].name }))
          }
        } catch {}
      }
      setSymbolsLoading(false)
    }
    fetchSymbols()
  }, [])

  // Poll for replay run
  useEffect(() => {
    if (!replayRunId || !replayRunning) return
    const t = setInterval(async () => {
      try {
        const run = await api.getBacktestRun(replayRunId)
        setReplayProgress({ pct: run.progress_pct ?? 0, step: run.current_step ?? '' })
        if (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') {
          setReplayRunning(false)
          setReplayRunId(null)
          setReplayResult(run)
          fetchPastRuns()
        }
      } catch {}
    }, POLL_INTERVAL_MS)
    return () => clearInterval(t)
  }, [replayRunId, replayRunning, fetchPastRuns])

  // Poll for optimizer run
  useEffect(() => {
    if (!optRunId || !optRunning) return
    const t = setInterval(async () => {
      try {
        const run = await api.getBacktestRun(optRunId)
        if (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') {
          setOptRunning(false)
          setOptRunId(null)
          setOptResult(run)
          fetchPastRuns()
        }
      } catch {}
    }, POLL_INTERVAL_MS)
    return () => clearInterval(t)
  }, [optRunId, optRunning, fetchPastRuns])

  const runIct = async () => {
    setIctRunning(true)
    setIctResult(null)
    try {
      const run = await api.startIctBacktest(ictConfig)
      setIctResult(run)
      fetchPastRuns()
    } catch (e) {
      console.error(e)
    } finally {
      setIctRunning(false)
    }
  }

  const estimateReplay = async () => {
    setReplayEstimating(true)
    setReplayEstimate(null)
    try {
      const est = await api.estimateReplayCost(replayConfig)
      setReplayEstimate({
        estimated_api_calls: est.estimated_api_calls,
        estimated_cost: est.estimated_cost,
      })
    } catch (e) {
      console.error(e)
    } finally {
      setReplayEstimating(false)
    }
  }

  const runReplay = async () => {
    setReplayResult(null)
    setReplayProgress({ pct: 0, step: 'Starting replay...' })
    try {
      const run = await api.startReplayBacktest(replayConfig)
      setReplayRunId(run.id)
      setReplayRunning(true)
    } catch (e) {
      console.error(e)
    }
  }

  const runOptimizer = async () => {
    setOptResult(null)
    try {
      const run = await api.startOptimizer(optConfig)
      setOptRunId(run.id)
      setOptRunning(true)
    } catch (e) {
      console.error(e)
    }
  }

  const loadRunDetail = async (id: number) => {
    if (selectedRunId === id && selectedRunDetail) {
      setSelectedRunId(null)
      setSelectedRunDetail(null)
      return
    }
    try {
      const run = await api.getBacktestRun(id)
      setSelectedRunId(id)
      setSelectedRunDetail(run)
    } catch (e) {
      console.error(e)
    }
  }

  const deleteRun = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api.deleteBacktestRun(id)
      if (selectedRunId === id) {
        setSelectedRunId(null)
        setSelectedRunDetail(null)
      }
      setPastRuns((prev) => prev.filter((r) => r.id !== id))
    } catch (e) {
      console.error(e)
    }
  }

  const applyOptimizerParams = async () => {
    const res = optResult?.result_json as Record<string, unknown> | undefined
    const best = res?.best_params as Record<string, unknown> | undefined
    if (!best) return
    try {
      const payload: Record<string, number> = {}
      if (best.min_confidence != null) payload.gate_min_confidence = best.min_confidence as number
      if (best.min_rr != null) payload.gate_counter_trend_rr_floor = best.min_rr as number
      if (best.cooldown_minutes != null) payload.gate_cooldown_minutes = best.cooldown_minutes as number
      await api.updateTradingConfig(payload as Parameters<typeof api.updateTradingConfig>[0])
    } catch (e) {
      console.error(e)
    }
  }

  const renderMetrics = (run: BacktestRun) => {
    const wr = run.win_rate ?? 0
    const trades = run.total_trades ?? 0
    const wins = run.result_json && typeof run.result_json.wins === 'number' ? run.result_json.wins : 0
    const losses = run.result_json && typeof run.result_json.losses === 'number' ? run.result_json.losses : 0
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400">Total Trades</p>
            <p className="text-2xl font-bold">{trades}</p>
            <p className="text-xs text-slate-400">{wins}W / {losses}L</p>
          </div>
        </div>
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400">Win Rate</p>
            <p className={cn('text-2xl font-bold', wr >= 50 ? 'text-green-400' : 'text-red-400')}>
              {wr.toFixed(1)}%
            </p>
          </div>
        </div>
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400">Net Profit</p>
            <p className={cn('text-2xl font-bold', (run.net_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400')}>
              {run.net_profit != null ? `$${run.net_profit.toFixed(2)}` : '—'}
            </p>
          </div>
        </div>
        <div className="card">
          <div className="card-body">
            <p className="text-sm text-slate-400">Sharpe / PF</p>
            <p className="text-2xl font-bold">
              {(run.sharpe_ratio ?? 0).toFixed(2)} / {(run.profit_factor ?? 0).toFixed(2)}
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Backtesting</h1>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-slate-700 pb-2">
        {(['ict', 'replay', 'optimizer'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-4 py-2 rounded-t-lg font-medium',
              tab === t ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
            )}
          >
            {t === 'ict' && 'ICT Strategy'}
            {t === 'replay' && 'Claude Replay'}
            {t === 'optimizer' && 'Walk-Forward Optimizer'}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Config panel */}
        <div className="lg:col-span-1">
          <div className="card">
            <div className="card-header">
              <h2 className="font-semibold flex items-center gap-2">
                <Settings className="w-4 h-4" />
                Configuration
              </h2>
            </div>
            <div className="card-body space-y-4">
              {tab === 'ict' && (
                <>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Symbol</label>
                    <select
                      value={ictConfig.symbol}
                      onChange={(e) => setIctConfig((c) => ({ ...c, symbol: e.target.value }))}
                      className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      disabled={symbolsLoading}
                    >
                      {symbols.map((s) => (
                        <option key={s.name} value={s.name}>{s.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Timeframe</label>
                    <select
                      value={ictConfig.timeframe}
                      onChange={(e) => setIctConfig((c) => ({ ...c, timeframe: e.target.value }))}
                      className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                    >
                      <option>M15</option><option>H1</option><option>H4</option><option>D1</option>
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-sm text-slate-400 mb-1">Start</label>
                      <input
                        type="date"
                        value={ictConfig.start_date}
                        onChange={(e) => setIctConfig((c) => ({ ...c, start_date: e.target.value }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-slate-400 mb-1">End</label>
                      <input
                        type="date"
                        value={ictConfig.end_date}
                        onChange={(e) => setIctConfig((c) => ({ ...c, end_date: e.target.value }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Initial Balance ($)</label>
                    <input
                      type="number"
                      value={ictConfig.initial_balance ?? 10000}
                      onChange={(e) => setIctConfig((c) => ({ ...c, initial_balance: parseFloat(e.target.value) }))}
                      className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Risk/Trade & Min R:R</label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        step="0.01"
                        value={(ictConfig.risk_per_trade ?? 0.01) * 100}
                        onChange={(e) => setIctConfig((c) => ({ ...c, risk_per_trade: parseFloat(e.target.value) / 100 }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                      <input
                        type="number"
                        step="0.1"
                        value={ictConfig.min_risk_reward ?? 2}
                        onChange={(e) => setIctConfig((c) => ({ ...c, min_risk_reward: parseFloat(e.target.value) }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                  </div>
                  <button
                    onClick={runIct}
                    disabled={ictRunning || !ictConfig.symbol}
                    className="btn btn-primary w-full flex items-center justify-center gap-2"
                  >
                    {ictRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    {ictRunning ? 'Running...' : 'Run ICT Backtest'}
                  </button>
                </>
              )}

              {tab === 'replay' && (
                <>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Symbol</label>
                    <select
                      value={replayConfig.symbol}
                      onChange={(e) => setReplayConfig((c) => ({ ...c, symbol: e.target.value }))}
                      className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      disabled={symbolsLoading}
                    >
                      {symbols.map((s) => (
                        <option key={s.name} value={s.name}>{s.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-sm text-slate-400 mb-1">Start</label>
                      <input
                        type="date"
                        value={replayConfig.start_date}
                        onChange={(e) => setReplayConfig((c) => ({ ...c, start_date: e.target.value }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-slate-400 mb-1">End</label>
                      <input
                        type="date"
                        value={replayConfig.end_date}
                        onChange={(e) => setReplayConfig((c) => ({ ...c, end_date: e.target.value }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Interval (h) / Max signals</label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        step="0.5"
                        value={replayConfig.interval_hours ?? 4}
                        onChange={(e) => setReplayConfig((c) => ({ ...c, interval_hours: parseFloat(e.target.value) }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                      <input
                        type="number"
                        value={replayConfig.max_signals ?? 100}
                        onChange={(e) => setReplayConfig((c) => ({ ...c, max_signals: parseInt(e.target.value, 10) }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={estimateReplay}
                      disabled={replayEstimating || !replayConfig.symbol}
                      className="btn flex-1 flex items-center justify-center gap-2 bg-slate-600 hover:bg-slate-500"
                    >
                      {replayEstimating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                      Estimate Cost
                    </button>
                    <button
                      onClick={runReplay}
                      disabled={replayRunning || !replayConfig.symbol}
                      className="btn btn-primary flex-1 flex items-center justify-center gap-2"
                    >
                      {replayRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                      Run Replay
                    </button>
                  </div>
                  {replayEstimate && (
                    <p className="text-sm text-slate-400">
                      ~{replayEstimate.estimated_api_calls} calls, {replayEstimate.estimated_cost}
                    </p>
                  )}
                </>
              )}

              {tab === 'optimizer' && (
                <>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Lookback (days)</label>
                    <input
                      type="number"
                      value={optConfig.lookback_days ?? 180}
                      onChange={(e) => setOptConfig((c) => ({ ...c, lookback_days: parseInt(e.target.value, 10) }))}
                      className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Folds / Train ratio</label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        min={2}
                        value={optConfig.n_folds ?? 3}
                        onChange={(e) => setOptConfig((c) => ({ ...c, n_folds: parseInt(e.target.value, 10) }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                      <input
                        type="number"
                        step="0.1"
                        min={0.5}
                        max={0.9}
                        value={optConfig.train_ratio ?? 0.7}
                        onChange={(e) => setOptConfig((c) => ({ ...c, train_ratio: parseFloat(e.target.value) }))}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                      />
                    </div>
                  </div>
                  <button
                    onClick={runOptimizer}
                    disabled={optRunning}
                    className="btn btn-primary w-full flex items-center justify-center gap-2"
                  >
                    {optRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Target className="w-4 h-4" />}
                    {optRunning ? 'Running...' : 'Run Optimizer'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Results panel */}
        <div className="lg:col-span-2">
          {tab === 'ict' && (
            ictRunning ? (
              <div className="card h-full flex items-center justify-center min-h-[200px]">
                <div className="text-center">
                  <Loader2 className="w-12 h-12 animate-spin mx-auto mb-2 text-blue-400" />
                  <p>Running ICT backtest...</p>
                </div>
              </div>
            ) : ictResult ? (
              <div className="space-y-4">
                {renderMetrics(ictResult)}
                {ictResult.error_message && (
                  <p className="text-red-400 text-sm">{ictResult.error_message}</p>
                )}
              </div>
            ) : (
              <div className="card h-full flex items-center justify-center min-h-[200px] text-slate-400">
                <div className="text-center">
                  <BarChart3 className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>Configure and run ICT backtest</p>
                </div>
              </div>
            )
          )}

          {tab === 'replay' && (
            replayRunning ? (
              <div className="card h-full flex flex-col items-center justify-center min-h-[200px] gap-4 p-6">
                <Loader2 className="w-10 h-10 animate-spin text-blue-400" />
                <p className="text-lg font-medium">Replay Backtest Running</p>
                <div className="w-full max-w-md">
                  <div className="flex justify-between text-sm text-slate-400 mb-1">
                    <span>{replayProgress.step || 'Starting replay...'}</span>
                    <span>{replayProgress.pct}%</span>
                  </div>
                  <div className="w-full bg-slate-700 rounded-full h-3 overflow-hidden">
                    <div
                      className="bg-blue-500 h-3 rounded-full transition-all duration-500"
                      style={{ width: `${replayProgress.pct}%` }}
                    />
                  </div>
                </div>
                <p className="text-xs text-slate-500">Each snapshot sends a chart to Claude for analysis (~15-30s per call)</p>
              </div>
            ) : replayResult ? (
              <div className="space-y-4">
                {renderMetrics(replayResult)}
                {(replayResult.result_json as Record<string, unknown>)?.learnings_stored != null && (
                  <p className="text-sm text-slate-400">
                    {(replayResult.result_json as Record<string, unknown>).learnings_stored as number} learnings captured
                  </p>
                )}
                {replayResult.error_message && (
                  <p className="text-red-400 text-sm">{replayResult.error_message}</p>
                )}
              </div>
            ) : (
              <div className="card h-full flex items-center justify-center min-h-[200px] text-slate-400">
                <div className="text-center">
                  <Zap className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>Estimate cost, then run Claude replay</p>
                </div>
              </div>
            )
          )}

          {tab === 'optimizer' && (
            optRunning ? (
              <div className="card h-full flex items-center justify-center min-h-[200px]">
                <div className="text-center">
                  <Loader2 className="w-12 h-12 animate-spin mx-auto mb-2 text-blue-400" />
                  <p>Walk-forward optimization running…</p>
                </div>
              </div>
            ) : optResult ? (
              <div className="space-y-4">
                {optResult.result_json && (
                  <>
                    <div className="card">
                      <div className="card-header">
                        <h2 className="font-semibold">Best parameters</h2>
                      </div>
                      <div className="card-body">
                        <pre className="text-sm bg-slate-800 p-3 rounded overflow-auto">
                          {JSON.stringify((optResult.result_json as Record<string, unknown>).best_params ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>
                    <div className="flex gap-4">
                      <p className="text-sm">
                        In-sample Sharpe: {(optResult.result_json as Record<string, unknown>)?.in_sample_sharpe ?? '—'}
                      </p>
                      <p className="text-sm">
                        Out-of-sample Sharpe: {(optResult.result_json as Record<string, unknown>)?.out_of_sample_sharpe ?? '—'}
                      </p>
                    </div>
                    <button
                      onClick={applyOptimizerParams}
                      className="btn btn-primary flex items-center gap-2"
                    >
                      Apply to Config
                    </button>
                  </>
                )}
                {optResult.error_message && (
                  <p className="text-red-400 text-sm">{optResult.error_message}</p>
                )}
              </div>
            ) : (
              <div className="card h-full flex items-center justify-center min-h-[200px] text-slate-400">
                <div className="text-center">
                  <Target className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>Run walk-forward optimizer on historical trades</p>
                </div>
              </div>
            )
          )}
        </div>
      </div>

      {/* Past runs */}
      <div className="card">
        <div className="card-header">
          <h2 className="font-semibold">Past runs</h2>
        </div>
        <div className="card-body">
          {pastRuns.length === 0 ? (
            <p className="text-slate-400 text-sm">No backtest runs yet.</p>
          ) : (
            <div className="space-y-1">
              {pastRuns.map((run) => (
                <div
                  key={run.id}
                  className="flex items-center gap-2 py-2 px-3 rounded-lg hover:bg-slate-700/50 cursor-pointer"
                  onClick={() => loadRunDetail(run.id)}
                >
                  {selectedRunId === run.id ? (
                    <ChevronDown className="w-4 h-4 text-slate-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-slate-400" />
                  )}
                  <span
                    className={cn(
                      'text-xs px-2 py-0.5 rounded',
                      run.run_type === 'ict' && 'bg-blue-500/20',
                      run.run_type === 'replay' && 'bg-amber-500/20',
                      run.run_type === 'optimizer' && 'bg-green-500/20'
                    )}
                  >
                    {run.run_type}
                  </span>
                  <span className="text-slate-300">{run.symbol ?? '—'}</span>
                  <span className="text-slate-500 text-sm">
                    {run.start_date} → {run.end_date}
                  </span>
                  <span
                    className={cn(
                      'text-xs ml-auto',
                      run.status === 'completed' && 'text-green-400',
                      run.status === 'failed' && 'text-red-400',
                      run.status === 'running' && 'text-amber-400'
                    )}
                  >
                    {run.status}
                  </span>
                  {run.win_rate != null && (
                    <span className="text-slate-400 text-sm">WR {run.win_rate.toFixed(0)}%</span>
                  )}
                  <button
                    type="button"
                    onClick={(e) => deleteRun(run.id, e)}
                    className="p-1 text-slate-500 hover:text-red-400"
                    aria-label="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {selectedRunDetail && (
            <div className="mt-4 pt-4 border-t border-slate-700">
              {renderMetrics(selectedRunDetail)}
              {selectedRunDetail.error_message && (
                <p className="text-red-400 text-sm mt-2">{selectedRunDetail.error_message}</p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
        <div>
          <p className="text-sm text-yellow-300 font-medium">Backtest disclaimer</p>
          <p className="text-sm text-yellow-200/70 mt-1">
            Past performance does not guarantee future results. Use demo accounts before live trading.
          </p>
        </div>
      </div>
    </div>
  )
}
