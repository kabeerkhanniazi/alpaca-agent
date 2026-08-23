"""Order construction, the credit/debit sign convention, and idempotency.

Everything runs against a fake broker. No test in this file can reach Alpaca.
"""

from __future__ import annotations

import pytest

from options_agent.nodes.executor import (
    build_client_order_id,
    build_spread_order,
    close_position,
    compute_limit_price,
    execute_spread,
)


class FakeOrder:
    def __init__(self, order_id="order-1", status="accepted"):
        self.id = order_id
        self.status = status
        self.filled_avg_price = 0.0
        self.filled_qty = 0


class FakeBroker:
    """Records what it was asked to do; never talks to a network."""

    def __init__(self, existing=None, raises=None):
        self.submitted = []
        self.closed = []
        self._existing = existing
        self._raises = raises
        self.trading = self

    def get_order_by_client_id(self, client_order_id):
        return self._existing

    def submit_order(self, request):
        if self._raises:
            raise self._raises
        self.submitted.append(request)
        return FakeOrder()

    def close_position(self, symbol):
        self.closed.append(symbol)
        return FakeOrder(order_id="close-1")


# ------------------------------------------------- the sign convention

def test_credit_spread_submits_a_negative_limit_price(spread, config):
    """Alpaca reads a negative mleg limit as a credit and a positive one as a debit.

    This is the single most consequential detail in the file. Getting it
    backwards would submit an order willing to *pay* to open a position that
    should be collecting premium.
    """
    assert compute_limit_price(spread, config) < 0


def test_limit_price_magnitude_is_per_share_not_per_contract(spread, config):
    """Options are quoted per share; $63 of credit is $0.63 on the ticket."""
    assert abs(compute_limit_price(spread, config)) < 5.0


def test_limit_price_gives_back_only_the_configured_slippage(spread, config):
    slippage = float(config.execution["limit_price_slippage"])
    expected = spread["mid_credit"] / 100.0 - slippage
    assert abs(compute_limit_price(spread, config)) == pytest.approx(expected, abs=0.01)


def test_conservative_mode_prices_off_the_worst_fill(spread, config):
    conservative = dict(config.options)
    conservative["execution"] = {**config.execution, "limit_price_mode": "conservative"}
    from dataclasses import replace

    cfg = replace(config, options=conservative)
    # net_credit (bid/ask) is worse than mid_credit, so the limit is lower.
    assert abs(compute_limit_price(spread, cfg)) < abs(compute_limit_price(spread, config))


def test_limit_price_never_goes_to_zero_or_positive(config):
    """Even a tiny credit must still be submitted as a credit."""
    thin = {"net_credit": 1.0, "mid_credit": 1.0}
    assert compute_limit_price(thin, config) < 0


# ---------------------------------------------------- order construction

def test_order_is_a_two_leg_mleg_order(spread, config):
    order = build_spread_order(spread, 4, "cid", config)
    assert order["order_class"] == "mleg"
    assert len(order["legs"]) == 2
    assert order["qty"] == "4"


def test_short_leg_sells_the_near_strike(spread, config):
    order = build_spread_order(spread, 4, "cid", config)
    sell_leg = next(leg for leg in order["legs"] if leg["side"] == "sell")
    assert sell_leg["symbol"] == spread["sell_symbol"]
    assert sell_leg["position_intent"] == "sell_to_open"


def test_long_leg_buys_the_protective_strike(spread, config):
    order = build_spread_order(spread, 4, "cid", config)
    buy_leg = next(leg for leg in order["legs"] if leg["side"] == "buy")
    assert buy_leg["symbol"] == spread["buy_symbol"]
    assert buy_leg["position_intent"] == "buy_to_open"


def test_legs_are_equally_weighted(spread, config):
    """A vertical spread is one-for-one; unequal ratios would be a different trade."""
    order = build_spread_order(spread, 4, "cid", config)
    assert {leg["ratio_qty"] for leg in order["legs"]} == {"1"}


def test_both_legs_go_in_one_order(spread, config):
    """Legging in separately risks a fill on the short side alone — a naked put."""
    order = build_spread_order(spread, 4, "cid", config)
    symbols = {leg["symbol"] for leg in order["legs"]}
    assert symbols == {spread["sell_symbol"], spread["buy_symbol"]}


