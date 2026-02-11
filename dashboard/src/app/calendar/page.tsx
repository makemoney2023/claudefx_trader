'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, NewsEvent, BlackoutStatus, GeopoliticalRisk } from '@/lib/api'
import { cn } from '@/lib/utils'
import { 
  Calendar, 
  Clock, 
  AlertTriangle, 
  Shield, 
  Globe2,
  Zap,
  TrendingUp,
  TrendingDown,
  Ban,
  CheckCircle,
  RefreshCw,
  ChevronRight
} from 'lucide-react'

const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD']
const TIME_RANGES = [
  { label: 'This Week', days: 7 },
  { label: '2 Weeks', days: 14 },
  { label: '1 Month', days: 30 },
  { label: '2 Months', days: 60 },
  { label: '3 Months', days: 90 },
]

export default function CalendarPage() {
  const [events, setEvents] = useState<NewsEvent[]>([])
  const [blackoutStatus, setBlackoutStatus] = useState<BlackoutStatus | null>(null)
  const [geopoliticalRisk, setGeopoliticalRisk] = useState<GeopoliticalRisk | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedCurrency, setSelectedCurrency] = useState<string>('')
  const [selectedDays, setSelectedDays] = useState<number>(90) // Default to 3 months
  const [refreshing, setRefreshing] = useState(false)

  const fetchData = useCallback(async () => {
    // Fetch data independently so one failure doesn't block others
    try {
      const calendarData = await api.getCalendar(selectedDays, selectedCurrency || undefined)
      setEvents(calendarData.events)
    } catch (error) {
      console.error('Error fetching calendar:', error)
      setEvents([])
    }
    
    try {
      const blackout = await api.getBlackoutStatus()
      setBlackoutStatus(blackout)
    } catch (error) {
      console.error('Error fetching blackout status:', error)
    }
    
    try {
      const geoRisk = await api.getGeopoliticalRisk()
      setGeopoliticalRisk(geoRisk)
    } catch (error) {
      console.error('Error fetching geopolitical risk:', error)
    }
  }, [selectedCurrency, selectedDays])

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await fetchData()
      setLoading(false)
    }
    loadData()

    // Auto-refresh every 60 seconds
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchData()
    setRefreshing(false)
  }

  const getImpactColor = (impact: string) => {
    const impactLower = impact.toLowerCase()
    if (impactLower === 'high' || impactLower === 'red' || impact === '3') {
      return 'bg-red-500/20 text-red-400 border-red-500/30'
    }
    if (impactLower === 'medium' || impactLower === 'orange' || impact === '2') {
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30'
    }
    return 'bg-slate-500/20 text-slate-400 border-slate-500/30'
  }

  const getImpactLabel = (impact: string) => {
    const impactLower = impact.toLowerCase()
    if (impactLower === 'high' || impactLower === 'red' || impact === '3') return 'HIGH'
    if (impactLower === 'medium' || impactLower === 'orange' || impact === '2') return 'MED'
    return 'LOW'
  }

  const formatEventTime = (datetime: string) => {
    const date = new Date(datetime)
    return {
      date: date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }),
      time: date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    }
  }

  const getRiskLevelColor = (level: string) => {
    switch (level) {
      case 'extreme': return 'text-red-400 bg-red-500/20'
      case 'high': return 'text-orange-400 bg-orange-500/20'
      case 'medium': return 'text-yellow-400 bg-yellow-500/20'
      default: return 'text-green-400 bg-green-500/20'
    }
  }

  const groupEventsByDate = (events: NewsEvent[]) => {
    const grouped: { [key: string]: NewsEvent[] } = {}
    
    events.forEach(event => {
      const date = new Date(event.datetime).toLocaleDateString('en-US', { 
        weekday: 'long', 
        month: 'long', 
        day: 'numeric' 
      })
      if (!grouped[date]) {
        grouped[date] = []
      }
      grouped[date].push(event)
    })
    
    return grouped
  }

  const groupedEvents = groupEventsByDate(events)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Calendar className="w-7 h-7 text-amber-400" />
            Economic Calendar
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            High-impact events and geopolitical news
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          <select
            value={selectedDays}
            onChange={(e) => setSelectedDays(Number(e.target.value))}
            className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg focus:outline-none focus:border-blue-500"
          >
            {TIME_RANGES.map(range => (
              <option key={range.days} value={range.days}>{range.label}</option>
            ))}
          </select>
          
          <select
            value={selectedCurrency}
            onChange={(e) => setSelectedCurrency(e.target.value)}
            className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg focus:outline-none focus:border-blue-500"
          >
            <option value="">All Currencies</option>
            {CURRENCIES.map(currency => (
              <option key={currency} value={currency}>{currency}</option>
            ))}
          </select>
          
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      {/* Blackout Status Banner */}
      {blackoutStatus && (
        <div className={cn(
          "p-4 rounded-lg border",
          blackoutStatus.is_blackout 
            ? "bg-red-500/10 border-red-500/30" 
            : "bg-green-500/10 border-green-500/30"
        )}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {blackoutStatus.is_blackout ? (
                <Ban className="w-6 h-6 text-red-400" />
              ) : (
                <CheckCircle className="w-6 h-6 text-green-400" />
              )}
              <div>
                <div className={cn(
                  "font-bold text-lg",
                  blackoutStatus.is_blackout ? "text-red-400" : "text-green-400"
                )}>
                  {blackoutStatus.is_blackout ? "BLACKOUT PERIOD - NO TRADING" : "TRADING ALLOWED"}
                </div>
                <div className="text-sm text-slate-400">
                  {blackoutStatus.reason || "No high-impact events in blackout window"}
                </div>
              </div>
            </div>
            
            {blackoutStatus.next_event && (
              <div className="text-right">
                <div className="text-sm text-slate-400">Next Event</div>
                <div className="font-medium">{blackoutStatus.next_event.event.title}</div>
                <div className="text-amber-400 font-mono">
                  {blackoutStatus.next_event.time_until.hours > 0 && `${blackoutStatus.next_event.time_until.hours}h `}
                  {blackoutStatus.next_event.time_until.minutes}m
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Stats Row */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Trading Status */}
        <div className="card p-4">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Shield className="w-4 h-4" />
            Trading Status
          </div>
          <div className={cn(
            "text-2xl font-bold mt-1",
            blackoutStatus?.should_trade ? "text-green-400" : "text-red-400"
          )}>
            {blackoutStatus?.should_trade ? "ALLOWED" : "BLOCKED"}
          </div>
        </div>
        
        {/* Geopolitical Risk */}
        <div className="card p-4">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Globe2 className="w-4 h-4" />
            Geopolitical Risk
          </div>
          <div className={cn(
            "text-2xl font-bold mt-1 capitalize",
            geopoliticalRisk && getRiskLevelColor(geopoliticalRisk.risk_level)
          )}>
            {geopoliticalRisk?.risk_level || 'Unknown'}
          </div>
        </div>
        
        {/* Total Events */}
        <div className="card p-4">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Calendar className="w-4 h-4" />
            Events ({TIME_RANGES.find(r => r.days === selectedDays)?.label || 'Selected'})
          </div>
          <div className="text-2xl font-bold mt-1">{events.length}</div>
        </div>
        
        {/* High Impact Count */}
        <div className="card p-4">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Zap className="w-4 h-4 text-red-400" />
            High Impact
          </div>
          <div className="text-2xl font-bold mt-1 text-red-400">
            {events.filter(e => e.impact.toLowerCase() === 'high' || e.impact === '3').length}
          </div>
        </div>
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Events List */}
        <div className="lg:col-span-2 space-y-4">
          <div className="card">
            <div className="card-header">
              <h2 className="card-title flex items-center gap-2">
                <Clock className="w-5 h-5" />
                Upcoming Events
              </h2>
            </div>
            
            <div className="card-body p-0 max-h-[600px] overflow-y-auto">
              {loading ? (
                <div className="p-12 text-center">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
                  <p className="text-slate-400 mt-4">Loading calendar...</p>
                </div>
              ) : Object.keys(groupedEvents).length === 0 ? (
                <div className="p-12 text-center">
                  <Calendar className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                  <p className="text-slate-400 text-lg">No events found</p>
                </div>
              ) : (
                <div className="divide-y divide-slate-700">
                  {Object.entries(groupedEvents).map(([date, dayEvents]) => (
                    <div key={date}>
                      <div className="px-4 py-2 bg-slate-700/50 font-medium text-sm text-slate-300">
                        {date}
                      </div>
                      <div className="divide-y divide-slate-700/50">
                        {dayEvents.map((event, idx) => {
                          const { time } = formatEventTime(event.datetime)
                          return (
                            <div key={idx} className="px-4 py-3 hover:bg-slate-700/30 transition-colors">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                  <div className="text-slate-400 font-mono text-sm w-16">
                                    {time}
                                  </div>
                                  <span className={cn(
                                    "px-2 py-0.5 text-xs font-bold rounded border",
                                    getImpactColor(event.impact)
                                  )}>
                                    {getImpactLabel(event.impact)}
                                  </span>
                                  <span className="font-medium text-blue-400">
                                    {event.currency}
                                  </span>
                                </div>
                              </div>
                              <div className="mt-1 ml-16 text-slate-300">
                                {event.title}
                              </div>
                              {(event.forecast || event.previous) && (
                                <div className="mt-1 ml-16 flex items-center gap-4 text-xs text-slate-500">
                                  {event.forecast && (
                                    <span>Forecast: <span className="text-slate-400">{event.forecast}</span></span>
                                  )}
                                  {event.previous && (
                                    <span>Previous: <span className="text-slate-400">{event.previous}</span></span>
                                  )}
                                  {event.actual && (
                                    <span className={cn(
                                      "font-medium",
                                      parseFloat(event.actual) > parseFloat(event.previous || '0') 
                                        ? "text-green-400" 
                                        : "text-red-400"
                                    )}>
                                      Actual: {event.actual}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* Countdown Widget */}
          {blackoutStatus?.next_event && (
            <div className="card p-4">
              <div className="text-slate-400 text-sm mb-2">Countdown to Next Event</div>
              <div className="text-center py-4">
                <div className="text-5xl font-bold font-mono text-amber-400">
                  {blackoutStatus.next_event.time_until.hours > 0 && (
                    <span>{blackoutStatus.next_event.time_until.hours}<span className="text-2xl">h</span> </span>
                  )}
                  <span>{blackoutStatus.next_event.time_until.minutes}<span className="text-2xl">m</span></span>
                </div>
                <div className="mt-3 text-slate-300 font-medium">
                  {blackoutStatus.next_event.event.title}
                </div>
                <div className="mt-1 text-sm text-slate-500">
                  {blackoutStatus.next_event.event.currency}
                </div>
              </div>
            </div>
          )}

          {/* Geopolitical News */}
          <div className="card">
            <div className="card-header">
              <h3 className="card-title flex items-center gap-2">
                <Globe2 className="w-5 h-5 text-amber-400" />
                Geopolitical News
              </h3>
            </div>
            <div className="card-body">
              {geopoliticalRisk && geopoliticalRisk.news_items.length > 0 ? (
                <div className="space-y-2">
                  {geopoliticalRisk.news_items.slice(0, 5).map((item, idx) => (
                    <div key={idx} className="flex items-start gap-2 text-sm">
                      <ChevronRight className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                      <span className="text-slate-300">{item}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-slate-500 text-sm">No significant geopolitical news</p>
              )}
              
              <div className="mt-4 pt-4 border-t border-slate-700">
                <div className="text-sm text-slate-400">Risk Level</div>
                <div className={cn(
                  "mt-1 px-3 py-1 rounded-full text-sm font-medium inline-block capitalize",
                  geopoliticalRisk && getRiskLevelColor(geopoliticalRisk.risk_level)
                )}>
                  {geopoliticalRisk?.risk_level || 'Unknown'}
                </div>
              </div>
            </div>
          </div>

          {/* Quick Reference */}
          <div className="card p-4">
            <h3 className="font-medium mb-3">Impact Legend</h3>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <span className={cn("px-2 py-0.5 text-xs font-bold rounded border", getImpactColor('high'))}>
                  HIGH
                </span>
                <span className="text-slate-400">Major market mover</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={cn("px-2 py-0.5 text-xs font-bold rounded border", getImpactColor('medium'))}>
                  MED
                </span>
                <span className="text-slate-400">Moderate impact</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={cn("px-2 py-0.5 text-xs font-bold rounded border", getImpactColor('low'))}>
                  LOW
                </span>
                <span className="text-slate-400">Minor impact</span>
              </div>
            </div>
            
            <div className="mt-4 pt-4 border-t border-slate-700 text-xs text-slate-500">
              <p>Trading is automatically paused 30 minutes before and after high-impact events (60 minutes for FOMC).</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
