'use client'

import { useEffect, useState } from 'react'
import { api, SessionSchedule } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Clock, CheckCircle, XCircle } from 'lucide-react'

export function SessionStatus() {
  const [schedule, setSchedule] = useState<SessionSchedule[]>([])
  const [currentSession, setCurrentSession] = useState<string>('')

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [scheduleData, sessionData] = await Promise.all([
          api.getSessionSchedule(),
          api.getSession(),
        ])
        setSchedule(scheduleData)
        setCurrentSession(sessionData.current_session)
      } catch (error) {
        console.error('Error fetching session data:', error)
        // Set default schedule for demo
        setSchedule([
          { name: 'Asian Session', session: 'asian', start: '19:00', end: '00:00', is_kill_zone: false, description: 'Accumulation phase' },
          { name: 'London Kill Zone', session: 'london', start: '02:00', end: '05:00', is_kill_zone: true, description: 'Primary kill zone' },
          { name: 'New York Kill Zone', session: 'new_york', start: '07:00', end: '10:00', is_kill_zone: true, description: 'Highest volume' },
          { name: 'London Close', session: 'london_close', start: '10:00', end: '12:00', is_kill_zone: true, description: 'Profit taking' },
        ])
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="font-semibold flex items-center gap-2">
          <Clock className="w-4 h-4" />
          Kill Zone Schedule (EST)
        </h2>
      </div>
      <div className="card-body">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {schedule.map((session) => {
            const isActive = session.session === currentSession
            return (
              <div
                key={session.session}
                className={cn(
                  'p-3 rounded-lg border transition-colors',
                  isActive
                    ? 'bg-blue-500/10 border-blue-500'
                    : 'bg-slate-700/30 border-slate-700'
                )}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className={cn(
                    'text-sm font-medium',
                    isActive ? 'text-blue-400' : 'text-slate-300'
                  )}>
                    {session.name}
                  </span>
                  {session.is_kill_zone ? (
                    <CheckCircle className="w-4 h-4 text-green-400" />
                  ) : (
                    <XCircle className="w-4 h-4 text-slate-500" />
                  )}
                </div>
                <div className="text-xs text-slate-400">
                  {session.start} - {session.end}
                </div>
                {isActive && (
                  <div className="mt-2 pt-2 border-t border-slate-600">
                    <span className="text-xs text-blue-400 animate-pulse">Active Now</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
