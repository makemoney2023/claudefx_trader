"""
LLM integration modules for chart analysis and trade decisions.

Provides Claude Opus 5 integration for:
- Chart screenshot analysis using vision API
- Strategy context loading
- Trade signal generation
- Risk/reward evaluation
"""

from .claude_client import ClaudeClient
from .context_builder import ContextBuilder
from .prompts import PromptTemplates

__all__ = [
    "ClaudeClient",
    "ContextBuilder",
    "PromptTemplates",
]
