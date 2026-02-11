'use client'

import { useEffect, useState } from 'react'
import { api, FullAnalysis, MT5Symbol } from '@/lib/api'
import { cn } from '@/lib/utils'
import { RefreshCw, ArrowUpRight, ArrowDownRight, ChevronDown } from 'lucide-react'

const timeframes = ['M15', 'H1', 'H4', 'D1']

export default function AnalysisPage() {
  const [symbols, setSymbols] = useState<MT5Symbol[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const [selectedTimeframe, setSelectedTimeframe] = useState('H1')
  const [analysis, setAnalysis] = useState<FullAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [symbolsLoading, setSymbolsLoading] = useState(true)
  const [showSymbolDropdown, setShowSymbolDropdown] = useState(false)

  // Fetch symbols from MT5 Market Watch
  const fetchSymbols = async () => {
    setSymbolsLoading(true)
    try {
      const data = await api.getMarketWatchSymbols()
      setSymbols(data.symbols)
      // Set first symbol as selected if none selected
      if (data.symbols.length > 0 && !selectedSymbol) {
        setSelectedSymbol(data.symbols[0].name)
      }
    } catch (error) {
      console.error('Error fetching symbols:', error)
      // Fallback to configured symbols
      try {
        const config = await api.getConfig()
        const fallbackSymbols = config.trading.symbols.map(s => ({ 
          name: s, 
          description: '', 
          path: '', 
          category: '', 
          visible: true, 
          tradeable: true 
        }))
        setSymbols(fallbackSymbols)
        if (fallbackSymbols.length > 0 && !selectedSymbol) {
          setSelectedSymbol(fallbackSymbols[0].name)
        }
      } catch (e) {
        console.error('Error fetching fallback symbols:', e)
      }
    } finally {
      setSymbolsLoading(false)
    }
  }

  const fetchAnalysis = async () => {
    if (!selectedSymbol) return
    setLoading(true)
    try {
      const data = await api.getAnalysis(selectedSymbol, selectedTimeframe)
      setAnalysis(data)
    } catch (error) {
      console.error('Error fetching analysis:', error)
    } finally {
      setLoading(false)
    }
  }

  // Fetch symbols on mount
  useEffect(() => {
    fetchSymbols()
  }, [])

  // Fetch analysis when symbol or timeframe changes
  useEffect(() => {
    if (selectedSymbol) {
      fetchAnalysis()
    }
  }, [selectedSymbol, selectedTimeframe])

  // Group symbols by category based on path
  const groupedSymbols = symbols.reduce((acc, symbol) => {
    const category = symbol.path.split('\\')[0] || 'Other'
    if (!acc[category]) acc[category] = []
    acc[category].push(symbol)
    return acc
  }, {} as Record<string, MT5Symbol[]>)

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          {/* Symbol Dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowSymbolDropdown(!showSymbolDropdown)}
              disabled={symbolsLoading}
              className="flex items-center gap-2 px-4 py-2 bg-slate-800 rounded-lg text-sm font-medium hover:bg-slate-700 transition-colors min-w-[140px]"
            >
              {symbolsLoading ? (
                <span className="text-slate-400">Loading...</span>
              ) : (
                <>
                  <span className="text-white">{selectedSymbol || 'Select Symbol'}</span>
                  <ChevronDown className={cn('w-4 h-4 text-slate-400 transition-transform', showSymbolDropdown && 'rotate-180')} />
                </>
              )}
            </button>
            
            {showSymbolDropdown && !symbolsLoading && (
              <div className="absolute top-full left-0 mt-1 w-64 bg-slate-800 rounded-lg shadow-xl border border-slate-700 z-50 max-h-80 overflow-auto">
                {Object.entries(groupedSymbols).map(([category, categorySymbols]) => (
                  <div key={category}>
                    <div className="px-3 py-1.5 text-xs font-semibold text-slate-400 uppercase bg-slate-900/50 sticky top-0">
                      {category}
                    </div>
                    {categorySymbols.map((symbol) => (
                      <button
                        key={symbol.name}
                        onClick={() => {
                          setSelectedSymbol(symbol.name)
                          setShowSymbolDropdown(false)
                        }}
                        className={cn(
                          'w-full px-3 py-2 text-left text-sm hover:bg-slate-700 transition-colors flex justify-between items-center',
                          selectedSymbol === symbol.name && 'bg-blue-600/20 text-blue-400'
                        )}
                      >
                        <span className="font-medium">{symbol.name}</span>
                        {symbol.description && (
                          <span className="text-xs text-slate-500 truncate ml-2 max-w-[120px]">
                            {symbol.description}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
          
          {/* Timeframe Buttons */}
          <div className="flex gap-1 p-1 bg-slate-800 rounded-lg">
            {timeframes.map((tf) => (
              <button
                key={tf}
                onClick={() => setSelectedTimeframe(tf)}
                className={cn(
                  'px-3 py-2 text-sm font-medium rounded-md transition-colors',
                  selectedTimeframe === tf
                    ? 'bg-slate-600 text-white'
                    : 'text-slate-400 hover:text-white hover:bg-slate-700'
                )}
              >
                {tf}
              </button>
            ))}
          </div>
          
          {/* Symbol count badge */}
          <span className="text-xs text-slate-500">
            {symbols.length} symbols from MT5
          </span>
        </div>
        <button
          onClick={fetchAnalysis}
          disabled={loading || !selectedSymbol}
          className="btn btn-secondary flex items-center gap-2"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          Refresh
        </button>
      </div>
      
      {/* Click outside to close dropdown */}
      {showSymbolDropdown && (
        <div 
          className="fixed inset-0 z-40" 
          onClick={() => setShowSymbolDropdown(false)}
        />
      )}

      {loading ? (
        <div className="h-96 flex items-center justify-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
        </div>
      ) : analysis ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Market Structure */}
          <div className="card">
            <div className="card-header">
              <h2 className="font-semibold">Market Structure</h2>
            </div>
            <div className="card-body">
              <div className="flex items-center gap-4 mb-4">
                <span className="text-slate-400">Trend:</span>
                <span className={cn(
                  'flex items-center gap-1 font-medium',
                  analysis.market_structure.trend === 'bullish' && 'text-green-400',
                  analysis.market_structure.trend === 'bearish' && 'text-red-400',
                  analysis.market_structure.trend === 'ranging' && 'text-yellow-400'
                )}>
                  {analysis.market_structure.trend === 'bullish' && <ArrowUpRight className="w-4 h-4" />}
                  {analysis.market_structure.trend === 'bearish' && <ArrowDownRight className="w-4 h-4" />}
                  {analysis.market_structure.trend.toUpperCase()}
                </span>
              </div>
              {analysis.market_structure.last_structure_break && (
                <div className="flex items-center gap-4 mb-4">
                  <span className="text-slate-400">Last Break:</span>
                  <span className="font-mono">{analysis.market_structure.last_structure_break}</span>
                </div>
              )}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-slate-400 mb-2">Swing Highs</p>
                  <div className="space-y-1">
                    {analysis.market_structure.swing_highs.slice(-5).map((sh, i) => (
                      <div key={i} className="text-sm font-mono text-green-400">
                        {sh.price.toFixed(5)}
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-sm text-slate-400 mb-2">Swing Lows</p>
                  <div className="space-y-1">
                    {analysis.market_structure.swing_lows.slice(-5).map((sl, i) => (
                      <div key={i} className="text-sm font-mono text-red-400">
                        {sl.price.toFixed(5)}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* OTE Zone */}
          {analysis.ote && (
            <div className="card">
              <div className="card-header">
                <h2 className="font-semibold">Optimal Trade Entry</h2>
              </div>
              <div className="card-body">
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Price Zone:</span>
                    <span className={cn(
                      'font-medium',
                      analysis.ote.price_zone === 'premium' && 'text-red-400',
                      analysis.ote.price_zone === 'discount' && 'text-green-400',
                      analysis.ote.price_zone === 'equilibrium' && 'text-yellow-400'
                    )}>
                      {analysis.ote.price_zone.toUpperCase()}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">In OTE Zone:</span>
                    <span className={analysis.ote.in_ote ? 'text-green-400' : 'text-slate-400'}>
                      {analysis.ote.in_ote ? 'Yes' : 'No'}
                    </span>
                  </div>
                  <div className="pt-2 border-t border-slate-700">
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">OTE Top (79%):</span>
                      <span className="font-mono">{analysis.ote.ote_top.toFixed(5)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">OTE Bottom (62%):</span>
                      <span className="font-mono">{analysis.ote.ote_bottom.toFixed(5)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">Equilibrium (50%):</span>
                      <span className="font-mono">{analysis.ote.equilibrium.toFixed(5)}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Fair Value Gaps */}
          <div className="card">
            <div className="card-header">
              <h2 className="font-semibold">Fair Value Gaps ({analysis.fvg_zones.length})</h2>
            </div>
            <div className="card-body p-0 max-h-64 overflow-auto">
              {analysis.fvg_zones.length === 0 ? (
                <div className="p-4 text-center text-slate-400">No active FVGs</div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-xs text-slate-400 uppercase bg-slate-700/50 sticky top-0">
                    <tr>
                      <th className="px-4 py-2 text-left">Type</th>
                      <th className="px-4 py-2 text-right">Top</th>
                      <th className="px-4 py-2 text-right">Bottom</th>
                      <th className="px-4 py-2 text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {analysis.fvg_zones.map((fvg, i) => (
                      <tr key={i}>
                        <td className="px-4 py-2">
                          <span className={cn(
                            'text-xs px-2 py-0.5 rounded',
                            fvg.type === 'bullish' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                          )}>
                            {fvg.type}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right font-mono">{fvg.top.toFixed(5)}</td>
                        <td className="px-4 py-2 text-right font-mono">{fvg.bottom.toFixed(5)}</td>
                        <td className="px-4 py-2 text-center text-xs text-slate-400">{fvg.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Order Blocks */}
          <div className="card">
            <div className="card-header">
              <h2 className="font-semibold">Order Blocks ({analysis.order_blocks.length})</h2>
            </div>
            <div className="card-body p-0 max-h-64 overflow-auto">
              {analysis.order_blocks.length === 0 ? (
                <div className="p-4 text-center text-slate-400">No active OBs</div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-xs text-slate-400 uppercase bg-slate-700/50 sticky top-0">
                    <tr>
                      <th className="px-4 py-2 text-left">Type</th>
                      <th className="px-4 py-2 text-right">Zone</th>
                      <th className="px-4 py-2 text-right">Strength</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {analysis.order_blocks.map((ob, i) => (
                      <tr key={i}>
                        <td className="px-4 py-2">
                          <span className={cn(
                            'text-xs px-2 py-0.5 rounded',
                            ob.type === 'bullish' ? 'bg-blue-500/20 text-blue-400' : 'bg-orange-500/20 text-orange-400'
                          )}>
                            {ob.type}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right font-mono text-xs">
                          {ob.top.toFixed(5)} - {ob.bottom.toFixed(5)}
                        </td>
                        <td className="px-4 py-2 text-right">{(ob.strength * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* AMD Analysis */}
          {analysis.amd && (
            <div className="card lg:col-span-2">
              <div className="card-header">
                <h2 className="font-semibold">Power of 3 (AMD)</h2>
              </div>
              <div className="card-body">
                <div className="flex items-center gap-8">
                  <div>
                    <span className="text-slate-400 text-sm">Current Phase:</span>
                    <p className={cn(
                      'text-lg font-medium mt-1',
                      analysis.amd.current_phase === 'accumulation' && 'text-yellow-400',
                      analysis.amd.current_phase === 'manipulation' && 'text-red-400',
                      analysis.amd.current_phase === 'distribution' && 'text-green-400'
                    )}>
                      {analysis.amd.current_phase.toUpperCase()}
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-400 text-sm">Judas Swing:</span>
                    <p className="text-lg font-medium mt-1">
                      {analysis.amd.judas_swing_detected ? (
                        <span className={analysis.amd.judas_direction === 'bullish' ? 'text-green-400' : 'text-red-400'}>
                          Detected ({analysis.amd.judas_direction})
                        </span>
                      ) : (
                        <span className="text-slate-400">Not Detected</span>
                      )}
                    </p>
                  </div>
                  {analysis.amd.expected_direction && (
                    <div>
                      <span className="text-slate-400 text-sm">Expected Move:</span>
                      <p className={cn(
                        'text-lg font-medium mt-1 flex items-center gap-1',
                        analysis.amd.expected_direction === 'bullish' ? 'text-green-400' : 'text-red-400'
                      )}>
                        {analysis.amd.expected_direction === 'bullish' ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownRight className="w-5 h-5" />}
                        {analysis.amd.expected_direction.toUpperCase()}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="text-center text-slate-400">No analysis data available</div>
      )}
    </div>
  )
}
