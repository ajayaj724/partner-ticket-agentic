"""F3 Smart Routing agent — pick queue and assignee given context.

DESIGN.md §3 F3: skill match * workload * SLA pressure * partner tier.
The decision is procedural (a ranked match against the runbook → queue
mapping) so the mock LLM doesn't need a custom rule — F3 is a
deterministic dispatcher even in production for the standard queues.
The "review" queue is the fallback when triage confidence is low or
category is OTHER (DESIGN.md §3 F3 Autonomy).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.agents.triage import TicketCategory
from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.directory import (
    Assignee,
    QueueWorkload,
    SlaPolicy,
    directory_resolve_assignee,
)
from partner_ticket_agentic.tools.registry import ToolDispatcher, ToolError

_log = get_logger("agents.router")

ALLOW_LIST = ToolAllowList.of(
    "router",
    "directory_resolve_assignee",
    "queue_workload_snapshot",
    "sla_policy_for_partner",
)

# Map triage category -> default queue. Derived from the runbooks' owner_queue
# field in data/runbooks.json so the mapping stays auditable.
_CATEGORY_TO_QUEUE: dict[TicketCategory, str] = {
    TicketCategory.CIRCUIT_DOWN: "NOC-L2",
    TicketCategory.THROUGHPUT_DEGRADED: "NOC-L2",
    TicketCategory.APPOINTMENT_REQUEST: "DISPATCH",
    TicketCategory.BILLING: "FIN-OPS",
    TicketCategory.PROVISIONING: "PROVISIONING",
    TicketCategory.OTHER: "FRONT-OFFICE",
}

_URGENCY_TO_SLA_FIELD = {
    "critical": "critical_minutes",
    "high": "high_minutes",
    "medium": "medium_minutes",
    "low": "low_minutes",
}


class RoutingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: str
    assignee: Assignee
    sla_minutes: int = Field(ge=0)
    workload: QueueWorkload
    sla_policy: SlaPolicy
    rationale: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


def _pick_queue(category: str, triage_confidence: float) -> tuple[str, float]:
    """Return (queue, confidence). Routes to REVIEW on low triage confidence."""

    if triage_confidence < 0.7:
        return "REVIEW", round(0.6, 4)
    try:
        cat_enum = TicketCategory(category)
    except ValueError:
        return "REVIEW", 0.55
    queue = _CATEGORY_TO_QUEUE.get(cat_enum, "FRONT-OFFICE")
    return queue, 0.9


def run_router(state: TicketState) -> RoutingOutput:
    triage = state.triage or {}
    enrichment = state.enrichment or {}

    category = str(triage.get("category", "other"))
    urgency = str(triage.get("urgency", "low"))
    triage_conf = float(triage.get("confidence", 0.5))

    partner_profile = enrichment.get("partner_profile") or {}
    tier = str(partner_profile.get("tier") or "bronze")

    queue, confidence = _pick_queue(category, triage_conf)

    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    try:
        assignee = dispatcher.call("directory_resolve_assignee", queue=queue)
    except ToolError:
        assignee = directory_resolve_assignee(queue="FRONT-OFFICE")
        queue = "FRONT-OFFICE"

    workload = dispatcher.call("queue_workload_snapshot", queue=queue)
    sla_policy = dispatcher.call("sla_policy_for_partner", partner_id=state.partner_id, tier=tier)

    sla_field = _URGENCY_TO_SLA_FIELD.get(urgency, "low_minutes")
    sla_minutes = int(getattr(sla_policy, sla_field))

    rationale = (
        f"category={category} → queue {queue}; partner tier={tier} → "
        f"{urgency} SLA {sla_minutes}m; current load {workload.open_tickets} open / "
        f"capacity {workload.on_call_capacity}."
    )

    return RoutingOutput(
        queue=queue,
        assignee=assignee,
        sla_minutes=sla_minutes,
        workload=workload,
        sla_policy=sla_policy,
        rationale=rationale,
        confidence=confidence,
    )


def router_node(state: TicketState) -> dict[str, Any]:
    with bind_log_context(agent="router", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("router_start")
        output = run_router(state)
        _log.info(
            "router_done",
            extra={
                "queue": output.queue,
                "assignee": output.assignee.user_id,
                "sla_minutes": output.sla_minutes,
            },
        )
    return {"routing": output.model_dump()}
