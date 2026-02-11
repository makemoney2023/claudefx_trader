'use client'

import { useEffect, useState } from 'react'
import { api, Config, TradingConfig } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Save, Plus, X, CheckCircle, AlertCircle, Bell, Key, Eye, EyeOff } from 'lucide-react'

interface APIKeysStatus {
  anthropic_configured: boolean
  firecrawl_configured: boolean
  firecrawl_enabled: boolean
  anthropic_key_preview: string
  firecrawl_key_preview: string
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Config | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const [formData, setFormData] = useState<Partial<TradingConfig>>({})
  const [newSymbol, setNewSymbol] = useState('')
  
  // Alert configuration
  const [alertConfig, setAlertConfig] = useState<Record<string, unknown>>({})
  const [showAlerts, setShowAlerts] = useState(false)
  
  // API Keys configuration
  const [apiKeysStatus, setApiKeysStatus] = useState<APIKeysStatus | null>(null)
  const [showApiKeys, setShowApiKeys] = useState(false)
  const [anthropicKey, setAnthropicKey] = useState('')
  const [firecrawlKey, setFirecrawlKey] = useState('')
  const [firecrawlEnabled, setFirecrawlEnabled] = useState(true)
  const [showAnthropicKey, setShowAnthropicKey] = useState(false)
  const [showFirecrawlKey, setShowFirecrawlKey] = useState(false)
  const [savingKeys, setSavingKeys] = useState(false)

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const data = await api.getConfig()
        setConfig(data)
        setFormData(data.trading)
        
