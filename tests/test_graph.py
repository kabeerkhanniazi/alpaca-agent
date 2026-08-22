"""End-to-end pipeline tests against a fake broker.

The safety-critical assertion in this file is that a rejected spread never
reaches the executor. Everything else — journalling, halting, exits — exists to
make sure a five-day unattended run degrades gracefully instead of crashing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from options_agent.graph import OptionsAgentGraph, route_after_gate
from options_agent.nodes.trade_journal import TradeJournal
from tests.conftest import FakeSnapshot, occ_symbol


class FakeAccount:
    def __init__(self, equity=100000.0, last_equity=100000.0, buying_power=400000.0):
        self.equity = equity
        self.last_equity = last_equity
        self.buying_power = buying_power
        self.cash = equity


class FakeBroker:
    """A broker whose chain always contains a tradeable spread."""

    def __init__(self, account=None, positions=None, chain=None, spot=765.0):
        self._account = account or FakeAccount()
        self._positions = positions or []
        self._spot = spot
        self._chain = chain if chain is not None else self._default_chain()
        self.submitted = []
        self.closed = []
        self.trading = self

    def _default_chain(self):
        expiry = date.today() + timedelta(days=9)
        chain = {}
        for offset in range(2, 40):
            strike = float(self._spot - offset)
            delta = -max(0.01, 0.32 - offset * 0.0085)
            mid = max(0.05, 3.2 - offset * 0.075)
            chain[occ_symbol("SPY", expiry, strike)] = FakeSnapshot(
                round(delta, 4), round(mid - 0.01, 2), round(mid + 0.01, 2)
            )
        return chain

    def get_account(self):
        return self._account

    def get_positions(self):
        return self._positions

    def get_option_positions(self):
        return self._positions

    def get_option_snapshots(self, symbols):
        return {}

    def get_spot_price(self, ticker):
        return self._spot

    def get_daily_bars(self, ticker):
        import pandas as pd

        n = 300
        closes = [700 + i * 0.2 for i in range(n)]
        return pd.DataFrame({
            "close": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "open": closes,
            "volume": [1_000_000] * n,
        })

    def get_put_chain(self, ticker, strike_low, strike_high, expiry_from, expiry_to):
        return self._chain

    def get_order_by_client_id(self, cid):
        return None

    def submit_order(self, request):
        self.submitted.append(request)

        class Order:
            id = "fake-order"
            status = "accepted"
            filled_avg_price = 0.0
            filled_qty = 0

        return Order()

    def close_position(self, symbol):
        self.closed.append(symbol)

        class Order:
            id = "close-order"
            status = "accepted"

        return Order()


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(tmp_path / "journal.jsonl")


@pytest.fixture
def agent(config, journal):
    return OptionsAgentGraph(FakeBroker(), config, journal)


# ------------------------------------------------------------ routing

def test_approved_trades_route_to_the_executor():
    assert route_after_gate({"approved": True, "halted": False}) == "executor"


def test_rejected_trades_route_straight_to_the_journal():
    assert route_after_gate({"approved": False, "halted": False}) == "journal"


def test_a_halted_cycle_never_reaches_the_executor():
    """Halts come from missing data or an empty chain. Neither may reach the broker."""
    assert route_after_gate({"approved": True, "halted": True}) == "journal"


# ------------------------------------------------------- graph shape

def test_graph_contains_every_node(agent):
    nodes = set(agent.graph.get_graph().nodes)
    assert {
        "analyst", "position_manager", "options_calculator",
        "spread_builder", "risk_gate", "executor", "journal",
    } <= nodes


def test_position_manager_runs_before_the_gate(agent):
    """The gate reads portfolio delta, daily P&L and buying power from it."""
    edges = [(e.source, e.target) for e in agent.graph.get_graph().edges]
    assert ("analyst", "position_manager") in edges
    assert ("position_manager", "options_calculator") in edges


def test_executor_leads_to_the_journal(agent):
    edges = [(e.source, e.target) for e in agent.graph.get_graph().edges]
    assert ("executor", "journal") in edges


# -------------------------------------------------------- full cycles

def test_a_clean_cycle_reaches_approval(agent):
    state = agent.run("SPY", dry_run=True)
    assert not state.get("halted"), state.get("halt_reason")
    assert state["approved"]
    assert state["contracts"] >= 1


def test_a_dry_run_submits_nothing(agent):
    agent.run("SPY", dry_run=True)
    assert agent.broker.submitted == []


def test_a_live_run_submits_the_order(agent):
    state = agent.run("SPY", dry_run=False)
    assert state["approved"]
    assert len(agent.broker.submitted) == 1


def test_a_rejected_cycle_submits_nothing(config, journal):
    """The safety invariant: rejection means no order, full stop.

    A tiny account makes every spread exceed the 2% loss budget.
    """
    broker = FakeBroker(account=FakeAccount(equity=3000.0, last_equity=3000.0, buying_power=3000.0))
    agent = OptionsAgentGraph(broker, config, journal)
    state = agent.run("SPY", dry_run=False)
    assert not state.get("approved")
    assert broker.submitted == []


def test_the_kill_switch_blocks_execution(config, journal):
    """Down 8% on the day: nothing gets through, regardless of how good the spread is."""
    broker = FakeBroker(account=FakeAccount(equity=92000.0, last_equity=100000.0))
    agent = OptionsAgentGraph(broker, config, journal)
    state = agent.run("SPY", dry_run=False)
    assert not state["approved"]
    assert "R8_kill_switch" in [c["rule"] for c in state["gate_checks"] if not c["passed"]]
    assert broker.submitted == []


def test_an_empty_chain_halts_cleanly(config, journal):
    """No data is a reason to stop, not to crash."""
    broker = FakeBroker(chain={})
    agent = OptionsAgentGraph(broker, config, journal)
    state = agent.run("SPY", dry_run=True)
    assert state["halted"]
    assert not state["approved"]
    assert broker.submitted == []


def test_a_market_data_failure_halts_instead_of_raising(config, journal):
    """An unattended agent must survive a bad cycle and try again in five minutes."""
    broker = FakeBroker()

    def boom(ticker):
        raise RuntimeError("data feed down")

    broker.get_spot_price = boom
    agent = OptionsAgentGraph(broker, config, journal)
    state = agent.run("SPY", dry_run=True)
    assert state["halted"]
    assert "data feed down" in state["halt_reason"]


# --------------------------------------------------------- journalling

def test_every_cycle_leaves_a_record(agent, journal):
    agent.run("SPY", dry_run=True)
    events = list(journal.read_all())
    assert events
    assert {"analysis", "trade_approved"} <= {e["event_type"] for e in events}


def test_a_rejection_records_the_failing_rule(config, journal):
    broker = FakeBroker(account=FakeAccount(equity=92000.0, last_equity=100000.0))
    agent = OptionsAgentGraph(broker, config, journal)
    agent.run("SPY", dry_run=True)

    rejections = [e for e in journal.read_all() if e["event_type"] == "trade_rejected"]
    assert rejections
    assert "R8_kill_switch" in rejections[0]["failing_rules"]


def test_a_halted_cycle_is_journalled_too(config, journal):
    agent = OptionsAgentGraph(FakeBroker(chain={}), config, journal)
    agent.run("SPY", dry_run=True)
    assert any(e["event_type"] == "cycle_skipped" for e in journal.read_all())


def test_stats_read_back_from_the_journal(agent, journal):
    agent.run("SPY", dry_run=True)
    stats = journal.compute_stats()
    assert stats["total_events"] > 0
    assert stats["approvals"] + stats["rejections"] >= 1


# --------------------------------------------------------------- exits

def test_no_positions_means_no_exits(agent):
    assert agent.manage_exits(dry_run=True) == []


def test_a_position_near_expiry_is_closed(config, journal):
    class Position:
        symbol = occ_symbol("SPY", date.today() + timedelta(days=1), 750.0)
        qty = -1
        avg_entry_price = 1.5
        market_value = -30.0
        cost_basis = -150.0
        unrealized_pl = 120.0
        unrealized_plpc = 0.8
        current_price = 0.3
        asset_class = "us_option"

    broker = FakeBroker(positions=[Position()])
    agent = OptionsAgentGraph(broker, config, journal)
    exits = agent.manage_exits(dry_run=False)
    assert len(exits) == 1
    assert "DTE" in exits[0]["exit_reason"]
    assert broker.closed == [Position.symbol]


def test_exits_are_journalled(config, journal):
    class Position:
        symbol = occ_symbol("SPY", date.today() + timedelta(days=1), 750.0)
        qty = -1
        avg_entry_price = 1.5
        market_value = -30.0
        cost_basis = -150.0
        unrealized_pl = 120.0
        unrealized_plpc = 0.8
        current_price = 0.3
        asset_class = "us_option"

    agent = OptionsAgentGraph(FakeBroker(positions=[Position()]), config, journal)
    agent.manage_exits(dry_run=True)
    assert any(e["event_type"] == "position_exit" for e in journal.read_all())
