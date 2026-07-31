"""Tests for trade outcome status derivation (open/closed/cancelled)."""

from types import SimpleNamespace

from trading_bot.api.routes.trades import derive_trade_status


def _trade(**kwargs):
    defaults = dict(
        entry_price=4035.0,
        exit_price=None,
        profit_loss=None,
        exit_reason=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestDeriveTradeStatus:
    def test_open_when_no_exit(self):
        assert derive_trade_status(_trade()) == "open"

    def test_closed_win(self):
        t = _trade(exit_price=4020.0, profit_loss=12.5, exit_reason="Closed on MT5 (TP/SL/manual)")
        assert derive_trade_status(t) == "closed"

    def test_closed_loss(self):
        t = _trade(exit_price=4048.28, profit_loss=-12.81, exit_reason="SL/TP hit (filled-then-closed, detected via trade sync)")
        assert derive_trade_status(t) == "closed"

    def test_cancelled_by_exit_reason(self):
        t = _trade(
            exit_price=4035.0,
            profit_loss=0.0,
            exit_reason="Cancelled/deleted (not found on MT5)",
        )
        assert derive_trade_status(t) == "cancelled"

    def test_cancelled_zero_pnl_same_price_heuristic(self):
        """Sync-cancelled rows with missing/blank reason still must not look closed."""
        t = _trade(exit_price=4035.0, profit_loss=0.0, exit_reason="")
        assert derive_trade_status(t) == "cancelled"

    def test_true_breakeven_with_real_exit_is_closed(self):
        """A real fill that exits flat at a different price is closed, not cancelled."""
        t = _trade(
            entry_price=4035.0,
            exit_price=4035.50,
            profit_loss=0.0,
            exit_reason="Closed on MT5 (TP/SL/manual)",
        )
        assert derive_trade_status(t) == "closed"
