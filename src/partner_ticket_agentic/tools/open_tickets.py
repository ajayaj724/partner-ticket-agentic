"""Open-ticket inventory used by F8 Watchdog.

Returns a deterministic snapshot of currently-open tickets with elapsed
time and the SLA that applies to them. Production wires this to the live
ticket store; the demo seeds a fixed snapshot anchored to
:data:`AS_OF_REFERENCE` so the panel sees the same at-risk set on every
run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.registry import register_tool

AS_OF_REFERENCE = datetime(2026, 5, 4, 11, 0, tzinfo=UTC)


class OpenTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    partner_id: str
    queue: str
    category: str
    urgency: str
    sla_minutes: int = Field(ge=0)
    opened_at: datetime
    last_activity_at: datetime

    @property
    def elapsed_minutes(self) -> int:
        return int((AS_OF_REFERENCE - self.opened_at).total_seconds() // 60)


# Seeded snapshot — varied so the watchdog finds a mix of at-risk and not.
_SNAPSHOT: list[OpenTicket] = [
    OpenTicket(
        ticket_id="T-OPEN-001",
        partner_id="P-1001",
        queue="NOC-L2",
        category="circuit_down",
        urgency="critical",
        sla_minutes=30,
        opened_at=AS_OF_REFERENCE - timedelta(minutes=29),
        last_activity_at=AS_OF_REFERENCE - timedelta(minutes=10),
    ),
    OpenTicket(
        ticket_id="T-OPEN-002",
        partner_id="P-1002",
        queue="DISPATCH",
        category="appointment_request",
        urgency="medium",
        sla_minutes=480,
        opened_at=AS_OF_REFERENCE - timedelta(minutes=120),
        last_activity_at=AS_OF_REFERENCE - timedelta(minutes=20),
    ),
    OpenTicket(
        ticket_id="T-OPEN-003",
        partner_id="P-1003",
        queue="FIN-OPS",
        category="billing",
        urgency="medium",
        sla_minutes=960,
        opened_at=AS_OF_REFERENCE - timedelta(minutes=900),
        last_activity_at=AS_OF_REFERENCE - timedelta(minutes=300),
    ),
    OpenTicket(
        ticket_id="T-OPEN-004",
        partner_id="P-1004",
        queue="PROVISIONING",
        category="provisioning",
        urgency="medium",
        sla_minutes=480,
        opened_at=AS_OF_REFERENCE - timedelta(minutes=410),
        last_activity_at=AS_OF_REFERENCE - timedelta(minutes=200),
    ),
    OpenTicket(
        ticket_id="T-OPEN-005",
        partner_id="P-1001",
        queue="NOC-L2",
        category="throughput_degraded",
        urgency="high",
        sla_minutes=60,
        opened_at=AS_OF_REFERENCE - timedelta(minutes=15),
        last_activity_at=AS_OF_REFERENCE - timedelta(minutes=5),
    ),
]


@register_tool(
    "tickets_open_with_state",
    description="Return all currently-open tickets with elapsed time and SLA.",
)
def tickets_open_with_state() -> list[OpenTicket]:
    return list(_SNAPSHOT)
