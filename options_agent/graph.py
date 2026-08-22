"""LangGraph wiring for the options agent.

The node-and-edge shape follows plan.md's design, with one change: the position
manager runs *before* the risk gate rather than after execution. The gate needs
current portfolio delta, daily P&L and buying power to evaluate Rules 3, 8 and
9, and those are exactly what the position manager produces. Running it
afterwards would have the gate deciding on facts from the previous cycle.

Every node here is a deterministic function. LangGraph is doing orchestration
and state merging, not agent reasoning — there is no LLM anywhere in this graph.

    START
      -> analyst              (spot, technicals, IV rank + regime)
      -> position_manager     (portfolio Greeks, daily P&L, open positions)
      -> options_calculator   (chain -> candidate short strikes)
      -> spread_builder       (candidates -> ranked vertical spreads)
      -> risk_gate            (nine rules; approve or reject)
           |- approved -> executor -> journal -> END
           `- otherwise -------------> journal -> END
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from .config import AgentConfig
from .nodes.analyst import make_analyst_node
from .nodes.executor import make_executor_node
from .nodes.options_calculator import make_options_calculator_node
from .nodes.position_manager import make_position_manager_node
from .nodes.risk_gate import make_risk_gate_node
from .nodes.spread_builder import make_spread_builder_node
from .nodes.trade_journal import TradeJournal, make_journal_node
from .state import OptionsAgentState, new_state

logger = logging.getLogger(__name__)


def route_after_gate(state: OptionsAgentState) -> str:
    """Send approved trades to the executor and everything else to the journal.

    A halted cycle skips execution too — halts come from missing market data or
    an empty candidate list, neither of which should ever reach the broker.
    """
    if state.get("halted"):
        return "journal"
    return "executor" if state.get("approved") else "journal"


class OptionsAgentGraph:
    """Compiled pipeline for one ticker per invocation."""

    def __init__(self, broker, config: AgentConfig, journal: TradeJournal | None = None):
        self.broker = broker
        self.config = config
        self.journal = journal or TradeJournal(config.paths["journal"])

        workflow = StateGraph(OptionsAgentState)

        workflow.add_node("analyst", make_analyst_node(broker, config))
        workflow.add_node("position_manager", make_position_manager_node(broker, config))
        workflow.add_node("options_calculator", make_options_calculator_node(broker, config))
        workflow.add_node("spread_builder", make_spread_builder_node(config))
        workflow.add_node("risk_gate", make_risk_gate_node(config))
        workflow.add_node("executor", make_executor_node(broker, config))
        workflow.add_node("journal", make_journal_node(self.journal))

        workflow.add_edge(START, "analyst")
        workflow.add_edge("analyst", "position_manager")
        workflow.add_edge("position_manager", "options_calculator")
        workflow.add_edge("options_calculator", "spread_builder")
        workflow.add_edge("spread_builder", "risk_gate")
        workflow.add_conditional_edges(
            "risk_gate",
            route_after_gate,
            {"executor": "executor", "journal": "journal"},
        )
        workflow.add_edge("executor", "journal")
        workflow.add_edge("journal", END)

        self.workflow = workflow
        self.graph = workflow.compile()

    def run(
        self,
        ticker: str,
        trade_date: str | None = None,
        dry_run: bool = True,
        run_id: str | None = None,
    ) -> OptionsAgentState:
        """Run one full cycle for one ticker."""
        trade_date = trade_date or datetime.now().date().isoformat()
        run_id = run_id or uuid.uuid4().hex[:12]

        state = new_state(ticker=ticker, run_id=run_id, trade_date=trade_date, dry_run=dry_run)
        state["cycle_started_at"] = datetime.now().astimezone().isoformat()

        logger.info("Cycle start: %s (%s, run %s)", ticker, "dry-run" if dry_run else "LIVE", run_id)
        final: dict[str, Any] = self.graph.invoke(state)

        if final.get("halted"):
            logger.info("Cycle halted for %s: %s", ticker, final.get("halt_reason"))
        else:
            logger.info(
                "Cycle done: %s — %s",
                ticker,
                "APPROVED" if final.get("approved") else "REJECTED",
            )
        return final  # type: ignore[return-value]

    def manage_exits(self, dry_run: bool = True, run_id: str | None = None) -> list[dict[str, Any]]:
        """Close any open position that has hit an exit trigger.

        Deliberately outside the per-ticker graph: exits are a whole-book
        concern, and running them once per cycle rather than once per ticker
        avoids evaluating the same position three times.
        """
        from .nodes.executor import close_position
        from .nodes.position_manager import build_portfolio_state, decide_exits

        run_id = run_id or uuid.uuid4().hex[:12]
        results: list[dict[str, Any]] = []

        try:
            portfolio = build_portfolio_state(self.broker, self.config)
        except Exception as exc:  # noqa: BLE001
            logger.error("Exit management skipped — portfolio unavailable: %s", exc)
            return results

        for group in decide_exits(portfolio, self.config):
            reason = group.get("exit_reason", "")
            logger.info("Closing %s (%d legs): %s", group["label"], group["leg_count"], reason)

            # Close every leg of the spread. Closing only the profitable leg
            # would leave the other one stranded and the position no longer a
            # spread at all.
            leg_results = [
                close_position(self.broker, leg, dry_run=dry_run) for leg in group["legs"]
            ]
            result = {
                "success": all(r["success"] for r in leg_results),
                "dry_run": dry_run,
                "status": "closed" if all(r["success"] for r in leg_results) else "partial",
                "symbols": group["symbols"],
                "legs": leg_results,
                # P&L is the group's, not any single leg's.
                "realized_pnl": round(group.get("unrealized_pl") or 0.0, 2),
                "message": f"Closed {group['leg_count']} leg(s) of {group['label']}.",
            }
            self.journal.log_exit(run_id, group, reason, result)
            results.append({**result, "exit_reason": reason})

        return results
