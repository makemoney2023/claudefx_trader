"""
TDD tests for winopt service/API critical fixes:
- News calendar refresh + fail-closed on stale/empty calendar
- Pending order budget reclaim on expire/cancel
- Judge fail-closed on parse/API errors
- API auth on mutating endpoints
"""

import os
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# News calendar reliability
# ---------------------------------------------------------------------------

class TestNewsCalendarReliability:
    def test_empty_calendar_is_unreliable(self, news_service):
        news_service._events = []
        news_service._last_fetch = datetime.now()
        assert news_service.is_calendar_unreliable() is True

    def test_stale_calendar_is_unreliable(self, news_service):
        news_service.set_events([
            {
                'title': 'CPI',
                'datetime': (datetime.now() + timedelta(days=2)).isoformat(),
                'impact': 'high',
                'currency': 'USD',
            }
        ])
        news_service._last_fetch = datetime.now() - timedelta(hours=3)
        assert news_service.is_calendar_unreliable() is True

    def test_fresh_populated_calendar_is_reliable(self, news_service):
        news_service.set_events([
            {
                'title': 'CPI',
                'datetime': (datetime.now() + timedelta(days=2)).isoformat(),
                'impact': 'high',
                'currency': 'USD',
            }
        ])
        assert news_service.is_calendar_unreliable() is False

    def test_should_trade_blocks_when_calendar_unreliable(self, news_service):
        news_service._events = []
        news_service._last_fetch = datetime.now() - timedelta(hours=2)
        assert news_service.should_trade() is False

    def test_should_reduce_size_when_calendar_unreliable(self, news_service):
        news_service._events = []
        news_service._last_fetch = datetime.now() - timedelta(hours=2)
        multiplier, reason = news_service.should_reduce_size('XAUUSD')
        assert multiplier < 1.0
        assert reason

    @pytest.mark.asyncio
    async def test_refresh_calendar_bypasses_cache(self, news_service):
        news_service.set_events([
            {
                'title': 'Old Event',
                'datetime': (datetime.now() + timedelta(days=1)).isoformat(),
                'impact': 'high',
                'currency': 'USD',
            }
        ])
        old_fetch = news_service._last_fetch

        new_events = [
            {
                'title': 'Fresh Event',
                'datetime': (datetime.now() + timedelta(days=3)).isoformat(),
                'impact': 'high',
                'currency': 'USD',
            }
        ]

        with patch.object(news_service, '_fetch_from_firecrawl', AsyncMock(return_value=new_events)):
            result = await news_service.refresh_calendar(force=True)

        assert result is True
        assert news_service._events[0]['title'] == 'Fresh Event'
        assert news_service._last_fetch >= old_fetch


# ---------------------------------------------------------------------------
# Pending order budget reclaim
# ---------------------------------------------------------------------------

