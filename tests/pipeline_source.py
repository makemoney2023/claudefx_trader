"""Shared helpers for source-level pipeline wiring tests."""

from __future__ import annotations

from pathlib import Path


def _runner_path() -> Path:
    import trading_bot.main as main_module

    return Path(main_module.__file__).parent / "services" / "analyze_and_trade_runner.py"


def analyze_and_trade_source() -> str:
    """Source of the extracted analyze-and-trade pipeline."""
    return _runner_path().read_text()


def pipeline_source() -> str:
    """Combined main + runner source for wiring checks after extraction."""
    import trading_bot.main as main_module

    return Path(main_module.__file__).read_text() + analyze_and_trade_source()
