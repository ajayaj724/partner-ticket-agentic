"""F2 Auto-Enrichment agent.

DESIGN.md §3 F2: save the engineer the dig. Calls four read-only tools in
parallel — partner profile, circuit inventory, recent tickets, runbook
search — and packages the results so the human reviewer sees full context
before the ticket lands in their queue.

Per-tool failure mode: omit that section and continue (DESIGN.md "if any
tool fails after retries, omit that section but continue. Engineer sees
what was unavailable"). The :class:`EnrichmentOutput` carries an explicit
``unavailable`` list so the trace and the UI can surface what we couldn't
fetch rather than presenting a partial picture as complete.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.crm import Partner
from partner_ticket_agentic.tools.inventory import CircuitInfo
from partner_ticket_agentic.tools.registry import ToolDispatcher, ToolError
from partner_ticket_agentic.tools.runbook import RunbookHitModel
from partner_ticket_agentic.tools.ticket_history import TicketSummary

_log = get_logger("agents.enricher")

ALLOW_LIST: ToolAllowList = ToolAllowList.of(
    "enricher",
    "crm_lookup_partner",
    "inventory_lookup_circuit",
    "ticket_history_recent",
    "runbook_search",
)
"""Tool allow-list pinned per DESIGN.md §3 F2 Tools."""


class EnrichmentOutput(BaseModel):
    """Validated output of the F2 Enricher agent."""

    model_config = ConfigDict(extra="forbid")

    partner_profile: Partner | None = None
    asset_state: list[CircuitInfo] = Field(default_factory=list)
    recent_tickets: list[TicketSummary] = Field(default_factory=list)
    relevant_runbooks: list[RunbookHitModel] = Field(default_factory=list)
    unavailable: list[str] = Field(
        default_factory=list,
        description="Tool calls that failed after retries — surfaced for the engineer.",
    )


def _safe_call(label: str, dispatcher: ToolDispatcher, tool: str, /, **kwargs: Any) -> Any:
    """Run one tool, returning ``None`` on failure and recording it in ``unavailable``.

    Caller is responsible for appending ``label`` to ``unavailable`` if the
    return is None — the helper only narrows the failure surface to a
    typed-None contract so the caller's code stays linear.
    """

    try:
        return dispatcher.call(tool, **kwargs)
    except ToolError as exc:
        _log.warning(
            "enricher_tool_omitted",
            extra={"tool": tool, "label": label, "error": str(exc)},
        )
        return None


def run_enricher(state: TicketState) -> EnrichmentOutput:
    """Run the F2 Enricher against ``state``.

    Tools are dispatched in parallel via a small ThreadPoolExecutor. Each
    tool is read-only and idempotent, so concurrent dispatch is safe and
    the wall-clock latency is the slowest tool, not the sum.
    """

    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    triage = state.triage or {}
    entities = triage.get("entities") or {}
    circuits: list[str] = list(entities.get("circuits") or [])

    query = f"{state.subject} {state.description}".strip()

    output = EnrichmentOutput()

    # Spawn all four tool families in parallel; collect results in deterministic order.
    with ThreadPoolExecutor(max_workers=4) as pool:
        partner_fut = pool.submit(
            _safe_call,
            "partner_profile",
            dispatcher,
            "crm_lookup_partner",
            partner_id=state.partner_id,
        )
        history_fut = pool.submit(
            _safe_call,
            "recent_tickets",
            dispatcher,
            "ticket_history_recent",
            partner_id=state.partner_id,
            limit=5,
        )
        runbook_fut = pool.submit(
            _safe_call, "relevant_runbooks", dispatcher, "runbook_search", query=query, k=3
        )
        circuit_futs = [
            (
                cid,
                pool.submit(
                    _safe_call,
                    f"asset_state[{cid}]",
                    dispatcher,
                    "inventory_lookup_circuit",
                    circuit_id=cid,
                ),
            )
            for cid in circuits
        ]

        partner = partner_fut.result()
        if partner is None:
            output.unavailable.append("partner_profile")
        else:
            output.partner_profile = partner

        history = history_fut.result()
        if history is None:
            output.unavailable.append("recent_tickets")
        else:
            output.recent_tickets = list(history)

        runbook_result = runbook_fut.result()
        if runbook_result is None:
            output.unavailable.append("relevant_runbooks")
        else:
            output.relevant_runbooks = list(runbook_result.hits)

        for cid, fut in circuit_futs:
            info = fut.result()
            if info is None:
                output.unavailable.append(f"asset_state[{cid}]")
            else:
                output.asset_state.append(info)

    return output


def enricher_node(state: TicketState) -> dict[str, Any]:
    """LangGraph node wrapper for :func:`run_enricher`."""

    with bind_log_context(agent="enricher", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("enricher_start")
        output = run_enricher(state)
        _log.info(
            "enricher_done",
            extra={
                "tools_succeeded": 4 - len(output.unavailable),
                "unavailable": output.unavailable,
                "asset_state_count": len(output.asset_state),
                "runbook_hits": len(output.relevant_runbooks),
            },
        )
    return {"enrichment": output.model_dump()}
