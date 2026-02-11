"""
Report generation for backtesting results.

Generates:
- HTML reports with charts
- Summary statistics
- Trade analysis
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from .engine import BacktestResult
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestReport:
    """Container for backtest report data."""
    result: BacktestResult
    generated_at: datetime
    report_path: Optional[str] = None


def generate_html_report(
    result: BacktestResult,
    output_dir: str = "reports"
) -> str:
    """
    Generate an HTML report for backtest results.
    
    Args:
        result: BacktestResult from backtester
        output_dir: Directory to save report
        
    Returns:
        Path to generated report
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backtest_{result.config.symbol}_{timestamp}.html"
    filepath = output_path / filename
    
    # Generate HTML content
    html = _generate_html_content(result)
    
    with open(filepath, 'w') as f:
        f.write(html)
    
    logger.info(f"Generated backtest report: {filepath}")
    return str(filepath)


def _generate_html_content(result: BacktestResult) -> str:
    """Generate HTML content for the report."""
    m = result.metrics
    
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report - {result.config.symbol}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            line-height: 1.6;
            padding: 2rem;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ font-size: 2rem; margin-bottom: 0.5rem; }}
        h2 {{ font-size: 1.25rem; margin-bottom: 1rem; color: #94a3b8; }}
        h3 {{ font-size: 1rem; margin-bottom: 0.5rem; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid #334155;
        }}
        .meta {{ color: #64748b; font-size: 0.875rem; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: #1e293b;
            border-radius: 0.5rem;
            padding: 1.25rem;
            border: 1px solid #334155;
        }}
        .card-title {{ font-size: 0.875rem; color: #64748b; margin-bottom: 0.5rem; }}
        .card-value {{ font-size: 1.5rem; font-weight: 600; }}
        .positive {{ color: #22c55e; }}
        .negative {{ color: #ef4444; }}
        .neutral {{ color: #94a3b8; }}
        .chart-container {{ 
            background: #1e293b;
            border-radius: 0.5rem;
            padding: 1.5rem;
            border: 1px solid #334155;
            margin-bottom: 2rem;
        }}
        .chart-title {{ margin-bottom: 1rem; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
        }}
        th, td {{
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        th {{ color: #64748b; font-weight: 500; text-transform: uppercase; font-size: 0.75rem; }}
        .section {{ margin-bottom: 2rem; }}
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }}
        @media (max-width: 768px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>Backtest Report</h1>
                <h2>{result.config.symbol} - {result.config.timeframe}</h2>
            </div>
            <div class="meta">
                <div>{result.config.start_date.strftime('%Y-%m-%d')} to {result.config.end_date.strftime('%Y-%m-%d')}</div>
                <div>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            </div>
        </div>

        <!-- Key Metrics -->
        <div class="grid">
            <div class="card">
                <div class="card-title">Total Trades</div>
                <div class="card-value">{m.total_trades}</div>
            </div>
            <div class="card">
                <div class="card-title">Win Rate</div>
                <div class="card-value {'positive' if m.win_rate >= 0.5 else 'negative'}">{m.win_rate*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-title">Net Profit</div>
                <div class="card-value {'positive' if m.net_profit >= 0 else 'negative'}">${m.net_profit:,.2f}</div>
            </div>
            <div class="card">
                <div class="card-title">Profit Factor</div>
                <div class="card-value {'positive' if m.profit_factor >= 1 else 'negative'}">{m.profit_factor:.2f}</div>
            </div>
            <div class="card">
                <div class="card-title">Total R</div>
                <div class="card-value {'positive' if m.total_r >= 0 else 'negative'}">{m.total_r:.2f}R</div>
            </div>
            <div class="card">
                <div class="card-title">Max Drawdown</div>
                <div class="card-value negative">{m.max_drawdown*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-title">Sharpe Ratio</div>
                <div class="card-value {'positive' if m.sharpe_ratio >= 1 else 'neutral'}">{m.sharpe_ratio:.2f}</div>
            </div>
            <div class="card">
                <div class="card-title">Expectancy</div>
                <div class="card-value {'positive' if m.expectancy >= 0 else 'negative'}">${m.expectancy:.2f}</div>
            </div>
        </div>

        <!-- Equity Chart -->
        <div class="chart-container">
            <h3 class="chart-title">Equity Curve</h3>
            <canvas id="equityChart" height="100"></canvas>
        </div>

        <div class="two-col">
            <!-- Detailed Stats -->
            <div class="section">
                <div class="card">
                    <h3 style="margin-bottom: 1rem;">Trade Statistics</h3>
                    <table>
                        <tr><td>Wins</td><td class="positive">{m.wins}</td></tr>
                        <tr><td>Losses</td><td class="negative">{m.losses}</td></tr>
                        <tr><td>Average Win</td><td class="positive">${m.avg_win:.2f}</td></tr>
                        <tr><td>Average Loss</td><td class="negative">${m.avg_loss:.2f}</td></tr>
                        <tr><td>Largest Win</td><td class="positive">${m.largest_win:.2f}</td></tr>
                        <tr><td>Largest Loss</td><td class="negative">${m.largest_loss:.2f}</td></tr>
                        <tr><td>Average R</td><td>{m.avg_r:.2f}R</td></tr>
                        <tr><td>Max Consecutive Wins</td><td>{m.max_consecutive_wins}</td></tr>
                        <tr><td>Max Consecutive Losses</td><td>{m.max_consecutive_losses}</td></tr>
                    </table>
                </div>
            </div>

            <!-- Risk Metrics -->
            <div class="section">
                <div class="card">
                    <h3 style="margin-bottom: 1rem;">Risk Metrics</h3>
                    <table>
                        <tr><td>Max Drawdown</td><td class="negative">{m.max_drawdown*100:.2f}%</td></tr>
                        <tr><td>Drawdown Duration</td><td>{m.max_drawdown_duration} bars</td></tr>
                        <tr><td>Recovery Factor</td><td>{m.recovery_factor:.2f}</td></tr>
                        <tr><td>Sharpe Ratio</td><td>{m.sharpe_ratio:.2f}</td></tr>
                        <tr><td>Sortino Ratio</td><td>{m.sortino_ratio:.2f}</td></tr>
                        <tr><td>Calmar Ratio</td><td>{m.calmar_ratio:.2f}</td></tr>
                        <tr><td>Expectancy (R)</td><td>{m.expectancy_r:.2f}R</td></tr>
                        <tr><td>Avg Trade Duration</td><td>{m.avg_trade_duration:.1f} hours</td></tr>
                        <tr><td>Trades/Day</td><td>{m.trades_per_day:.2f}</td></tr>
                    </table>
                </div>
            </div>
        </div>

        <!-- ICT Concept Performance -->
        {_generate_ict_stats_html(m.ict_concept_stats)}

        <!-- Monte Carlo Results -->
        {_generate_monte_carlo_html(result.monte_carlo)}

        <!-- Configuration -->
        <div class="section">
            <div class="card">
                <h3 style="margin-bottom: 1rem;">Configuration</h3>
                <table>
                    <tr><td>Initial Balance</td><td>${result.config.initial_balance:,.2f}</td></tr>
                    <tr><td>Risk Per Trade</td><td>{result.config.risk_per_trade*100:.1f}%</td></tr>
                    <tr><td>Min Risk/Reward</td><td>{result.config.min_risk_reward:.1f}:1</td></tr>
                    <tr><td>Max Daily Trades</td><td>{result.config.max_daily_trades}</td></tr>
                    <tr><td>Spread</td><td>{result.config.spread_pips} pips</td></tr>
                    <tr><td>Slippage</td><td>{result.config.slippage_pips} pips</td></tr>
                </table>
            </div>
        </div>
    </div>

    <script>
        const equityData = {json.dumps(result.equity_curve)};
        const ctx = document.getElementById('equityChart').getContext('2d');
        
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: equityData.map((_, i) => i),
                datasets: [{{
                    label: 'Equity',
                    data: equityData,
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                scales: {{
                    x: {{ display: false }},
                    y: {{
                        grid: {{ color: '#334155' }},
                        ticks: {{ color: '#94a3b8' }}
                    }}
                }},
                plugins: {{
                    legend: {{ display: false }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    return html


def _generate_ict_stats_html(ict_stats: dict) -> str:
    """Generate HTML for ICT concept statistics."""
    if not ict_stats:
        return ""
    
    rows = ""
    for concept, stats in ict_stats.items():
        win_class = "positive" if stats['win_rate'] >= 0.5 else "negative"
        r_class = "positive" if stats['avg_r'] >= 0 else "negative"
        pnl_class = "positive" if stats['total_pnl'] >= 0 else "negative"
        
        rows += f"""
        <tr>
            <td style="text-transform: capitalize;">{concept.replace('_', ' ')}</td>
            <td>{stats['trades']}</td>
            <td>{stats['wins']}</td>
            <td class="{win_class}">{stats['win_rate']*100:.1f}%</td>
            <td class="{r_class}">{stats['avg_r']:.2f}R</td>
            <td class="{pnl_class}">${stats['total_pnl']:.2f}</td>
        </tr>
        """
    
    return f"""
    <div class="section">
        <div class="card">
            <h3 style="margin-bottom: 1rem;">ICT Concept Performance</h3>
            <table>
                <thead>
                    <tr>
                        <th>Concept</th>
                        <th>Trades</th>
                        <th>Wins</th>
                        <th>Win Rate</th>
                        <th>Avg R</th>
                        <th>Total P/L</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
    </div>
    """


def _generate_monte_carlo_html(mc_results: dict) -> str:
    """Generate HTML for Monte Carlo results."""
    if not mc_results:
        return ""
    
    profit_prob = mc_results.get('probability_of_profit', 0) * 100
    prob_class = "positive" if profit_prob >= 50 else "negative"
    
    return f"""
    <div class="section">
        <div class="card">
            <h3 style="margin-bottom: 1rem;">Monte Carlo Analysis ({mc_results.get('num_simulations', 0)} simulations)</h3>
            <table>
                <tr><td>Probability of Profit</td><td class="{prob_class}">{profit_prob:.1f}%</td></tr>
                <tr><td>Final Balance (Mean)</td><td>${mc_results.get('final_balance_mean', 0):,.2f}</td></tr>
                <tr><td>Final Balance (Median)</td><td>${mc_results.get('final_balance_median', 0):,.2f}</td></tr>
                <tr><td>5th Percentile</td><td class="negative">${mc_results.get('final_balance_5th_percentile', 0):,.2f}</td></tr>
                <tr><td>95th Percentile</td><td class="positive">${mc_results.get('final_balance_95th_percentile', 0):,.2f}</td></tr>
                <tr><td>Max Drawdown (Mean)</td><td class="negative">{mc_results.get('max_drawdown_mean', 0)*100:.1f}%</td></tr>
                <tr><td>Max Drawdown (95th)</td><td class="negative">{mc_results.get('max_drawdown_95th_percentile', 0)*100:.1f}%</td></tr>
            </table>
        </div>
    </div>
    """
