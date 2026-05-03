"""F5 Drafted Partner Reply agent — HITL by default.

DESIGN.md §3 F5: draft the initial acknowledgement / status update /
resolution message. Engineer approves before send. Compliance filter is a
hard gate — if it trips, the draft is blocked and the engineer is
notified.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.registry import ToolDispatcher

_log = get_logger("agents.drafter")

ALLOW_LIST = ToolAllowList.of("drafter", "template_lookup", "compliance_filter")


class DrafterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str
    template_id: str
    requires_approval: bool = True
    compliance_flags: list[str] = Field(default_factory=list)
    blocked: bool = False
    rationale: str = Field(max_length=500)


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _format_template(text: str, ctx: dict[str, str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return ctx.get(key, "{" + key + "}")

    return _PLACEHOLDER_RE.sub(_sub, text)


def _build_context(state: TicketState) -> dict[str, str]:
    triage = state.triage or {}
    enrichment = state.enrichment or {}
    routing = state.routing or {}

    entities = triage.get("entities") or {}
    partner_profile = enrichment.get("partner_profile") or {}

    ctx: dict[str, str] = {
        "ticket_id": state.ticket_id,
        "partner_id": state.partner_id,
        "partner_name": str(partner_profile.get("name") or "partner"),
        "sla_minutes": str(routing.get("sla_minutes", 60)),
    }
    circuits = entities.get("circuits") or []
    if circuits:
        ctx["circuit_id"] = str(circuits[0])
    appointments = entities.get("appointments") or []
    if appointments:
        ctx["appointment_id"] = str(appointments[0])
    invoices = entities.get("invoices") or []
    if invoices:
        ctx["invoice_id"] = str(invoices[0])
    return ctx


def run_drafter(state: TicketState) -> DrafterOutput:
    triage = state.triage or {}
    category = str(triage.get("category", "other"))

    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    template = dispatcher.call("template_lookup", category=category)
    ctx = _build_context(state)
    subject = _format_template(template.subject, ctx)
    body = _format_template(template.body, ctx)

    compliance = dispatcher.call("compliance_filter", subject=subject, body=body)
    blocked = bool(compliance.blocked)
    flags = list(compliance.flags)

    rationale = f"template={template.template_id}; category={category}; compliance_flags={flags}"
    if blocked:
        rationale = "BLOCKED: " + rationale

    return DrafterOutput(
        subject=subject,
        body=body,
        template_id=template.template_id,
        requires_approval=True,
        compliance_flags=flags,
        blocked=blocked,
        rationale=rationale,
    )


def drafter_node(state: TicketState) -> dict[str, Any]:
    with bind_log_context(agent="drafter", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("drafter_start")
        output = run_drafter(state)
        _log.info(
            "drafter_done",
            extra={
                "template_id": output.template_id,
                "blocked": output.blocked,
                "compliance_flags": output.compliance_flags,
            },
        )
    return {"draft": output.model_dump()}