        // Fetch API keys status using proper api helper
        try {
          const keysData = await api.getApiKeysStatus()
          setApiKeysStatus(keysData)
          setFirecrawlEnabled(keysData.firecrawl_enabled)
        } catch (keysError) {
          console.warn('Could not fetch API keys status, using config fallback:', keysError)
          // Fallback: use the config response to show basic status
          setApiKeysStatus({
            anthropic_configured: data.claude_configured,
            firecrawl_configured: false,
            firecrawl_enabled: true,
            anthropic_key_preview: data.claude_configured ? '(from .env.local)' : '',
            firecrawl_key_preview: ''
          })
        }
      } catch (error) {
        console.error('Error fetching config:', error)
        setMessage({ type: 'error', text: 'Failed to load configuration' })
      } finally {
        setLoading(false)
      }
    }

    fetchConfig()
  }, [])
  
  const handleSaveApiKeys = async () => {
    setSavingKeys(true)
    setMessage(null)
    try {
      const result = await api.updateApiKeys({
        anthropic_api_key: anthropicKey || undefined,
        firecrawl_api_key: firecrawlKey || undefined,
        firecrawl_enabled: firecrawlEnabled
      })
      
      setMessage({ type: 'success', text: 'API keys saved successfully' })
      
      // Clear the input fields
      setAnthropicKey('')
      setFirecrawlKey('')
      
      // Refresh API keys status
      try {
        const keysData = await api.getApiKeysStatus()
        setApiKeysStatus(keysData)
      } catch {
        // Silently handle refresh failure
      }
      
      // Update config status
      setConfig(prev => prev ? {
        ...prev,
        claude_configured: result.anthropic_configured
      } : null)
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : 'Failed to save API keys' })
    } finally {
      setSavingKeys(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await api.updateTradingConfig(formData)
      setMessage({ type: 'success', text: 'Configuration saved successfully' })
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to save configuration' })
    } finally {
      setSaving(false)
    }
  }

  const handleAddSymbol = async () => {
    if (!newSymbol) return
    try {
      const result = await api.addSymbol(newSymbol)
      setFormData({ ...formData, symbols: result.symbols })
      setNewSymbol('')
      setMessage({ type: 'success', text: `Added ${newSymbol.toUpperCase()}` })
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to add symbol' })
    }
  }

  const handleRemoveSymbol = async (symbol: string) => {
    try {
      const result = await api.removeSymbol(symbol)
      setFormData({ ...formData, symbols: result.symbols })
      setMessage({ type: 'success', text: `Removed ${symbol}` })
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to remove symbol' })
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Bot Settings</h1>
        <button
          onClick={handleSave}
          disabled={saving}
          className="btn btn-primary flex items-center gap-2"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      {message && (
        <div className={cn(
          'flex items-center gap-2 p-4 rounded-lg',
          message.type === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        )}>
          {message.type === 'success' ? <CheckCircle className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
          {message.text}
        </div>
      )}

      {/* Connection Status */}
      <div className="card">
        <div className="card-header">
          <h2 className="font-semibold">Connection Status</h2>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center gap-3">
              <div className={cn(
                'w-3 h-3 rounded-full',
                config?.mt5_connected ? 'bg-green-500' : 'bg-red-500'
              )} />
              <span>MT5 Connection</span>
              <span className={cn(
                'text-sm',
                config?.mt5_connected ? 'text-green-400' : 'text-red-400'
              )}>
                {config?.mt5_connected ? 'Connected' : 'Not Connected'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className={cn(
                'w-3 h-3 rounded-full',
                config?.claude_configured ? 'bg-green-500' : 'bg-yellow-500'
              )} />
              <span>Claude API</span>
              <span className={cn(
                'text-sm',
                config?.claude_configured ? 'text-green-400' : 'text-yellow-400'
              )}>
                {config?.claude_configured ? 'Configured' : 'Not Configured'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className={cn(
                'w-3 h-3 rounded-full',
                apiKeysStatus?.firecrawl_configured ? 'bg-green-500' : 'bg-yellow-500'
              )} />
              <span>Firecrawl API</span>
              <span className={cn(
                'text-sm',
                apiKeysStatus?.firecrawl_configured ? 'text-green-400' : 'text-yellow-400'
              )}>
                {apiKeysStatus?.firecrawl_configured ? 'Configured' : 'Not Configured'}
              </span>
            </div>
          </div>
        </div>
      </div>
      
      {/* API Keys Configuration */}
      <div className="card">
        <div className="card-header cursor-pointer" onClick={() => setShowApiKeys(!showApiKeys)}>
          <div className="flex items-center justify-between w-full">
            <div className="flex items-center gap-2">
              <Key className="w-5 h-5 text-blue-500" />
              <h2 className="font-semibold">API Keys</h2>
            </div>
            <span className="text-sm text-slate-400">{showApiKeys ? '▼' : '▶'}</span>
          </div>
        </div>
        {showApiKeys && (
          <div className="card-body space-y-4">
            <p className="text-sm text-slate-400 mb-4">
              Configure your API keys for Claude and Firecrawl intelligence. Keys are stored securely in .env.local.
            </p>
            
            {/* Anthropic API Key */}
            <div>
              <label className="block text-sm text-slate-400 mb-2">
                Anthropic API Key {apiKeysStatus?.anthropic_configured && (
                  <span className="text-green-400 ml-2">✓ Configured ({apiKeysStatus.anthropic_key_preview})</span>
                )}
              </label>
              <div className="relative">
                <input
                  type={showAnthropicKey ? 'text' : 'password'}
                  value={anthropicKey}
                  onChange={(e) => setAnthropicKey(e.target.value)}
                  placeholder={apiKeysStatus?.anthropic_configured ? 'Enter new key to update...' : 'sk-ant-...'}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowAnthropicKey(!showAnthropicKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300"
                >
                  {showAnthropicKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-1">Get your API key from <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">console.anthropic.com</a></p>
            </div>
            
            {/* Firecrawl API Key */}
            <div>
              <label className="block text-sm text-slate-400 mb-2">
                Firecrawl API Key {apiKeysStatus?.firecrawl_configured && (
                  <span className="text-green-400 ml-2">✓ Configured ({apiKeysStatus.firecrawl_key_preview})</span>
                )}
              </label>
              <div className="relative">
                <input
                  type={showFirecrawlKey ? 'text' : 'password'}
                  value={firecrawlKey}
                  onChange={(e) => setFirecrawlKey(e.target.value)}
                  placeholder={apiKeysStatus?.firecrawl_configured ? 'Enter new key to update...' : 'fc-...'}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowFirecrawlKey(!showFirecrawlKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300"
                >
                  {showFirecrawlKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-1">Get your API key from <a href="https://firecrawl.dev" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">firecrawl.dev</a></p>
            </div>
            
            {/* Firecrawl Enabled Toggle */}
            <label className="flex items-center gap-3 p-3 bg-slate-700/30 rounded-lg cursor-pointer hover:bg-slate-700/50">
              <input
                type="checkbox"
                checked={firecrawlEnabled}
                onChange={(e) => setFirecrawlEnabled(e.target.checked)}
                className="w-5 h-5 rounded border-slate-600 text-blue-600 focus:ring-blue-500"
              />
              <div>
                <span className="block font-medium">Enable Firecrawl Intelligence</span>
                <span className="text-xs text-slate-400">Real-time market intelligence from web sources</span>
              </div>
            </label>
            
            <button
              onClick={handleSaveApiKeys}
              disabled={savingKeys || (!anthropicKey && !firecrawlKey && apiKeysStatus?.firecrawl_enabled === firecrawlEnabled)}
              className={cn(
                "w-full py-2 rounded-lg font-medium transition-colors",
                savingKeys || (!anthropicKey && !firecrawlKey && apiKeysStatus?.firecrawl_enabled === firecrawlEnabled)
                  ? "bg-slate-600 text-slate-400 cursor-not-allowed"
                  : "bg-blue-600 hover:bg-blue-500 text-white"
              )}
            >
              {savingKeys ? 'Saving...' : 'Save API Keys'}
            </button>
          </div>
        )}
      </div>

      {/* Trading Symbols */}
      <div className="card">
        <div className="card-header">
          <h2 className="font-semibold">Trading Symbols</h2>
        </div>
        <div className="card-body">
          <div className="flex flex-wrap gap-2 mb-4">
            {formData.symbols?.map((symbol) => (
              <span
                key={symbol}
                className="flex items-center gap-2 px-3 py-1.5 bg-slate-700 rounded-lg"
              >
                {symbol}
                <button
                  onClick={() => handleRemoveSymbol(symbol)}
                  className="text-slate-400 hover:text-red-400"
                >
                  <X className="w-4 h-4" />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
              placeholder="Add symbol (e.g., AUDUSD)"
              className="flex-1 px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={handleAddSymbol}
              className="btn btn-secondary flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Add
            </button>
          </div>
        </div>
      </div>

      {/* Risk Management */}
      <div className="card">
        <div className="card-header">
          <h2 className="font-semibold">Risk Management</h2>
        </div>
        <div className="card-body space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-slate-400 mb-2">Risk Per Trade (%)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="5"
                value={(formData.risk_per_trade || 0.01) * 100}
                onChange={(e) => setFormData({ ...formData, risk_per_trade: parseFloat(e.target.value) / 100 })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Max Daily Trades</label>
              <input
                type="number"
                min="1"
                max="20"
                value={formData.max_daily_trades || 5}
                onChange={(e) => setFormData({ ...formData, max_daily_trades: parseInt(e.target.value) })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Min Risk/Reward</label>
              <input
                type="number"
                step="0.1"
                min="1"
                max="10"
                value={formData.min_risk_reward || 2}
                onChange={(e) => setFormData({ ...formData, min_risk_reward: parseFloat(e.target.value) })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Max Daily Drawdown (%)</label>
              <input
                type="number"
                step="0.5"
                min="1"
                max="20"
                value={(formData.max_daily_drawdown || 0.05) * 100}
                onChange={(e) => setFormData({ ...formData, max_daily_drawdown: parseFloat(e.target.value) / 100 })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Max Weekly Drawdown (%)</label>
              <input
                type="number"
                step="0.5"
                min="1"
                max="30"
                value={(formData.max_weekly_drawdown || 0.10) * 100}
                onChange={(e) => setFormData({ ...formData, max_weekly_drawdown: parseFloat(e.target.value) / 100 })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Daily Profit Target (%)</label>
              <input
                type="number"
                step="1"
                min="1"
                max="100"
                value={(formData.max_daily_profit_target || 0.50) * 100}
                onChange={(e) => setFormData({ ...formData, max_daily_profit_target: parseFloat(e.target.value) / 100 })}
                className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              />
              <p className="text-xs text-slate-500 mt-1">Stop opening new trades after this realized profit %. Existing positions continue to be managed.</p>
            </div>
          </div>
        </div>
      </div>

      {/* Trading Sessions */}
      <div className="card">
        <div className="card-header">
          <h2 className="font-semibold">Allowed Sessions</h2>
        </div>
        <div className="card-body space-y-4">
          {/* All Sessions Toggle */}
          <label
            className={cn(
              'flex items-center gap-3 p-4 rounded-lg border cursor-pointer transition-colors',
              formData.allowed_sessions?.includes('all')
                ? 'bg-green-500/10 border-green-500'
                : 'bg-slate-700/30 border-slate-700 hover:border-slate-600'
            )}
          >
            <input
              type="checkbox"
              checked={formData.allowed_sessions?.includes('all') || false}
              onChange={(e) => {
                if (e.target.checked) {
                  setFormData({ ...formData, allowed_sessions: ['all'] })
                } else {
                  setFormData({ ...formData, allowed_sessions: ['london', 'new_york'] })
                }
              }}
              className="w-5 h-5 rounded border-slate-600 text-green-600 focus:ring-green-500"
            />
            <div>
              <span className="font-medium text-green-400">Trade All Sessions (24/7)</span>
              <p className="text-xs text-slate-400 mt-0.5">Bot will analyze and trade during all market hours</p>
            </div>
          </label>
          
          {/* Individual Sessions (disabled when "all" is selected) */}
          <div className={cn(
            'grid grid-cols-2 md:grid-cols-4 gap-4',
            formData.allowed_sessions?.includes('all') && 'opacity-50 pointer-events-none'
          )}>
            {['asian', 'london', 'new_york', 'london_close'].map((session) => (
              <label
                key={session}
                className={cn(
                  'flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                  formData.allowed_sessions?.includes(session) && !formData.allowed_sessions?.includes('all')
                    ? 'bg-blue-500/10 border-blue-500'
                    : 'bg-slate-700/30 border-slate-700 hover:border-slate-600'
                )}
              >
                <input
                  type="checkbox"
                  checked={formData.allowed_sessions?.includes(session) || false}
                  disabled={formData.allowed_sessions?.includes('all')}
                  onChange={(e) => {
                    const sessions = (formData.allowed_sessions || []).filter(s => s !== 'all')
                    if (e.target.checked) {
                      setFormData({ ...formData, allowed_sessions: [...sessions, session] })
                    } else {
                      setFormData({ ...formData, allowed_sessions: sessions.filter((s) => s !== session) })
                    }
                  }}
                  className="w-4 h-4 rounded border-slate-600 text-blue-600 focus:ring-blue-500"
                />
                <span className="capitalize">{session.replace('_', ' ')}</span>
              </label>
            ))}
          </div>
          
          {formData.allowed_sessions?.includes('all') && (
            <p className="text-xs text-green-400/70">
              ✓ Bot is configured to trade during all sessions
            </p>
          )}
        </div>
      </div>

      {/* Alert Configuration */}
      <div className="card">
        <div className="card-header cursor-pointer" onClick={() => setShowAlerts(!showAlerts)}>
          <div className="flex items-center justify-between w-full">
            <div className="flex items-center gap-2">
              <Bell className="w-5 h-5 text-yellow-500" />
              <h2 className="font-semibold">Alert Thresholds</h2>
            </div>
            <span className="text-sm text-slate-400">{showAlerts ? '▼' : '▶'}</span>
          </div>
        </div>
        {showAlerts && (
          <div className="card-body space-y-4">
            <p className="text-sm text-slate-400 mb-4">
              Configure when alerts are triggered. Changes are saved automatically.
            </p>
            
            {/* Profit/Loss Alerts */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-slate-400 mb-2">Profit Alert ($)</label>
                <input
                  type="number"
                  step="10"
                  min="0"
                  value={alertConfig.profit_alert_usd as number || 100}
                  onChange={(e) => setAlertConfig({ ...alertConfig, profit_alert_usd: parseFloat(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
                <p className="text-xs text-slate-500 mt-1">Alert when trade profit exceeds this</p>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-2">Loss Alert ($)</label>
                <input
                  type="number"
                  step="10"
                  max="0"
                  value={alertConfig.loss_alert_usd as number || -50}
                  onChange={(e) => setAlertConfig({ ...alertConfig, loss_alert_usd: parseFloat(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
                <p className="text-xs text-slate-500 mt-1">Alert when trade loss exceeds this (negative)</p>
              </div>
            </div>
            
            {/* Streak Alerts */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-slate-400 mb-2">Win Streak Alert</label>
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={alertConfig.win_streak_alert as number || 5}
                  onChange={(e) => setAlertConfig({ ...alertConfig, win_streak_alert: parseInt(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-2">Loss Streak Alert</label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  value={alertConfig.loss_streak_alert as number || 3}
                  onChange={(e) => setAlertConfig({ ...alertConfig, loss_streak_alert: parseInt(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            
            {/* Drawdown Alerts */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-slate-400 mb-2">Drawdown Warning (%)</label>
                <input
                  type="number"
                  step="0.5"
                  min="1"
                  max="10"
                  value={alertConfig.drawdown_warning_pct as number || 3}
                  onChange={(e) => setAlertConfig({ ...alertConfig, drawdown_warning_pct: parseFloat(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-2">Drawdown Critical (%)</label>
                <input
                  type="number"
                  step="0.5"
                  min="2"
                  max="20"
                  value={alertConfig.drawdown_critical_pct as number || 5}
                  onChange={(e) => setAlertConfig({ ...alertConfig, drawdown_critical_pct: parseFloat(e.target.value) })}
                  className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            
            {/* Toggle Alerts */}
            <div className="grid grid-cols-2 gap-4 pt-4 border-t border-slate-700">
              {[
                { key: 'milestone_alerts', label: 'Milestone Alerts', desc: 'Alert on equity milestones' },
                { key: 'equity_high_alert', label: 'New Equity High', desc: 'Alert on new equity highs' },
                { key: 'news_blackout_alert', label: 'News Blackout', desc: 'Alert when entering blackout' },
                { key: 'daily_summary_alert', label: 'Daily Summary', desc: 'Send daily summary' },
              ].map(({ key, label, desc }) => (
                <label key={key} className="flex items-center gap-3 p-3 bg-slate-700/30 rounded-lg cursor-pointer hover:bg-slate-700/50">
                  <input
                    type="checkbox"
                    checked={alertConfig[key] as boolean ?? true}
                    onChange={(e) => setAlertConfig({ ...alertConfig, [key]: e.target.checked })}
                    className="w-5 h-5 rounded border-slate-600 text-blue-600 focus:ring-blue-500"
                  />
                  <div>
                    <span className="block font-medium">{label}</span>
                    <span className="text-xs text-slate-400">{desc}</span>
                  </div>
                </label>
              ))}
            </div>
            
            <button
              onClick={async () => {
                try {
                  await api.updateAlertConfig(alertConfig as Partial<typeof alertConfig & Record<string, unknown>>)
                  setMessage({ type: 'success', text: 'Alert thresholds saved' })
                } catch {
                  setMessage({ type: 'error', text: 'Failed to save alert thresholds' })
                }
              }}
              className="btn btn-secondary w-full"
            >
              Save Alert Settings
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
