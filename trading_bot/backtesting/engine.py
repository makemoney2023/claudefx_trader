"""
Main backtesting engine.

Orchestrates the backtest:
- Loads historical data
- Runs strategy on each bar
- Manages positions via simulator
- Collects metrics
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
import pandas as pd
import numpy as np

from .data_loader import DataLoader, DataConfig
from .simulator import OrderSimulator, SimulatedPosition
from .metrics import PerformanceMetrics, calculate_metrics, run_monte_carlo
from ..analysis import (
    MarketStructureAnalyzer,
    FVGDetector,
    OrderBlockDetector,
    LiquidityMapper,
    FibonacciAnalyzer,
    PowerOfThreeAnalyzer
)
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for backtest."""
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    
    # Account settings
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.01  # 1% risk
    
    # Trading settings
    min_risk_reward: float = 2.0
    max_daily_trades: int = 3
    
    # Simulation settings
    spread_pips: float = 1.0
    slippage_pips: float = 0.5
    commission_per_lot: float = 7.0
    
    # Data source
    data_source: str = "sample"  # csv, mt5, sample


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    config: BacktestConfig
    metrics: PerformanceMetrics
    
    # Trade data
    trades: List[Dict[str, Any]]
    equity_curve: List[float]
    timestamps: List[datetime]
    
    # Drawdown data
    drawdown_curve: List[float]
    
    # Monte Carlo results
    monte_carlo: Optional[Dict[str, Any]] = None
    
    # Timing
    duration_seconds: float = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "config": {
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe,
                "start_date": self.config.start_date.isoformat(),
                "end_date": self.config.end_date.isoformat(),
                "initial_balance": self.config.initial_balance,
                "risk_per_trade": self.config.risk_per_trade,
            },
            "metrics": self.metrics.to_dict(),
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "monte_carlo": self.monte_carlo,
            "duration_seconds": self.duration_seconds
        }


