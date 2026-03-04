'use client'

import { useEffect, useState } from 'react'
import { StatsCard } from '@/components/StatsCard'
import { TradeMonitor } from '@/components/TradeMonitor'
import { EquityChart } from '@/components/EquityChart'
import { SessionStatus } from '@/components/SessionStatus'
import { RecentSignals } from '@/components/RecentSignals'
import { MarketIntelligence } from '@/components/MarketIntelligence'
import { PendingOrdersTable } from '@/components/PendingOrdersTable'
import { EdgeHealthCard } from '@/components/EdgeHealthCard'
import { api, ScalingStatus, GoalProgress, CurrentSessionResponse } from '@/lib/api'
import { Scale, Target, Clock, TrendingUp, DollarSign } from 'lucide-react'

interface PerformanceStats {
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_profit: number
  total_r: number
  avg_r: number
  profit_factor: number
}

interface AccountInfo {
  balance: number
  equity: number
  profit: number
  is_live: boolean
}

export default function Dashboard() {
  const [stats, setStats] = useState<PerformanceStats | null>(null)
  const [account, setAccount] = useState<AccountInfo | null>(null)
  const [scaling, setScaling] = useState<ScalingStatus | null>(null)
  const [goal, setGoal] = useState<GoalProgress | null>(null)
  const [session, setSession] = useState<CurrentSessionResponse | null>(null)
  const [config, setConfig] = useState<{ max_daily_profit_target?: number; max_weekly_drawdown?: number } | null>(null)
  const [dailyStartBalance, setDailyStartBalance] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        // Fetch with error handling on all calls
        const [statsData, accountData] = await Promise.all([
          api.getPerformanceStats().catch(() => null),
          api.getAccountInfo().catch(() => null),
        ])
        
        if (statsData) setStats(statsData)
        if (accountData) setAccount(accountData)
        
        // Fetch enhanced stats with current equity
        const equity = accountData?.equity || 1000
        const [scalingData, goalData, sessionData, configData] = await Promise.all([
          api.getScalingStatus(equity).catch(() => null),
          api.getGoalProgress(equity).catch(() => null),
          api.getCurrentSession().catch(() => null),
          api.getConfig().catch(() => null),
        ])
        if (scalingData) setScaling(scalingData)
        if (goalData) setGoal(goalData)
        if (sessionData) setSession(sessionData)
        if (configData?.trading) {
          setConfig({
            max_daily_profit_target: configData.trading.max_daily_profit_target ?? 0.50,
            max_weekly_drawdown: configData.trading.max_weekly_drawdown,
          })
        }
        // Track the starting balance for daily profit calculation
        if (accountData?.balance && !dailyStartBalance) {
          setDailyStartBalance(accountData.balance)
        }
      } catch (error) {
        console.error('Error fetching dashboard data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000) // Refresh every 30s

    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Simulation Mode Warning */}
      {account && !account.is_live && (
        <div className="bg-yellow-500/10 border border-yellow-500/50 rounded-lg p-3 mb-4">
          <div className="flex items-center gap-2 text-yellow-500">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span className="font-medium">Simulation Mode</span>
          </div>
          <p className="text-yellow-500/80 text-sm mt-1">
            MT5 is not connected or running in demo mode. Data shown is simulated.
          </p>
        </div>
      )}

      {/* Top Stats Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatsCard
          title={account?.is_live ? "Account Balance (Live)" : "Account Balance (Demo)"}
          value={`$${(account?.balance || 10000).toLocaleString()}`}
          change={account?.profit || 0}
          changeType={account?.profit && account.profit >= 0 ? 'positive' : 'negative'}
        />
        <StatsCard
          title="Total Trades"
          value={stats?.total_trades || 0}
          subtitle={`${stats?.wins || 0}W / ${stats?.losses || 0}L`}
        />
        <StatsCard
          title="Win Rate"
          value={`${((stats?.win_rate || 0) * 100).toFixed(1)}%`}
          changeType={stats?.win_rate && stats.win_rate >= 0.5 ? 'positive' : 'negative'}
        />
        <StatsCard
          title="Total R"
          value={`${(stats?.total_r || 0).toFixed(2)}R`}
          subtitle={`Avg: ${(stats?.avg_r || 0).toFixed(2)}R`}
          changeType={stats?.total_r && stats.total_r >= 0 ? 'positive' : 'negative'}
        />
      </div>

      {/* Edge Health + Enhanced Status Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        <EdgeHealthCard />
        {/* Daily Profit Target */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <DollarSign className="h-5 w-5 text-emerald-500" />
            <span className="font-medium">Daily Profit Target</span>
          </div>
          <div className="space-y-2">
            {(() => {
              const startBal = dailyStartBalance || account?.balance || 0
              const currentBal = account?.balance || 0
              const profitPct = startBal > 0 ? ((currentBal - startBal) / startBal) : 0
              const targetPct = config?.max_daily_profit_target || 0.50
              const progressPct = targetPct > 0 ? Math.min((profitPct / targetPct) * 100, 100) : 0
              const isProfit = profitPct >= 0

              return (
                <>
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">
                      {(targetPct * 100).toFixed(0)}% Target
                    </span>
                    <span className={`font-bold ${isProfit ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isProfit ? '+' : ''}{(profitPct * 100).toFixed(2)}%
                    </span>
                  </div>
                  <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all duration-500 ${
                        isProfit
                          ? 'bg-gradient-to-r from-emerald-600 to-emerald-400'
                          : 'bg-gradient-to-r from-red-600 to-red-400'
                      }`}
                      style={{ width: `${Math.max(isProfit ? progressPct : Math.min(Math.abs(profitPct / targetPct) * 100, 100), 0)}%` }}
                    />
                  </div>
                  <div className="flex justify-between text-xs text-slate-400">
                    <span>${startBal.toFixed(0)} start</span>
                    <span>${currentBal.toFixed(0)} now</span>
                  </div>
                </>
              )
            })()}
          </div>
        </div>

        {/* Goal Progress */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Target className="h-5 w-5 text-green-500" />
            <span className="font-medium">Goal Progress</span>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-slate-400">$100K Goal</span>
              <span className="font-bold text-green-500">
                {goal?.progress_percent?.toFixed(1) || 0}%
              </span>
            </div>
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div 
                className="h-full bg-gradient-to-r from-green-600 to-green-400 transition-all duration-500"
                style={{ width: `${Math.min(goal?.progress_percent || 0, 100)}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-slate-400">
              <span>${(account?.equity || 1000).toLocaleString()}</span>
              <span>$100,000</span>
            </div>
          </div>
        </div>

        {/* Scaling Tier */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Scale className="h-5 w-5 text-blue-500" />
            <span className="font-medium">Scaling Status</span>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Mode</span>
              <span className={`text-sm font-bold px-2 py-0.5 rounded ${
                scaling?.current_mode === 'aggressive' ? 'bg-green-500/20 text-green-400' :
                scaling?.current_mode === 'normal' ? 'bg-blue-500/20 text-blue-400' :
                scaling?.current_mode === 'conservative' ? 'bg-yellow-500/20 text-yellow-400' :
                'bg-red-500/20 text-red-400'
              }`}>
                {scaling?.current_mode?.toUpperCase() || 'NORMAL'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Risk</span>
              <span className="text-sm font-medium">{scaling?.risk_multiplier || 1}x</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Daily DD</span>
              <span className={`text-sm font-medium ${
                (scaling?.daily_drawdown || 0) > 3 ? 'text-yellow-400' : 'text-green-400'
              }`}>
                {scaling?.daily_drawdown?.toFixed(1) || 0}%
              </span>
            </div>
          </div>
        </div>

        {/* Current Session */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Clock className="h-5 w-5 text-purple-500" />
            <span className="font-medium">Trading Session</span>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Active</span>
              <span className={`text-sm font-bold px-2 py-0.5 rounded ${
                session?.session === 'london_ny_overlap' ? 'bg-yellow-500/20 text-yellow-400' :
                session?.session === 'london' ? 'bg-blue-500/20 text-blue-400' :
                session?.session === 'new_york' ? 'bg-green-500/20 text-green-400' :
                session?.session === 'asian' ? 'bg-purple-500/20 text-purple-400' :
                'bg-slate-500/20 text-slate-400'
              }`}>
                {session?.session?.replace(/_/g, ' ').toUpperCase() || 'OFF HOURS'}
              </span>
            </div>
            {session?.is_overlap && (
              <div className="flex items-center gap-2 text-yellow-400 text-sm">
                <TrendingUp className="h-4 w-4" />
                <span>Kill Zone Active!</span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Win Rate</span>
              <span className="text-sm font-medium">
                {scaling?.recent_performance?.win_rate?.toFixed(0) || 50}%
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Session Status */}
      <SessionStatus />

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Equity Chart - Takes 2 columns */}
        <div className="lg:col-span-2">
          <EquityChart />
        </div>

        {/* Recent Signals */}
        <div className="lg:col-span-1">
          <RecentSignals />
        </div>
      </div>

      {/* Pending Orders & Intelligence Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Pending Orders */}
        <PendingOrdersTable compact maxOrders={5} />
        
        {/* Market Intelligence */}
        <MarketIntelligence compact />
      </div>

      {/* Trade Monitor - Full Width */}
      <TradeMonitor />
    </div>
  )
}
