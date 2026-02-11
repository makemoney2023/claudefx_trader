'use client'

import { TrendingUp, TrendingDown } from 'lucide-react'
import { cn } from '@/lib/utils'

interface StatsCardProps {
  title: string
  value: string | number
  change?: number
  changeType?: 'positive' | 'negative' | 'neutral'
  subtitle?: string
}

export function StatsCard({ title, value, change, changeType, subtitle }: StatsCardProps) {
  return (
    <div className="card">
      <div className="card-body">
        <p className="stat-label">{title}</p>
        <div className="flex items-end justify-between mt-1">
          <p className={cn(
            'stat-value',
            changeType === 'positive' && 'text-green-500',
            changeType === 'negative' && 'text-red-500'
          )}>
            {value}
          </p>
          {change !== undefined && (
            <div className={cn(
              'flex items-center gap-1 text-sm',
              changeType === 'positive' && 'text-green-500',
              changeType === 'negative' && 'text-red-500',
              changeType === 'neutral' && 'text-slate-400'
            )}>
              {changeType === 'positive' ? (
                <TrendingUp className="w-4 h-4" />
              ) : changeType === 'negative' ? (
                <TrendingDown className="w-4 h-4" />
              ) : null}
              <span>
                {changeType === 'positive' ? '+' : ''}
                {typeof change === 'number' ? change.toFixed(2) : change}
              </span>
            </div>
          )}
        </div>
        {subtitle && (
          <p className="text-xs text-slate-400 mt-1">{subtitle}</p>
        )}
      </div>
    </div>
  )
}
