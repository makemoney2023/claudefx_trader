export type ActivityType =
  | 'trade_opened'
  | 'trade_closed'
  | 'pending_order_placed'
  | 'pending_order_cancelled'
  | 'pending_upgraded_to_market'
  | 'judge_approve'
  | 'judge_reject'
  | 'judge_demote'
  | 'signal_generated'
  | 'position_managed'
  | 'position_replaced'
  | 'emergency_close'
  | 'claude_close'
  | 'manual_close'
  | 'direction_flip_blocked'
  | 'kill_switch'
  | 'profit_target'
  | 'goal_reached'
  | 'volatility_alert'
  | 'error'
  | 'info'
  | 'warning'
  | 'mode_change'
  | 'edge_health_change'
  | 'symbol_blocked'
  | 'tier_change'
  | 'blackout_start'
  | 'cooldown_set'
  | 'correlation_block'
  | 'weekly_review'
  | 'learning_stored'

export interface ActivityEvent {
  type: 'activity'
  timestamp: string
  data: {
    activity_type: ActivityType
    symbol?: string
    message: string
    details?: Record<string, unknown>
    timestamp: string
  }
}

export interface TradeUpdateEvent {
  type: 'trade_update'
  timestamp: string
  data: {
    event:
      | 'trade_opened'
      | 'trade_closed'
      | 'pending_order_placed'
      | 'pending_order_filled'
      | 'pending_order_cancelled'
      | 'position_synced'
      | 'position_actions'
    ticket?: number
    symbol: string
    direction?: string
    volume?: number
    entry_price?: number
    stop_loss?: number
    take_profit?: number
    profit_loss?: number
    close_reason?: string
    r_multiple?: number
    pips?: number
    confidence?: number
    actions?: Array<{
      action: string
      ticket: number
      symbol: string
      new_sl?: number
    }>
    [key: string]: unknown
  }
}

export interface PriceUpdateEvent {
  type: 'price_update'
  timestamp: string
  data: {
    symbol: string
    bid: number
    ask: number
    spread: number
  }
}

export interface AnalysisUpdateEvent {
  type: 'analysis_update'
  timestamp: string
  data: {
    symbol: string
    direction?: string
    confidence?: number
    rr_ratio?: number
    judge_verdict?: string
    reasoning?: string
    order_type?: string
    [key: string]: unknown
  }
}

export interface PingEvent {
  type: 'ping'
}

export interface PongEvent {
  type: 'pong'
}

export interface ConnectedEvent {
  type: 'connected'
  channel: string
  message: string
}

export interface SubscribedEvent {
  type: 'subscribed'
  symbols: string[]
}

export interface UnsubscribedEvent {
  type: 'unsubscribed'
  symbols: string[]
}

export type WebSocketMessage =
  | ActivityEvent
  | TradeUpdateEvent
  | PriceUpdateEvent
  | AnalysisUpdateEvent
  | PingEvent
  | PongEvent
  | ConnectedEvent
  | SubscribedEvent
  | UnsubscribedEvent
