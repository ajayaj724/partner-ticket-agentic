"""F7 Duplicate / Related-Ticket Detection agent.

DESIGN.md §3 F7. Runs in parallel with F1 Triage (no triage dependency).
Returns related tickets ranked by vector similarity over the partner's
own recent ticket history; tenant-scoped — never returns tickets from
other partners. Suggestion-only — never auto-merges.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.registry import ToolDispatcher
from partner_ticket_agentic.tools.ticket_search import TicketHit

_log = get_logger("agents.linker")

ALLOW_LIST = ToolAllowList.of("linker", "ticket_search_recent", "ticket_status_lookup")

DUPLICATE_THRESHOLD = 0.65
"""Cosine-similarity threshold for the "likely duplicate" verdict."""


class LinkerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    related: list[TicketHit] = Field(default_factory=list)
    is_likely_duplicate: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=500)


def run_linker(state: TicketState) -> LinkerOutput:
    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)
    query = f"{state.subject} {state.description}".strip()
    result = dispatcher.call(
        "ticket_search_recent",
        partner_id=state.partner_id,
        query=query,
        k=3,
    )
    hits = list(result.hits)
    if not hits:
        return LinkerOutput(
            related=[],
            is_likely_duplicate=False,
            confidence=0.0,
            rationale="no recent tickets indexed for this partner",
        )
    top = hits[0]
    is_duplicate = top.similarity >= DUPLICATE_THRESHOLD
    rationale = (
        f"top match {top.ticket_id} (sim={top.similarity:.3f}, status={top.status}); "
        f"threshold={DUPLICATE_THRESHOLD}; "
        f"verdict={'likely_duplicate' if is_duplicate else 'distinct'}"
    )
    confidence = round(min(1.0, max(0.0, abs(top.similarity))), 4)
    return LinkerOutput(
        related=hits,
        is_likely_duplicate=is_duplicate,
        confidence=confidence,
        rationale=rationale,
    )


def linker_node(state: TicketState) -> dict[str, Any]:
    with bind_log_context(agent="linker", ticket_id=state.ticket_id, trace_id=state.trace_id):
        _log.info("linker_start")
        output = run_linker(state)
        _log.info(
            "linker_done",
            extra={
                "is_likely_duplicate": output.is_likely_duplicate,
                "top": output.related[0].ticket_id if output.related else None,
                "confidence": output.confidence,
            },
        )
    return {"related": output.model_dump()}
