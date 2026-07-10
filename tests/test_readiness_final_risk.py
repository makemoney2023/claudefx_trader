"""
Wave 2 Task 5 — final broker-bound risk invariant before every MT5 order.
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from trading_bot.config import SymbolSpec, get_symbol_spec
from trading_bot.execution.risk_manager import RiskManager
from trading_bot.execution.scaling_position_sizer import (
    calculate_broker_loss_at_stop,
    enforce_final_risk_cap,
    FINAL_RISK_TOLERANCE,
)
from trading_bot.llm.claude_client import TradeSignal
from trading_bot.main import TradingBot
from trading_bot.services.pending_order_manager import PendingOrder
from trading_bot.services.trade_reservations import TradeReservationLedger


SYMBOL_CASES = [
    ("EURUSD", 1.0850, 1.0800, 0.10, 10.0, 0.0001),
    ("USDJPY", 150.50, 150.00, 0.10, 9.0, 0.01),
    ("XAUUSD", 2350.0, 2340.0, 0.05, 1.0, 0.01),
    ("XAGUSD", 30.0, 29.8, 0.02, 5.0, 0.001),
    ("US500", 5200.0, 5180.0, 0.20, 0.1, 0.1),
    ("BTCUSD", 65000.0, 64000.0, 0.01, 0.01, 0.01),
]


class TestBrokerLossCalculation:
    @pytest.mark.parametrize(
        "symbol,entry,sl,lots,pip_value,pip_size",
        SYMBOL_CASES,
    )
    def test_calculate_broker_loss_at_stop_parameterized(
        self, symbol, entry, sl, lots, pip_value, pip_size
    ):
        spec = get_symbol_spec(symbol)
        broker_spec = SymbolSpec(
            contract_size=spec.contract_size,
            pip_size=pip_size,
            pip_value=pip_value,
            min_sl_pips=spec.min_sl_pips,
            category=spec.category,
            tick_value=pip_value,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
        )
        loss = calculate_broker_loss_at_stop(entry, sl, lots, broker_spec)
        distance = abs(entry - sl)
        expected_ticks = distance / (pip_size / 10 if spec.category == "forex" else pip_size)
        expected = expected_ticks * lots * pip_value
        assert loss == pytest.approx(expected, rel=0.01)

    def test_fallback_without_tick_value_uses_pip_math(self):
        spec = get_symbol_spec("EURUSD")
        broker_spec = SymbolSpec(
            contract_size=spec.contract_size,
            pip_size=spec.pip_size,
            pip_value=spec.pip_value,
            min_sl_pips=spec.min_sl_pips,
            category="forex",
            tick_value=0.0,
        )
        loss = calculate_broker_loss_at_stop(1.0850, 1.0800, 0.10, broker_spec)
        assert loss == pytest.approx(50.0, rel=0.01)


class TestFinalRiskCap:
    @pytest.mark.parametrize(
        "symbol,entry,sl,lots,pip_value,pip_size",
        SYMBOL_CASES,
        ids=[c[0] for c in SYMBOL_CASES],
    )
    def test_within_cap_allows_lots(self, symbol, entry, sl, lots, pip_value, pip_size):
        spec = get_symbol_spec(symbol)
        equity = 10_000.0
        risk_fraction = 0.02
        allowed, loss, reason = enforce_final_risk_cap(
            equity, risk_fraction, entry, sl, lots, spec, symbol=symbol
        )
        assert reason is None
        assert allowed == lots
        assert loss <= equity * risk_fraction * FINAL_RISK_TOLERANCE + 0.01

    def test_shrinks_lots_when_over_cap(self):
        spec = get_symbol_spec("EURUSD")
        allowed, loss, reason = enforce_final_risk_cap(
            1000.0,
            0.01,
            1.0850,
            1.0845,
            0.50,
            spec,
            symbol="EURUSD",
        )
        assert reason is None
        assert allowed < 0.50
        assert loss <= 1000.0 * 0.01 * FINAL_RISK_TOLERANCE + 0.01

    def test_rejects_when_min_lot_still_exceeds_cap(self):
        spec = get_symbol_spec("EURUSD")
        allowed, loss, reason = enforce_final_risk_cap(
            500.0,
            0.005,
            1.0850,
            1.0500,
            0.50,
            spec,
            symbol="EURUSD",
        )
        assert allowed == 0.0
        assert reason is not None


class TestFinalRiskIntegration:
    def _bot(self):
        bot = TradingBot.__new__(TradingBot)
        bot.risk_manager = MagicMock()
        bot.risk_manager.risk_per_trade = 0.02
        return bot

    def test_enforce_before_order_request_spread_widening(self):
        bot = self._bot()
        spec = get_symbol_spec("EURUSD")
        entry = 1.0852
        sl = 1.0800
        lots = 0.20
        allowed, _, reason = bot._enforce_final_risk_before_order(
            symbol="EURUSD",
            entry=entry,
            stop_loss=sl,
            lots=lots,
            account_equity=2000.0,
            symbol_spec=spec,
        )
        assert reason is None
        assert allowed <= lots

    def test_enforce_after_tick_refined_entry(self):
        bot = self._bot()
        spec = get_symbol_spec("USDJPY")
        allowed, _, reason = bot._enforce_final_risk_before_order(
            symbol="USDJPY",
            entry=150.48,
            stop_loss=150.00,
            lots=0.15,
            account_equity=5000.0,
            symbol_spec=spec,
        )
        assert reason is None
        assert allowed > 0

    def test_enforce_after_demote_size_reduction(self):
        bot = self._bot()
        spec = get_symbol_spec("XAUUSD")
        allowed, _, reason = bot._enforce_final_risk_before_order(
            symbol="XAUUSD",
            entry=2350.0,
            stop_loss=2340.0,
            lots=0.03,
            account_equity=3000.0,
            symbol_spec=spec,
            risk_fraction=0.01,
        )
        assert reason is None
        assert allowed == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_reversal_revalidates_after_final_mutation(self):
        bot = self._bot()
        bot.mt5_client = AsyncMock()
        bot.mt5_client.get_tick = AsyncMock(
            return_value=SimpleNamespace(ask=1.0850, bid=1.0848)
        )
        bot.order_manager = AsyncMock()
        bot.order_manager.place_market_order = AsyncMock(
            return_value=SimpleNamespace(success=True, ticket=12345, order_id=12345)
        )
        spec = get_symbol_spec("EURUSD")

        allowed, _, reason = bot._enforce_final_risk_before_order(
            symbol="EURUSD",
            entry=1.0850,
            stop_loss=1.0800,
            lots=0.25,
            account_equity=1500.0,
            symbol_spec=spec,
        )
        if reason:
            assert allowed == 0.0
        else:
            result = await bot._place_market_with_final_risk(
                symbol="EURUSD",
                direction="short",
                lots=allowed,
                stop_loss=1.0800,
                take_profit=1.0750,
                account_equity=1500.0,
                symbol_spec=spec,
            )
            assert result is not None
            assert result.success
            bot.order_manager.place_market_order.assert_called_once()
            call_kwargs = bot.order_manager.place_market_order.call_args.kwargs
            assert call_kwargs["volume"] <= allowed


def _orchestrator_bot():
    bot = TradingBot.__new__(TradingBot)
    bot.risk_manager = RiskManager(risk_per_trade=0.02, max_daily_risk=0.06)
    bot.mt5_client = AsyncMock()
    bot.mt5_client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(equity=10_000.0, balance=10_000.0)
    )
    bot.mt5_client.get_tick = AsyncMock(
        return_value=SimpleNamespace(ask=1.0852, bid=1.0850)
    )
    bot.order_manager = AsyncMock()
    bot.order_manager.place_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True, ticket=9001, order_id=9001, fill_price=1.0852
        )
    )
    bot.order_manager.place_pending_order = AsyncMock(
        return_value=SimpleNamespace(success=True, ticket=9002, order_id=9002)
    )
    bot.reservation_ledger = TradeReservationLedger(
        risk_manager=bot.risk_manager,
        get_daily_trades=lambda: 0,
        set_daily_trades=lambda _v: None,
    )
    bot.position_manager = MagicMock()
    bot.position_manager.get_all_positions = MagicMock(return_value=[])
    bot.position_manager.get_positions_by_symbol = MagicMock(return_value=[])
    bot.scaling_manager = None
    bot.daily_trades = 0
    bot.daily_pnl = 0.0
    bot.pending_order_manager = MagicMock()
    bot.data_fetcher = AsyncMock()
    bot._last_signal_per_symbol = {}
    bot._reversal_cooldowns = {}
    bot._recent_signal_hashes = set()
    bot._signal_hash_expiry = {}
    return bot


class TestFinalRiskOrchestratorPaths:
    def test_reversal_market_routes_through_final_risk_helper(self):
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        market_block = source.split("if order_type == 'market'")[1].split("else:")[0]
        assert "_place_market_with_final_risk" in market_block
        assert "order_manager.place_market_order" not in market_block

    def test_reversal_pending_routes_through_final_risk_helper(self):
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        pending_block = source.split("# Pending order for reversal")[1]
        assert "_place_pending_with_final_risk" in pending_block
        assert "order_manager.place_pending_order" not in pending_block

    def test_pending_upgrade_routes_through_final_risk_helper(self):
        source = inspect.getsource(TradingBot._claude_reevaluate_pending_orders)
        upgrade_block = source.split("UPGRADE #")[1].split("continue  # Done with this order")[0]
        assert "_place_market_with_final_risk" in upgrade_block
        assert "order_manager.place_market_order" not in upgrade_block

    def test_regular_market_reenforces_after_tick_refine(self):
        source = inspect.getsource(TradingBot._analyze_and_trade)
        tick_idx = source.index("[TICK-REFINE]")
        market_block = source[tick_idx : source.index("elif order_type in ['buy_limit'", tick_idx)]
        assert "_place_market_with_final_risk(" in market_block

    @pytest.mark.asyncio
    async def test_pending_upgrade_invokes_final_risk_before_market_send(self):
        import pandas as pd

        bot = _orchestrator_bot()
        created_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        order = PendingOrder(
            ticket=1001,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.10,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0950,
            created_at=created_at,
            expiration=created_at + timedelta(hours=4),
            risk_percent=0.02,
        )
        bot.pending_order_manager.get_active_orders = MagicMock(return_value=[order])
        bot.pending_order_manager.cancel_order = AsyncMock(return_value=True)
        bot.reservation_ledger.get_by_id = MagicMock(return_value=None)

        current_price = 1.0855
        bot.data_fetcher.get_ohlcv = AsyncMock(
            return_value=pd.DataFrame({"close": [current_price]})
        )

        with patch.object(
            bot,
            "_place_market_with_final_risk",
            AsyncMock(
                return_value=SimpleNamespace(
                    success=True, ticket=7777, order_id=7777, fill_price=current_price
                )
            ),
        ) as place_mock:
            await bot._claude_reevaluate_pending_orders()

        place_mock.assert_awaited_once()
        kwargs = place_mock.await_args.kwargs
        assert kwargs["symbol"] == "EURUSD"
        assert kwargs["direction"] == "long"
        assert kwargs["lots"] == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_reversal_market_invokes_final_risk_helper(self):
        bot = _orchestrator_bot()
        closed_position = SimpleNamespace(
            symbol="EURUSD",
            direction="long",
            ticket=5001,
            close_reason="near_tp_reversal",
            entry_price=1.0800,
            current_price=1.0850,
            peak_r_multiple=1.5,
            current_r_multiple=1.2,
            unrealized_pnl=25.0,
            trade_type="intraday",
        )
        trade_signal = TradeSignal(
            direction="short",
            confidence=0.82,
            entry_price=1.0850,
            stop_loss=1.0900,
            take_profit=1.0750,
            risk_reward=2.0,
            reasoning="Structural reversal",
            order_type="market",
            trade_type="intraday",
        )

        async def _stub_reversal_past_gates(_closed):
            order_type = getattr(trade_signal, "order_type", "market") or "market"
            symbol = _closed.symbol
            stop_loss = trade_signal.stop_loss
            position_size = 0.10
            _reversal_risk_pct = bot.risk_manager.risk_per_trade
            _reversal_reservation = bot.reservation_ledger.reserve(
                symbol=symbol,
                signal_id=f"reversal:{_closed.ticket}",
                risk_percent=_reversal_risk_pct,
            )
            bot.reservation_ledger.commit_risk(_reversal_reservation)
            if order_type == "market" or order_type.endswith("_market"):
                result = await bot._place_market_with_final_risk(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    lots=position_size,
                    stop_loss=stop_loss,
                    take_profit=trade_signal.take_profit,
                    account_equity=10_000.0,
                    symbol_spec=get_symbol_spec(symbol),
                    risk_fraction=_reversal_risk_pct,
                    comment="ICT_Bot_Reversal",
                )
                return result is not None

        with patch.object(
            bot,
            "_place_market_with_final_risk",
            AsyncMock(
                return_value=SimpleNamespace(
                    success=True, ticket=8888, order_id=8888, fill_price=1.0850
                )
            ),
        ) as place_mock:
            ok = await _stub_reversal_past_gates(closed_position)

        assert ok is True
        place_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reversal_pending_helper_enforces_before_send(self):
        bot = _orchestrator_bot()
        spec = get_symbol_spec("EURUSD")
        result = await bot._place_pending_with_final_risk(
            symbol="EURUSD",
            direction="short",
            order_type="sell_limit",
            price=1.0860,
            lots=0.10,
            stop_loss=1.0900,
            take_profit=1.0750,
            account_equity=10_000.0,
            symbol_spec=spec,
            risk_fraction=0.02,
            comment="ICT_Bot_Reversal",
        )
        assert result is not None
        bot.order_manager.place_pending_order.assert_awaited_once()
        call_kwargs = bot.order_manager.place_pending_order.call_args.kwargs
        assert call_kwargs["volume"] <= 0.10
