"""F6 Appointment Slot Suggestion agent.

DESIGN.md §3 F6. Triggered when triage.category ∈ {appointment_request,
provisioning, on-site circuit_down}. Proposes top 3 ranked slots
balancing engineer availability, travel time, and urgency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.calendar import SlotScore
from partner_ticket_agentic.tools.registry import ToolDispatcher, ToolError

_log = get_logger("agents.scheduler")

ALLOW_LIST = ToolAllowList.of(
    "scheduler",
    "engineer_calendar_available_slots",
    "partner_address_lookup",
    "travel_time_estimate",
    "slot_score",
)

ON_SITE_CATEGORIES: frozenset[str] = frozenset(
    {"appointment_request", "provisioning", "circuit_down"}
)


class ProposedSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engineer_id: str
    starts_at: datetime
    ends_at: datetime
    score: float
    rationale: str


class SchedulerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_slots: list[ProposedSlot] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    fallback_reason: str | None = None


def should_run(state: TicketState) -> bool:
    """Conditional gate: run F6 only for on-site-eligible categories."""

    triage = state.triage or {}
    category = str(triage.get("category", ""))
    return category in ON_SITE_CATEGORIES


def run_scheduler(state: TicketState) -> SchedulerOutput:
    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    triage = state.triage or {}
    urgency = str(triage.get("urgency", "low"))

    try:
        address = dispatcher.call("partner_address_lookup", partner_id=state.partner_id)
    except ToolError as exc:
        return SchedulerOutput(
            proposed_slots=[],
            confidence=0.0,
            fallback_reason=f"partner address unavailable: {exc}",
        )
    slots = dispatcher.call("engineer_calendar_available_slots", region=address.region, limit=12)
    if not slots:
        return SchedulerOutput(
            proposed_slots=[],
            confidence=0.0,
            fallback_reason=f"no engineers available in region {address.region!r}",
        )
    scored: list[SlotScore] = []
    for slot in slots:
        score = dispatcher.call("slot_score", slot=slot, partner_address=address, urgency=urgency)
        scored.append(score)
    scored.sort(key=lambda s: s.score, reverse=True)
    top3 = scored[:3]
    proposed = [
        ProposedSlot(
            engineer_id=s.engineer_id,
            starts_at=s.starts_at,
            ends_at=s.ends_at,
            score=s.score,
            rationale=s.rationale,
        )
        for s in top3
    ]
    confidence = round(top3[0].score if top3 else 0.0, 4)
    return SchedulerOutput(proposed_slots=proposed, confidence=confidence)


def scheduler_node(state: TicketState) -> dict[str, Any]:
    with bind_log_context(agent="scheduler", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("scheduler_start")
        output = run_scheduler(state)
        _log.info(
            "scheduler_done",
            extra={
                "slot_count": len(output.proposed_slots),
                "confidence": output.confidence,
                "fallback": output.fallback_reason,
            },
        )
    return {"schedule": output.model_dump()}
