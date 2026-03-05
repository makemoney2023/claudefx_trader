'use client'

import { useRef, useState, useEffect, useCallback } from 'react'
import { createWebSocket, getApiKey } from '../lib/api'
import type { WebSocketMessage } from '../lib/wsTypes'

interface UseWebSocketOptions {
  onReconnect?: () => void
  onMessage?: (msg: WebSocketMessage) => void
  enabled?: boolean
}

const INITIAL_BACKOFF = 1000
const MAX_BACKOFF = 30000

export function useWebSocket(channel: string, options: UseWebSocketOptions = {}) {
  const { onReconnect, onMessage, enabled = true } = options

  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null)
  const [isConnected, setIsConnected] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const backoffRef = useRef(INITIAL_BACKOFF)
  const hadConnectionRef = useRef(false)
  const mountedRef = useRef(true)

  const onReconnectRef = useRef(onReconnect)
  const onMessageRef = useRef(onMessage)
  onReconnectRef.current = onReconnect
  onMessageRef.current = onMessage

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    if (!mountedRef.current) return

    clearReconnectTimer()

    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onmessage = null
      wsRef.current.onerror = null
      wsRef.current.close()
      wsRef.current = null
    }

    const ws = createWebSocket(channel, getApiKey())
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      setIsConnected(true)

      const wasReconnect = hadConnectionRef.current
      hadConnectionRef.current = true
      backoffRef.current = INITIAL_BACKOFF

      if (wasReconnect) {
        onReconnectRef.current?.()
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setIsConnected(false)

      const delay = backoffRef.current
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF)
      reconnectTimerRef.current = setTimeout(connect, delay)
    }

    ws.onerror = (ev) => {
      console.warn(`[WebSocket] error on channel "${channel}"`, ev)
    }

    ws.onmessage = (event: MessageEvent) => {
      if (!mountedRef.current) return

      let data: WebSocketMessage
      try {
        data = JSON.parse(event.data) as WebSocketMessage
      } catch (e) {
        console.warn('[WebSocket] malformed JSON message:', event.data, e)
        return
      }

      if (data.type === 'ping') {
        ws.send('ping')
        return
      }

      setLastMessage(data)
      onMessageRef.current?.(data)
    }
  }, [channel, clearReconnectTimer])

  useEffect(() => {
    mountedRef.current = true

    if (enabled) {
      connect()
    }

    return () => {
      mountedRef.current = false
      clearReconnectTimer()

      if (wsRef.current) {
        wsRef.current.onopen = null
        wsRef.current.onclose = null
        wsRef.current.onmessage = null
        wsRef.current.onerror = null
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [enabled, connect, clearReconnectTimer])

  return { lastMessage, isConnected }
}