class TestPendingOrderBudgetReclaim:
    @pytest.mark.asyncio
    async def test_cancel_order_reclaims_risk_and_trade_slot(self):
        from trading_bot.services.pending_order_manager import (
            PendingOrderManager, PendingOrder, PendingOrderStatus
        )
        from trading_bot.execution.risk_manager import RiskManager
        from trading_bot.services.trade_reservations import TradeReservationLedger

        risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
        risk_manager.update_daily_risk(0.02)
        daily_trades = {'count': 2}

        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(success=True, message='ok')

        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
        )
        ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )
        ledger.restore_pending("reservation-99999", "EURUSD", 99999, 0.015)
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            reservation_ledger=ledger,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )

        order = PendingOrder(
            ticket=99999,
            symbol='EURUSD',
            order_type='buy_limit',
            direction='long',
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.015,
            reservation_id="reservation-99999",
        )
        manager.pending_orders[99999] = order

        ok = await manager.cancel_order(99999, reason='manual')
        assert ok is True
        assert daily_trades['count'] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.005, abs=0.0001)

    @pytest.mark.asyncio
    async def test_cancel_expired_reclaims_budget(self):
        from trading_bot.services.pending_order_manager import (
            PendingOrderManager, PendingOrder, PendingOrderStatus
        )
        from trading_bot.execution.risk_manager import RiskManager
        from trading_bot.services.trade_reservations import TradeReservationLedger

        risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
        risk_manager.update_daily_risk(0.01)
        daily_trades = {'count': 1}

        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(success=True, message='ok')

        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
        )
        ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )
        ledger.restore_pending("reservation-11111", "GBPUSD", 11111, 0.01)
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            reservation_ledger=ledger,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )

        expired = PendingOrder(
            ticket=11111,
            symbol='GBPUSD',
            order_type='sell_limit',
            direction='short',
            volume=0.03,
            price=1.2500,
            stop_loss=1.2600,
            take_profit=1.2400,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expiration=datetime.now(timezone.utc) - timedelta(minutes=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.01,
            reservation_id="reservation-11111",
        )
        manager.pending_orders[11111] = expired

        result = await manager.cancel_expired_orders()
        assert result['cancelled'] == 1
        assert daily_trades['count'] == 0
        assert risk_manager.daily_risk_used == 0.0

    @pytest.mark.asyncio
    async def test_sync_external_cancel_reclaims_budget(self):
        from trading_bot.services.pending_order_manager import (
            PendingOrderManager, PendingOrder, PendingOrderStatus
        )
        from trading_bot.execution.risk_manager import RiskManager
        from trading_bot.services.trade_reservations import TradeReservationLedger

        risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
        risk_manager.update_daily_risk(0.01)
        daily_trades = {'count': 1}

        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []
        mock_mt5.get_positions.return_value = []
        mock_mt5.get_history.return_value = []

        manager = PendingOrderManager(mt5_client=mock_mt5)
        ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )
        ledger.restore_pending("reservation-22222", "BTCUSD", 22222, 0.01)
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            reservation_ledger=ledger,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )

        order = PendingOrder(
            ticket=22222,
            symbol='BTCUSD',
            order_type='sell_limit',
            direction='short',
            volume=0.01,
            price=67500.0,
            stop_loss=68000.0,
            take_profit=67000.0,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.01,
            reservation_id="reservation-22222",
        )
        manager.pending_orders[22222] = order

        await manager.sync_with_mt5()
        assert daily_trades['count'] == 0
        assert risk_manager.daily_risk_used == 0.0

    @pytest.mark.asyncio
    async def test_cancel_can_skip_reclaim_for_replace_flow(self):
        from trading_bot.services.pending_order_manager import (
            PendingOrderManager, PendingOrder, PendingOrderStatus
        )
        from trading_bot.execution.risk_manager import RiskManager

        risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
        risk_manager.update_daily_risk(0.01)
        daily_trades = {'count': 1}

        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(success=True, message='ok')

        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
        )
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            get_daily_trades=lambda: daily_trades['count'],
            set_daily_trades=lambda v: daily_trades.update(count=v),
        )

        order = PendingOrder(
            ticket=33333,
            symbol='EURUSD',
            order_type='buy_limit',
            direction='long',
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.01,
        )
        manager.pending_orders[33333] = order

        await manager.cancel_order(33333, reason='replaced_by_newer', reclaim_budget=False)
        assert daily_trades['count'] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.01, abs=0.0001)


# ---------------------------------------------------------------------------
# Judge fail-closed
# ---------------------------------------------------------------------------

class TestJudgeFailClosed:
    def _get_claude_client(self):
        from trading_bot.llm.claude_client import ClaudeClient
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            client.api_key = 'test'
            client.model_heavy = 'test'
            client.async_client = None
            return client

    @pytest.mark.asyncio
    async def test_judge_no_client_defaults_demote(self):
        client = self._get_claude_client()
        signal = {
            'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
            'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
            'order_type': 'market', 'reasoning': 'Test',
        }
        risk_metrics = {'account_balance': 200.0, 'trades_today': 0, 'max_daily_trades': 5}
        result = await client.judge_trade(signal, risk_metrics, '')
        assert result['verdict'] == 'UNAVAILABLE'
        assert result['verdict'] != 'APPROVE'

    @pytest.mark.asyncio
    async def test_judge_parse_error_defaults_demote(self):
        client = self._get_claude_client()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='not json')]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)

        signal = {
            'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
            'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
            'order_type': 'market', 'reasoning': 'Test',
        }
        risk_metrics = {'account_balance': 200.0, 'trades_today': 0, 'max_daily_trades': 5}
        result = await client.judge_trade(signal, risk_metrics, '')
        assert result['verdict'] == 'UNAVAILABLE'

    @pytest.mark.asyncio
    async def test_judge_api_error_defaults_demote(self):
        client = self._get_claude_client()
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(side_effect=Exception('API down'))

        signal = {
            'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
            'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
            'order_type': 'market', 'reasoning': 'Test',
        }
        risk_metrics = {'account_balance': 200.0, 'trades_today': 0, 'max_daily_trades': 5}
        result = await client.judge_trade(signal, risk_metrics, '')
        assert result['verdict'] == 'UNAVAILABLE'