class Backtester:
    """
    Main backtesting engine for ICT strategies.
    
    Features:
    - Full ICT analysis integration
    - Realistic order simulation
    - Comprehensive metrics
    - Monte Carlo analysis
    """
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        
        # Initialize components
        self.data_loader = DataLoader()
        self.simulator = OrderSimulator(
            initial_balance=config.initial_balance,
            spread_pips=config.spread_pips,
            slippage_pips=config.slippage_pips,
            commission_per_lot=config.commission_per_lot
        )
        
        # Initialize analyzers
        self.structure_analyzer = MarketStructureAnalyzer()
        self.fvg_detector = FVGDetector()
        self.ob_detector = OrderBlockDetector()
        self.liquidity_mapper = LiquidityMapper()
        self.fib_analyzer = FibonacciAnalyzer()
        self.amd_analyzer = PowerOfThreeAnalyzer()
        
        # State tracking
        self.equity_curve: List[float] = []
        self.timestamps: List[datetime] = []
        self.daily_trades: Dict[str, int] = {}
        
        logger.info(f"Backtester initialized for {config.symbol} {config.timeframe}")
    
    def run(
        self,
        strategy_func: Optional[Callable] = None,
        run_monte_carlo_sim: bool = True
    ) -> BacktestResult:
        """
        Run the backtest.
        
        Args:
            strategy_func: Optional custom strategy function
            run_monte_carlo_sim: Whether to run Monte Carlo simulation
            
        Returns:
            BacktestResult with all metrics and trade data
        """
        import time
        start_time = time.time()
        
        logger.info(f"Starting backtest: {self.config.symbol} {self.config.start_date} to {self.config.end_date}")
        
        # Reset simulator
        self.simulator.reset()
        self.equity_curve = [self.config.initial_balance]
        self.timestamps = []
        self.daily_trades = {}
        
        # Load data
        data_config = DataConfig(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            source=self.config.data_source
        )
        df = self.data_loader.load(data_config)
        
        if df.empty:
            raise ValueError("No data loaded for backtest")
        
        logger.info(f"Loaded {len(df)} bars for backtest")
        
        # Use default strategy if none provided
        if strategy_func is None:
            strategy_func = self._default_ict_strategy
        
        # Run through each bar
        lookback = 100  # Bars needed for analysis
        
        for i in range(lookback, len(df)):
            # Get current bar data
            current_bar = df.iloc[i]
            timestamp = df.index[i]
            
            # Get lookback data for analysis
            analysis_df = df.iloc[i-lookback:i+1].copy()
            
            # Update existing positions
            closed = self.simulator.update_positions(
                self.config.symbol,
                current_bar['high'],
                current_bar['low'],
                current_bar['close'],
                timestamp
            )
            
            # Track equity
            self.equity_curve.append(self.simulator.equity)
            self.timestamps.append(timestamp)
            
            # Check daily trade limit
            date_str = timestamp.strftime("%Y-%m-%d")
            if date_str not in self.daily_trades:
                self.daily_trades[date_str] = 0
            
            if self.daily_trades[date_str] >= self.config.max_daily_trades:
                continue
            
            # Skip if we have open positions
            if self.simulator.positions:
                continue
            
            # Run strategy
            signal = strategy_func(
                df=analysis_df,
                current_price=current_bar['close'],
                timestamp=timestamp
            )
            
            if signal and signal.get('direction') in ['long', 'short']:
                # Calculate position size
                position_size = self._calculate_position_size(
                    signal['entry'],
                    signal['stop_loss']
                )
                
                # Place order
                position = self.simulator.place_market_order(
                    symbol=self.config.symbol,
                    direction=signal['direction'],
                    volume=position_size,
                    current_price=signal['entry'],
                    stop_loss=signal['stop_loss'],
                    take_profit=signal['take_profit'],
                    timestamp=timestamp,
                    ict_concepts=signal.get('ict_concepts', {})
                )
                
                if position:
                    self.daily_trades[date_str] += 1
        
        # Close any remaining positions at end
        if self.simulator.positions:
            final_price = df.iloc[-1]['close']
            self.simulator.close_all_positions(
                final_price,
                df.index[-1],
                "backtest_end"
            )
        
        # Calculate metrics
        metrics = calculate_metrics(
            self.simulator.closed_positions,
            self.equity_curve,
            self.config.initial_balance
        )
        
        # Run Monte Carlo if requested
        mc_results = None
        if run_monte_carlo_sim and self.simulator.closed_positions:
            mc_results = run_monte_carlo(
                self.simulator.closed_positions,
                self.config.initial_balance
            )
        
        # Calculate drawdown curve
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown_curve = ((peak - equity) / peak).tolist()
        
        # Compile results
        duration = time.time() - start_time
        
        result = BacktestResult(
            config=self.config,
            metrics=metrics,
            trades=[p.to_dict() for p in self.simulator.closed_positions],
            equity_curve=self.equity_curve,
            timestamps=self.timestamps,
            drawdown_curve=drawdown_curve,
            monte_carlo=mc_results,
            duration_seconds=duration
        )
        
        logger.info(
            f"Backtest completed in {duration:.2f}s: "
            f"{metrics.total_trades} trades, "
            f"Net P/L: ${metrics.net_profit:.2f}, "
            f"Win Rate: {metrics.win_rate*100:.1f}%"
        )
        
        return result
    
    def _default_ict_strategy(
        self,
        df: pd.DataFrame,
        current_price: float,
        timestamp: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Default ICT-based trading strategy.
        
        Entry conditions:
        1. Clear market structure (trend)
        2. Price at OB or in FVG
        3. In OTE zone
        4. Not at major liquidity
        """
        try:
            # Run analysis
            structure = self.structure_analyzer.analyze(df)
            fvg_result = self.fvg_detector.detect(df)
            ob_result = self.ob_detector.detect(df)
            
            # Determine direction from structure
            direction = None
            if structure.trend.value == "bullish":
                direction = "long"
            elif structure.trend.value == "bearish":
                direction = "short"
            else:
                return None
            
            # OTE analysis
            ote = self.fib_analyzer.analyze_ote(df, direction)
            
            # Check for entry conditions
            entry_signal = self._check_entry_conditions(
                direction=direction,
                current_price=current_price,
                fvg_result=fvg_result,
                ob_result=ob_result,
                ote=ote
            )
            
            if not entry_signal:
                return None
            
            # Calculate SL/TP
            pip_value = 0.01 if "JPY" in self.config.symbol else 0.0001
            atr = self._calculate_atr(df, 14)
            
            if direction == "long":
                stop_loss = current_price - (atr * 1.5)
                risk = current_price - stop_loss
                take_profit = current_price + (risk * self.config.min_risk_reward)
            else:
                stop_loss = current_price + (atr * 1.5)
                risk = stop_loss - current_price
                take_profit = current_price - (risk * self.config.min_risk_reward)
            
            return {
                'direction': direction,
                'entry': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'ict_concepts': entry_signal.get('concepts', {})
            }
            
        except Exception as e:
            logger.debug(f"Strategy error: {e}")
            return None
    
    def _check_entry_conditions(
        self,
        direction: str,
        current_price: float,
        fvg_result,
        ob_result,
        ote
    ) -> Optional[Dict[str, Any]]:
        """Check if entry conditions are met."""
        concepts = {}
        
        # Check for FVG entry
        for fvg in fvg_result.active_fvgs:
            if fvg.bottom <= current_price <= fvg.top:
                if direction == "long" and fvg.type.value == "bullish":
                    concepts['fvg'] = True
                elif direction == "short" and fvg.type.value == "bearish":
                    concepts['fvg'] = True
        
        # Check for OB entry
        for ob in ob_result.active_obs:
            if ob.bottom <= current_price <= ob.top:
                if direction == "long" and ob.type.value == "bullish":
                    concepts['order_block'] = True
                elif direction == "short" and ob.type.value == "bearish":
                    concepts['order_block'] = True
        
        # Check OTE zone
        if ote and ote.in_ote:
            concepts['ote'] = True
        
        # Require at least one ICT concept
        if concepts:
            return {'concepts': concepts}
        
        return None
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]
    
    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float
    ) -> float:
        """Calculate position size based on risk."""
        pip_value = 0.01 if "JPY" in self.config.symbol else 0.0001
        
        risk_amount = self.simulator.balance * self.config.risk_per_trade
        risk_pips = abs(entry_price - stop_loss) / pip_value
        
        if risk_pips == 0:
            return 0.01  # Minimum lot
        
        # Position size = Risk Amount / (Risk in Pips * Pip Value * 100000)
        pip_value_per_lot = pip_value * 100000
        position_size = risk_amount / (risk_pips * pip_value_per_lot)
        
        # Round to 2 decimal places and apply limits
        position_size = round(position_size, 2)
        position_size = max(0.01, min(position_size, 10.0))  # 0.01 to 10 lots
        
        return position_size
    
    def optimize(
        self,
        param_grid: Dict[str, List[Any]],
        metric: str = "net_profit"
    ) -> Dict[str, Any]:
        """
        Run parameter optimization.
        
        Args:
            param_grid: Dictionary of parameter names to test values
            metric: Metric to optimize for
            
        Returns:
            Dictionary with best parameters and results
        """
        results = []
        
        # Generate all combinations
        import itertools
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        
        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo))
            
            # Apply parameters to config
            for name, value in params.items():
                if hasattr(self.config, name):
                    setattr(self.config, name, value)
            
            # Run backtest
            result = self.run(run_monte_carlo_sim=False)
            
            results.append({
                'params': params,
                'metrics': result.metrics.to_dict()
            })
        
        # Find best result
        best = max(results, key=lambda x: x['metrics'].get(metric, 0))
        
        return {
            'best_params': best['params'],
            'best_metrics': best['metrics'],
            'all_results': results
        }
