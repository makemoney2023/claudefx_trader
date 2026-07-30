"""E2E fill-path safety: PRICE-FIX, tickets, reservations, gates, goal keys."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from trading_bot.execution.order_manager import OrderManager, OrderResult, OrderStatus
from trading_bot.execution.trade_execution import ExecutionCoordinator
from trading_bot.services.trade_reservations import (
    ReservationState,
    TradeReservation,
    TradeReservationLedger,
)
EST = pytz.timezone("US/Eastern")


def _symbol_info(**overrides):
    base = dict(
        ask=2650.50,
        bid=2650.20,
        digits=2,
        point=0.01,
        trade_stops_level=0,
        expiration_mode=1,
        filling_mode=1,  # FOK only
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mt5_base(symbol_info, order_send_fn, **extra):
    ns = SimpleNamespace(
        TRADE_RETCODE_DONE=10009,
        TRADE_RETCODE_PLACED=10008,
        TRADE_ACTION_DEAL=1,
        TRADE_ACTION_PENDING=5,
        ORDER_TIME_GTC=0,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        symbol_info=lambda symbol: symbol_info,
        order_send=order_send_fn,
        last_error=lambda: (1, "test"),
        positions_get=lambda **k: [],
    )
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


async def _route_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


# ---------------------------------------------------------------------------
# 1) MT5 PRICE-FIX payload + OrderResult plumbing
# ---------------------------------------------------------------------------


class TestPriceFixPayload:
    @pytest.mark.asyncio
    async def test_sell_limit_at_market_returns_converted_to_market(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=1, password="x", server="T")
        client._use_simulation = False
        client._connected = True

        # sell_limit at/below bid → market sell
        info = _symbol_info(bid=2650.20, ask=2650.50)
        captured = {}

        def order_send(request):
            captured["request"] = request
            return SimpleNamespace(
                retcode=10009,
                order=111,
                deal=222,
                price=2650.20,
                volume=0.01,
                comment="done",
            )

        client._mcp_client = _mt5_base(info, order_send)

        with patch.object(client, "ensure_connected", new=AsyncMock(return_value=True)):
            with patch(
                "trading_bot.mt5.client.asyncio.to_thread",
                side_effect=_route_to_thread,
            ):
                result = await client.place_order(
                    symbol="XAUUSD",
                    order_type="sell_limit",
                    volume=0.01,
                    price=2650.10,  # <= bid
                    stop_loss=2660.0,
                    take_profit=2630.0,
                )

        assert result["success"] is True
        assert result["converted_to_market"] is True
        assert result["final_order_type"] == "sell"
        assert result["price"] == pytest.approx(2650.20)
        assert "sl" in result and result["sl"] is not None
        assert "tp" in result and result["tp"] is not None
        assert captured["request"]["action"] == 1  # DEAL

    @pytest.mark.asyncio
    async def test_true_pending_not_converted(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=1, password="x", server="T")
        client._use_simulation = False
        client._connected = True

        info = _symbol_info(bid=2650.20, ask=2650.50)
        # sell_limit well above bid
        order_result = SimpleNamespace(
            retcode=10008,
            order=333,
            deal=0,
            price=2655.00,
            volume=0.01,
            comment="placed",
        )
        client._mcp_client = _mt5_base(info, lambda r: order_result)

        with patch.object(client, "ensure_connected", new=AsyncMock(return_value=True)):
            with patch(
                "trading_bot.mt5.client.asyncio.to_thread",
                side_effect=_route_to_thread,
            ):
                result = await client.place_order(
                    symbol="XAUUSD",
                    order_type="sell_limit",
                    volume=0.01,
                    price=2655.00,
                    stop_loss=2665.0,
                    take_profit=2635.0,
                )

        assert result["success"] is True
        assert result.get("converted_to_market") is False
        assert result["final_order_type"] == "sell_limit"

    @pytest.mark.asyncio
    async def test_order_manager_pending_propagates_conversion(self):
        mt5 = AsyncMock()
        mt5.place_order = AsyncMock(
            return_value={
                "success": True,
                "order_id": 111,
                "ticket": 999,
                "price": 2650.20,
                "volume": 0.01,
                "converted_to_market": True,
                "final_order_type": "sell",
                "sl": 2660.0,
                "tp": 2630.0,
            }
        )
        om = OrderManager(mt5_client=mt5)
        om._check_spread = AsyncMock(return_value=(True, 0.1, 1.0))

        result = await om.place_pending_order(
            symbol="XAUUSD",
            direction="short",
            order_type="sell_limit",
            volume=0.01,
            price=2650.10,
            stop_loss=2660.0,
            take_profit=2630.0,
        )

        assert result.success is True
        assert result.converted_to_market is True
        assert result.final_order_type == "sell"
        assert result.status == OrderStatus.FILLED
        assert result.broker_sl == 2660.0
        assert result.broker_tp == 2630.0
        assert result.fill_price == pytest.approx(2650.20)


# ---------------------------------------------------------------------------
# 2) Coordinator routes converted fills as market
# ---------------------------------------------------------------------------


class TestCoordinatorConvertedRoute:
    @pytest.mark.asyncio
    async def test_converted_pending_skips_pending_manager(self):
        bot = MagicMock()
        bot.kill_zone_checker = None
        bot.pending_order_manager = MagicMock()
        bot.pending_order_manager.get_active_orders = MagicMock(return_value=[])
        bot.reservation_ledger = MagicMock()
        converted = OrderResult(
            success=True,
            order_id=111,
            ticket=999,
            status=OrderStatus.FILLED,
            message="converted",
            fill_price=2650.20,
            converted_to_market=True,
            final_order_type="sell",
        )
        bot.order_manager = MagicMock()
        bot.order_manager.place_pending_order = AsyncMock(return_value=converted)
        reservation = TradeReservation(
            reservation_id="r1",
            symbol="XAUUSD",
            signal_id=None,
            risk_percent=0.01,
            state=ReservationState.RESERVED,
        )

        result = await ExecutionCoordinator()._place_pending_order(
            bot=bot,
            symbol="XAUUSD",
            trade_signal=SimpleNamespace(direction="short"),
            order_type="sell_limit",
            entry_price=2650.10,
            position_size=SimpleNamespace(lots=0.01),
            size_result=SimpleNamespace(risk_percent=0.01),
            final_sl=2660.0,
            final_tp=2630.0,
            is_crypto=False,
            trade_reservation=reservation,
        )

        assert result.converted_to_market is True
        bot.pending_order_manager.add_order.assert_not_called()
        bot.reservation_ledger.transfer_to_pending.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_execute_transfers_reservation_early(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "dry_run", False)

        reservation = TradeReservation(
            reservation_id="r2",
            symbol="EURUSD",
            signal_id=None,
            risk_percent=0.01,
            state=ReservationState.RESERVED,
        )
        filled = OrderResult(
            success=True,
            order_id=10,
            ticket=20,
            status=OrderStatus.FILLED,
            message="ok",
            fill_price=1.0850,
        )
        bot = MagicMock()
        bot._enforce_final_risk_before_order = MagicMock(
            return_value=(0.01, 0.01, None)
        )
        bot._place_market_with_final_risk = AsyncMock(return_value=filled)
        bot.reservation_ledger = TradeReservationLedger(
            get_daily_trades=lambda: 1,
            set_daily_trades=lambda n: None,
            risk_manager=None,
        )
        bot.reservation_ledger._reservations[reservation.reservation_id] = reservation

        with patch(
            "trading_bot.execution.trade_execution.verify_post_sizing_risk",
            return_value=(0.01, 0.01, None),
        ):
            with patch(
                "trading_bot.execution.trade_execution.ExecutionCoordinator._run_tick_refine",
                new=AsyncMock(
                    return_value=SimpleNamespace(allowed=True, adjusted_entry=None, reason=None)
                ),
            ):
                await ExecutionCoordinator().execute(
                    bot=bot,
                    symbol="EURUSD",
                    trade_signal=SimpleNamespace(
                        direction="short",
                        entry_price=1.0850,
                        stop_loss=1.0900,
                        take_profit=1.0750,
                        confidence=0.8,
                        amd_phase="distribution",
                        order_type="market",
                    ),
                    order_type="market",
                    entry_price=1.0850,
                    current_price=1.0850,
                    position_size=SimpleNamespace(lots=0.01),
                    size_result=SimpleNamespace(lots=0.01, risk_percent=0.01),
                    account_info=SimpleNamespace(equity=1000.0),
                    market_data={},
                    is_crypto=False,
                    trade_reservation=reservation,
                )

        assert reservation.state == ReservationState.TRANSFERRED
        assert reservation.position_ticket == 20


# ---------------------------------------------------------------------------
# 3) Fill handler
# ---------------------------------------------------------------------------


def _fill_bot(**overrides):
    bot = MagicMock()
    bot.mt5_client = MagicMock()
    bot.mt5_client.is_simulation = True
    bot.risk_manager = SimpleNamespace(
        daily_risk_used=0.0, max_daily_risk=0.06, risk_per_trade=0.01
    )
    bot.reservation_ledger = MagicMock()
    bot.position_manager = MagicMock()
    bot._recent_signal_hashes = set()
    bot._signal_hash_expiry = {}
    bot._record_terminal_decision = AsyncMock()
    bot._release_trade_reservation = MagicMock()
    bot.kill_zone_checker = None
    bot._last_regime_by_symbol = {}
    bot.correlation_service = None
    bot.__dict__.update(overrides)
    return bot


class TestFillHandlerSafety:
    @pytest.mark.asyncio
    async def test_converted_to_market_tracks_as_position(self):
        from trading_bot.execution.trade_fill_handler import TradeFillHandler

        bot = _fill_bot()
        result = OrderResult(
            success=True,
            order_id=111,
            ticket=999,
            status=OrderStatus.FILLED,
            message="converted",
            fill_price=2650.20,
            fill_volume=0.01,
            converted_to_market=True,
            final_order_type="sell",
        )
        reservation = TradeReservation(
            reservation_id="r3",
            symbol="XAUUSD",
            signal_id=None,
            risk_percent=0.01,
            state=ReservationState.RESERVED,
        )

        with patch(
            "trading_bot.execution.trade_fill_handler.compute_booked_risk_percent",
            return_value=0.01,
        ), patch(
            "trading_bot.execution.trade_fill_handler.broadcast_trade_update",
            new=AsyncMock(),
        ), patch(
            "trading_bot.api.routes.activity.add_activity",
        ), patch(
            "trading_bot.execution.trade_fill_handler.notify",
            new=AsyncMock(),
        ):
            await TradeFillHandler.handle_result(
                bot,
                symbol="XAUUSD",
                result=result,
                order_type="sell_limit",
                entry_price=2650.10,
                current_price=2650.20,
                trade_signal=SimpleNamespace(
                    direction="short",
                    entry_price=2650.10,
                    stop_loss=2655.0,
                    take_profit=2640.0,
                    confidence=0.8,
                    trade_type="intraday",
                ),
                position_size=SimpleNamespace(lots=0.01),
                size_result=SimpleNamespace(risk_percent=0.01),
                account_info=SimpleNamespace(balance=1000.0),
                trade_reservation=reservation,
                signal_hash="h1",
                final_sl=2660.0,
                final_tp=2630.0,
                final_entry=2650.20,
                judge_verdict=None,
                confluence_factors=[],
                confluence_count=3,
                setup_grade="A",
                take_profit_levels=None,
                save_trade_to_db=AsyncMock(),
            )

        bot.position_manager.add_position.assert_called()
        pos = bot.position_manager.add_position.call_args[0][0]
        assert pos.stop_loss == 2660.0
        assert pos.take_profit == 2630.0
        bot.reservation_ledger.transfer_to_position.assert_called()

    @pytest.mark.asyncio
    async def test_verify_miss_retains_reservation_on_ambiguous_success(self):
        from trading_bot.execution.trade_fill_handler import TradeFillHandler

        bot = _fill_bot(
            mt5_client=MagicMock(
                is_simulation=False,
                get_positions=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            ticket=555, symbol="EURUSD", magic=12345, volume=0.01
                        )
                    ]
                ),
            )
        )
        result = OrderResult(
            success=True,
            order_id=111,
            ticket=999,
            status=OrderStatus.FILLED,
            message="ok",
            fill_price=1.0850,
            fill_volume=0.01,
        )

        with patch(
            "trading_bot.execution.trade_fill_handler.compute_booked_risk_percent",
            return_value=0.01,
        ), patch(
            "trading_bot.execution.trade_fill_handler.broadcast_trade_update",
            new=AsyncMock(),
        ), patch(
            "trading_bot.api.routes.activity.add_activity",
        ), patch(
            "trading_bot.execution.trade_fill_handler.notify",
            new=AsyncMock(),
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ):
            await TradeFillHandler.handle_result(
                bot,
                symbol="EURUSD",
                result=result,
                order_type="market",
                entry_price=1.0850,
                current_price=1.0850,
                trade_signal=SimpleNamespace(
                    direction="short",
                    entry_price=1.0850,
                    stop_loss=1.0900,
                    take_profit=1.0750,
                    confidence=0.8,
                    trade_type="intraday",
                ),
                position_size=SimpleNamespace(lots=0.01),
                size_result=SimpleNamespace(risk_percent=0.01),
                account_info=SimpleNamespace(balance=1000.0),
                trade_reservation=TradeReservation(
                    reservation_id="r4",
                    symbol="EURUSD",
                    signal_id=None,
                    risk_percent=0.01,
                ),
                signal_hash="h2",
                final_sl=1.0900,
                final_tp=1.0750,
                final_entry=1.0850,
                judge_verdict=None,
                confluence_factors=[],
                confluence_count=3,
                setup_grade="A",
                take_profit_levels=None,
                save_trade_to_db=AsyncMock(),
            )

        bot._release_trade_reservation.assert_not_called()
        bot.position_manager.add_position.assert_called()
        pos = bot.position_manager.add_position.call_args[0][0]
        assert pos.ticket == 555


# ---------------------------------------------------------------------------
# 4) Filling-mode retry
# ---------------------------------------------------------------------------


class TestFillingModeRetry:
    @pytest.mark.asyncio
    async def test_retries_ioc_after_10030(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=1, password="x", server="T")
        client._use_simulation = False
        client._connected = True

        # FOK + IOC supported
        info = _symbol_info(filling_mode=3)
        calls = {"n": 0, "fillings": []}

        def order_send(request):
            calls["n"] += 1
            calls["fillings"].append(request["type_filling"])
            if calls["n"] == 1:
                return SimpleNamespace(
                    retcode=10030,
                    order=0,
                    deal=0,
                    price=0,
                    volume=0,
                    comment="Unsupported filling mode",
                )
            return SimpleNamespace(
                retcode=10009,
                order=50,
                deal=51,
                price=1.1000,
                volume=0.01,
                comment="done",
            )

        client._mcp_client = _mt5_base(info, order_send)

        with patch.object(client, "ensure_connected", new=AsyncMock(return_value=True)):
            with patch(
                "trading_bot.mt5.client.asyncio.to_thread",
                side_effect=_route_to_thread,
            ):
                result = await client.place_order(
                    symbol="EURUSD",
                    order_type="buy",
                    volume=0.01,
                )

        assert result["success"] is True
        assert calls["n"] == 2
        assert calls["fillings"][0] == 0  # FOK
        assert calls["fillings"][1] == 1  # IOC


# ---------------------------------------------------------------------------
# 5) Reservation except safety
# ---------------------------------------------------------------------------


class TestReservationExceptSafety:
    def test_release_noop_when_transferred(self):
        daily = {"n": 0}

        def get_daily():
            return daily["n"]

        def set_daily(n):
            daily["n"] = n

        risk = SimpleNamespace(daily_risk_used=0.0)

        def update_daily_risk(delta):
            risk.daily_risk_used += delta

        risk.update_daily_risk = update_daily_risk
        ledger = TradeReservationLedger(
            get_daily_trades=get_daily,
            set_daily_trades=set_daily,
            risk_manager=risk,
        )
        res = ledger.reserve("EURUSD", risk_percent=0.02)
        ledger.commit_risk(res)
        ledger.transfer_to_position(res, ticket=42)
        assert res.state == ReservationState.TRANSFERRED
        assert daily["n"] == 1
        assert risk.daily_risk_used == pytest.approx(0.02)

        # Bot helper should only release RESERVED
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.reservation_ledger = ledger
        bot.daily_trades = daily["n"]
        bot._release_trade_reservation(res)

        assert res.state == ReservationState.TRANSFERRED
        assert daily["n"] == 1
        assert risk.daily_risk_used == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# 6) Friday gate helpers (wiring tested via extracted policy)
# ---------------------------------------------------------------------------


class TestFridayGatePolicy:
    def test_helpers_independent(self):
        from trading_bot.utils.win_optimization import (
            is_friday_afternoon_entry_block,
            is_friday_weekend_close_time,
        )

        noon = EST.localize(datetime(2026, 7, 10, 12, 0))
        assert is_friday_afternoon_entry_block(noon) is True
        assert is_friday_weekend_close_time(noon) is False

        late = EST.localize(datetime(2026, 7, 10, 17, 0))
        assert is_friday_weekend_close_time(late) is True
        assert is_friday_afternoon_entry_block(late) is True

    def test_apply_friday_gates_filters_and_closes_separately(self):
        from trading_bot.utils.win_optimization import apply_friday_session_gates

        noon = EST.localize(datetime(2026, 7, 10, 12, 30))
        decision = apply_friday_session_gates(
            noon,
            symbols=["EURUSD", "BTCUSD"],
            crypto_symbols={"BTCUSD"},
        )
        assert decision.close_forex is False
        assert decision.entry_symbols == ["BTCUSD"]

        late = EST.localize(datetime(2026, 7, 10, 17, 0))
        decision2 = apply_friday_session_gates(
            late,
            symbols=["EURUSD", "BTCUSD"],
            crypto_symbols={"BTCUSD"},
        )
        assert decision2.close_forex is True
        assert decision2.entry_symbols == ["BTCUSD"]


# ---------------------------------------------------------------------------
# 7) Direction / order_type
# ---------------------------------------------------------------------------


class TestDirectionOrderType:
    def test_mismatch_rejected(self):
        from trading_bot.utils.win_optimization import order_type_matches_direction

        assert order_type_matches_direction("buy_limit", "short") is False
        assert order_type_matches_direction("sell_limit", "short") is True
        assert order_type_matches_direction("market", "long") is True

    def test_prepare_order_blocks_mismatch(self):
        signal = SimpleNamespace(
            direction="short",
            entry_price=1.0900,
            order_type="buy_limit",
            confidence=0.8,
        )
        prep = ExecutionCoordinator().prepare_order(
            trade_signal=signal,
            current_price=1.0850,
            existing_positions=[],
            analysis_results={},
        )
        assert prep.blocked is True
        assert prep.gate_id == "direction_order_type_mismatch"


# ---------------------------------------------------------------------------
# 8) Goal aliases
# ---------------------------------------------------------------------------


class TestGoalAliases:
    def test_calculate_progress_has_main_aliases(self):
        from trading_bot.services.goal_tracker import GoalTracker

        gt = GoalTracker(starting_equity=1000, target_equity=10000)
        progress = gt.calculate_progress(5000)
        assert "percent" in progress
        assert progress["progress_percent"] == progress["percent"]
        assert progress["current_equity"] == progress["current"]
        assert progress["target_equity"] == 10000
        assert "remaining" in progress


class TestDisplacementGateHelper:
    def test_not_confirmed_non_amd_rejects(self):
        from trading_bot.utils.win_optimization import displacement_gate_action

        assert (
            displacement_gate_action(
                "market",
                distribution_confirmed=False,
                amd_phase="distribution",
            )
            == "reject"
        )

    def test_not_confirmed_manip_converts(self):
        from trading_bot.utils.win_optimization import displacement_gate_action

        assert (
            displacement_gate_action(
                "market",
                distribution_confirmed=False,
                amd_phase="manipulation",
            )
            == "convert_pending"
        )

    def test_confirmed_allows_market(self):
        from trading_bot.utils.win_optimization import displacement_gate_action

        assert (
            displacement_gate_action(
                "market",
                distribution_confirmed=True,
                amd_phase="distribution",
            )
            == "allow_market"
        )
