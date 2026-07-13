"""Shared helpers for source-level pipeline wiring tests."""

from __future__ import annotations

from pathlib import Path


def _services_dir() -> Path:
    import trading_bot.main as main_module

    return Path(main_module.__file__).parent / "services"


def _execution_dir() -> Path:
    import trading_bot.main as main_module

    return Path(main_module.__file__).parent / "execution"


def analyze_and_trade_source() -> str:
    """Source of the live analyze-and-trade pipeline modules."""
    services = _services_dir()
    execution = _execution_dir()
    parts = [
        services / "analyze_and_trade_runner.py",
        services / "expanded_analysis.py",
        services / "claude_analysis_stage.py",
        services / "analysis_orchestrator.py",
        services / "post_claude_gates.py",
        execution / "trade_execution.py",
        execution / "trade_fill_handler.py",
    ]
    return "\n".join(path.read_text() for path in parts if path.exists())


def pipeline_source() -> str:
    """Combined main + pipeline module source for wiring checks."""
    import trading_bot.main as main_module

    return Path(main_module.__file__).read_text() + analyze_and_trade_source()
