"""F4 Knowledge-Grounded Suggestion agent.

DESIGN.md §3 F4: retrieve and present the relevant runbook + suggested
troubleshooting steps with citations. Suggestion-only — never auto-applies.
Falls back to "no high-confidence match" rather than guessing if nothing
beats the threshold (DESIGN.md F4 Failure mode).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.registry import ToolDispatcher
from partner_ticket_agentic.tools.runbook import RunbookHitModel

_log = get_logger("agents.knowledge")

ALLOW_LIST = ToolAllowList.of("knowledge", "runbook_search", "cross_encode_rerank")

CONFIDENCE_THRESHOLD = 0.20

# Suggested-steps templates per runbook category. Deterministic, mirrors the
# runbook summary in data/runbooks.json so a reviewer can predict output.
_STEPS_BY_CATEGORY: dict[str, list[str]] = {
    "circuit_down": [
        "Confirm circuit status in inventory (inventory_lookup_circuit).",
        "Run probe; if probe fails for >5 minutes, escalate to NOC-L2.",
        "Notify partner with status update once probe result is known.",
    ],
    "appointment_change": [
        "Verify partner identity from CRM record.",
        "Find an alternative slot in the dispatch system.",
        "Confirm new slot with partner via email; update appointment record.",
    ],
    "billing_discrepancy": [
        "Pull last 3 invoices for the partner and compare to contracted rate card.",
        "If delta > EUR 50, route to Finance Ops with the rate-card snapshot.",
        "Send acknowledgement to partner; promise resolution in 1 business day.",
    ],
    "provisioning_request": [
        "Validate site address against postal code coverage.",
        "Run capacity check on the requested bandwidth class.",
        "Schedule field engineer; confirm SLA window with partner.",
    ],
    "performance_degradation": [
        "Pull last 24h metrics for the affected circuit.",
        "Run latency probe; open incident if loss > 1% sustained.",
        "Notify partner once root cause is hypothesised.",
    ],
    "credentials": [
        "Verify partner identity via CRM.",
        "Trigger password reset via IDP — never include the secret in reply.",
        "Confirm reset email delivery.",
    ],
    "config_change": [
        "Validate change window with the partner.",
        "Raise CRQ in change management.",
        "Schedule with NetEng for off-peak execution.",
    ],
    "general_query": [
        "Answer from the knowledge base if applicable.",
        "Route to FrontOffice for human handling otherwise.",
    ],
}


class KnowledgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_runbook: RunbookHitModel | None = None
    suggested_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    citation: str | None = Field(default=None, description="`<doc_id>` or `<doc_id>#<section>`.")
    fallback_reason: str | None = None


def run_knowledge(state: TicketState) -> KnowledgeOutput:
    triage = state.triage or {}
    category = str(triage.get("category", "other"))
    query = f"{category} {state.subject} {state.description}".strip()

    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    result = dispatcher.call("runbook_search", query=query, k=4)
    if not result.hits:
        return KnowledgeOutput(
            top_runbook=None,
            suggested_steps=[],
            confidence=0.0,
            fallback_reason="no runbook hits returned",
        )
    reranked = dispatcher.call("cross_encode_rerank", query=query, hits=result.hits)
    top = reranked[0]
    if top.score < CONFIDENCE_THRESHOLD:
        return KnowledgeOutput(
            top_runbook=top,
            suggested_steps=[],
            confidence=round(max(0.0, top.score), 4),
            fallback_reason=(
                f"top runbook score {top.score:.3f} below threshold {CONFIDENCE_THRESHOLD}"
            ),
        )
    steps = _STEPS_BY_CATEGORY.get(top.category, [])
    return KnowledgeOutput(
        top_runbook=top,
        suggested_steps=list(steps),
        confidence=round(min(1.0, top.score + 0.05), 4),
        citation=f"{top.runbook_id}",
    )


def knowledge_node(state: TicketState) -> dict[str, Any]:
    with bind_log_context(agent="knowledge", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("knowledge_start")
        output = run_knowledge(state)
        _log.info(
            "knowledge_done",
            extra={
                "top_runbook": output.top_runbook.runbook_id if output.top_runbook else None,
                "confidence": output.confidence,
                "fallback": output.fallback_reason,
            },
        )
    return {"knowledge": output.model_dump()}
