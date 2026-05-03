"""Directory + queue-workload + SLA-policy tools used by F3 Smart Routing.

All three are read-only and seeded with explicit, deterministic data so the
demo run is reproducible. Production swaps these for the real directory
service, queue-workload metrics, and SLA contract source.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.registry import ToolError, register_tool


class Assignee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    queue: str
    skills: list[str] = Field(default_factory=list)


class QueueWorkload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: str
    open_tickets: int
    avg_handle_min: int
    on_call_capacity: int


class SlaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_id: str
    tier: str
    critical_minutes: int
    high_minutes: int
    medium_minutes: int
    low_minutes: int


# Closed seeds — readers can predict tool output.

_QUEUE_TO_OWNER: dict[str, Assignee] = {
    "NOC-L2": Assignee(
        user_id="u-noc-001",
        name="NOC L2 On-call",
        queue="NOC-L2",
        skills=["circuit_down", "performance_degradation"],
    ),
    "DISPATCH": Assignee(
        user_id="u-dis-014", name="Dispatch Lead", queue="DISPATCH", skills=["appointment_change"]
    ),
    "FIN-OPS": Assignee(
        user_id="u-fin-007", name="Finance Ops", queue="FIN-OPS", skills=["billing_discrepancy"]
    ),
    "PROVISIONING": Assignee(
        user_id="u-prv-022",
        name="Provisioning Lead",
        queue="PROVISIONING",
        skills=["provisioning_request"],
    ),
    "NETENG": Assignee(
        user_id="u-net-009", name="NetEng Lead", queue="NETENG", skills=["config_change"]
    ),
    "IDP-SUPPORT": Assignee(
        user_id="u-idp-003", name="IDP Support", queue="IDP-SUPPORT", skills=["credentials"]
    ),
    "FRONT-OFFICE": Assignee(
        user_id="u-fo-001", name="Front Office", queue="FRONT-OFFICE", skills=["general_query"]
    ),
    "REVIEW": Assignee(
        user_id="u-rev-001", name="Triage Review", queue="REVIEW", skills=["review"]
    ),
}

_QUEUE_WORKLOADS: dict[str, QueueWorkload] = {
    "NOC-L2": QueueWorkload(queue="NOC-L2", open_tickets=14, avg_handle_min=42, on_call_capacity=4),
    "DISPATCH": QueueWorkload(
        queue="DISPATCH", open_tickets=7, avg_handle_min=28, on_call_capacity=3
    ),
    "FIN-OPS": QueueWorkload(
        queue="FIN-OPS", open_tickets=22, avg_handle_min=180, on_call_capacity=2
    ),
    "PROVISIONING": QueueWorkload(
        queue="PROVISIONING", open_tickets=5, avg_handle_min=320, on_call_capacity=2
    ),
    "NETENG": QueueWorkload(queue="NETENG", open_tickets=9, avg_handle_min=95, on_call_capacity=3),
    "IDP-SUPPORT": QueueWorkload(
        queue="IDP-SUPPORT", open_tickets=3, avg_handle_min=18, on_call_capacity=2
    ),
    "FRONT-OFFICE": QueueWorkload(
        queue="FRONT-OFFICE", open_tickets=11, avg_handle_min=20, on_call_capacity=4
    ),
    "REVIEW": QueueWorkload(queue="REVIEW", open_tickets=2, avg_handle_min=15, on_call_capacity=1),
}

_SLA_BY_TIER: dict[str, dict[str, int]] = {
    "gold": {"critical": 30, "high": 60, "medium": 240, "low": 480},
    "silver": {"critical": 60, "high": 120, "medium": 480, "low": 960},
    "bronze": {"critical": 120, "high": 240, "medium": 960, "low": 1440},
}


@register_tool("directory_resolve_assignee", description="Owner of a named queue.")
def directory_resolve_assignee(*, queue: str) -> Assignee:
    if queue not in _QUEUE_TO_OWNER:
        raise ToolError(f"queue {queue!r} not in directory")
    return _QUEUE_TO_OWNER[queue]


@register_tool("queue_workload_snapshot", description="Current workload for a queue.")
def queue_workload_snapshot(*, queue: str) -> QueueWorkload:
    if queue not in _QUEUE_WORKLOADS:
        raise ToolError(f"queue {queue!r} not in workload snapshot")
    return _QUEUE_WORKLOADS[queue]


@register_tool("sla_policy_for_partner", description="SLA policy for a partner by tier.")
def sla_policy_for_partner(*, partner_id: str, tier: str) -> SlaPolicy:
    if tier not in _SLA_BY_TIER:
        raise ToolError(f"unknown tier {tier!r}")
    minutes = _SLA_BY_TIER[tier]
    return SlaPolicy(
        partner_id=partner_id,
        tier=tier,
        critical_minutes=minutes["critical"],
        high_minutes=minutes["high"],
        medium_minutes=minutes["medium"],
        low_minutes=minutes["low"],
    )
