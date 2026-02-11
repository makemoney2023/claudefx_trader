'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, GoalSummary as ApiGoalSummary, CompoundGrowth as ApiCompoundGrowth } from '@/lib/api'
import { cn } from '@/lib/utils'
import { 
  Target, 
  TrendingUp, 
  Calendar,
  DollarSign,
  Award,
  Rocket,
  RefreshCw,
  CheckCircle,
  Circle,
  ArrowRight
} from 'lucide-react'

interface GoalSummary {
  starting_equity: number
  target_equity: number
  current_equity: number
  progress: {
    percent: number
    current: number
    remaining: number
    multiple_achieved: number
  }
  milestones: {
    achieved: number[]
    next: number | null
    all: number[]
  }
  projections: {
    conservative_10pct: {
      days: number
      months: number
      date: string | null
    }
    aggressive_15pct: {
      days: number
      months: number
      date: string | null
    }
  }
}

interface CompoundGrowth {
  curve: { month: number; equity: number }[]
  final: number
  total_return: number
}

const MILESTONES = [
  { value: 1000, label: 'Start', color: 'bg-slate-500' },
  { value: 2500, label: '$2.5K', color: 'bg-blue-500' },
  { value: 5000, label: '$5K', color: 'bg-cyan-500' },
  { value: 10000, label: '$10K', color: 'bg-green-500' },
  { value: 25000, label: '$25K', color: 'bg-emerald-500' },
  { value: 50000, label: '$50K', color: 'bg-yellow-500' },
  { value: 75000, label: '$75K', color: 'bg-orange-500' },
  { value: 100000, label: '$100K', color: 'bg-red-500' },
]

