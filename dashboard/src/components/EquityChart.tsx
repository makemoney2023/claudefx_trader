'use client'

import { useEffect, useState, useRef } from 'react'
import { api, EquityPoint } from '@/lib/api'

export function EquityChart() {
  const [data, setData] = useState<EquityPoint[]>([])
  const [loading, setLoading] = useState(true)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const equityData = await api.getEquityCurve(90)
        setData(equityData)
      } catch (error) {
        console.error('Error fetching equity curve:', error)
        // Don't use hardcoded sample data - let it show empty state
        setData([])
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    // Refresh every 60 seconds to get updated equity
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!canvasRef.current || data.length === 0) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Set canvas size
    const rect = canvas.getBoundingClientRect()
    canvas.width = rect.width * window.devicePixelRatio
    canvas.height = rect.height * window.devicePixelRatio
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio)

    const width = rect.width
    const height = rect.height
    const padding = { top: 20, right: 20, bottom: 30, left: 60 }

    // Clear canvas
    ctx.fillStyle = '#1e293b'
    ctx.fillRect(0, 0, width, height)

    // Calculate scales
    const equities = data.map(d => d.equity)
    const minEquity = Math.min(...equities) * 0.99
    const maxEquity = Math.max(...equities) * 1.01

    const chartWidth = width - padding.left - padding.right
    const chartHeight = height - padding.top - padding.bottom

    const xScale = (i: number) => padding.left + (i / (data.length - 1)) * chartWidth
    const yScale = (v: number) => padding.top + ((maxEquity - v) / (maxEquity - minEquity)) * chartHeight

    // Draw grid lines
    ctx.strokeStyle = '#334155'
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (i / 4) * chartHeight
      ctx.beginPath()
      ctx.moveTo(padding.left, y)
      ctx.lineTo(width - padding.right, y)
      ctx.stroke()

      // Y-axis labels
      const value = maxEquity - (i / 4) * (maxEquity - minEquity)
      ctx.fillStyle = '#94a3b8'
      ctx.font = '11px system-ui'
      ctx.textAlign = 'right'
      ctx.fillText(`$${value.toFixed(0)}`, padding.left - 8, y + 4)
    }

    // Draw equity line
    ctx.beginPath()
    ctx.strokeStyle = '#3b82f6'
    ctx.lineWidth = 2
    data.forEach((point, i) => {
      const x = xScale(i)
      const y = yScale(point.equity)
      if (i === 0) {
        ctx.moveTo(x, y)
      } else {
        ctx.lineTo(x, y)
      }
    })
    ctx.stroke()

    // Draw gradient fill
    const gradient = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom)
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.3)')
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0)')

    ctx.beginPath()
    data.forEach((point, i) => {
      const x = xScale(i)
      const y = yScale(point.equity)
      if (i === 0) {
        ctx.moveTo(x, y)
      } else {
        ctx.lineTo(x, y)
      }
    })
    ctx.lineTo(xScale(data.length - 1), height - padding.bottom)
    ctx.lineTo(padding.left, height - padding.bottom)
    ctx.closePath()
    ctx.fillStyle = gradient
    ctx.fill()

    // Draw current value
    if (data.length > 0) {
      const lastPoint = data[data.length - 1]
      const x = xScale(data.length - 1)
      const y = yScale(lastPoint.equity)

      // Dot
      ctx.beginPath()
      ctx.arc(x, y, 5, 0, Math.PI * 2)
      ctx.fillStyle = '#3b82f6'
      ctx.fill()
      ctx.strokeStyle = '#1e293b'
      ctx.lineWidth = 2
      ctx.stroke()
    }

  }, [data])

  const currentEquity = data.length > 0 ? data[data.length - 1].equity : 0
  const startEquity = data.length > 0 ? data[0].equity : 0
  const change = currentEquity - startEquity
  const changePercent = startEquity > 0 ? ((change / startEquity) * 100).toFixed(2) : '0.00'

  return (
    <div className="card h-80">
      <div className="card-header flex items-center justify-between">
        <h2 className="font-semibold">Equity Curve</h2>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <p className="text-sm text-slate-400">Current</p>
            <p className="font-semibold">${currentEquity.toFixed(2)}</p>
          </div>
          <div className="text-right">
            <p className="text-sm text-slate-400">Change</p>
            <p className={`font-semibold ${change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {change >= 0 ? '+' : ''}{changePercent}%
            </p>
          </div>
        </div>
      </div>
      <div className="flex-1 p-4">
        {loading ? (
          <div className="h-full flex items-center justify-center text-slate-400">
            Loading chart...
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            className="w-full h-full"
            style={{ width: '100%', height: '100%' }}
          />
        )}
      </div>
    </div>
  )
}
