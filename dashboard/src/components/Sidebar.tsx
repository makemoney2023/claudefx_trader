'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard,
  LineChart,
  History,
  Settings,
  BarChart3,
  Activity,
  BookOpen,
  Cpu,
  Shield,
  Calendar,
  Gem,
  Target,
  Coins,
  Scale,
  Clock,
  Brain,
  Globe,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Bot Activity', href: '/bot', icon: Cpu },
  { name: 'Positions', href: '/positions', icon: Shield },
  { name: 'Intelligence', href: '/intelligence', icon: Globe },
  { name: 'Learning', href: '/learning', icon: Brain },
  { name: 'Scaling', href: '/scaling', icon: Scale },
  { name: 'Sessions', href: '/sessions', icon: Clock },
  { name: 'Calendar', href: '/calendar', icon: Calendar },
  { name: 'Precious Metals', href: '/precious-metals', icon: Gem },
  { name: 'Crypto', href: '/crypto', icon: Coins },
  { name: 'Goal Tracker', href: '/goal', icon: Target },
  { name: 'Analysis', href: '/analysis', icon: LineChart },
  { name: 'Trades', href: '/trades', icon: History },
  { name: 'Performance', href: '/performance', icon: BarChart3 },
  { name: 'Backtesting', href: '/backtest', icon: Activity },
  { name: 'Strategy Docs', href: '/docs', icon: BookOpen },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <div className="w-64 bg-slate-800 border-r border-slate-700 flex flex-col">
      {/* Logo */}
      <div className="h-16 flex items-center px-6 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <LineChart className="w-5 h-5 text-white" />
          </div>
          <span className="font-semibold text-lg">ICT Bot</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-slate-700'
              )}
            >
              <item.icon className="w-5 h-5" />
              {item.name}
            </Link>
          )
        })}
      </nav>

      {/* Bot Status */}
      <div className="p-4 border-t border-slate-700">
        <div className="flex items-center gap-3 px-3 py-2 bg-slate-700/50 rounded-lg">
          <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
          <div className="flex-1">
            <p className="text-sm font-medium">Bot Status</p>
            <p className="text-xs text-slate-400">Running</p>
          </div>
        </div>
      </div>
    </div>
  )
}
