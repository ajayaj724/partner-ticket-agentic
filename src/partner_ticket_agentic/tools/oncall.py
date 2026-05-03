"""On-call notification + manager-escalation tools used by F8 Watchdog.

Side-effecting tools — DESIGN.md §2 commits to idempotency keys on every
side-effecting tool call. Both notification tools take an explicit
``idempotency_key`` and a process-local registry deduplicates inside one
watchdog scan. Production wires these to PagerDuty / Slack / etc., with
the registry backed by Redis or a DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from partner_ticket_agentic.obs import get_logger
from partner_ticket_agentic.tools.registry import register_tool

_log = get_logger("tools.oncall")


class NotificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    channel: str
    delivered: bool
    deduplicated: bool
    sent_at: datetime
    idempotency_key: str


class _Idempotency:
    seen: ClassVar[set[str]] = set()

    @classmethod
    def reset(cls) -> None:
        cls.seen.clear()


def reset_oncall_idempotency() -> None:
    """Clear the dedup registry — called per watchdog scan."""

    _Idempotency.reset()


@register_tool(
    "notify_oncall",
    description="Notify on-call about a ticket at risk of breach. Deduplicates by idempotency_key.",
)
def notify_oncall(
    *, ticket_id: str, queue: str, idempotency_key: str, message: str
) -> NotificationResult:
    if idempotency_key in _Idempotency.seen:
        _log.info(
            "notify_oncall_deduplicated",
            extra={"ticket_id": ticket_id, "queue": queue, "key": idempotency_key},
        )
        return NotificationResult(
            ticket_id=ticket_id,
            channel=f"oncall:{queue}",
            delivered=False,
            deduplicated=True,
            sent_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
    _Idempotency.seen.add(idempotency_key)
    _log.info(
        "notify_oncall_delivered",
        extra={
            "ticket_id": ticket_id,
            "queue": queue,
            "key": idempotency_key,
            "oncall_message": message,
        },
    )
    return NotificationResult(
        ticket_id=ticket_id,
        channel=f"oncall:{queue}",
        delivered=True,
        deduplicated=False,
        sent_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
    )


@register_tool(
    "escalate_to_manager",
    description="Escalate a ticket to a manager — idempotent, demo-only side effect.",
)
def escalate_to_manager(
    *, ticket_id: str, queue: str, idempotency_key: str, reason: str
) -> NotificationResult:
    if idempotency_key in _Idempotency.seen:
        return NotificationResult(
            ticket_id=ticket_id,
            channel=f"manager:{queue}",
            delivered=False,
            deduplicated=True,
            sent_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
    _Idempotency.seen.add(idempotency_key)
    _log.warning(
        "escalate_to_manager",
        extra={
            "ticket_id": ticket_id,
            "queue": queue,
            "key": idempotency_key,
            "reason": reason,
        },
    )
    return NotificationResult(
        ticket_id=ticket_id,
        channel=f"manager:{queue}",
        delivered=True,
        deduplicated=False,
        sent_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
    )