export default function GoalPage() {
  const [currentEquity, setCurrentEquity] = useState(1000)
  const [summary, setSummary] = useState<GoalSummary | null>(null)
  const [growthCurve, setGrowthCurve] = useState<CompoundGrowth | null>(null)
  const [loading, setLoading] = useState(true)
  const [monthlyReturn, setMonthlyReturn] = useState(10)

  const fetchData = useCallback(async () => {
    try {
      const [summaryData, growthData] = await Promise.all([
        api.getGoalSummary(currentEquity).catch(() => null),
        api.getCompoundGrowth(currentEquity, monthlyReturn / 100, 24).catch(() => null)
      ])
      
      if (summaryData) {
        setSummary(summaryData as unknown as GoalSummary)
      }
      if (growthData) {
        setGrowthCurve(growthData)
      }
    } catch (error) {
      console.error('Error fetching goal data:', error)
    }
  }, [currentEquity, monthlyReturn])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await fetchData()
      setLoading(false)
    }
    loadData()
  }, [fetchData])

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0
    }).format(value)
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'N/A'
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Target className="w-7 h-7 text-amber-400" />
            Equity Goal Tracker
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Journey from $1,000 to $100,000
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-400">Current:</label>
            <div className="relative">
              <DollarSign className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="number"
                value={currentEquity}
                onChange={(e) => setCurrentEquity(parseFloat(e.target.value) || 0)}
                className="w-32 pl-8 pr-3 py-2 bg-slate-800 border border-slate-700 rounded-lg"
              />
            </div>
          </div>
          <button
            onClick={() => fetchData()}
            className="flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded-lg"
          >
            <RefreshCw className="w-4 h-4" />
            Update
          </button>
        </div>
      </div>

      {/* Main Progress Bar */}
      {summary && (
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-slate-400 text-sm">Progress to Goal</div>
              <div className="text-4xl font-bold text-amber-400">
                {summary.progress.percent.toFixed(1)}%
              </div>
            </div>
            <div className="text-right">
              <div className="text-slate-400 text-sm">Current Equity</div>
              <div className="text-3xl font-bold text-green-400">
                {formatCurrency(summary.current_equity)}
              </div>
            </div>
          </div>
          
          {/* Progress Bar */}
          <div className="relative h-8 bg-slate-700 rounded-full overflow-hidden">
            <div 
              className="absolute h-full bg-gradient-to-r from-amber-500 to-green-500 transition-all duration-500"
              style={{ width: `${Math.min(summary.progress.percent, 100)}%` }}
            />
            
            {/* Milestone Markers */}
            {MILESTONES.map((m, i) => {
              const position = (Math.log(m.value) - Math.log(1000)) / (Math.log(100000) - Math.log(1000)) * 100
              const achieved = summary.milestones.achieved.includes(m.value)
              return (
                <div
                  key={m.value}
                  className="absolute top-0 h-full flex items-center"
                  style={{ left: `${position}%` }}
                >
                  <div className={cn(
                    "w-3 h-3 rounded-full border-2 border-slate-900 transform -translate-x-1/2",
                    achieved ? m.color : "bg-slate-600"
                  )} />
                </div>
              )
            })}
          </div>
          
          {/* Milestone Labels */}
          <div className="relative mt-2 h-6">
            {MILESTONES.filter((_, i) => i % 2 === 0 || _ .value === 100000).map((m) => {
              const position = (Math.log(m.value) - Math.log(1000)) / (Math.log(100000) - Math.log(1000)) * 100
              return (
                <div
                  key={m.value}
                  className="absolute text-xs text-slate-500 transform -translate-x-1/2"
                  style={{ left: `${position}%` }}
                >
                  {m.label}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="card p-4">
          <div className="text-slate-400 text-sm flex items-center gap-2">
            <Rocket className="w-4 h-4" />
            Multiple Achieved
          </div>
          <div className="text-2xl font-bold mt-1">
            {summary?.progress.multiple_achieved.toFixed(2)}x
          </div>
          <div className="text-xs text-slate-500">of starting equity</div>
        </div>
        
        <div className="card p-4">
          <div className="text-slate-400 text-sm flex items-center gap-2">
            <Target className="w-4 h-4" />
            Remaining
          </div>
          <div className="text-2xl font-bold mt-1 text-amber-400">
            {formatCurrency(summary?.progress.remaining || 0)}
          </div>
          <div className="text-xs text-slate-500">to reach $100K</div>
        </div>
        
        <div className="card p-4">
          <div className="text-slate-400 text-sm flex items-center gap-2">
            <Award className="w-4 h-4" />
            Next Milestone
          </div>
          <div className="text-2xl font-bold mt-1 text-green-400">
            {summary?.milestones.next ? formatCurrency(summary.milestones.next) : 'GOAL!'}
          </div>
          <div className="text-xs text-slate-500">
            {summary?.milestones.next 
              ? `${formatCurrency(summary.milestones.next - currentEquity)} away`
              : 'Target achieved!'}
          </div>
        </div>
        
        <div className="card p-4">
          <div className="text-slate-400 text-sm flex items-center gap-2">
            <Calendar className="w-4 h-4" />
            Projected (10%/mo)
          </div>
          <div className="text-2xl font-bold mt-1">
            {summary?.projections.conservative_10pct.months.toFixed(0)} months
          </div>
          <div className="text-xs text-slate-500">
            {formatDate(summary?.projections.conservative_10pct.date || null)}
          </div>
        </div>
      </div>

      {/* Milestones List */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title flex items-center gap-2">
            <Award className="w-5 h-5 text-amber-400" />
            Milestone Progress
          </h2>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {MILESTONES.slice(1).map((milestone) => {
              const achieved = summary?.milestones.achieved.includes(milestone.value)
              const isNext = summary?.milestones.next === milestone.value
              
              return (
                <div
                  key={milestone.value}
                  className={cn(
                    "p-4 rounded-lg border transition-all",
                    achieved 
                      ? "bg-green-500/10 border-green-500/30" 
                      : isNext
                        ? "bg-amber-500/10 border-amber-500/30 animate-pulse"
                        : "bg-slate-700/30 border-slate-700"
                  )}
                >
                  <div className="flex items-center gap-2">
                    {achieved ? (
                      <CheckCircle className="w-5 h-5 text-green-400" />
                    ) : isNext ? (
                      <ArrowRight className="w-5 h-5 text-amber-400" />
                    ) : (
                      <Circle className="w-5 h-5 text-slate-600" />
                    )}
                    <span className={cn(
                      "font-bold",
                      achieved ? "text-green-400" : isNext ? "text-amber-400" : "text-slate-500"
                    )}>
                      {milestone.label}
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-slate-500">
                    {achieved 
                      ? "✓ Achieved" 
                      : `${formatCurrency(milestone.value - currentEquity)} to go`}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Projections */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Growth Calculator */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title flex items-center gap-2">
              <TrendingUp className="w-5 h-5" />
              Growth Calculator
            </h2>
          </div>
          <div className="card-body">
            <div className="mb-4">
              <label className="text-sm text-slate-400 mb-2 block">
                Expected Monthly Return: {monthlyReturn}%
              </label>
              <input
                type="range"
                min="5"
                max="25"
                value={monthlyReturn}
                onChange={(e) => setMonthlyReturn(parseInt(e.target.value))}
                className="w-full"
              />
              <div className="flex justify-between text-xs text-slate-500 mt-1">
                <span>Conservative (5%)</span>
                <span>Aggressive (25%)</span>
              </div>
            </div>
            
            {growthCurve && (
              <div className="space-y-4">
                <div className="p-4 bg-slate-700/50 rounded-lg">
                  <div className="text-sm text-slate-400">After 24 Months</div>
                  <div className="text-3xl font-bold text-green-400">
                    {formatCurrency(growthCurve.final)}
                  </div>
                  <div className="text-sm text-slate-500">
                    +{growthCurve.total_return.toFixed(0)}% total return
                  </div>
                </div>
                
                {/* Mini Chart */}
                <div className="h-32 flex items-end gap-1">
                  {growthCurve.curve.filter((_, i) => i % 3 === 0).map((point, i) => {
                    const maxEquity = growthCurve.curve[growthCurve.curve.length - 1].equity
                    const height = (point.equity / maxEquity) * 100
                    
                    return (
                      <div
                        key={i}
                        className="flex-1 bg-gradient-to-t from-amber-500/50 to-green-500/50 rounded-t"
                        style={{ height: `${height}%` }}
                        title={`Month ${point.month}: ${formatCurrency(point.equity)}`}
                      />
                    )
                  })}
                </div>
                <div className="flex justify-between text-xs text-slate-500">
                  <span>Month 0</span>
                  <span>Month 24</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Motivation */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title flex items-center gap-2">
              <Rocket className="w-5 h-5 text-amber-400" />
              Your Journey
            </h2>
          </div>
          <div className="card-body space-y-4">
            <div className="p-4 bg-gradient-to-r from-amber-500/10 to-green-500/10 border border-amber-500/30 rounded-lg">
              <div className="text-lg font-medium text-amber-400 mb-2">
                "The journey of 100x starts with a single trade"
              </div>
              <p className="text-sm text-slate-400">
                With consistent 10% monthly returns and proper risk management, 
                reaching $100K from $1K is achievable in under 2 years.
              </p>
            </div>
            
            <div className="space-y-2">
              <div className="text-sm font-medium">Growth Path (10%/month):</div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="p-2 bg-slate-700/50 rounded">
                  <span className="text-slate-400">6 months:</span>
                  <span className="ml-2 text-green-400">${Math.round(1000 * Math.pow(1.1, 6)).toLocaleString()}</span>
                </div>
                <div className="p-2 bg-slate-700/50 rounded">
                  <span className="text-slate-400">12 months:</span>
                  <span className="ml-2 text-green-400">${Math.round(1000 * Math.pow(1.1, 12)).toLocaleString()}</span>
                </div>
                <div className="p-2 bg-slate-700/50 rounded">
                  <span className="text-slate-400">18 months:</span>
                  <span className="ml-2 text-green-400">${Math.round(1000 * Math.pow(1.1, 18)).toLocaleString()}</span>
                </div>
                <div className="p-2 bg-slate-700/50 rounded">
                  <span className="text-slate-400">24 months:</span>
                  <span className="ml-2 text-green-400">${Math.round(1000 * Math.pow(1.1, 24)).toLocaleString()}</span>
                </div>
              </div>
            </div>
            
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm">
              <div className="font-medium text-red-400 mb-1">Risk Reminder</div>
              <p className="text-slate-400">
                Trading involves risk. Never risk more than you can afford to lose.
                The 5% risk per trade rule helps protect your capital.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
