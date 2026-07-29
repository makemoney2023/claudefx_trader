"""News gates can be disabled via TRADING_NEWS_GATES_ENABLED."""

from unittest.mock import MagicMock

import pytest


def test_news_allows_trading_when_gates_disabled(monkeypatch):
    from trading_bot.config import settings
    from trading_bot.services.live_trade_gates import news_allows_trading

    monkeypatch.setattr(settings.trading, "news_gates_enabled", False)
    news = MagicMock()
    news.should_trade.return_value = False

    allowed, reason = news_allows_trading(news)
    assert allowed is True
    assert "disabled" in reason
    news.should_trade.assert_not_called()


def test_news_blocks_when_gates_enabled_and_unreliable(monkeypatch):
    from trading_bot.config import settings
    from trading_bot.services.live_trade_gates import news_allows_trading

    monkeypatch.setattr(settings.trading, "news_gates_enabled", True)
    news = MagicMock()
    news.should_trade.return_value = False

    allowed, reason = news_allows_trading(news)
    assert allowed is False
    assert "fail-closed" in reason