def test_credit_spread_submits_as_a_credit(spread, config):
    """The sign convention, pinned at the order-construction boundary.

    Alpaca reads a negative multi-leg limit price as a credit and a positive one
    as a debit. Inverting this would submit an order willing to *pay* to open a
    position that should collect, so it is asserted on the exact string that
    reaches the CLI rather than on the intermediate float.
    """
    order = build_spread_order(spread, 4, "cid", config)
    assert order["limit_price"].startswith("-")
    assert float(order["limit_price"]) < 0


# ------------------------------------------------------- idempotency

def test_client_order_id_is_stable_for_the_same_trade(spread):
    first = build_client_order_id(spread, "2026-08-24", 4)
    second = build_client_order_id(spread, "2026-08-24", 4)
    assert first == second


def test_client_order_id_changes_with_size(spread):
    assert build_client_order_id(spread, "2026-08-24", 4) != build_client_order_id(spread, "2026-08-24", 3)


def test_client_order_id_changes_with_date(spread):
    assert build_client_order_id(spread, "2026-08-24", 4) != build_client_order_id(spread, "2026-08-25", 4)


def test_client_order_id_changes_with_strike(spread):
    other = {**spread, "sell_strike": 750.0}
    assert build_client_order_id(spread, "2026-08-24", 4) != build_client_order_id(other, "2026-08-24", 4)


def test_client_order_id_fits_alpacas_field(spread):
    assert len(build_client_order_id(spread, "2026-08-24", 4)) <= 128


def test_an_existing_order_is_adopted_rather_than_duplicated(spread, config):
    """A crash between submit and record must not open a second position."""
    broker = FakeBroker(existing=FakeOrder(order_id="already-there", status="filled"))
    result = execute_spread(broker, spread, 4, "2026-08-24", config, dry_run=False)
    assert result["success"]
    assert result["duplicate"] is True
    assert result["order_id"] == "already-there"
    assert broker.submitted == []


# ----------------------------------------------------------- dry run

def test_dry_run_submits_nothing(spread, config):
    broker = FakeBroker()
    result = execute_spread(broker, spread, 4, "2026-08-24", config, dry_run=True)
    assert result["dry_run"] is True
    assert result["status"] == "dry_run"
    assert broker.submitted == []


def test_dry_run_still_reports_the_full_trade(spread, config):
    """The journal and dashboard read dry runs the same way as live trades."""
    result = execute_spread(FakeBroker(), spread, 4, "2026-08-24", config, dry_run=True)
    for field in ("client_order_id", "contracts", "limit_price", "max_loss_total", "sell_symbol"):
        assert field in result


def test_live_run_submits_once(spread, config):
    broker = FakeBroker()
    result = execute_spread(broker, spread, 4, "2026-08-24", config, dry_run=False)
    assert result["success"]
    assert len(broker.submitted) == 1


# ------------------------------------------------------ error handling

def test_a_broker_rejection_is_reported_not_raised(spread, config):
    """An unattended agent must survive a rejected order and journal the reason."""
    broker = FakeBroker(raises=RuntimeError("insufficient buying power"))
    result = execute_spread(broker, spread, 4, "2026-08-24", config, dry_run=False)
    assert result["success"] is False
    assert result["status"] == "failed"
    assert "insufficient buying power" in result["error"]


def test_max_loss_total_scales_with_contracts(spread, config):
    result = execute_spread(FakeBroker(), spread, 3, "2026-08-24", config, dry_run=True)
    assert result["max_loss_total"] == pytest.approx(spread["max_loss"] * 3, abs=0.01)


# ---------------------------------------------------------- exits

def test_close_position_dry_run_does_nothing(config):
    broker = FakeBroker()
    result = close_position(broker, {"symbol": "SPY260831P00753000", "unrealized_pl": 30.0}, dry_run=True)
    assert result["dry_run"] is True
    assert broker.closed == []


def test_close_position_live_closes_the_symbol(config):
    broker = FakeBroker()
    result = close_position(broker, {"symbol": "SPY260831P00753000", "unrealized_pl": 30.0}, dry_run=False)
    assert result["success"]
    assert broker.closed == ["SPY260831P00753000"]


def test_close_failure_is_reported_not_raised():
    class Failing(FakeBroker):
        def close_position(self, symbol):
            raise RuntimeError("position not found")

    result = close_position(Failing(), {"symbol": "X", "unrealized_pl": 0}, dry_run=False)
    assert result["success"] is False
    assert "position not found" in result["error"]
