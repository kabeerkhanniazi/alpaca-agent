"""Shared state passed between graph nodes.

One flat TypedDict rather than nested objects: LangGraph merges node returns
into this dict, and a flat shape makes each node's contribution obvious in a
trace. Every field is optional because a cycle can legitimately stop early —
a LOW_IV regime skip, an empty chain, a gate rejection — and later nodes must
tolerate the fields their predecessors never filled.
"""

from __future__ import annotations

from typing import Any, TypedDict


class OptionsAgentState(TypedDict, total=False):
    # ---- Run identity
    ticker: str
    run_id: str
    trade_date: str
    cycle_started_at: str
    dry_run: bool

    # ---- analyst node
    spot: float
    market_context: dict[str, Any]
    iv_rank: float | None
    iv_rank_source: str
    regime: str

    # ---- options_calculator node
    candidates: list[dict[str, Any]]
    chain_index: dict[str, dict[str, Any]]
    chain_size: int

    # ---- spread_builder node
    spreads: list[dict[str, Any]]
    selected_spread: dict[str, Any] | None

    # ---- position_manager node (runs before the gate; feeds it)
    portfolio: dict[str, Any]
    exits: list[dict[str, Any]]

    # ---- risk_gate node
    approved: bool
    gate_reason: str
    gate_checks: list[dict[str, Any]]
    contracts: int

    # ---- executor node
    execution: dict[str, Any] | None

    # ---- control flow
    halted: bool
    halt_reason: str
    errors: list[str]


def new_state(ticker: str, run_id: str, trade_date: str, dry_run: bool = True) -> OptionsAgentState:
    """Build the initial state for one ticker's pass through the graph."""
    return OptionsAgentState(
        ticker=ticker,
        run_id=run_id,
        trade_date=trade_date,
        dry_run=dry_run,
        candidates=[],
        spreads=[],
        selected_spread=None,
        exits=[],
        approved=False,
        gate_reason="",
        gate_checks=[],
        contracts=0,
        execution=None,
        halted=False,
        halt_reason="",
        errors=[],
    )
