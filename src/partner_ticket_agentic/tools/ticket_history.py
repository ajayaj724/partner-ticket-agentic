"""Ticket-history tool — pulls recent tickets for a partner.

Backed by the episodic-memory store in production. For the demo we use a
deterministic pre-seeded history per partner so that the panel run prints
something reproducible without first having to run the pipeline against
many tickets to build up history.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.registry import register_tool


class TicketSummary(BaseModel):
    """Compact ticket summary for the F2 Enricher's context block."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    category: str
    urgency: str | None
    summary: str
    resolved: bool = Field(description="True if the ticket reached a terminal state.")


# Hand-written seed history. Deterministic, partner-keyed; mirrors the
# kind of data the episodic store would accumulate over time.
_SEED_HISTORY: dict[str, list[TicketSummary]] = {
    "P-1001": [
        TicketSummary(
            ticket_id="T-9001",
            category="circuit_down",
            urgency="critical",
            summary="CIRC-44781 outage Q1 2026 — restored after carrier reroute (47m).",
            resolved=True,
        ),
        TicketSummary(
            ticket_id="T-9014",
            category="performance_degradation",
            urgency="high",
            summary="CIRC-44782 packet loss 1.5% over 2h — cleared after BNG reload.",
            resolved=True,
        ),
    ],
    "P-1002": [
        TicketSummary(
            ticket_id="T-9032",
            category="appointment_request",
            urgency="medium",
            summary="APT-20210 rescheduled twice in March; partner notified Tuesdays unavailable.",
            resolved=True,
        ),
    ],
    "P-1003": [
        TicketSummary(
            ticket_id="T-9077",
            category="billing_discrepancy",
            urgency="medium",
            summary="INV-2026-0312 rate-card mismatch resolved by Finance Ops in March.",
            resolved=True,
        ),
        TicketSummary(
            ticket_id="T-9081",
            category="general_query",
            urgency="low",
            summary="Partner asked for portal credentials; password reset issued.",
            resolved=True,
        ),
    ],
    "P-1004": [
        TicketSummary(
            ticket_id="T-9099",
            category="provisioning_request",
            urgency="medium",
            summary="CIRC-77410 commissioned in Ghent; SLA window confirmed.",
            resolved=True,
        ),
    ],
}


@register_tool(
    "ticket_history_recent",
    description="Recent tickets for a partner — drives the Enricher context block.",
)
def ticket_history_recent(*, partner_id: str, limit: int = 5) -> list[TicketSummary]:
    """Return the most recent ticket summaries for ``partner_id``."""

    rows = _SEED_HISTORY.get(partner_id, [])
    return list(rows[:limit])
