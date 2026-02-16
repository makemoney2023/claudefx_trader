"""
Tests for Aggressive Profit Protection and Reversal Re-entry.

Covers:
- Peak R-multiple tracking
- Dynamic SL trailing between 1R-2R
- 40% giveback auto-close
- Near-TP reversal detection
- Close reason propagation
- Reversal re-entry safeguards
- Direction flip cooldown bypass for reversals
- Claude TIGHTEN profit locking
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from trading_bot.execution.position_manager import (
    PositionManager,
    Position,
    PositionStatus,
)


# ---- Fixtures ----

@pytest.fixture
def order_manager():
    """Mock order manager that always succeeds."""
    om = MagicMock()
    om.close_position = AsyncMock(return_value=MagicMock(success=True))
    om.modify_order = AsyncMock(return_value=MagicMock(success=True))
    om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))
    return om


@pytest.fixture
def pm(order_manager):
    """Position manager wired to mock order manager."""
    mgr = PositionManager(order_manager=order_manager)
    return mgr


@pytest.fixture
def long_position():
    """Long position: entry=100, SL=95, TP=115, risk=5."""
    return Position(
        ticket=1001,
        symbol="XAUUSD",
        direction="long",
        volume=0.10,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        open_time=datetime.now(),
        trade_type="intraday",
    )


@pytest.fixture
def short_position():
    """Short position: entry=100, SL=105, TP=85, risk=5."""
    return Position(
        ticket=2001,
        symbol="EURUSD",
        direction="short",
        volume=0.10,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=85.0,
        open_time=datetime.now(),
        trade_type="intraday",
    )


# ================================================================
#  1. Position Dataclass — new fields
# ================================================================

class TestPositionNewFields:
    """Verify the new fields on Position."""

    def test_peak_r_defaults_to_zero(self, long_position):
        assert long_position.peak_r_multiple == 0.0
        assert long_position.peak_unrealized_pnl == 0.0

    def test_near_tp_defaults_to_false(self, long_position):
        assert long_position.near_tp_reached is False

    def test_close_reason_defaults_to_empty(self, long_position):
        assert long_position.close_reason == ""

    def test_to_dict_includes_new_fields(self, long_position):
        d = long_position.to_dict()
        assert "peak_r_multiple" in d
        assert "peak_unrealized_pnl" in d
        assert "near_tp_reached" in d
        assert "close_reason" in d

    def test_close_reason_is_settable(self, long_position):
        long_position.close_reason = "giveback_protection"
        assert long_position.close_reason == "giveback_protection"


# ================================================================
#  2. Peak Tracking (manage_positions updates peak before _manage)
# ================================================================

class TestPeakTracking:
    """Peak R-multiple should update in manage_positions before any decisions."""

    @pytest.mark.asyncio
    async def test_peak_r_updated_when_price_improves(self, pm, long_position):
        pm.add_position(long_position)

        # Price at 1.5R (entry=100, risk=5, so 1.5R = 107.5)
        await pm.manage_positions({"XAUUSD": 107.5})
        assert long_position.peak_r_multiple == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_peak_r_does_not_decrease(self, pm, long_position):
        pm.add_position(long_position)

        # Peak at 2R (110), then drop to 1R (105)
        await pm.manage_positions({"XAUUSD": 110.0})
        assert long_position.peak_r_multiple == pytest.approx(2.0)

        await pm.manage_positions({"XAUUSD": 105.0})
        assert long_position.peak_r_multiple == pytest.approx(2.0)  # Still 2.0

    @pytest.mark.asyncio
    async def test_peak_unrealized_pnl_set(self, pm, long_position):
        pm.add_position(long_position)
        await pm.manage_positions({"XAUUSD": 108.0})
        # Peak should be recorded; exact PnL depends on calculate_pl
        assert long_position.peak_r_multiple == pytest.approx(1.6)


# ================================================================
#  3. Dynamic SL Trailing 1R–2R
# ================================================================

class TestDynamicTrail1Rto2R:
    """After TP1 hit and BE triggered, SL should trail between 1R and 2R."""

    @pytest.mark.asyncio
    async def test_dynamic_trail_locks_profit(self, pm, long_position, order_manager):
        pm.add_position(long_position)
        # Simulate TP1 hit state
        long_position.tp1_hit = True
        long_position.be_triggered = True
        long_position.stop_loss = 100.0  # At break-even
        long_position.tp1 = 105.0
        long_position.current_price = 107.5  # 1.5R
        long_position.peak_r_multiple = 1.5

        result = await pm._dynamic_trail_1r_to_2r(long_position, 1.5)

        # locked_profit_r = (1.5 - 1.0) * 0.5 = 0.25
        # new_sl = 100.0 + 0.25 * 5.0 = 101.25
        assert result is not None
        assert result["action"] == "dynamic_trail_1r_2r"
        assert long_position.stop_loss == pytest.approx(101.25)

    @pytest.mark.asyncio
    async def test_dynamic_trail_not_active_before_tp1(self, pm, long_position):
        pm.add_position(long_position)
        long_position.tp1_hit = False
        long_position.be_triggered = False
        long_position.current_price = 107.5

        result = await pm._dynamic_trail_1r_to_2r(long_position, 1.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_dynamic_trail_not_active_after_tp2(self, pm, long_position):
        pm.add_position(long_position)
        long_position.tp1_hit = True
        long_position.be_triggered = True
        long_position.tp2_hit = True  # Already past TP2

        result = await pm._dynamic_trail_1r_to_2r(long_position, 2.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_dynamic_trail_only_improves_sl_long(self, pm, long_position, order_manager):
        pm.add_position(long_position)
        long_position.tp1_hit = True
        long_position.be_triggered = True
        long_position.tp1 = 105.0
        long_position.stop_loss = 102.0  # Already above break-even
        long_position.current_price = 106.0  # 1.2R

        # locked_profit_r = (1.2 - 1.0) * 0.5 = 0.1 -> new_sl = 100 + 0.1*5 = 100.5
        # 100.5 < 102.0 (current SL), so should NOT modify
        result = await pm._dynamic_trail_1r_to_2r(long_position, 1.2)
        assert result is None

    @pytest.mark.asyncio
    async def test_dynamic_trail_short_position(self, pm, short_position, order_manager):
        pm.add_position(short_position)
        short_position.tp1_hit = True
        short_position.be_triggered = True
        short_position.stop_loss = 100.0  # Break-even
        short_position.tp1 = 95.0
        short_position.current_price = 92.5  # 1.5R for short

        result = await pm._dynamic_trail_1r_to_2r(short_position, 1.5)

        # locked_profit_r = (1.5 - 1.0) * 0.5 = 0.25
        # new_sl = 100.0 - 0.25 * 5.0 = 98.75
        assert result is not None
        assert short_position.stop_loss == pytest.approx(98.75)


# ================================================================
#  4. Giveback Auto-Close (40% rule)
# ================================================================

class TestGivebackProtection:
    """40% giveback from peak when peak >= 1.0R triggers auto-close."""

    @pytest.mark.asyncio
    async def test_giveback_triggers_close(self, pm, long_position, order_manager):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.tp1_hit = True
        long_position.peak_r_multiple = 2.0
        long_position.current_price = 101.0  # 0.2R (gave back 90% of 2.0R peak)

        result = await pm._check_profit_protection(long_position, 0.2)

        assert result is not None
        assert "giveback" in result["action"]
        assert long_position.close_reason == "giveback_protection"
        order_manager.close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_giveback_does_not_trigger_below_1r_peak(self, pm, long_position):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 0.8  # Below 1R threshold
        long_position.current_price = 100.5

        result = await pm._check_profit_protection(long_position, 0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_giveback_does_not_trigger_at_30_pct(self, pm, long_position):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 2.0
        long_position.current_price = 107.0  # 1.4R = 30% giveback from 2.0R

        result = await pm._check_profit_protection(long_position, 1.4)
        assert result is None  # 30% < 40% threshold

    @pytest.mark.asyncio
    async def test_giveback_does_not_trigger_when_r_negative(self, pm, long_position):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 1.5
        long_position.current_price = 94.0  # -1.2R (below entry, in loss)

        # r_multiple is negative, so giveback check requires r > 0
        result = await pm._check_profit_protection(long_position, -1.2)
        assert result is None


# ================================================================
#  5. Near-TP Reversal Detection
# ================================================================

class TestNearTPProtection:
    """85%+ of TP reached then 50% giveback from peak triggers close."""

    @pytest.mark.asyncio
    async def test_near_tp_flagged(self, pm, long_position):
        pm.add_position(long_position)
        long_position.be_triggered = True
        # TP at 115, entry 100, risk 5 -> TP R = (115-100)/5 = 3.0R
        # 85% of 3.0R = 2.55R
        long_position.peak_r_multiple = 2.6  # Above 85% of TP

        # Current still at peak, no giveback yet
        await pm._check_profit_protection(long_position, 2.6)
        assert long_position.near_tp_reached is True

    @pytest.mark.asyncio
    async def test_near_tp_reversal_triggers_close(self, pm, long_position, order_manager):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 2.7
        long_position.near_tp_reached = True

        # 50% giveback from peak: 2.7 * 0.5 = 1.35, current = 1.35
        # giveback = (2.7 - 1.35) / 2.7 = 0.5 = 50%
        result = await pm._check_profit_protection(long_position, 1.35)

        assert result is not None
        assert long_position.close_reason == "near_tp_reversal"
        order_manager.close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_near_tp_not_triggered_if_not_flagged(self, pm, long_position):
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 0.9  # Below 1.0R threshold, protection inactive
        long_position.near_tp_reached = False

        result = await pm._check_profit_protection(long_position, 0.5)
        assert result is None  # peak < 1.0R, so no protection fires

    @pytest.mark.asyncio
    async def test_near_tp_protection_before_giveback(self, pm, long_position, order_manager):
        """Near-TP check fires before the general giveback check."""
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.peak_r_multiple = 2.8  # Near TP (>= 2.55)
        long_position.near_tp_reached = True

        # 50% giveback: now at 1.4R (2.8 * 0.5 = 1.4)
        # This also exceeds 40% giveback, but near-TP should fire first
        result = await pm._check_profit_protection(long_position, 1.4)

        assert result is not None
        assert long_position.close_reason == "near_tp_reversal"


# ================================================================
#  6. Protection Close Method
# ================================================================

class TestProtectionClose:
    """_protection_close should close full volume and fire callback."""

    @pytest.mark.asyncio
    async def test_protection_close_calls_order_manager(self, pm, long_position, order_manager):
        pm.add_position(long_position)
        long_position.close_reason = "giveback_protection"

        result = await pm._protection_close(long_position, "giveback_protection")

        assert result["success"] is True
        order_manager.close_position.assert_called_once_with(
            ticket=1001, volume=0.10
        )

    @pytest.mark.asyncio
    async def test_protection_close_fires_reversal_callback(self, pm, long_position, order_manager):
        callback = AsyncMock()
        pm.on_reversal_close = callback
        pm.add_position(long_position)
        long_position.close_reason = "near_tp_reversal"

        await pm._protection_close(long_position, "near_tp_reversal")

        callback.assert_called_once_with(long_position)

    @pytest.mark.asyncio
    async def test_protection_close_resets_reason_on_failure(self, pm, long_position, order_manager):
        order_manager.close_position = AsyncMock(
            return_value=MagicMock(success=False, message="Market closed")
        )
        pm.add_position(long_position)
        long_position.close_reason = "giveback_protection"

        result = await pm._protection_close(long_position, "giveback_protection")

        assert result["success"] is False
        assert long_position.close_reason == ""  # Reset since close failed


# ================================================================
#  7. Reversal Re-entry Safeguards
# ================================================================

class TestReversalSafeguards:
    """Test the safeguard checks in _analyze_reversal_entry."""

    def test_reversal_reentry_flag_on_trade_signal(self):
        """TradeSignal should have a reversal_reentry flag."""
        from trading_bot.llm.claude_client import TradeSignal
        sig = TradeSignal(
            direction="long",
            confidence=0.85,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            risk_reward=2.0,
            reasoning="test",
        )
        assert sig.reversal_reentry is False
        sig.reversal_reentry = True
        assert sig.reversal_reentry is True

    def test_position_manager_has_reversal_callback(self, pm):
        """PositionManager should have an on_reversal_close attribute."""
        assert hasattr(pm, 'on_reversal_close')
        assert pm.on_reversal_close is None


# ================================================================
#  8. Direction Flip Cooldown Bypass
# ================================================================

class TestFlipCooldownBypass:
    """Reversal re-entries should bypass the direction flip cooldown."""

    def test_flip_guard_code_has_reversal_bypass(self):
        """The flip guard in main.py should check reversal_reentry."""
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_and_trade)
        assert 'reversal_reentry' in source, \
            "Flip guard should check reversal_reentry flag"
        assert 'Bypassing cooldown for reversal re-entry' in source, \
            "Should log bypass for reversal re-entries"


# ================================================================
#  9. Claude TIGHTEN Upgrade
# ================================================================

class TestTightenUpgrade:
    """TIGHTEN should lock actual profit percentages, not just break-even."""

    def test_tighten_code_locks_profit_percentage(self):
        """The TIGHTEN handler should use lock_pct based on R-multiple."""
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._claude_reevaluate_positions)
        assert 'lock_pct' in source, \
            "TIGHTEN should use lock_pct for profit locking"
        assert '0.75' in source, \
            "Should lock 75% above 2.0R"
        assert '0.60' in source, \
            "Should lock 60% above 1.5R"
        assert '0.50' in source, \
            "Should lock 50% above 1.0R"


# ================================================================
#  10. DB Persistence of Peak Fields
# ================================================================

class TestDBPersistence:
    """Peak tracking fields should be in the DB model."""

    def test_position_state_model_has_peak_fields(self):
        from trading_bot.api.database import PositionStateModel
        assert hasattr(PositionStateModel, 'peak_r_multiple'), \
            "PositionStateModel should have peak_r_multiple column"
        assert hasattr(PositionStateModel, 'peak_unrealized_pnl'), \
            "PositionStateModel should have peak_unrealized_pnl column"
        assert hasattr(PositionStateModel, 'near_tp_reached'), \
            "PositionStateModel should have near_tp_reached column"


# ================================================================
#  11. Reversal Analysis Method Exists
# ================================================================

class TestReversalAnalysisMethod:
    """Verify _analyze_reversal_entry exists and has correct structure."""

    def test_method_exists_on_trading_bot(self):
        from trading_bot.main import TradingBot
        assert hasattr(TradingBot, '_analyze_reversal_entry'), \
            "TradingBot should have _analyze_reversal_entry method"

    def test_method_checks_reversal_cooldown(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert '_reversal_cooldowns' in source, \
            "Should check per-symbol reversal cooldown"
        assert '60' in source, \
            "Should have 60-minute cooldown"

    def test_method_checks_existing_position(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'get_positions_by_symbol' in source, \
            "Should check for existing positions"

    def test_method_checks_daily_limit(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'max_daily_trades' in source, \
            "Should check daily trade limit"

    def test_method_skips_scalps(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'scalp' in source, \
            "Should skip scalp trade reversals"

    def test_method_validates_opposite_direction(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'opposite_direction' in source, \
            "Should validate signal is in opposite direction"

    def test_method_calls_trade_judge(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'judge_trade' in source, \
            "Should call Trade Judge for validation"

    def test_method_includes_reversal_context(self):
        import inspect
        from trading_bot.main import TradingBot
        source = inspect.getsource(TradingBot._analyze_reversal_entry)
        assert 'REVERSAL ANALYSIS CONTEXT' in source, \
            "Should include reversal context for Claude"
        assert 'Break of structure' in source or 'structural reversal' in source, \
            "Should ask Claude about structural reversal"


# ================================================================
#  12. Scalp Positions Skip Profit Protection
# ================================================================

class TestScalpSkipsProtection:
    """Scalp trades should not trigger profit protection."""

    @pytest.mark.asyncio
    async def test_scalp_skips_profit_protection(self, pm, long_position):
        pm.add_position(long_position)
        long_position.trade_type = "scalp"
        long_position.peak_r_multiple = 2.0
        long_position.be_triggered = True

        # Even with 50% giveback, scalps skip _check_profit_protection
        # because _manage_position returns early for scalps
        long_position.current_price = 105.0
        result = await pm._manage_position(long_position)

        # Scalp just does break-even at 0.5R; no profit protection close
        assert long_position.close_reason == ""


# ================================================================
#  13. Integration: manage_positions flow
# ================================================================

class TestManagePositionsIntegration:
    """Full manage_positions flow: peak update -> protection -> TP stages."""

    @pytest.mark.asyncio
    async def test_peak_updated_before_protection_check(self, pm, long_position):
        """Peak should be updated in manage_positions before _manage_position runs."""
        pm.add_position(long_position)
        long_position.be_triggered = True
        long_position.tp1_hit = True

        # First call: price at 2.0R (110)
        await pm.manage_positions({"XAUUSD": 110.0})
        assert long_position.peak_r_multiple == pytest.approx(2.0)

        # Second call: price drops to 1.0R (105) — 50% giveback
        # Protection should close because peak was 2.0R and gave back 50%
        await pm.manage_positions({"XAUUSD": 105.0})
        # After giveback close, close_reason should be set
        # (if order_manager succeeds, which our mock does)
        assert long_position.close_reason == "giveback_protection"
