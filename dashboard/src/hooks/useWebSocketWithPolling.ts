'use client'

import { useRef, useState, useEffect, useCallback } from 'react'
import { useWebSocket } from './useWebSocket'
import type { WebSocketMessage } from '../lib/wsTypes'

const DEBOUNCE_MS = 500

const FETCH_TRIGGER_TYPES = new Set([
  'trade_update',
  'activity',
  'analysis_update',
])

interface UseWebSocketWithPollingOptions<T> {
  channel: string
  fetchFn: () => Promise<T>
  fastInterval: number
  slowInterval?: number
}

export function useWebSocketWithPolling<T>({
  channel,
  fetchFn,
  fastInterval,
  slowInterval = 60000,
}: UseWebSocketWithPollingOptions<T>) {
  const [data, setData] = useState<T | null>(null)

  const mountedRef = useRef(true)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fetchFnRef = useRef(fetchFn)
  fetchFnRef.current = fetchFn

  const doFetch = useCallback(async () => {
    try {
      const result = await fetchFnRef.current()
      if (mountedRef.current) {
        setData(result)
      }
    } catch {
      // Silently ignore — polling will retry
    }
  }, [])

  const debouncedFetch = useCallback(() => {
    if (debounceTimerRef.current !== null) {
      clearTimeout(debounceTimerRef.current)
    }
    debounceTimerRef.current = setTimeout(() => {
      debounceTimerRef.current = null
      doFetch()
    }, DEBOUNCE_MS)
  }, [doFetch])

  const handleMessage = useCallback(
    (msg: WebSocketMessage) => {
      if (FETCH_TRIGGER_TYPES.has(msg.type)) {
        debouncedFetch()
      }
    },
    [debouncedFetch],
  )

  const handleReconnect = useCallback(() => {
    doFetch()
  }, [doFetch])

  const { lastMessage: lastWsMessage, isConnected: isWsConnected } =
    useWebSocket(channel, {
      onMessage: handleMessage,
      onReconnect: handleReconnect,
    })

  // Adaptive polling interval
  useEffect(() => {
    const interval = isWsConnected ? slowInterval : fastInterval

    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current)
    }

    pollTimerRef.current = setInterval(doFetch, interval)

    return () => {
      if (pollTimerRef.current !== null) {
        clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [isWsConnected, fastInterval, slowInterval, doFetch])

  // Initial fetch on mount
  useEffect(() => {
    mountedRef.current = true
    doFetch()

    return () => {
      mountedRef.current = false
      if (debounceTimerRef.current !== null) {
        clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
    }
  }, [doFetch])

  const refresh = useCallback(() => {
    return doFetch()
  }, [doFetch])

  return { data, isWsConnected, lastWsMessage, refresh }
}
