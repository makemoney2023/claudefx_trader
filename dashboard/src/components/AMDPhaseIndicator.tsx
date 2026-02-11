'use client'

import { cn } from '@/lib/utils'
import {
  Layers,
  TrendingUp,
  TrendingDown,
  Zap,
  AlertTriangle,
  CheckCircle,
  HelpCircle,
} from 'lucide-react'

interface AMDPhaseData {
  current_phase: 'accumulation' | 'manipulation' | 'distribution' | 'unknown'
  judas_swing_detected: boolean
  judas_direction?: 'bullish' | 'bearish'
  manipulation_complete: boolean
  expected_direction?: 'long' | 'short'
  confidence: number
}

interface AMDPhaseIndicatorProps {
  data?: AMDPhaseData
  symbol?: string
  compact?: boolean
}

export function AMDPhaseIndicator({ data, symbol, compact = false }: AMDPhaseIndicatorProps) {
  const phaseConfig = {
    accumulation: {
      label: 'ACCUMULATION',
      description: 'Smart money building positions',
      color: 'text-blue-400',
      bgColor: 'bg-blue-500/20',
      borderColor: 'border-blue-500/50',
      icon: Layers,
      action: 'Wait for manipulation',
    },
    manipulation: {
      label: 'MANIPULATION',
      description: 'Judas swing / stop hunt in progress',
      color: 'text-yellow-400',
      bgColor: 'bg-yellow-500/20',
      borderColor: 'border-yellow-500/50',
      icon: AlertTriangle,
      action: 'Prepare for entry after sweep',
    },
    distribution: {
      label: 'DISTRIBUTION',
      description: 'Real move starting - execution zone',
      color: 'text-green-400',
      bgColor: 'bg-green-500/20',
      borderColor: 'border-green-500/50',
      icon: Zap,
      action: 'Execute trade if setup confirms',
    },
    unknown: {
      label: 'UNKNOWN',
      description: 'Phase not yet identified',
      color: 'text-slate-400',
      bgColor: 'bg-slate-500/20',
      borderColor: 'border-slate-500/50',
      icon: HelpCircle,
      action: 'Waiting for structure',
    },
  }

  const phase = data?.current_phase || 'unknown'
  const config = phaseConfig[phase]
  const Icon = config.icon

  if (compact) {
    return (
      <div
        className={cn(
          "inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border",
          config.bgColor,
          config.borderColor
        )}
      >
        <Icon className={cn("w-4 h-4", config.color)} />
        <span className={cn("text-sm font-medium", config.color)}>{config.label}</span>
        {data?.manipulation_complete && phase === 'manipulation' && (
          <CheckCircle className="w-3.5 h-3.5 text-green-400" />
        )}
      </div>
    )
  }

  return (
    <div className={cn("rounded-lg border p-4", config.bgColor, config.borderColor)}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon className={cn("w-5 h-5", config.color)} />
          <div>
            <h3 className={cn("font-semibold", config.color)}>{config.label}</h3>
            {symbol && <span className="text-xs text-slate-400">{symbol}</span>}
          </div>
        </div>
        {data && (
          <div className="flex items-center gap-2">
            {data.confidence > 0 && (
              <span className="text-xs text-slate-400">
                {Math.round(data.confidence * 100)}% conf
              </span>
            )}
          </div>
        )}
      </div>

      <p className="text-sm text-slate-300 mb-3">{config.description}</p>

      {/* Judas Swing Status */}
      {data?.judas_swing_detected && (
        <div className="flex items-center gap-2 mb-3 p-2 rounded bg-slate-700/50">
          <AlertTriangle className="w-4 h-4 text-yellow-400" />
          <span className="text-sm">
            Judas swing detected:{' '}
            <span
              className={cn(
                "font-medium",
                data.judas_direction === 'bullish' ? 'text-green-400' : 'text-red-400'
              )}
            >
              {data.judas_direction?.toUpperCase()}
            </span>
          </span>
          {data.manipulation_complete && (
            <CheckCircle className="w-4 h-4 text-green-400 ml-auto" />
          )}
        </div>
      )}

      {/* Expected Direction */}
      {data?.expected_direction && phase === 'distribution' && (
        <div className="flex items-center gap-2 mb-3 p-2 rounded bg-slate-700/50">
          {data.expected_direction === 'long' ? (
            <TrendingUp className="w-4 h-4 text-green-400" />
          ) : (
            <TrendingDown className="w-4 h-4 text-red-400" />
          )}
          <span className="text-sm">
            Expected move:{' '}
            <span
              className={cn(
                "font-medium",
                data.expected_direction === 'long' ? 'text-green-400' : 'text-red-400'
              )}
            >
              {data.expected_direction.toUpperCase()}
            </span>
          </span>
        </div>
      )}

      {/* Action Recommendation */}
      <div className="pt-3 border-t border-slate-600/50">
        <div className="flex items-center gap-2 text-sm">
          <Zap className="w-4 h-4 text-amber-400" />
          <span className="text-slate-300">{config.action}</span>
        </div>
      </div>

      {/* Phase Progress Indicator */}
      <div className="mt-4 flex items-center gap-1">
        <div
          className={cn(
            "flex-1 h-1.5 rounded-full transition-all",
            phase === 'accumulation' || phase === 'manipulation' || phase === 'distribution'
              ? 'bg-blue-500'
              : 'bg-slate-600'
          )}
        />
        <div
          className={cn(
            "flex-1 h-1.5 rounded-full transition-all",
            phase === 'manipulation' || phase === 'distribution'
              ? 'bg-yellow-500'
              : 'bg-slate-600'
          )}
        />
        <div
          className={cn(
            "flex-1 h-1.5 rounded-full transition-all",
            phase === 'distribution' ? 'bg-green-500' : 'bg-slate-600'
          )}
        />
      </div>
      <div className="flex justify-between mt-1 text-[10px] text-slate-500">
        <span>Accumulation</span>
        <span>Manipulation</span>
        <span>Distribution</span>
      </div>
    </div>
  )
}
