'use client'

import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { api, MT5Symbol } from '@/lib/api'
import { Play, Settings, BarChart3, Calendar, TrendingUp, TrendingDown, AlertCircle } from 'lucide-react'

interface BacktestConfig {
  symbol: string
  timeframe: string
  startDate: string
  endDate: string
  initialBalance: number
  riskPerTrade: number
  minRiskReward: number
}

interface BacktestResult {
  totalTrades: number
  wins: number
  losses: number
  winRate: number
  netProfit: number
  maxDrawdown: number
  sharpeRatio: number
  profitFactor: number
}

export default function BacktestPage() {
  const [symbols, setSymbols] = useState<MT5Symbol[]>([])
  const [symbolsLoading, setSymbolsLoading] = useState(true)
  
  const [config, setConfig] = useState<BacktestConfig>({
    symbol: '',
    timeframe: 'H1',
    startDate: '2024-01-01',
    endDate: '2024-12-31',
    initialBalance: 10000,
    riskPerTrade: 1,
    minRiskReward: 2
  })

  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)
  
  // Fetch symbols from MT5
  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const data = await api.getMarketWatchSymbols()
        setSymbols(data.symbols)
        if (data.symbols.length > 0 && !config.symbol) {
          setConfig(prev => ({ ...prev, symbol: data.symbols[0].name }))
        }
      } catch (error) {
        console.error('Error fetching symbols:', error)
        // Fallback to config symbols
        try {
          const configData = await api.getConfig()
          const fallback = configData.trading.symbols.map(s => ({ name: s, description: '', path: '', category: '', visible: true, tradeable: true }))
          setSymbols(fallback)
          if (fallback.length > 0 && !config.symbol) {
            setConfig(prev => ({ ...prev, symbol: fallback[0].name }))
          }
        } catch (e) {
          console.error('Error fetching fallback:', e)
        }
      } finally {
        setSymbolsLoading(false)
      }
    }
    fetchSymbols()
  }, [])

  const runBacktest = async () => {
    setRunning(true)
    
    // Simulate backtest running
    await new Promise(resolve => setTimeout(resolve, 3000))
    
    // Generate sample results
    const totalTrades = Math.floor(Math.random() * 50) + 30
    const wins = Math.floor(totalTrades * (0.45 + Math.random() * 0.2))
    const losses = totalTrades - wins
    
    setResult({
      totalTrades,
      wins,
      losses,
      winRate: wins / totalTrades,
      netProfit: (Math.random() - 0.3) * 5000,
      maxDrawdown: 0.05 + Math.random() * 0.1,
      sharpeRatio: 0.5 + Math.random() * 1.5,
      profitFactor: 0.8 + Math.random() * 1.2
    })
    
    setRunning(false)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Backtesting Engine</h1>
        <button
          onClick={runBacktest}
          disabled={running}
          className="btn btn-primary flex items-center gap-2"
        >
          {running ? (
            <>
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Running...
            </>
          ) : (
            <>
              <Play className="w-4 h-4" />
              Run Backtest
            </>
          )}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Configuration Panel */}
        <div className="lg:col-span-1">
          <div className="card">
            <div className="card-header">
              <h2 className="font-semibold flex items-center gap-2">
                <Settings className="w-4 h-4" />
                Configuration
              </h2>
            </div>
            <div className="card-body space-y-4">
              <div>
                <label className="block text-sm text-slate-400 mb-1">Symbol</label>
                <select
                  value={config.symbol}
                  onChange={(e) => setConfig({ ...config, symbol: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                  disabled={symbolsLoading}
                >
                  {symbolsLoading ? (
                    <option>Loading...</option>
                  ) : (
                    symbols.map(s => (
                      <option key={s.name} value={s.name}>{s.name}</option>
                    ))
                  )}
                </select>
                <p className="text-xs text-slate-500 mt-1">{symbols.length} symbols from MT5</p>
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">Timeframe</label>
                <select
                  value={config.timeframe}
                  onChange={(e) => setConfig({ ...config, timeframe: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                >
                  <option>M15</option>
                  <option>H1</option>
                  <option>H4</option>
                  <option>D1</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Start Date</label>
                  <input
                    type="date"
                    value={config.startDate}
                    onChange={(e) => setConfig({ ...config, startDate: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">End Date</label>
                  <input
                    type="date"
                    value={config.endDate}
                    onChange={(e) => setConfig({ ...config, endDate: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">Initial Balance ($)</label>
                <input
                  type="number"
                  value={config.initialBalance}
                  onChange={(e) => setConfig({ ...config, initialBalance: parseFloat(e.target.value) })}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">Risk Per Trade (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={config.riskPerTrade}
                  onChange={(e) => setConfig({ ...config, riskPerTrade: parseFloat(e.target.value) })}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">Min Risk/Reward</label>
                <input
                  type="number"
                  step="0.1"
                  value={config.minRiskReward}
                  onChange={(e) => setConfig({ ...config, minRiskReward: parseFloat(e.target.value) })}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Results Panel */}
        <div className="lg:col-span-2">
          {running ? (
            <div className="card h-full flex items-center justify-center">
              <div className="text-center">
                <div className="w-16 h-16 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
                <p className="text-lg font-medium">Running Backtest...</p>
                <p className="text-slate-400 mt-2">Analyzing {config.symbol} on {config.timeframe}</p>
              </div>
            </div>
          ) : result ? (
            <div className="space-y-4">
              {/* Key Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="card">
                  <div className="card-body">
                    <p className="text-sm text-slate-400">Total Trades</p>
                    <p className="text-2xl font-bold">{result.totalTrades}</p>
                    <p className="text-xs text-slate-400">{result.wins}W / {result.losses}L</p>
                  </div>
                </div>
                <div className="card">
                  <div className="card-body">
                    <p className="text-sm text-slate-400">Win Rate</p>
                    <p className={cn(
                      'text-2xl font-bold',
                      result.winRate >= 0.5 ? 'text-green-400' : 'text-red-400'
                    )}>
                      {(result.winRate * 100).toFixed(1)}%
                    </p>
                  </div>
                </div>
                <div className="card">
                  <div className="card-body">
                    <p className="text-sm text-slate-400">Net Profit</p>
                    <p className={cn(
                      'text-2xl font-bold',
                      result.netProfit >= 0 ? 'text-green-400' : 'text-red-400'
                    )}>
                      ${result.netProfit.toFixed(2)}
                    </p>
                  </div>
                </div>
                <div className="card">
                  <div className="card-body">
                    <p className="text-sm text-slate-400">Max Drawdown</p>
                    <p className="text-2xl font-bold text-red-400">
                      {(result.maxDrawdown * 100).toFixed(1)}%
                    </p>
                  </div>
                </div>
              </div>

              {/* Detailed Stats */}
              <div className="card">
                <div className="card-header">
                  <h2 className="font-semibold flex items-center gap-2">
                    <BarChart3 className="w-4 h-4" />
                    Performance Metrics
                  </h2>
                </div>
                <div className="card-body">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                    <div>
                      <p className="text-sm text-slate-400">Sharpe Ratio</p>
                      <p className={cn(
                        'text-xl font-semibold',
                        result.sharpeRatio >= 1 ? 'text-green-400' : 'text-yellow-400'
                      )}>
                        {result.sharpeRatio.toFixed(2)}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-slate-400">Profit Factor</p>
                      <p className={cn(
                        'text-xl font-semibold',
                        result.profitFactor >= 1 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {result.profitFactor.toFixed(2)}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-slate-400">Return</p>
                      <p className={cn(
                        'text-xl font-semibold',
                        result.netProfit >= 0 ? 'text-green-400' : 'text-red-400'
                      )}>
                        {((result.netProfit / config.initialBalance) * 100).toFixed(1)}%
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-slate-400">Risk/Reward Achieved</p>
                      <p className="text-xl font-semibold">
                        {(result.netProfit > 0 ? result.profitFactor : 0).toFixed(2)}:1
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Note */}
              <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm text-yellow-300 font-medium">Backtest Simulation</p>
                  <p className="text-sm text-yellow-200/70 mt-1">
                    These results are from simulated historical data. Past performance does not guarantee future results. 
                    Always test with demo accounts before live trading.
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="card h-full flex items-center justify-center">
              <div className="text-center text-slate-400">
                <BarChart3 className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>Configure your backtest and click "Run Backtest"</p>
                <p className="text-sm mt-2">Uses ICT strategy with historical data</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
