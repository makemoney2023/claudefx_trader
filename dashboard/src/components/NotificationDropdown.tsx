'use client'

import { useState, useEffect, useRef } from 'react'
import { Bell, ArrowUpRight, ArrowDownRight, AlertTriangle, Info, TrendingUp, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Activity {
  id: string
  timestamp: string
  type: 'trade_opened' | 'trade_closed' | 'signal_generated' | 'error' | 'warning' | 'info'
  symbol?: string
  message: string
  details?: Record<string, any>
}

export function NotificationDropdown() {
  const [activities, setActivities] = useState<Activity[]>([])
  const [recentCount, setRecentCount] = useState(0)
  const [isOpen, setIsOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

  const fetchActivities = async () => {
    try {
      const [activitiesRes, countRes] = await Promise.all([
        fetch(`${apiBase}/api/activities?limit=20`),
        fetch(`${apiBase}/api/activities/count`)
      ])
      
      if (activitiesRes.ok) {
        const activitiesData = await activitiesRes.json()
        setActivities(activitiesData)
      }
      
      if (countRes.ok) {
        const countData = await countRes.json()
        setRecentCount(countData.recent_count)
      }
    } catch (error) {
      console.error('Error fetching activities:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchActivities()
    const interval = setInterval(fetchActivities, 10000) // Refresh every 10s
    return () => clearInterval(interval)
  }, [])

  const getIcon = (type: string) => {
    switch (type) {
      case 'trade_opened':
        return <ArrowUpRight className="w-4 h-4 text-green-400" />
      case 'trade_closed':
        return <ArrowDownRight className="w-4 h-4 text-blue-400" />
      case 'signal_generated':
        return <TrendingUp className="w-4 h-4 text-purple-400" />
      case 'error':
        return <AlertTriangle className="w-4 h-4 text-red-400" />
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-yellow-400" />
      default:
        return <Info className="w-4 h-4 text-slate-400" />
    }
  }

  const getTypeBadgeColor = (type: string) => {
    switch (type) {
      case 'trade_opened':
        return 'bg-green-500/20 text-green-400'
      case 'trade_closed':
        return 'bg-blue-500/20 text-blue-400'
      case 'signal_generated':
        return 'bg-purple-500/20 text-purple-400'
      case 'error':
        return 'bg-red-500/20 text-red-400'
      case 'warning':
        return 'bg-yellow-500/20 text-yellow-400'
      default:
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp)
    const now = new Date()
    const diff = now.getTime() - date.getTime()
    
    const minutes = Math.floor(diff / 60000)
    const hours = Math.floor(diff / 3600000)
    
    if (minutes < 1) return 'Just now'
    if (minutes < 60) return `${minutes}m ago`
    if (hours < 24) return `${hours}h ago`
    return date.toLocaleDateString()
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors relative"
        title="Activity Feed"
      >
        <Bell className="w-5 h-5" />
        {recentCount > 0 && (
          <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 bg-red-500 rounded-full text-xs flex items-center justify-center text-white font-medium">
            {recentCount > 9 ? '9+' : recentCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-96 bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50">
          {/* Header */}
          <div className="p-3 border-b border-slate-700 flex items-center justify-between">
            <h3 className="font-semibold">Activity Feed</h3>
            <button
              onClick={() => setIsOpen(false)}
              className="p-1 hover:bg-slate-700 rounded transition-colors"
            >
              <X className="w-4 h-4 text-slate-400" />
            </button>
          </div>

          {/* Activity List */}
          <div className="max-h-[400px] overflow-y-auto">
            {loading ? (
              <div className="p-8 text-center text-slate-400">Loading...</div>
            ) : activities.length === 0 ? (
              <div className="p-8 text-center text-slate-400">
                <Bell className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p>No recent activity</p>
                <p className="text-sm mt-1">Activities will appear here when the bot takes actions</p>
              </div>
            ) : (
              activities.map((activity) => (
                <div
                  key={activity.id}
                  className="p-3 hover:bg-slate-700/50 border-b border-slate-700/50 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5">{getIcon(activity.type)}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        {activity.symbol && (
                          <span className="font-medium text-sm">{activity.symbol}</span>
                        )}
                        <span className={cn(
                          'text-xs px-1.5 py-0.5 rounded',
                          getTypeBadgeColor(activity.type)
                        )}>
                          {activity.type.replace('_', ' ')}
                        </span>
                      </div>
                      <p className="text-sm text-slate-300 line-clamp-2">{activity.message}</p>
                      {activity.details && Object.keys(activity.details).length > 0 && (
                        <div className="mt-1 text-xs text-slate-500 space-x-2">
                          {activity.details.direction && (
                            <span className={activity.details.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                              {activity.details.direction.toUpperCase()}
                            </span>
                          )}
                          {activity.details.entry_price && (
                            <span>@ {activity.details.entry_price}</span>
                          )}
                          {activity.details.confidence !== undefined && (
                            <span>{(activity.details.confidence * 100).toFixed(0)}% conf</span>
                          )}
                        </div>
                      )}
                      <p className="text-xs text-slate-500 mt-1">
                        {formatTime(activity.timestamp)}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Footer */}
          {activities.length > 0 && (
            <div className="p-2 border-t border-slate-700 text-center">
              <button
                onClick={fetchActivities}
                className="text-sm text-blue-400 hover:text-blue-300 transition-colors"
              >
                Refresh
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
