'use client'

import { useEffect, useState } from 'react'
import { RefreshCw, Wifi, WifiOff } from 'lucide-react'
import { api } from '@/lib/api'
import { NotificationDropdown } from './NotificationDropdown'
import { useWebSocket } from '@/hooks/useWebSocket'

export function Header() {
  const [session, setSession] = useState<{
    session_name: string
    is_kill_zone: boolean
    is_tradeable: boolean
    minutes_remaining: number
  } | null>(null)
  const [time, setTime] = useState<Date | null>(null)
  const [mounted, setMounted] = useState(false)
  const { isConnected } = useWebSocket('all')

  useEffect(() => {
    setMounted(true)
    setTime(new Date()) // Set time only on client
    
    const fetchSession = async () => {
      try {
        const data = await api.getSession()
        setSession(data)
      } catch (error) {
        console.error('Error fetching session:', error)
      }
    }

    fetchSession()
    const sessionInterval = setInterval(fetchSession, 60000) // Update every minute
    const timeInterval = setInterval(() => setTime(new Date()), 1000)

    return () => {
      clearInterval(sessionInterval)
      clearInterval(timeInterval)
    }
  }, [])

  return (
    <header className="h-16 bg-slate-800 border-b border-slate-700 px-6 flex items-center justify-between">
      {/* Left - Session Info */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 text-sm">Session:</span>
          <span className={`font-medium ${session?.is_kill_zone ? 'text-green-400' : 'text-slate-300'}`}>
            {session?.session_name || 'Loading...'}
          </span>
          {session?.is_kill_zone && (
            <span className="px-2 py-0.5 bg-green-500/20 text-green-400 text-xs rounded-full">
              Kill Zone
            </span>
          )}
        </div>
        {session && (
          <div className="text-sm text-slate-400">
            {session.minutes_remaining}m remaining
          </div>
        )}
      </div>

      {/* Center - Time */}
      <div className="text-center">
        <div className="text-lg font-mono">
          {mounted && time ? time.toLocaleTimeString('en-US', { hour12: false }) : '--:--:--'}
        </div>
        <div className="text-xs text-slate-400">
          {mounted && time ? time.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : '---'}
        </div>
      </div>

      {/* Right - Actions */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md" title={isConnected ? 'WebSocket connected' : 'Polling fallback'}>
          {isConnected ? (
            <Wifi className="w-3.5 h-3.5 text-green-400" />
          ) : (
            <WifiOff className="w-3.5 h-3.5 text-amber-400" />
          )}
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-amber-400'}`} />
        </div>
        <button className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors">
          <RefreshCw className="w-5 h-5" />
        </button>
        <NotificationDropdown />
        <div className="w-px h-6 bg-slate-700 mx-2"></div>
        <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-700 rounded-lg">
          <div className={`w-2 h-2 rounded-full ${session?.is_tradeable ? 'bg-green-500' : 'bg-yellow-500'}`}></div>
          <span className="text-sm">
            {session?.is_tradeable ? 'Tradeable' : 'Waiting'}
          </span>
        </div>
      </div>
    </header>
  )
}