# ---------------------------------------------------------------------------
# API auth
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(monkeypatch):
    """Minimal API app with auth middleware and selected routes."""
    test_key = 'test-api-key-winopt'
    monkeypatch.setenv('BOT_API_KEY', test_key)

    import trading_bot.api.auth as auth_module
    auth_module._API_KEY = None
    from trading_bot.api.auth import get_api_key, requires_auth, RequireAuth
    get_api_key()

    app = FastAPI()

    @app.middleware('http')
    async def auth_middleware(request, call_next):
        if requires_auth(request.method, request.url.path):
            api_key = request.headers.get('X-API-Key') or request.query_params.get('api_key')
            if not api_key:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=401, content={'detail': 'Authentication required'})
            if api_key != get_api_key():
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={'detail': 'Invalid API key'})
        return await call_next(request)

    from trading_bot.api.routes import bot_status, config

    app.include_router(bot_status.router, prefix='/api/bot')
    app.include_router(config.router, prefix='/api/config')

    from fastapi import Depends

    @app.post('/api/admin/reset-daily-risk', dependencies=[Depends(RequireAuth())])
    async def reset_daily_risk_route():
        return {'status': 'ok'}

    return TestClient(app), test_key


class TestAPIAuth:
    def test_status_public_without_auth(self, api_client):
        client, _ = api_client
        response = client.get('/api/bot/status')
        assert response.status_code == 200

    def test_start_bot_requires_auth(self, api_client, monkeypatch):
        client, key = api_client
        monkeypatch.setattr(
            'trading_bot.api.main.start_bot_task',
            AsyncMock(return_value=True),
        )
        with patch('trading_bot.api.main.get_bot_instance', return_value=None):
            no_auth = client.post('/api/bot/start')
            assert no_auth.status_code == 401
            authed = client.post('/api/bot/start', headers={'X-API-Key': key})
            assert authed.status_code == 200

    def test_stop_bot_requires_auth(self, api_client):
        client, key = api_client
        mock_bot = MagicMock()
        mock_bot.running = True
        mock_bot.stop = MagicMock()
        with patch('trading_bot.api.main.get_bot_instance', return_value=mock_bot):
            assert client.post('/api/bot/stop').status_code == 401
            assert client.post('/api/bot/stop', headers={'X-API-Key': key}).status_code == 200

    def test_update_trading_config_requires_auth(self, api_client):
        client, key = api_client
        payload = {'risk_per_trade': 0.02}
        assert client.put('/api/config/trading', json=payload).status_code == 401
        assert client.put('/api/config/trading', json=payload, headers={'X-API-Key': key}).status_code == 200

    def test_update_api_keys_requires_auth(self, api_client):
        client, key = api_client
        payload = {'anthropic_api_key': 'sk-test1234567890'}
        assert client.put('/api/config/api-keys', json=payload).status_code == 401
        with patch('trading_bot.config.save_config_to_env_local'):
            assert client.put(
                '/api/config/api-keys', json=payload, headers={'X-API-Key': key}
            ).status_code == 200

    def test_reset_daily_risk_requires_auth(self, api_client):
        client, key = api_client
        assert client.post('/api/admin/reset-daily-risk').status_code == 401
        assert client.post('/api/admin/reset-daily-risk', headers={'X-API-Key': key}).status_code == 200

    def test_get_trading_config_public(self, api_client):
        client, _ = api_client
        assert client.get('/api/config/trading').status_code == 200
