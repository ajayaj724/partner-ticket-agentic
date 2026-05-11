"""LangGraph wiring for the request/response pipeline.

Builds a real :class:`langgraph.graph.StateGraph` with :class:`TicketState`
as the state object. The graph encodes the design-doc topology:

* F1 Triage and F7 Linker fan out from START in parallel; both join at F2
  Enricher (which waits for both upstream nodes).
* F2 Enricher fans out to F3 Router and F4 Knowledge in parallel; both
  join at a passthrough decision node.
* The decision node conditionally routes to F6 Scheduler (when triage
  category is on-site-eligible) or directly to F5 Drafter.
* F5 Drafter is the terminal node — its output requires_approval=True
  always, per DESIGN.md §3 F5.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from partner_ticket_agentic.agents.drafter import drafter_node
from partner_ticket_agentic.agents.enricher import enricher_node
from partner_ticket_agentic.agents.knowledge import knowledge_node
from partner_ticket_agentic.agents.linker import linker_node
from partner_ticket_agentic.agents.router import router_node
from partner_ticket_agentic.agents.scheduler import scheduler_node, should_run
from partner_ticket_agentic.agents.triage import triage_node
from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger, new_trace_id, span
from partner_ticket_agentic.providers import LLMProvider, MockProvider

_log = get_logger("graph")


def _route_decision(state: TicketState) -> str:
    """Conditional after router+knowledge join: scheduler or drafter."""

    return "scheduler" if should_run(state) else "drafter"


def _passthrough(_state: TicketState) -> dict[str, Any]:
    """No-op join node — exists so two parallel branches can synchronise."""

    return {}


def build_graph(provider: LLMProvider | None = None) -> Any:
    """Compile the StateGraph and return a runnable.

    The provider is captured by closure so node functions get the same
    provider instance for the run. Default is the mock provider — that's
    the panel-demo path (offline, deterministic).
    """

    if provider is None:
        provider = MockProvider()

    def _triage(state: TicketState) -> dict[str, Any]:
        return triage_node(state, provider)

    graph: StateGraph = StateGraph(TicketState)
    graph.add_node("triage", _triage)
    graph.add_node("linker", linker_node)
    graph.add_node("enricher", enricher_node)
    graph.add_node("router", router_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("route_decision", _passthrough)
    graph.add_node("scheduler", scheduler_node)
    graph.add_node("drafter", drafter_node)

    # Parallel fan-out from START — F1 and F7 run concurrently.
    graph.add_edge(START, "triage")
    graph.add_edge(START, "linker")

    # Both upstreams join at the Enricher.
    graph.add_edge("triage", "enricher")
    graph.add_edge("linker", "enricher")

    # Enricher fans out to F3 Router and F4 Knowledge in parallel.
    graph.add_edge("enricher", "router")
    graph.add_edge("enricher", "knowledge")

    # Both join at the route_decision passthrough.
    graph.add_edge("router", "route_decision")
    graph.add_edge("knowledge", "route_decision")

    # Conditional: F6 Scheduler runs only for on-site-eligible categories.
    graph.add_conditional_edges(
        "route_decision",
        _route_decision,
        {"scheduler": "scheduler", "drafter": "drafter"},
    )
    graph.add_edge("scheduler", "drafter")
    graph.add_edge("drafter", END)

    return graph.compile()


def run_pipeline(
    ticket: dict[str, Any],
    *,
    provider: LLMProvider | None = None,
    trace_id: str | None = None,
) -> TicketState:
    """Run a single ticket through the full pipeline and return the final state."""

    import json
    from pathlib import Path

    from partner_ticket_agentic.cost import (
        BudgetState,
        CostLedger,
        bind_budget,
        bind_ledger,
        load_budgets,
    )
    from partner_ticket_agentic.safety import detect_pii

    trace_id = trace_id or new_trace_id()

    # Resolve the partner's tier (for tier-based budget defaults) by
    # reading the seed file. Kept simple because the demo's persistence
    # layer is JSON; production would hit the partner-CRM through a
    # tool. Fail-soft: missing tier yields "unlimited" cap.
    partner_tier: str | None = None
    try:
        partners_path = Path(__file__).resolve().parents[2] / "data" / "partners.json"
        if partners_path.exists():
            partners = json.loads(partners_path.read_text())
            for p in partners:
                if p.get("partner_id") == ticket["partner_id"]:
                    partner_tier = p.get("tier")
                    break
    except Exception:
        partner_tier = None

    budgets = load_budgets()
    cap = budgets.cap_for(ticket["partner_id"], partner_tier)
    budget_state = BudgetState(
        partner_id=ticket["partner_id"],
        cap=cap,
        alert_thresholds=budgets.alert_thresholds,
    )
    # Detect PII in the partner-supplied description at the ingest
    # boundary (deck slide 18). Findings are attached to the state so
    # the trace and web UI can surface them; the description itself is
    # left untouched because the downstream agents need the real text
    # to operate (entities, runbook matching, etc.).
    pii = detect_pii(ticket.get("description", ""))
    initial = TicketState.from_ticket(ticket).model_copy(
        update={
            "trace_id": trace_id,
            "provider": (provider.name if provider else "mock"),
            "pii_findings": [{"kind": p.kind, "match": p.match} for p in pii],
        }
    )
    runnable = build_graph(provider=provider)
    ledger = CostLedger()
    with (
        bind_log_context(trace_id=trace_id, ticket_id=ticket["ticket_id"]),
        bind_ledger(ledger),
        bind_budget(budget_state),
        span(
            "pipeline",
            **{
                "trace_id": trace_id,
                "ticket_id": ticket["ticket_id"],
                "partner_id": ticket.get("partner_id", ""),
            },
        ),
    ):
        _log.info(
            "pipeline_start",
            extra={
                "pii_findings_count": len(pii),
                "pii_kinds": sorted({p.kind for p in pii}),
                "partner_tier": partner_tier,
                "budget_max_tokens": cap.max_tokens,
                "budget_max_usd": cap.max_usd,
            },
        )
        result = runnable.invoke(initial)
        _log.info(
            "pipeline_done",
            extra={
                "cost_usd": round(ledger.summary()["cost_usd"], 6),
                "tokens_in": ledger.summary()["tokens_in"],
                "tokens_out": ledger.summary()["tokens_out"],
                "cache_hit_rate": ledger.summary()["cache_hit_rate"],
            },
        )

    if isinstance(result, TicketState):
        state = result
    elif isinstance(result, dict):
        state = TicketState.model_validate(result)
    else:
        raise TypeError(f"unexpected pipeline result type: {type(result).__name__}")

    # Attach the per-ticket cost roll-up so the trace, CLI, and web UI can
    # surface it without recomputing. Budget summary too, so the UI can
    # show a "X% of token cap used" indicator.
    cost_summary = ledger.summary()
    if cap.max_tokens > 0 or cap.max_usd > 0.0:
        cost_summary["budget"] = {
            "partner_tier": partner_tier,
            "max_tokens": cap.max_tokens,
            "max_usd": cap.max_usd,
            "used_tokens": budget_state.used_tokens,
            "used_usd": round(budget_state.used_usd, 6),
            "tokens_fraction": (
                budget_state.used_tokens / cap.max_tokens if cap.max_tokens > 0 else 0.0
            ),
            "usd_fraction": (budget_state.used_usd / cap.max_usd if cap.max_usd > 0 else 0.0),
        }
    return state.model_copy(update={"cost": cost_summary})
