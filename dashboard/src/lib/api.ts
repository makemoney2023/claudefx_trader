/**
 * API client for the ICT Trading Bot backend
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const API_TIMEOUT = 60000

interface FetchOptions extends RequestInit {
  params?: Record<string, string | number | boolean>
  timeout?: number
  requiresAuth?: boolean
}

// API key for protected endpoints - loaded from environment or localStorage
function getApiKey(): string {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('bot_api_key') || process.env.NEXT_PUBLIC_BOT_API_KEY || ''
  }
  return process.env.NEXT_PUBLIC_BOT_API_KEY || ''
}

async function fetchApi<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { params, timeout = API_TIMEOUT, requiresAuth = false, ...fetchOptions } = options
  
  let url = `${API_BASE}${endpoint}`
  
  if (params) {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      searchParams.append(key, String(value))
    })
    url += `?${searchParams.toString()}`
  }
  
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)
  
  // Build headers with optional auth
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(fetchOptions.headers as Record<string, string> || {}),
  }
  
  if (requiresAuth) {
    const apiKey = getApiKey()
    if (apiKey) {
      headers['X-API-Key'] = apiKey
    }
  }
  
  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers,
    })
    
    clearTimeout(timeoutId)
    
    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`)
    }
    
    return response.json()
  } catch (error) {
    clearTimeout(timeoutId)
    
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error(`Request timeout: ${endpoint}`)
    }
    
    throw error
  }
}

export const api = {
  health: () => fetchApi<{ status: string }>('/api/health'),
  
  getTrades: (params?: { page?: number; page_size?: number; symbol?: string; status?: string }) =>
    fetchApi<{
      trades: Trade[]
      total: number
      page: number
      page_size: number
      has_more: boolean
    }>('/api/trades', { params }),
  
  getTrade: (tradeId: string) =>
    fetchApi<Trade>(`/api/trades/${tradeId}`),
  
  exportTradesUrl: (format: 'csv' | 'json' = 'csv', startDate?: string, endDate?: string) => {
    const params = new URLSearchParams({ format })
    if (startDate) params.append('start_date', startDate)
    if (endDate) params.append('end_date', endDate)
    return `${API_BASE}/api/trades/export?${params.toString()}`
  },
  
  syncPositionsFromMt5: () =>
    fetchApi<{ success: boolean; synced_count: number; message: string }>('/api/trades/sync-from-mt5', { method: 'POST' }),
  
  syncTradeHistory: (days: number = 7) =>
    fetchApi<{ success: boolean; synced_count: number; days_synced: number; message: string }>('/api/trades/sync-history', { 
      method: 'POST',
      params: { days }
    }),
  
  getOpenPositions: () =>
    fetchApi<Position[]>('/api/trades/positions/open'),
  
  getAccountInfo: () =>
    fetchApi<AccountInfo>('/api/trades/account/info'),
  
  getAnalysis: (symbol: string, timeframe: string = 'H1') =>
    fetchApi<FullAnalysis>(`/api/analysis/${symbol}`, { params: { timeframe } }),
  
  // =========================================================================
  // SYMBOL-SPECIFIC ANALYSIS ENDPOINTS
  // =========================================================================
  
  getMarketStructure: (symbol: string) =>
    fetchApi<any>(`/api/analysis/${symbol}/structure`),

  getFVGAnalysis: (symbol: string) =>
    fetchApi<any>(`/api/analysis/${symbol}/fvg`),

  getOrderBlocks: (symbol: string) =>
    fetchApi<any>(`/api/analysis/${symbol}/orderblocks`),

  getSession: () =>
    fetchApi<SessionInfo>('/api/analysis/session', { timeout: 15000 }),
  
  getSessionSchedule: () =>
    fetchApi<SessionSchedule[]>('/api/analysis/session/schedule'),
  
  getSignals: (limit: number = 10) =>
    fetchApi<Signal[]>('/api/analysis/signals', { params: { limit } }),
  
  getConfig: () =>
    fetchApi<Config>('/api/config'),
  
  updateTradingConfig: (config: Partial<TradingConfig>) =>
    fetchApi<TradingConfig>('/api/config/trading', {
      method: 'PUT',
      body: JSON.stringify(config),
    }),

  // =========================================================================
  // CONFIG - TIMEFRAMES & SYMBOLS
  // =========================================================================

  getTimeframeConfig: () =>
    fetchApi<any>('/api/config/timeframes'),

  updateTimeframeConfig: (config: any) =>
    fetchApi<any>('/api/config/timeframes', { method: 'PUT', body: JSON.stringify(config) }),

  getAvailableSymbols: () =>
    fetchApi<any>('/api/config/symbols/available'),

  addToMarketWatch: (symbol: string) =>
    fetchApi<any>(`/api/config/symbols/add-to-market-watch/${symbol}`, { method: 'POST' }),

  removeFromMarketWatch: (symbol: string) =>
    fetchApi<any>(`/api/config/symbols/remove-from-market-watch/${symbol}`, { method: 'DELETE' }),

  getSymbols: () =>
    fetchApi<{ symbols: string[]; available: string[] }>('/api/config/symbols'),
  
  getMarketWatchSymbols: () =>
    fetchApi<{ symbols: MT5Symbol[]; total: number; source: string }>('/api/config/symbols/market-watch'),
  
  syncSymbolsFromMarketWatch: () =>
    fetchApi<{ synced: string[]; added: string[]; removed: string[]; current_symbols: string[] }>(
      '/api/config/symbols/sync-market-watch',
      { method: 'POST' }
    ),
  
  getBotStatus: () =>
    fetchApi<BotStatus>('/api/bot/status'),
  
  getBotLogs: (limit: number = 50, symbol?: string, logType?: string) =>
    fetchApi<{ logs: BotLog[]; total: number }>('/api/bot/logs', {
      params: { limit, ...(symbol && { symbol }), ...(logType && { log_type: logType }) }
    }),
  
  getTradingSymbols: () =>
    fetchApi<{ trading_symbols: string[]; count: number }>('/api/bot/symbols/trading'),
  
  addSymbol: (symbol: string) =>
    fetchApi<{ message: string; symbols: string[] }>(`/api/config/symbols/${symbol}`, {
      method: 'POST',
    }),
  
  removeSymbol: (symbol: string) =>
    fetchApi<{ message: string; symbols: string[] }>(`/api/config/symbols/${symbol}`, {
      method: 'DELETE',
    }),
  
  getAlertConfig: () =>
    fetchApi<AlertThresholds>('/api/config/alerts'),
  
  updateAlertConfig: (config: Partial<AlertThresholds>) =>
    fetchApi<{ message: string; updated_fields: string[]; thresholds: AlertThresholds }>('/api/config/alerts', {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  
  resetAlertConfig: () =>
    fetchApi<{ message: string; thresholds: AlertThresholds }>('/api/config/alerts/reset', {
      method: 'POST',
    }),

  getApiKeysStatus: () =>
    fetchApi<{
      anthropic_configured: boolean
      firecrawl_configured: boolean
      firecrawl_enabled: boolean
      anthropic_key_preview: string
      firecrawl_key_preview: string
    }>('/api/config/api-keys'),

  updateApiKeys: (keys: { anthropic_api_key?: string; firecrawl_api_key?: string; firecrawl_enabled?: boolean }) =>
    fetchApi<{ message: string; anthropic_configured: boolean; firecrawl_configured: boolean; firecrawl_enabled: boolean }>('/api/config/api-keys', {
      method: 'PUT',
      body: JSON.stringify(keys),
    }),
  
  startBot: () =>
    fetchApi<{ status: string; message: string }>('/api/bot/start', { method: 'POST' }),

  stopBot: () =>
    fetchApi<{ status: string; message: string }>('/api/bot/stop', { method: 'POST' }),
  
  generateWeeklyReview: () =>
    fetchApi<WeeklyReviewResponse>('/api/bot/weekly-review', { method: 'POST', requiresAuth: true }),
  
  getPerformanceSummary: () =>
    fetchApi<PerformanceSummary>('/api/bot/performance-summary'),
  
  getCalendar: async (days: number = 90, currency?: string) => {
    try {
      const intelligenceData = await fetchApi<{ events: any[], total: number, source: string }>(
        '/api/intelligence/calendar',
        { params: { days }, timeout: 15000 } // 15 second timeout for slow Firecrawl
      )
      if (intelligenceData.events && intelligenceData.events.length > 0) {
        let events = intelligenceData.events
        if (currency) {
          events = events.filter((e: any) => e.currency === currency)
        }
        return {
          events: events.map((e: any) => ({
            title: e.title || e.event || 'Unknown',
            datetime: e.datetime || new Date().toISOString(),
            impact: e.impact || 'low',
            currency: e.currency || 'USD',
            forecast: e.forecast,
            previous: e.previous,
            actual: e.actual
          })),
          total: events.length,
          is_blackout: false
        }
      }
    } catch (e) {
      console.log('Intelligence calendar unavailable, falling back to news calendar')
    }
    
    return fetchApi<CalendarResponse>('/api/news/calendar', { 
      params: { days, ...(currency && { currency }) },
      timeout: 20000 // 20 second timeout for slow calendar API
    })
  },
  
  getBlackoutStatus: () =>
    fetchApi<BlackoutStatus>('/api/news/blackout', { timeout: 15000 }),
  
  getUpcomingEvents: (hours: number = 24) =>
    fetchApi<UpcomingEventsResponse>('/api/news/upcoming', { params: { hours } }),
  
  getGeopoliticalRisk: () =>
    fetchApi<GeopoliticalRisk>('/api/news/geopolitical', { timeout: 15000 }),
  
  getNewsStatus: () =>
    fetchApi<NewsStatus>('/api/news/status'),
  
  getManagedPositions: () =>
    fetchApi<ManagedPositionsResponse>('/api/bot/positions'),
  
  emergencyCloseAll: (reason: string = 'Manual emergency close') =>
    fetchApi<EmergencyCloseResponse>('/api/bot/emergency-close', {
      method: 'POST',
      requiresAuth: true,
      body: JSON.stringify({ reason }),
    }),
  
  closePosition: (ticket: number, reason: string = 'Manual close') =>
    fetchApi<ClosePositionResponse>(`/api/bot/positions/${ticket}/close`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  
  modifyPosition: (ticket: number, stopLoss?: number, takeProfit?: number) =>
    fetchApi<ModifyPositionResponse>(`/api/bot/positions/${ticket}/modify`, {
      method: 'POST',
      body: JSON.stringify({ stop_loss: stopLoss, take_profit: takeProfit }),
    }),
  
  getActivities: (limit: number = 20, type?: string) =>
    fetchApi<Activity[]>('/api/activities', { 
      params: { limit, ...(type && { type }) } 
    }),
  
  getActivityCount: () =>
    fetchApi<{ count: number; recent_count: number }>('/api/activities/count'),
  
  clearActivities: () =>
    fetchApi<{ message: string }>('/api/activities', { method: 'DELETE' }),

  getPerformanceStats: (periodDays?: number) =>
    fetchApi<PerformanceStats>('/api/performance', {
      params: periodDays ? { period_days: periodDays } : undefined,
    }),
  
  getDailySummaries: (days: number = 30) =>
    fetchApi<DailySummary[]>('/api/performance/daily', { params: { days } }),
  
  getEquityCurve: (days: number = 90) =>
    fetchApi<EquityPoint[]>('/api/performance/equity-curve', { params: { days } }),
  
  getICTConceptStats: () =>
    fetchApi<ICTConceptStats[]>('/api/performance/ict-concepts'),
  
  getPerformanceBySymbol: () =>
    fetchApi<SymbolStats[]>('/api/performance/by-symbol'),

  // =========================================================================
  // PERFORMANCE BY SESSION & REPORTS
  // =========================================================================

  getPerformanceBySession: () =>
    fetchApi<any>('/api/performance/by-session'),

  getAccountPerformance: () =>
    fetchApi<any>('/api/performance/account'),

  getPerformanceReport: () =>
    fetchApi<any>('/api/performance/report'),

  getSilverLevels: () =>
    fetchApi<SilverKeyLevels>('/api/silver/levels'),
  
  analyzeSilver: (currentPrice: number, prices?: number[], volume?: number[], physicalPremium?: number) =>
    fetchApi<SilverAnalysis>('/api/silver/analyze', {
      method: 'POST',
      body: JSON.stringify({ 
        current_price: currentPrice, 
        prices: prices || [],
        volume: volume || [],
        physical_premium: physicalPremium || 0
      }),
    }),
  
  getSilverPatternMatch: () =>
    fetchApi<PatternMatchResult>('/api/silver/pattern-1979'),
  
  checkSilverEntryZone: (price: number) =>
    fetchApi<{ price: number; in_entry_zone: boolean; entry_zone: { low: number; high: number }; recommendation: string }>(`/api/silver/entry-check/${price}`),
  
  getSilverTargets: (entryPrice: number) =>
    fetchApi<{ entry_price: number; stop_loss: number; targets: { tp1: number; tp2: number; tp3: number }; risk_reward: { to_tp1: number; to_tp2: number; to_tp3: number } }>(`/api/silver/targets/${entryPrice}`),

  getLiveMetalsPrices: () =>
    fetchApi<{ gold_price: number; silver_price: number; source: string }>('/api/precious-metals/live-prices'),

  getPreciousMetalsSummary: (goldPrice: number, silverPrice: number, geopoliticalRisk: string = 'normal') =>
    fetchApi<PreciousMetalsSummary>('/api/precious-metals/summary', {
      params: { gold_price: goldPrice, silver_price: silverPrice, geopolitical_risk: geopoliticalRisk }
    }),
  
  getGoldLevels: () =>
    fetchApi<GoldKeyLevels>('/api/precious-metals/gold/levels'),
  
  analyzeGold: (currentPrice: number, prices?: number[]) =>
    fetchApi<GoldAnalysis>('/api/precious-metals/gold/analyze', {
      method: 'POST',
      body: JSON.stringify({ current_price: currentPrice, prices: prices || [] }),
    }),
  
  getGoldSilverRatio: (goldPrice: number, silverPrice: number) =>
    fetchApi<GoldSilverRatio>('/api/precious-metals/ratio', {
      params: { gold_price: goldPrice, silver_price: silverPrice }
    }),
  
  getSafeHavenDemand: (goldPrice: number, silverPrice: number, geopoliticalRisk: string = 'normal') =>
    fetchApi<SafeHavenDemand>('/api/precious-metals/safe-haven', {
      params: { gold_price: goldPrice, silver_price: silverPrice, geopolitical_risk: geopoliticalRisk }
    }),
  
  analyzePreciousMetals: (goldPrice: number, silverPrice: number, goldPrices?: number[], silverPrices?: number[], geopoliticalRisk: string = 'normal') =>
    fetchApi<PreciousMetalsAnalysis>('/api/precious-metals/analyze', {
      method: 'POST',
      body: JSON.stringify({
        gold_price: goldPrice,
        silver_price: silverPrice,
        gold_prices: goldPrices || [],
        silver_prices: silverPrices || [],
        geopolitical_risk: geopoliticalRisk
      }),
    }),
  
  getMetalsCorrelation: () =>
    fetchApi<MetalsCorrelation>('/api/precious-metals/correlation'),

  getCryptoSummary: () =>
    fetchApi<CryptoSummary>('/api/crypto/summary'),
  
  getCryptoLevels: (symbol: string) =>
    fetchApi<CryptoLevelsResponse>(`/api/crypto/${symbol}/levels`),
  
  analyzeCrypto: (symbol: string, currentPrice: number, prices?: number[]) =>
    fetchApi<CryptoAnalysis>(`/api/crypto/${symbol}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ current_price: currentPrice, prices: prices || [] }),
    }),
  
  getCryptoRegulatoryRisk: (symbol: string) =>
    fetchApi<RegulatoryRisk>(`/api/crypto/${symbol}/regulatory-risk`),
  
  getCryptoPositionSize: (symbol: string, baseSize: number = 0.01) =>
    fetchApi<{ symbol: string; base_size: number; adjusted_size: number; volatility_multiplier: number; reduction_percent: number }>(`/api/crypto/${symbol}/position-size`, {
      params: { base_size: baseSize }
    }),
  
  checkCryptoLevels: (symbol: string, price: number) =>
    fetchApi<{ symbol: string; price: number; near_support: { is_near: boolean; level_name: string | null; level_price: number | null }; near_resistance: { is_near: boolean; level_name: string | null; level_price: number | null }; recommendation: string }>(`/api/crypto/${symbol}/check-levels`, {
      params: { price }
    }),

  getGoalProgress: (currentEquity?: number) =>
    fetchApi<GoalProgress>('/api/goal/progress', {
      params: currentEquity ? { current_equity: currentEquity } : undefined
    }),
  
  getGoalSummary: (currentEquity?: number) =>
    fetchApi<GoalSummary>('/api/goal/summary', {
      params: currentEquity ? { current_equity: currentEquity } : undefined
    }),
  
  getGoalMilestones: (currentEquity?: number) =>
    fetchApi<GoalMilestones>('/api/goal/milestones', {
      params: currentEquity ? { current_equity: currentEquity } : undefined
    }),
  
  getGoalProjections: (currentEquity?: number, monthlyReturn?: number) =>
    fetchApi<GoalProjections>('/api/goal/projection', {
      params: { 
        current_equity: currentEquity || 1000,
        monthly_return: monthlyReturn || 0.10
      }
    }),
  
  addEquitySnapshot: (equity: number) =>
    fetchApi<{ message: string; equity: number; timestamp: string }>('/api/goal/snapshot', {
      method: 'POST',
      params: { equity }
    }),
  
  getCompoundGrowth: (starting: number = 1000, monthlyReturn: number = 0.10, months: number = 24) =>
    fetchApi<CompoundGrowth>('/api/goal/compound-growth', {
      params: { starting, monthly_return: monthlyReturn, months }
    }),
  
  getEquityHistory: () =>
    fetchApi<{ history: { equity: number; timestamp: string }[] }>('/api/goal/history'),
  
  getRequiredReturn: (currentEquity: number, targetDays: number) =>
    fetchApi<{ current_equity: number; target_equity: number; target_days: number; required: { daily_percent: number; monthly_percent: number } }>('/api/goal/required-return', {
      params: { current_equity: currentEquity, target_days: targetDays }
    }),
  
  getScalingStatus: (currentEquity?: number) =>
    fetchApi<ScalingStatus>('/api/scaling/status', {
      params: currentEquity ? { current_equity: currentEquity } : undefined
    }),
  
  getScalingMode: (currentEquity?: number) =>
    fetchApi<ScalingMode>('/api/scaling/mode', {
      params: currentEquity ? { current_equity: currentEquity } : undefined
    }),
  
  getScalingTier: (equity?: number) =>
    fetchApi<ScalingTier>('/api/scaling/tier', {
      params: equity ? { equity } : undefined
    }),
  
  calculatePositionSize: (request: PositionSizeRequest) =>
    fetchApi<PositionSizeResponse>('/api/scaling/calculate-size', {
      method: 'POST',
      body: JSON.stringify(request)
    }),
  
  getScalingTiers: () =>
    fetchApi<{ tiers: ScalingTierInfo[] }>('/api/scaling/tiers'),
  
  getScalingProjection: (startingEquity?: number, targetEquity?: number) =>
    fetchApi<ScalingProjection>('/api/scaling/projection', {
      params: {
        starting_equity: startingEquity || 1000,
        target_equity: targetEquity || 100000
      }
    }),
  
  getCurrentSession: () =>
    fetchApi<CurrentSessionResponse>('/api/session/current', { timeout: 15000 }),
  
  getSessionStats: () =>
    fetchApi<SessionStatsResponse>('/api/session/stats'),
  
  getSpecificSessionStats: (sessionName: string) =>
    fetchApi<SessionStats>(`/api/session/stats/${sessionName}`),
  
  getBestSession: () =>
    fetchApi<BestSessionResponse>('/api/session/best'),
  
  getWorstSession: () =>
    fetchApi<WorstSessionResponse>('/api/session/worst'),
  
  getSessionMatrix: () =>
    fetchApi<SessionMatrixResponse>('/api/session/matrix'),
  
  getSessionRecommendations: () =>
    fetchApi<{ recommendations: string[] }>('/api/session/recommendations'),
  
  getSessionSummary: () =>
    fetchApi<SessionSummary>('/api/session/summary'),
  
  getPendingOrders: async (): Promise<PendingOrder[]> => {
    try {
      const response = await fetchApi<{ total: number; active: number; orders: PendingOrder[] } | PendingOrder[]>('/api/orders/pending')
      if (Array.isArray(response)) {
        return response
      }
      return response?.orders || []
    } catch (error) {
      console.error('Error fetching pending orders:', error)
      return []
    }
  },
  
  cancelPendingOrder: (ticket: number) =>
    fetchApi<OrderResult>(`/api/orders/pending/${ticket}`, { method: 'DELETE' }),
  
  placePendingOrder: (order: PendingOrderRequest) =>
    fetchApi<OrderResult>('/api/orders/pending', {
      method: 'POST',
      body: JSON.stringify(order),
    }),

  // =========================================================================
  // PENDING ORDER MANAGEMENT
  // =========================================================================

  getPendingOrderSummary: () =>
    fetchApi<any>('/api/orders/pending/summary'),

  syncPendingOrders: () =>
    fetchApi<any>('/api/orders/pending/sync', { method: 'POST' }),

  cancelAllPendingOrders: () =>
    fetchApi<any>('/api/orders/pending/all', { method: 'DELETE' }),

  cancelPendingOrdersBySymbol: (symbol: string) =>
    fetchApi<any>(`/api/orders/pending/symbol/${symbol}`, { method: 'DELETE' }),

  // =========================================================================
  // INTELLIGENCE - GEOPOLITICAL & CENTRAL BANKS
  // =========================================================================

  getGeopoliticalIntelligence: () =>
    fetchApi<any>('/api/intelligence/geopolitical', { timeout: 15000 }),

  getCentralBankIntelligence: () =>
    fetchApi<any>('/api/intelligence/central-banks', { timeout: 15000 }),

  getIntelligenceContext: () =>
    fetchApi<any>('/api/intelligence/context', { timeout: 15000 }),

  getIntelligenceStatus: () =>
    fetchApi<IntelligenceStatus>('/api/intelligence/status'),
  
  getDXYAnalysis: () =>
    fetchApi<DXYAnalysis>('/api/intelligence/dxy'),
  
  getCOTPositioning: (currency: string = 'EUR') =>
    fetchApi<COTData>(`/api/intelligence/cot/${currency}`),
  
  getBreakingNews: () =>
    fetchApi<BreakingNews[]>('/api/intelligence/news/breaking'),
  
  refreshIntelligence: (symbols: string = 'EURUSD,GBPUSD,XAUUSD') =>
    fetchApi<{ message: string }>(`/api/intelligence/refresh?symbols=${encodeURIComponent(symbols)}`, { method: 'POST' }),
  
  getVIXSentiment: () =>
    fetchApi<VIXSentiment>('/api/intelligence/vix'),
  
  getRetailSentiment: (symbol: string) =>
    fetchApi<RetailSentiment>(`/api/intelligence/retail/${symbol}`),
  
  getCurrencyStrength: () =>
    fetchApi<CurrencyStrength>('/api/intelligence/currency-strength'),
  
  getTradingViewTechnical: (symbol: string) =>
    fetchApi<TradingViewTechnical>(`/api/intelligence/tradingview/${symbol}`),
  
  getRateExpectations: () =>
    fetchApi<RateExpectations>('/api/intelligence/rates'),
  
  getCommodityCorrelation: (commodity: 'oil' | 'gold') =>
    fetchApi<CommodityCorrelation>(`/api/intelligence/commodity/${commodity}`),
  
  getSocialSentiment: (symbol: string) =>
    fetchApi<SocialSentiment>(`/api/intelligence/social/${symbol}`),
  
  getOptionsFlow: (symbol: string) =>
    fetchApi<OptionsFlow>(`/api/intelligence/options/${symbol}`),
  
  getBondYields: () =>
    fetchApi<BondYields>('/api/intelligence/yields'),
  
  getBTCDominance: () =>
    fetchApi<BTCDominance>('/api/intelligence/btc-dominance'),
  
  getEconomicSurprise: () =>
    fetchApi<EconomicSurprise>('/api/intelligence/economic-surprise'),
  
  getSeasonalPattern: (symbol: string) =>
    fetchApi<SeasonalPattern>(`/api/intelligence/seasonal/${symbol}`),
  
  getIntermarketAnalysis: () =>
    fetchApi<IntermarketAnalysis>('/api/intelligence/intermarket'),
  
  getCompleteAnalysis: (symbol: string) =>
    fetchApi<CompleteAnalysis>(`/api/intelligence/complete/${symbol}`),
  
  // =========================================================================
  // DEEP RESEARCH (AGENT) ENDPOINTS - AI-Powered Analysis
  // =========================================================================
  
  getDeepGeopolitical: () =>
    fetchApi<DeepResearchResponse>('/api/intelligence/deep-research/geopolitical', { timeout: 120000 }),
  
  getDeepCentralBanks: () =>
    fetchApi<DeepResearchResponse>('/api/intelligence/deep-research/central-banks', { timeout: 120000 }),
  
  getDeepIntermarket: () =>
    fetchApi<DeepResearchResponse>('/api/intelligence/deep-research/intermarket', { timeout: 120000 }),
  
  getDeepFundamentals: (symbol: string) =>
    fetchApi<DeepResearchResponse>(`/api/intelligence/deep-research/fundamentals/${symbol}`, { timeout: 120000 }),
  
  getComprehensiveIntelligence: (symbol: string = 'EURUSD') =>
    fetchApi<ComprehensiveIntelligence>(`/api/intelligence/deep-research/comprehensive?symbol=${symbol}`),
  
  // =========================================================================
  // EXTRACT ENDPOINTS - Structured Data
  // =========================================================================
  
  getExtractedCalendar: () =>
    fetchApi<ExtractedDataResponse>('/api/intelligence/extract/calendar'),
  
  getExtractedCOT: () =>
    fetchApi<ExtractedDataResponse>('/api/intelligence/extract/cot'),
  
  getExtractedRates: () =>
    fetchApi<ExtractedDataResponse>('/api/intelligence/extract/rates'),
  
  // =========================================================================
  // REFRESH ENDPOINTS
  // =========================================================================
  
  refreshIntelligenceQuick: (symbol: string = 'EURUSD') =>
    fetchApi<{ message: string }>('/api/intelligence/refresh/quick', { 
      method: 'POST',
      params: { symbol }
    }),
  
  refreshIntermarket: () =>
    fetchApi<{ message: string }>('/api/intelligence/refresh/intermarket', { method: 'POST' }),

  // =========================================================================
  // DEBUG ENDPOINTS
  // =========================================================================

  getMt5Debug: () =>
    fetchApi<any>('/api/debug/mt5'),

  getLearningRecent: (params?: { limit?: number; offset?: number; symbol?: string }) =>
    fetchApi<TradeLearning[]>('/api/learning/recent', { params }),
  
  getLearningMistakes: (limit: number = 5) =>
    fetchApi<{ mistakes: string[]; count: number }>('/api/learning/mistakes', { params: { limit } }),
  
  getLearningPatterns: (limit: number = 5) =>
    fetchApi<{ patterns: string[]; count: number }>('/api/learning/patterns', { params: { limit } }),
  
  getLearningKnowledge: (params?: { category?: string; include_expired?: boolean }) =>
    fetchApi<KnowledgeEntry[]>('/api/learning/knowledge', { params }),
  
  getLearningWeeklyReport: () =>
    fetchApi<{ message: string; report: WeeklyLearningReport | null }>('/api/learning/weekly-report'),
  
  getLearningStats: () =>
    fetchApi<LearningStats>('/api/learning/stats'),
  
  getLearningContext: (symbol: string, session?: string) =>
    fetchApi<{ symbol: string; session: string | null; context: string; context_length: number }>(
      `/api/learning/context/${symbol}`,
      { params: session ? { session } : undefined }
    ),
  
  postLearningConsolidate: () =>
    fetchApi<{ message: string; success: boolean; grade?: string; trades_reviewed?: number }>(
      '/api/learning/consolidate',
      { method: 'POST' }
    ),
  
  postLearningReviewHistory: (limit: number = 50, minLoss: number = -10) =>
    fetchApi<{ message: string; success: boolean; reviewed: number; total_found: number; errors: string[] }>(
      `/api/learning/review-history?limit=${limit}&min_loss=${minLoss}`,
      { method: 'POST' }
    ),
  
  deleteLearningPrune: () =>
    fetchApi<{ message: string; count: number }>('/api/learning/prune', { method: 'DELETE', requiresAuth: true }),

  // =========================================================================
  // BACKTESTING
  // =========================================================================

  listBacktestRuns: (params?: { run_type?: string; status?: string; limit?: number; offset?: number }) =>
    fetchApi<BacktestRun[]>('/api/backtest/runs', { params: params as Record<string, string | number | boolean> | undefined }),

  getBacktestRun: (id: number) =>
    fetchApi<BacktestRun>(`/api/backtest/runs/${id}`),

  deleteBacktestRun: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/backtest/runs/${id}`, { method: 'DELETE', requiresAuth: true }),

  cancelBacktestRun: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/backtest/runs/${id}/cancel`, { method: 'POST', requiresAuth: true }),

  startIctBacktest: (config: IctBacktestConfig) =>
    fetchApi<BacktestRun>('/api/backtest/ict', {
      method: 'POST',
      body: JSON.stringify(config),
      requiresAuth: true,
      timeout: 300000,
    }),

  estimateReplayCost: (config: ReplayBacktestConfig) =>
    fetchApi<{ symbol: string; period: string; estimated_api_calls: number; estimated_cost: string; interval_hours: number }>(
      '/api/backtest/replay/estimate',
      { method: 'POST', body: JSON.stringify(config) }
    ),

  startReplayBacktest: (config: ReplayBacktestConfig) =>
    fetchApi<BacktestRun>('/api/backtest/replay', {
      method: 'POST',
      body: JSON.stringify(config),
      requiresAuth: true,
    }),

  startOptimizer: (config: OptimizerConfig) =>
    fetchApi<BacktestRun>('/api/backtest/optimizer', {
      method: 'POST',
      body: JSON.stringify(config),
      requiresAuth: true,
      timeout: 120000,
    }),
}

export function createWebSocket(channel: string): WebSocket {
  const wsUrl = API_BASE.replace('http', 'ws')
  return new WebSocket(`${wsUrl}/ws/${channel}`)
}

export interface Trade {
  trade_id: string
  timestamp: string
  symbol: string
  direction: 'long' | 'short'
  entry_price: number
  entry_time: string
  stop_loss: number
  take_profit: number
  position_size: number
  exit_price?: number
  exit_time?: string
  profit_loss?: number
  profit_loss_pips?: number
  r_multiple?: number
  status: 'open' | 'closed'
}

export interface Position {
  ticket: number
  symbol: string
  direction: string
  volume: number
  entry_price: number
  current_price: number
  stop_loss: number
  take_profit: number
  unrealized_pnl: number
  r_multiple: number
}

export interface AccountInfo {
  balance: number
  equity: number
  margin: number
  free_margin: number
  margin_level: number
  profit: number
  currency: string
  is_live: boolean
}

export interface SessionInfo {
  current_session: string
  session_name: string
  is_kill_zone: boolean
  is_tradeable: boolean
  minutes_remaining: number
  next_kill_zone?: string
}

export interface SessionSchedule {
  name: string
  session: string
  start: string
  end: string
  is_kill_zone: boolean
  description: string
}

export interface FullAnalysis {
  symbol: string
  timeframe: string
  timestamp: string
  session: SessionInfo
  market_structure: MarketStructure
  fvg_zones: FVGZone[]
  order_blocks: OrderBlock[]
  liquidity: LiquidityData
  ote?: OTEData
  amd?: AMDData
}

export interface MarketStructure {
  trend: string
  last_structure_break?: string
  swing_highs: { index: number; price: number }[]
  swing_lows: { index: number; price: number }[]
}

export interface FVGZone {
  type: string
  top: number
  bottom: number
  midpoint: number
  status: string
  index: number
}

export interface OrderBlock {
  type: string
  top: number
  bottom: number
  midpoint: number
  status: string
  strength: number
  index: number
}

export interface LiquidityData {
  nearest_bsl?: number
  nearest_ssl?: number
  bsl_pools: any[]
  ssl_pools: any[]
  recent_sweeps: any[]
}

export interface OTEData {
  swing_high: number
  swing_low: number
  equilibrium: number
  ote_top: number
  ote_bottom: number
  current_price: number
  price_zone: string
  in_ote: boolean
}

export interface AMDData {
  current_phase: string
  judas_swing_detected: boolean
  judas_direction?: string
  expected_direction?: string
}

export interface Config {
  trading: TradingConfig
  timeframes: TimeframeConfig
  mt5_connected: boolean
  claude_configured: boolean
}

export interface AlertThresholds {
  profit_alert_usd: number
  loss_alert_usd: number
  daily_profit_alert: number
  daily_loss_alert: number
  position_count_alert: number
  exposure_alert_lots: number
  win_streak_alert: number
  loss_streak_alert: number
  drawdown_warning_pct: number
  drawdown_critical_pct: number
  equity_high_alert: boolean
  milestone_alerts: boolean
  volatility_alert_atr_multiple: number
  spread_alert_pips: number
  news_blackout_alert: boolean
  high_impact_news_alert: boolean
  connection_lost_alert: boolean
  error_alert: boolean
  daily_summary_alert: boolean
  weekly_review_alert: boolean
}

export interface TradingConfig {
  symbols: string[]
  risk_per_trade: number
  max_daily_trades: number
  min_risk_reward: number
  allowed_sessions: string[]
  max_daily_drawdown: number
  max_weekly_drawdown: number
  max_daily_profit_target: number
}

export interface TimeframeConfig {
  higher_tf: string
  execution_tf: string
  confirmation_tf: string
}

export interface Activity {
  id: string
  type: 'trade_opened' | 'trade_closed' | 'signal' | 'analysis' | 'error' | 'info'
  message: string
  timestamp: string
  details?: Record<string, unknown>
  symbol?: string
}

export interface PerformanceStats {
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_profit: number
  total_r: number
  avg_r: number
  avg_win: number
  avg_loss: number
  profit_factor: number
  largest_win: number
  largest_loss: number
}

export interface DailySummary {
  date: string
  trades_opened: number
  trades_closed: number
  daily_pnl: number
  daily_r: number
}

export interface EquityPoint {
  timestamp: string
  equity: number
  drawdown: number
}

export interface ICTConceptStats {
  concept: string
  trades: number
  wins: number
  win_rate: number
  avg_r: number
}

export interface SymbolStats {
  symbol: string
  trades: number
  wins: number
  win_rate: number
  total_pnl: number
  avg_r: number
}

export interface Signal {
  id: string
  timestamp: string
  symbol: string
  direction: 'long' | 'short' | 'no_trade'
  confidence: number
  reasoning: string
  market_structure: string
  entry_price?: number
  stop_loss?: number
  take_profit?: number
  risk_reward?: number
}

export interface MT5Symbol {
  name: string
  description: string
  path: string
  category: string
  visible: boolean
  tradeable: boolean
  bid?: number
  ask?: number
  spread?: number
  digits?: number
  volume_min?: number
  volume_max?: number
}

export interface BotStatus {
  is_running: boolean
  current_action: string
  current_symbol?: string
  session: {
    name: string
    is_tradeable: boolean
  }
  cycle_info: {
    count: number
    last_cycle_time?: string
    symbols_this_cycle: string[]
  }
  config: {
    trading_symbols: string[]
    allowed_sessions: string[]
    risk_per_trade: number
    max_daily_trades: number
  }
  last_error?: string
}

export interface BotLog {
  timestamp: string
  type: string
  symbol?: string
  message: string
  details: Record<string, any>
}

export interface ManagedPosition {
  ticket: number
  symbol: string
  direction: 'long' | 'short'
  volume: number
  entry_price: number
  current_price: number
  stop_loss: number
  take_profit: number
  unrealized_pnl: number
  r_multiple: number
  status: 'open' | 'break_even' | 'trailing' | 'partial_close' | 'closed'
  be_triggered: boolean
  trailing_active: boolean
  partial_closed: boolean
  open_time: string | null
  initial_sl?: number
  tp1?: number
  tp2?: number
  tp3?: number
  tp1_hit?: boolean
  tp2_hit?: boolean
  initial_volume?: number
}

export interface ManagedPositionsResponse {
  positions: ManagedPosition[]
  count: number
  total_pnl: number
}

export interface EmergencyCloseResponse {
  status: 'success' | 'error'
  message: string
  timestamp?: string
}

export interface ClosePositionResponse {
  status: 'success' | 'error'
  message: string
  symbol?: string
}

export interface ModifyPositionResponse {
  status: 'success' | 'error'
  message: string
  new_sl?: number
  new_tp?: number
}

export interface NewsEvent {
  title: string
  datetime: string
  impact: string
  currency: string
  forecast?: string
  previous?: string
  actual?: string
}

export interface CalendarResponse {
  events: NewsEvent[]
  total: number
  is_blackout: boolean
  blackout_reason: string
}

export interface BlackoutStatus {
  is_blackout: boolean
  reason: string
  should_trade: boolean
  next_event?: {
    event: NewsEvent
    time_until: {
      hours: number
      minutes: number
      total_minutes: number
    }
  }
}

export interface UpcomingEventsResponse {
  events: NewsEvent[]
  total: number
  countdown_to_next?: {
    event: NewsEvent
    time_until: {
      hours: number
      minutes: number
      total_minutes: number
    }
  }
}

export interface GeopoliticalRisk {
  risk_level: 'low' | 'medium' | 'high' | 'extreme'
  news_items: string[]
}

export interface NewsStatus {
  is_blackout: boolean
  blackout_reason: string
  next_event?: {
    event: NewsEvent
    time_until: {
      hours: number
      minutes: number
      total_minutes: number
    }
  }
  geopolitical_risk: string
  total_events_cached: number
  last_fetch?: string
}

export interface SilverKeyLevels {
  recent_low: number
  recent_high: number
  target_1: number
  target_2: number
  euphoria: number
  invalidation: number
  entry_zone_low: number
  entry_zone_high: number
}

export interface SilverAnalysis {
  current_price: number
  recommendation: string
  reasoning: string
  rsi: number
  pattern_phase: string
  pattern_completion: number
  projected_targets: number[]
  risk_level: string
  entry_zone: { low: number; high: number }
  key_levels: SilverKeyLevels
}

export interface PatternMatchResult {
  pattern_1979: {
    december_gain: string
    january_continuation: string
    peak: string
    total_move: string
  }
  current_match: Record<string, any>
  phases: string[]
  current_phase: string
}

export interface PreciousMetalsSummary {
  timestamp: string
  gold_price: number
  silver_price: number
  gold_recommendation: string
  silver_recommendation: string
  ratio: number
  ratio_interpretation: string
  primary_metal: string
  primary_reasoning: string
  safe_haven_level: string
}

export interface GoldKeyLevels {
  recent_low: number
  recent_high: number
  all_time_high: number
  support_1: number
  support_2: number
  resistance_1: number
  resistance_2: number
  invalidation: number
  entry_zone_low: number
  entry_zone_high: number
}

export interface GoldAnalysis {
  symbol: string
  name: string
  current_price: number
  recommendation: string
  reasoning: string
  entry_zone_status: string
  rsi: number
  key_levels: GoldKeyLevels & { distance_to_ath_percent: number }
  targets: {
    tp1: number
    tp2: number
    tp3: number
    final: number
  }
  stop_loss: number
  risk_assessment: {
    level: string
    score: number
    factors: string[]
  }
}

export interface GoldSilverRatio {
  current_ratio: number
  historical_avg: number
  normal_low: number
  normal_high: number
  interpretation: string
  trade_bias: string
}

export interface SafeHavenDemand {
  level: string
  score: number
  factors: string[]
  recommendation: string
}

export interface PreciousMetalsAnalysis {
  timestamp: string
  gold: GoldAnalysis
  silver: SilverAnalysis
  ratio: {
    current: number
    historical_avg: number
    interpretation: string
    trade_bias: string
    normal_range: string
  }
  cross_signals: Array<{
    type: string
    signal: string
    action: string
    strength: string
  }>
  safe_haven: SafeHavenDemand
  primary_recommendation: {
    metal: string
    reasoning: string
  }
  correlation: {
    gold_silver: number
    note: string
  }
}

export interface MetalsCorrelation {
  gold_silver_correlation: number
  characteristics: {
    gold: {
      volatility: string
      safe_haven_strength: string
      typical_daily_range_percent: number
      description: string
    }
    silver: {
      volatility: string
      safe_haven_strength: string
      typical_daily_range_percent: number
      description: string
    }
  }
  trading_implications: string[]
}

export interface CryptoSummary {
  cryptos: Record<string, {
    name: string
    symbol: string
    levels: {
      support: number
      resistance: number
      ath: number
    }
    regulatory_sensitive: boolean
    use_case: string
  }>
  trading_hours: string
  note: string
}

export interface CryptoLevelsResponse {
  symbol: string
  name: string
  levels: {
    support_1: number
    support_2: number
    resistance_1: number
    resistance_2: number
    recent_low: number
    recent_high: number
    all_time_high: number
  }
}

export interface CryptoAnalysis {
  symbol: string
  name: string
  current_price: number
  recommendation: string
  reasoning: string
  technical: {
    rsi: number
    volatility: number
    is_high_volatility: boolean
  }
  levels: {
    support_1: number
    support_2: number
    resistance_1: number
    resistance_2: number
    all_time_high: number
    distance_to_ath_percent: number
  }
  position_near: {
    support: boolean
    support_level: string
    resistance: boolean
    resistance_level: string
  }
  risk: {
    factors: string[]
    level: string
    regulatory: {
      risk: string
      details: string
      recommendation: string
    }
  }
  position_size_adjustment: number
  use_case: string
  is_24_7: boolean
}

export interface RegulatoryRisk {
  risk: 'normal' | 'elevated' | 'unknown'
  symbol?: string
  details: string
  recommendation: string
}

export interface GoalProgress {
  current_equity: number
  target_equity: number
  starting_equity: number
  progress_percent: number
  remaining: number
  gain_percent: number
}

export interface GoalMilestones {
  milestones: {
    name: string
    target: number
    achieved: boolean
    achieved_date?: string
  }[]
  next_milestone?: {
    name: string
    target: number
    remaining: number
  }
}

export interface GoalProjections {
  current_equity: number
  monthly_return_percent: number
  projection: {
    days: number
    months: number
    date: string | null
    achieved: boolean
  }
}

export interface CompoundGrowth {
  curve: { month: number; equity: number }[]
  final: number
  total_return: number
}

export interface GoalSummary {
  progress: {
    percent: number
    current: number
    remaining: number
    multiple_achieved: number
  }
  milestones: {
    achieved: number[]
    next: number | null
    all: number[]
    status: Record<string, boolean>
  }
  projection: {
    days: number
    months: number
    date: string | null
    achieved: boolean
  }
}

export interface ScalingStatus {
  current_mode: string
  mode_description: string
  risk_multiplier: number
  confidence_threshold: number
  setup_filter: string
  max_daily_trades: number
  daily_drawdown: number
  weekly_drawdown: number
  goal_progress: number
  recent_performance: {
    win_rate: number
    avg_r: number
    max_drawdown: number
    current_streak: string
    trades_count: number
  }
  daily_pnl: number
  weekly_pnl: number
}

export interface ScalingMode {
  mode: 'aggressive' | 'normal' | 'conservative' | 'defensive'
  description: string
  risk_multiplier: number
  confidence_threshold: number
  setup_filter: string
  max_daily_trades: number
}

export interface ScalingTier {
  current_tier: string
  equity_range: {
    min: number
    max: number | null
  }
  progress_percent: number
  base_lots: number
  max_lots: number
  risk_percent: number
  max_daily_trades: number
  max_exposure_percent: number
  next_tier: {
    name: string
    equity_needed: number
    base_lots: number | null
  } | null
}

export interface PositionSizeRequest {
  equity: number
  entry_price: number
  stop_loss: number
  symbol: string
  confidence?: number
  setup_grade?: string
}

export interface PositionSizeResponse {
  lots: number
  risk_amount: number
  risk_percent: number
  tier_name: string
  adjustments: string[]
}

export interface ScalingProjection {
  starting_equity: number
  target_equity: number
  projections: {
    month: number
    equity: number
    tier: string
    monthly_return_pct: number
    lots_per_trade: number
  }[]
  estimated_months: number
  final_equity: number
}

export interface ScalingTierInfo {
  name: string
  equity_min: number
  equity_max: number | null
  base_lots: number
  max_lots: number
  risk_percent: number
  max_daily_trades: number
}

export interface CurrentSessionResponse {
  session: string
  is_overlap: boolean
  is_off_hours: boolean
}

export interface SessionStats {
  session: string
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_pnl: number
  total_r: number
  avg_r: number
  best_trade_r: number
  worst_trade_r: number
  expectancy: number
  top_symbols: Record<string, number>
}

export interface SessionStatsResponse {
  asian: SessionStats
  london: SessionStats
  new_york: SessionStats
  london_ny_overlap: SessionStats
  off_hours: SessionStats
}

export interface BestSessionResponse {
  session: string | null
  stats?: SessionStats
  message?: string
}

export interface WorstSessionResponse {
  session: string | null
  stats?: SessionStats
  message?: string
}

export interface SessionMatrixResponse {
  matrix: Record<string, Record<string, {
    trades: number
    wins: number
    total_r: number
    win_rate: number
    avg_r: number
  }>>
}

export interface SessionSummary {
  total_trades: number
  total_pnl: number
  sessions: SessionStatsResponse
  best_session: string | null
  worst_session: string | null
  recommendations: string[]
}

export interface WeeklyReviewResponse {
  status: 'success' | 'error'
  review?: {
    performance_grade: string
    summary: string
    patterns_identified: string[]
    strengths: string[]
    weaknesses: string[]
    recommendations: string[]
    focus_for_next_week: string
    risk_adjustment: string
  }
  message?: string
}

export interface TradeLearning {
  id: number
  trade_id: string
  timestamp: string
  symbol: string
  direction: string
  session: string
  setup_type: string
  profit_loss: number
  r_multiple: number
  outcome: 'win' | 'loss' | 'breakeven'
  grade: string
  analysis: string
  what_went_right: string[] | null
  what_went_wrong: string[] | null
  learnings: string[] | null
  would_take_again: boolean
}

export interface KnowledgeEntry {
  category: string
  key: string
  insight: string
  confidence: number
  sample_size: number
  win_rate: number
  avg_r: number
  expires_at: string
}

export interface WeeklyLearningReport {
  week_start: string
  week_end: string
  performance_grade: string
  summary: string
  total_trades: number
  wins: number
  losses: number
  total_pnl: number
  total_r: number
  patterns_identified: string[] | null
  recurring_mistakes: string[] | null
  winning_patterns: string[] | null
  recommendations: string[] | null
  symbol_insights: Record<string, string> | null
  session_insights: Record<string, string> | null
  focus_area: string
  best_setup: string
  created_at: string
}

export interface LearningStats {
  total_learnings: number
  by_grade: Record<string, number>
  by_outcome: Record<string, number>
  by_symbol: Record<string, number>
  recent_mistakes_count: number
  winning_patterns_count: number
  knowledge_entries: number
}

export interface PendingOrder {
  ticket: number
  symbol: string
  order_type: 'buy_limit' | 'sell_limit' | 'buy_stop' | 'sell_stop'
  direction: 'long' | 'short'
  volume: number
  price: number
  stop_loss: number
  take_profit: number
  expiration?: string
  comment?: string
  time_placed: string
}

export interface PendingOrderRequest {
  symbol: string
  order_type: 'buy_limit' | 'sell_limit' | 'buy_stop' | 'sell_stop'
  volume: number
  price: number
  stop_loss: number
  take_profit: number
  expiration_minutes?: number
  comment?: string
}

export interface OrderResult {
  success: boolean
  order_id?: string
  ticket?: number
  message: string
}

export interface IntelligenceStatus {
  enabled: boolean
  available: boolean
  firecrawl_sdk: boolean
  api_key_configured: boolean
  refresh_minutes: number
  cached_keys: string[]
  cache_status: Record<string, { expired: boolean; age_minutes: number }>
  last_refresh?: string
  dxy_available: boolean
  cot_available: boolean
  news_available: boolean
  central_bank_available: boolean
}

export interface DXYAnalysis {
  trend: 'bullish' | 'bearish' | 'neutral'
  bias?: string
  current_value?: number
  change_percent?: number
  key_level?: number
  impact_on_eur?: string
  impact_on_gbp?: string
  last_updated?: string
  error?: string
}

export interface COTData {
  currency: string
  net_position: number
  change_weekly: number
  positioning: 'net_long' | 'net_short' | 'neutral'
  extreme_level: boolean
  interpretation: string
  last_updated: string
}

export interface BreakingNews {
  headline: string
  source: string
  timestamp: string
  impact: 'high' | 'medium' | 'low'
  currencies_affected: string[]
  sentiment: 'bullish' | 'bearish' | 'neutral'
}

export interface VIXSentiment {
  level: number | null
  sentiment: 'extreme_fear' | 'fear' | 'neutral' | 'complacency'
  note: string
  risk_mode: 'risk_on' | 'risk_off' | 'neutral'
  error?: string
}

export interface RetailSentiment {
  bias: 'extreme_long' | 'long' | 'neutral' | 'short' | 'extreme_short' | 'unknown'
  long_percent?: number
  short_percent?: number
  contrarian_signal: 'long' | 'short' | 'neutral' | 'unknown'
  note?: string
  error?: string
}

export interface CurrencyStrength {
  rankings: { currency: string; rank: number; status: string }[]
  strongest: string | null
  weakest: string | null
  recommendation: string | null
  error?: string
}

export interface TradingViewTechnical {
  consensus: 'strong_buy' | 'buy' | 'neutral' | 'sell' | 'strong_sell' | 'unknown'
  signal: 'buy' | 'sell' | 'neutral'
  strength: 'strong' | 'moderate' | 'weak'
  error?: string
}

export interface RateExpectations {
  fed: {
    next_move: 'hike' | 'cut' | 'hold' | 'uncertain' | 'unknown'
    probability?: string
    usd_impact?: 'bullish' | 'bearish' | 'neutral'
  }
  error?: string
}

export interface CommodityCorrelation {
  commodity: string
  trend: 'bullish' | 'bearish' | 'neutral' | 'unknown'
  currency_implication: {
    CAD?: string
    AUD?: string
    pair_recommendation?: string
    reason?: string
    safe_haven?: string
  }
  error?: string
}

export interface SocialSentiment {
  sentiment: 'bullish' | 'slightly_bullish' | 'neutral' | 'slightly_bearish' | 'bearish' | 'unknown'
  volume: 'high' | 'medium' | 'low'
  bullish_mentions: number
  bearish_mentions: number
  note: string
  contrarian_signal: 'long' | 'short' | 'neutral'
  error?: string
}

export interface OptionsFlow {
  flow: 'bullish' | 'bearish' | 'neutral'
  expiries: { level: number; note: string }[]
  magnet_levels: number[]
  note: string
  error?: string
}

export interface BondYields {
  us_10y: number | null
  de_10y: number | null
  spread: number | null
  implication: string
  eurusd_bias: 'bullish' | 'bearish' | 'neutral'
  error?: string
}

export interface BTCDominance {
  dominance: number | null
  trend: 'rising' | 'falling' | 'stable' | 'unknown'
  altcoin_sentiment: 'bullish' | 'bearish' | 'neutral' | 'unknown'
  note: string
  eth_implication: string
  error?: string
}

export interface EconomicSurprise {
  us: 'positive' | 'negative' | 'neutral' | 'unknown'
  eu: 'positive' | 'negative' | 'neutral' | 'unknown'
  uk: 'positive' | 'negative' | 'neutral' | 'unknown'
  implications?: Record<string, string>
  eurusd_bias?: 'bullish' | 'bearish' | 'neutral'
  error?: string
}

export interface SeasonalPattern {
  current_month: string
  current_month_bias: 'bullish' | 'bearish' | 'neutral' | 'unknown'
  historical_accuracy: number
  note: string
  confidence: 'high' | 'moderate' | 'low'
  error?: string
}

export interface IntermarketAnalysis {
  spx_trend: 'bullish' | 'bearish' | 'neutral' | 'unknown'
  equity_sentiment: 'risk_on' | 'risk_off' | 'neutral'
  vix_sentiment?: string
  dxy_trend?: string
  gold_trend?: string
  risk_environment: 'strong_risk_on' | 'risk_on' | 'neutral' | 'risk_off' | 'strong_risk_off' | 'unknown'
  error?: string
}

export interface CompleteAnalysis {
  symbol: string
  overall_bias: 'strong_bullish' | 'bullish' | 'neutral' | 'bearish' | 'strong_bearish'
  bias_strength: number
  bullish_signals: number
  bearish_signals: number
  total_signals: number
  dxy: DXYAnalysis
  vix: VIXSentiment
  retail_sentiment: RetailSentiment
  currency_strength: CurrencyStrength
  tradingview_technical: TradingViewTechnical
  rate_expectations: RateExpectations
  oil_correlation: CommodityCorrelation
  gold_correlation: CommodityCorrelation
  social_sentiment: SocialSentiment
  options_flow: OptionsFlow
  bond_yields: BondYields
  economic_surprise: EconomicSurprise
  seasonal_pattern: SeasonalPattern
  intermarket: IntermarketAnalysis
  btc_dominance: BTCDominance | null
  timestamp: string
  is_crypto: boolean
}

// =========================================================================
// DEEP RESEARCH (AI-POWERED) INTERFACES
// =========================================================================

export interface GeopoliticalEvent {
  headline: string
  source: string
  impact_level: 'low' | 'medium' | 'high' | 'extreme'
  affected_currencies: string[]
  summary: string
  region: string
}

export interface GeopoliticalAnalysis {
  risk_level: 'low' | 'medium' | 'high' | 'extreme'
  events: GeopoliticalEvent[]
  trading_recommendation: string
  safe_haven_demand: string
  risk_currencies_warning: string
  timestamp: string
}

export interface CentralBankStance {
  bank: string
  stance: 'hawkish' | 'dovish' | 'neutral'
  current_rate?: number
  next_meeting?: string
  expected_action: 'hike' | 'cut' | 'hold'
  probability?: number
  key_statement: string
  currency_impact: string
}

export interface CentralBankAnalysis {
  fed?: CentralBankStance
  ecb?: CentralBankStance
  boe?: CentralBankStance
  boj?: CentralBankStance
  rba?: CentralBankStance
  boc?: CentralBankStance
  divergence_plays: string[]
  overall_bias: string
  timestamp: string
}

export interface MarketTrend {
  market: string
  trend: 'bullish' | 'bearish' | 'neutral'
  current_value?: number
  change_percent?: number
  key_level?: number
}

export interface DeepIntermarketAnalysis {
  spx?: MarketTrend
  vix?: MarketTrend
  dxy?: MarketTrend
  gold?: MarketTrend
  oil?: MarketTrend
  us10y?: MarketTrend
  risk_environment: 'strong_risk_on' | 'risk_on' | 'neutral' | 'risk_off' | 'strong_risk_off'
  correlations_normal: boolean
  anomalies: string[]
  trading_implications: string[]
  timestamp: string
}

export interface SymbolFundamentals {
  symbol: string
  base_currency: string
  quote_currency: string
  fundamental_bias: 'bullish' | 'bearish' | 'neutral'
  key_drivers: string[]
  upcoming_events: string[]
  rate_differential?: number
  rate_differential_trend: 'widening' | 'narrowing' | 'stable'
  economic_strength_comparison: string
  trade_recommendation: string
  confidence: number
  timestamp: string
}

export interface DeepResearchResponse {
  available: boolean
  source?: string
  data?: GeopoliticalAnalysis | CentralBankAnalysis | DeepIntermarketAnalysis | SymbolFundamentals
  cached?: boolean
  message?: string
}

export interface ComprehensiveIntelligence {
  available: boolean
  symbol: string
  timestamp: string
  overall_risk_level: 'low' | 'normal' | 'elevated' | 'high'
  trading_environment: 'excellent' | 'good' | 'normal' | 'caution' | 'difficult' | 'avoid'
  key_themes: string[]
  warnings: string[]
  geopolitical?: GeopoliticalAnalysis
  central_banks?: CentralBankAnalysis
  intermarket?: DeepIntermarketAnalysis
  claude_context?: string
}

export interface ExtractedDataResponse {
  available: boolean
  source?: string
  data?: any
  cached?: boolean
  message?: string
}

export interface AMDPhaseData {
  current_phase: 'accumulation' | 'manipulation' | 'distribution' | 'unknown'
  judas_swing_detected: boolean
  judas_direction?: 'bullish' | 'bearish'
  manipulation_complete: boolean
  expected_direction?: 'long' | 'short'
  confidence: number
}

export interface PerformanceSummary {
  bot_running: boolean
  timestamp: string
  account?: {
    equity: number
    balance: number
    profit: number
  }
  goal?: {
    percent: number
    current: number
    remaining: number
    multiple_achieved: number
  }
  scaling?: ScalingStatus
  sessions?: {
    current: string
    best: string | null
    worst: string | null
    recommendations: string[]
  }
  tier?: ScalingTier
  streaks?: {
    win_streak: number
    loss_streak: number
  }
  error?: string
}

// Backtesting API
export type BacktestRunType = 'ict' | 'replay' | 'optimizer'
export type BacktestRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface BacktestRun {
  id: number
  run_type: BacktestRunType
  status: BacktestRunStatus
  symbol: string | null
  timeframe: string | null
  start_date: string
  end_date: string
  progress_pct: number
  current_step: string
  total_trades: number | null
  win_rate: number | null
  net_profit: number | null
  sharpe_ratio: number | null
  profit_factor: number | null
  max_drawdown: number | null
  estimated_cost: number | null
  actual_cost: number | null
  error_message: string | null
  config_json: Record<string, unknown>
  result_json: Record<string, unknown> | null
  created_at: string
  completed_at: string | null
}

export interface IctBacktestConfig {
  symbol: string
  timeframe: string
  start_date: string
  end_date: string
  initial_balance?: number
  risk_per_trade?: number
  min_risk_reward?: number
}

export interface ReplayBacktestConfig {
  symbol: string
  start_date: string
  end_date: string
  interval_hours?: number
  max_signals?: number
}

export interface OptimizerConfig {
  lookback_days?: number
  n_folds?: number
  train_ratio?: number
  param_space?: Record<string, unknown[]>
}