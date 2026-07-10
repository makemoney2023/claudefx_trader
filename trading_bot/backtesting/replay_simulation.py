"""Replay signal types and baseline trade simulation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from ..services.gate_funnel import resolve_same_bar_tp_sl


@dataclass
class ReplaySignal:
    timestamp: datetime
    symbol: str
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reasoning: str = ""
    trade_type: str = "intraday"
    market_structure: str = "unknown"


@dataclass
class ReplayTrade:
    signal: ReplaySignal
    outcome: str
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl_pips: float = 0.0
    r_multiple: float = 0.0
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    bars_held: int = 0


def simulate_raw_trade(
    signal: ReplaySignal,
    future_data: pd.DataFrame,
    max_bars: int = 200,
    pip_size: float = 0.0001,
) -> ReplayTrade:
    """Raw strategy simulation without execution policy overlays."""
    if future_data is None or future_data.empty:
        return ReplayTrade(signal=signal, outcome="timeout")

    entry = signal.entry_price
    sl = signal.stop_loss
    tp = signal.take_profit
    is_long = signal.direction == "long"

    sl_dist = abs(entry - sl) if sl is not None else 1.0
    _pip = pip_size if pip_size > 0 else 1.0
    mfe = 0.0
    mae = 0.0

    for i, (_, bar) in enumerate(future_data.iterrows()):
        if i >= max_bars:
            break

        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])

        if is_long:
            fav = high - entry
            adv = entry - low
        else:
            fav = entry - low
            adv = high - entry

        mfe = max(mfe, fav / _pip)
        mae = max(mae, adv / _pip)

        sl_hit, tp_hit = resolve_same_bar_tp_sl(
            signal.direction, bar_open, high, low, sl, tp
        )

        if sl_hit:
            pnl_raw = (sl - entry) if is_long else (entry - sl)
            return ReplayTrade(
                signal=signal,
                outcome="loss",
                exit_price=sl,
                exit_time=bar.name if hasattr(bar, "name") else None,
                pnl_pips=pnl_raw / _pip,
                r_multiple=-1.0,
                mfe_pips=mfe,
                mae_pips=mae,
                bars_held=i + 1,
            )

        if tp_hit:
            pnl_raw = (tp - entry) if is_long else (entry - tp)
            r = pnl_raw / sl_dist if sl_dist > 0 else 0
            return ReplayTrade(
                signal=signal,
                outcome="win",
                exit_price=tp,
                exit_time=bar.name if hasattr(bar, "name") else None,
                pnl_pips=pnl_raw / _pip,
                r_multiple=r,
                mfe_pips=mfe,
                mae_pips=mae,
                bars_held=i + 1,
            )

    last_close = float(future_data.iloc[-1]["close"])
    pnl_raw = (last_close - entry) if is_long else (entry - last_close)
    r = pnl_raw / sl_dist if sl_dist > 0 else 0
    return ReplayTrade(
        signal=signal,
        outcome="timeout",
        exit_price=last_close,
        pnl_pips=pnl_raw / _pip,
        r_multiple=r,
        mfe_pips=mfe,
        mae_pips=mae,
        bars_held=len(future_data),
    )
