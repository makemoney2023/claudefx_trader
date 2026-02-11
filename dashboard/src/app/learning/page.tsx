'use client'

import { useEffect, useState } from 'react'
import { api, TradeLearning, KnowledgeEntry, WeeklyLearningReport, LearningStats } from '@/lib/api'
import { BookOpen, Brain, Target, AlertTriangle, TrendingUp, Award, Lightbulb, RefreshCw } from 'lucide-react'

export default function LearningPage() {
  const [stats, setStats] = useState<LearningStats | null>(null)
  const [recentLearnings, setRecentLearnings] = useState<TradeLearning[]>([])
  const [mistakes, setMistakes] = useState<string[]>([])
  const [patterns, setPatterns] = useState<string[]>([])
  const [knowledge, setKnowledge] = useState<KnowledgeEntry[]>([])
  const [weeklyReport, setWeeklyReport] = useState<WeeklyLearningReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [consolidating, setConsolidating] = useState(false)
  const [reviewing, setReviewing] = useState(false)

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      setLoading(true)
      setError(null)

      const [statsRes, learningsRes, mistakesRes, patternsRes, knowledgeRes, reportRes] = await Promise.all([
        api.getLearningStats(),
        api.getLearningRecent({ limit: 20 }),
        api.getLearningMistakes(5),
        api.getLearningPatterns(5),
        api.getLearningKnowledge(),
        api.getLearningWeeklyReport()
      ])

      setStats(statsRes)
      setRecentLearnings(learningsRes)
      setMistakes(mistakesRes.mistakes)
      setPatterns(patternsRes.patterns)
      setKnowledge(knowledgeRes)
      setWeeklyReport(reportRes.report)
    } catch (err) {
      console.error('Error fetching learning data:', err)
      setError(err instanceof Error ? err.message : 'Failed to fetch learning data')
    } finally {
      setLoading(false)
    }
  }

  const handleConsolidate = async () => {
    try {
      setConsolidating(true)
      const result = await api.postLearningConsolidate()
      if (result.success) {
        alert(`Consolidation complete! Grade: ${result.grade}, Trades reviewed: ${result.trades_reviewed}`)
        fetchData()
      } else {
        alert(result.message || 'Consolidation failed')
      }
    } catch (err) {
      console.error('Error consolidating:', err)
      alert('Failed to consolidate. Make sure you have an API key configured.')
    } finally {
      setConsolidating(false)
    }
  }

  const handleReviewHistory = async () => {
    try {
      setReviewing(true)
      const result = await api.postLearningReviewHistory(50, -10) // Review 50 trades with losses > $10
      if (result.success) {
        alert(`Reviewed ${result.reviewed} of ${result.total_found} historical trades!`)
        fetchData()
      } else {
        alert(result.message || 'Review failed')
      }
    } catch (err) {
      console.error('Error reviewing history:', err)
      alert('Failed to review history. Make sure Claude API key is configured.')
    } finally {
      setReviewing(false)
    }
  }

  const getGradeColor = (grade: string) => {
    switch (grade.toUpperCase()) {
      case 'A': return 'bg-emerald-500 text-white'
      case 'B': return 'bg-blue-500 text-white'
      case 'C': return 'bg-yellow-500 text-black'
      case 'D': return 'bg-orange-500 text-white'
      case 'F': return 'bg-red-500 text-white'
      default: return 'bg-gray-500 text-white'
    }
  }

  const getOutcomeColor = (outcome: string) => {
    switch (outcome.toLowerCase()) {
      case 'win': return 'text-emerald-400'
      case 'loss': return 'text-red-400'
      case 'breakeven': return 'text-yellow-400'
      default: return 'text-gray-400'
    }
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-screen">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto text-indigo-400" />
          <p className="mt-2 text-gray-400">Loading learning data...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-4">
          <p className="text-red-400">{error}</p>
          <button 
            onClick={fetchData}
            className="mt-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded text-white text-sm"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6 bg-gray-900 min-h-screen">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Brain className="w-8 h-8 text-indigo-400" />
          <div>
            <h1 className="text-2xl font-bold text-white">Claude Learning System</h1>
            <p className="text-gray-400">Trade review insights and continuous improvement</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchData}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg flex items-center gap-2 text-white"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={handleReviewHistory}
            disabled={reviewing}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded-lg flex items-center gap-2 text-white"
          >
            {reviewing ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin" />
                Reviewing...
              </>
            ) : (
              <>
                <BookOpen className="w-4 h-4" />
                Review History (50 trades)
              </>
            )}
          </button>
          <button
            onClick={handleConsolidate}
            disabled={consolidating}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg flex items-center gap-2 text-white"
          >
            {consolidating ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin" />
                Consolidating...
              </>
            ) : (
              <>
                <Award className="w-4 h-4" />
                Run Weekly Consolidation
              </>
            )}
          </button>
        </div>
      </div>

      {/* Stats Overview */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <BookOpen className="w-4 h-4" />
              Total Learnings
            </div>
            <p className="text-2xl font-bold text-white mt-1">{stats.total_learnings}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <AlertTriangle className="w-4 h-4" />
              Mistakes Tracked
            </div>
            <p className="text-2xl font-bold text-red-400 mt-1">{stats.recent_mistakes_count}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <TrendingUp className="w-4 h-4" />
              Winning Patterns
            </div>
            <p className="text-2xl font-bold text-emerald-400 mt-1">{stats.winning_patterns_count}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <Lightbulb className="w-4 h-4" />
              Knowledge Entries
            </div>
            <p className="text-2xl font-bold text-blue-400 mt-1">{stats.knowledge_entries}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <Target className="w-4 h-4" />
              Wins
            </div>
            <p className="text-2xl font-bold text-emerald-400 mt-1">{stats.by_outcome?.win || 0}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <Target className="w-4 h-4" />
              Losses
            </div>
            <p className="text-2xl font-bold text-red-400 mt-1">{stats.by_outcome?.loss || 0}</p>
          </div>
        </div>
      )}

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Weekly Report */}
        {weeklyReport && (
          <div className="bg-gray-800 rounded-lg p-6 lg:col-span-2">
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Award className="w-5 h-5 text-indigo-400" />
              Weekly Learning Report
              <span className={`ml-2 px-3 py-1 rounded-full text-sm font-bold ${getGradeColor(weeklyReport.performance_grade)}`}>
                Grade: {weeklyReport.performance_grade}
              </span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
              <div className="bg-gray-700 rounded p-3">
                <p className="text-gray-400 text-sm">Trades Reviewed</p>
                <p className="text-xl font-bold text-white">{weeklyReport.total_trades}</p>
              </div>
              <div className="bg-gray-700 rounded p-3">
                <p className="text-gray-400 text-sm">Win/Loss</p>
                <p className="text-xl font-bold">
                  <span className="text-emerald-400">{weeklyReport.wins}</span>
                  <span className="text-gray-400"> / </span>
                  <span className="text-red-400">{weeklyReport.losses}</span>
                </p>
              </div>
              <div className="bg-gray-700 rounded p-3">
                <p className="text-gray-400 text-sm">Total R</p>
                <p className={`text-xl font-bold ${weeklyReport.total_r >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {weeklyReport.total_r >= 0 ? '+' : ''}{weeklyReport.total_r.toFixed(1)}R
                </p>
              </div>
            </div>
            <p className="text-gray-300 mb-4">{weeklyReport.summary}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-gray-400 text-sm mb-2">🎯 Focus Area</p>
                <p className="text-white bg-indigo-900/30 rounded p-2">{weeklyReport.focus_area || 'Not specified'}</p>
              </div>
              <div>
                <p className="text-gray-400 text-sm mb-2">📈 Best Setup</p>
                <p className="text-white bg-emerald-900/30 rounded p-2">{weeklyReport.best_setup || 'Not identified'}</p>
              </div>
            </div>
          </div>
        )}

        {/* Recent Mistakes */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-red-400" />
            Recent Mistakes to Avoid
          </h2>
          {mistakes.length > 0 ? (
            <ul className="space-y-2">
              {mistakes.map((mistake, index) => (
                <li key={index} className="flex items-start gap-2 text-gray-300">
                  <span className="text-red-400 mt-1">•</span>
                  <span>{mistake}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400">No mistakes recorded yet</p>
          )}
        </div>

        {/* Winning Patterns */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-emerald-400" />
            Winning Patterns
          </h2>
          {patterns.length > 0 ? (
            <ul className="space-y-2">
              {patterns.map((pattern, index) => (
                <li key={index} className="flex items-start gap-2 text-gray-300">
                  <span className="text-emerald-400 mt-1">•</span>
                  <span>{pattern}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400">No winning patterns identified yet</p>
          )}
        </div>

        {/* Knowledge Base */}
        <div className="bg-gray-800 rounded-lg p-6 lg:col-span-2">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Lightbulb className="w-5 h-5 text-yellow-400" />
            Knowledge Base
          </h2>
          {knowledge.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-gray-400 text-sm border-b border-gray-700">
                    <th className="pb-2">Category</th>
                    <th className="pb-2">Key</th>
                    <th className="pb-2">Insight</th>
                    <th className="pb-2">Confidence</th>
                    <th className="pb-2">Win Rate</th>
                    <th className="pb-2">Avg R</th>
                  </tr>
                </thead>
                <tbody className="text-gray-300">
                  {knowledge.map((entry, index) => (
                    <tr key={index} className="border-b border-gray-700/50">
                      <td className="py-2">
                        <span className="px-2 py-1 rounded bg-gray-700 text-xs">{entry.category}</span>
                      </td>
                      <td className="py-2 font-mono text-sm">{entry.key}</td>
                      <td className="py-2 text-sm">{entry.insight}</td>
                      <td className="py-2">
                        <span className={`${entry.confidence >= 0.7 ? 'text-emerald-400' : 'text-yellow-400'}`}>
                          {(entry.confidence * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className="py-2">{(entry.win_rate * 100).toFixed(0)}%</td>
                      <td className="py-2">{entry.avg_r.toFixed(2)}R</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-400">No knowledge entries yet. They will be created during weekly consolidation.</p>
          )}
        </div>

        {/* Grade Distribution */}
        {stats && (
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-white mb-4">Grade Distribution</h2>
            <div className="space-y-3">
              {['A', 'B', 'C', 'D', 'F'].map(grade => {
                const count = stats.by_grade?.[grade] || 0
                const percentage = stats.total_learnings > 0 
                  ? (count / stats.total_learnings * 100).toFixed(0)
                  : 0
                return (
                  <div key={grade} className="flex items-center gap-3">
                    <span className={`w-8 h-8 rounded flex items-center justify-center font-bold ${getGradeColor(grade)}`}>
                      {grade}
                    </span>
                    <div className="flex-1">
                      <div className="h-4 bg-gray-700 rounded overflow-hidden">
                        <div 
                          className={`h-full ${getGradeColor(grade).split(' ')[0]}`}
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                    </div>
                    <span className="text-gray-400 w-16 text-right">{count} ({percentage}%)</span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Symbol Performance */}
        {stats && Object.keys(stats.by_symbol || {}).length > 0 && (
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-white mb-4">Reviews by Symbol</h2>
            <div className="space-y-2">
              {Object.entries(stats.by_symbol)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10)
                .map(([symbol, count]) => (
                  <div key={symbol} className="flex items-center justify-between">
                    <span className="text-gray-300 font-mono">{symbol}</span>
                    <span className="text-white font-semibold">{count}</span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>

      {/* Recent Trade Reviews */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <BookOpen className="w-5 h-5 text-indigo-400" />
          Recent Trade Reviews
        </h2>
        {recentLearnings.length > 0 ? (
          <div className="space-y-4">
            {recentLearnings.map((learning) => (
              <div 
                key={learning.id} 
                className="bg-gray-700/50 rounded-lg p-4 border-l-4"
                style={{
                  borderLeftColor: learning.outcome === 'win' ? '#10b981' : learning.outcome === 'loss' ? '#ef4444' : '#f59e0b'
                }}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-white">{learning.symbol}</span>
                    <span className={`px-2 py-0.5 rounded text-xs ${learning.direction === 'long' ? 'bg-emerald-900 text-emerald-300' : 'bg-red-900 text-red-300'}`}>
                      {learning.direction.toUpperCase()}
                    </span>
                    <span className={`px-2 py-1 rounded font-bold text-xs ${getGradeColor(learning.grade)}`}>
                      {learning.grade}
                    </span>
                    <span className={`text-sm ${getOutcomeColor(learning.outcome)}`}>
                      {learning.r_multiple >= 0 ? '+' : ''}{learning.r_multiple.toFixed(1)}R
                    </span>
                  </div>
                  <span className="text-gray-400 text-sm">
                    {new Date(learning.timestamp).toLocaleDateString()}
                  </span>
                </div>
                <p className="text-gray-300 text-sm mb-2">{learning.analysis}</p>
                <div className="flex flex-wrap gap-4 text-xs">
                  {learning.what_went_right && learning.what_went_right.length > 0 && (
                    <div>
                      <span className="text-emerald-400">✓ Right: </span>
                      <span className="text-gray-400">{learning.what_went_right.join(', ')}</span>
                    </div>
                  )}
                  {learning.what_went_wrong && learning.what_went_wrong.length > 0 && (
                    <div>
                      <span className="text-red-400">✗ Wrong: </span>
                      <span className="text-gray-400">{learning.what_went_wrong.join(', ')}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-gray-400">No trade reviews yet. Reviews are created when trades close.</p>
        )}
      </div>
    </div>
  )
}
