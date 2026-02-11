'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { BookOpen, ChevronRight } from 'lucide-react'

const strategyDocs = [
  {
    id: 'ict',
    title: 'ICT Strategy',
    description: 'Complete Inner Circle Trading methodology',
    sections: [
      'Market structure analysis (BOS, CHoCH, MSS)',
      'Order blocks (regular, breaker, mitigation)',
      'Liquidity concepts (buy-side, sell-side, sweeps)',
      'Optimal Trade Entry (OTE) with Fibonacci',
      'Kill zones and session timing',
      'Multi-timeframe analysis workflow'
    ]
  },
  {
    id: 'fvg',
    title: 'Fair Value Gaps',
    description: 'FVG identification and trading rules',
    sections: [
      'Three-candle pattern detection',
      'Bullish vs bearish FVG identification',
      'Entry/exit rules and confirmation',
      'Mitigation methods (close, wick, average)',
      'Risk-reward calculations',
      'Inverse FVG patterns'
    ]
  },
  {
    id: 'market-maker',
    title: 'Market Maker Concepts',
    description: 'Institutional order flow analysis',
    sections: [
      'Institutional order flow mechanics',
      'Liquidity hunting patterns',
      'Stop hunt identification',
      'Accumulation/distribution phases',
      'Smart money concepts'
    ]
  },
  {
    id: 'structure',
    title: 'Market Structure',
    description: 'Trend and structure analysis',
    sections: [
      'Swing high/low identification',
      'Break of Structure (BOS) rules',
      'Change of Character (CHoCH)',
      'Market Structure Shift (MSS)',
      'Trend identification across timeframes'
    ]
  },
  {
    id: 'order-blocks',
    title: 'Order Blocks',
    description: 'Institutional entry zone detection',
    sections: [
      'Bullish OB: last down-candle before impulse',
      'Bearish OB: last up-candle before impulse',
      'Breaker block formation',
      'Mitigation block patterns',
      'Validation criteria'
    ]
  },
  {
    id: 'liquidity',
    title: 'Liquidity Concepts',
    description: 'Liquidity pool mapping',
    sections: [
      'Equal highs/lows (EQH/EQL)',
      'Buy-side liquidity (BSL)',
      'Sell-side liquidity (SSL)',
      'Liquidity sweep/grab patterns',
      'Inducement recognition'
    ]
  },
  {
    id: 'kill-zones',
    title: 'Kill Zones',
    description: 'Optimal trading sessions',
    sections: [
      'Asian session (19:00-00:00 EST)',
      'London session (02:00-05:00 EST)',
      'New York session (07:00-10:00 EST)',
      'London close (10:00-12:00 EST)',
      'Session overlap timing'
    ]
  },
  {
    id: 'risk',
    title: 'Risk Management',
    description: 'Position sizing and protection',
    sections: [
      'Maximum 1-2% risk per trade',
      'Position sizing formulas',
      'Stop loss placement strategies',
      'Take profit targeting (minimum 2:1 RR)',
      'Daily/weekly drawdown limits'
    ]
  },
  {
    id: 'ote',
    title: 'Optimal Trade Entry',
    description: 'Fibonacci-based entry zones',
    sections: [
      'OTE zone: 62%-79% retracement',
      'Premium vs Discount zones',
      '70.5% sweet spot',
      'Combining OTE with OBs/FVGs',
      'Entry confirmation rules'
    ]
  },
  {
    id: 'amd',
    title: 'Power of 3 (AMD)',
    description: 'Accumulation, Manipulation, Distribution',
    sections: [
      'Accumulation phase (Asian session)',
      'Manipulation phase (Judas Swing)',
      'Distribution phase (true move)',
      'Identifying phase transitions',
      'Trading the AMD cycle'
    ]
  }
]

export default function DocsPage() {
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null)

  const selected = strategyDocs.find(d => d.id === selectedDoc)

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      {/* Sidebar */}
      <div className="w-72 flex-shrink-0">
        <div className="card h-full overflow-auto">
          <div className="card-header">
            <h2 className="font-semibold flex items-center gap-2">
              <BookOpen className="w-4 h-4" />
              Strategy Documentation
            </h2>
          </div>
          <div className="p-2">
            {strategyDocs.map((doc) => (
              <button
                key={doc.id}
                onClick={() => setSelectedDoc(doc.id)}
                className={cn(
                  'w-full text-left px-3 py-2 rounded-lg transition-colors',
                  selectedDoc === doc.id
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700'
                )}
              >
                <div className="font-medium">{doc.title}</div>
                <div className={cn(
                  'text-xs mt-0.5',
                  selectedDoc === doc.id ? 'text-blue-200' : 'text-slate-400'
                )}>
                  {doc.description}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1">
        {selected ? (
          <div className="card h-full overflow-auto">
            <div className="card-header border-b border-slate-700">
              <h1 className="text-xl font-bold">{selected.title}</h1>
              <p className="text-slate-400 mt-1">{selected.description}</p>
            </div>
            <div className="card-body">
              <div className="space-y-6">
                <div>
                  <h3 className="text-sm font-semibold text-slate-400 uppercase mb-3">
                    Key Concepts
                  </h3>
                  <ul className="space-y-2">
                    {selected.sections.map((section, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <ChevronRight className="w-4 h-4 text-blue-400 mt-0.5 flex-shrink-0" />
                        <span>{section}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="pt-4 border-t border-slate-700">
                  <h3 className="text-sm font-semibold text-slate-400 uppercase mb-3">
                    Documentation File
                  </h3>
                  <code className="text-sm bg-slate-700 px-3 py-1.5 rounded">
                    trading_bot/docs/{selected.id === 'ict' ? 'ict_strategy' : selected.id.replace('-', '_')}.md
                  </code>
                </div>

                <div className="p-4 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                  <p className="text-sm text-blue-300">
                    This documentation is used by Claude Opus 4.5 as context when analyzing charts. 
                    The AI references these rules to make trading decisions aligned with ICT methodology.
                  </p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="card h-full flex items-center justify-center">
            <div className="text-center text-slate-400">
              <BookOpen className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>Select a strategy document to view</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
