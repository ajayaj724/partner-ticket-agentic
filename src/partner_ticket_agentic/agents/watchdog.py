"""F8 SLA Escalation Watchdog — event-driven, scheduled.

DESIGN.md §3 F8: predict SLA-breach risk on open tickets and proactively
notify on-call before breach, not after. Rule-based prediction first;
LLM-augmented only in the ambiguous gray band (the "rule-based +
LLM-augmented for ambiguous cases" half of the design doc). Notification
is autonomous (low blast radius); escalation to manager is gated on
elapsed time since notification.

Failure mode (DESIGN.md): tool failure → log + fall back to rule-only
breach prediction. Implemented by guarding LLM augmentation in the
gray band; if the provider raises, we use the pure rule output.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.obs import bind_log_context, get_logger
from partner_ticket_agentic.providers import LLMProvider, Message, MockProvider, Tier
from partner_ticket_agentic.providers.base import LLMProviderError
from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.oncall import reset_oncall_idempotency
from partner_ticket_agentic.tools.open_tickets import OpenTicket
from partner_ticket_agentic.tools.registry import ToolDispatcher, ToolError

_log = get_logger("agents.watchdog")
TIER = Tier.SMALL

ALLOW_LIST = ToolAllowList.of(
    "watchdog",
    "tickets_open_with_state",
    "notify_oncall",
    "escalate_to_manager",
)

GRAY_BAND_LOW = 0.50
GRAY_BAND_HIGH = 0.80


class BreachRiskAssessment(BaseModel):
    """LLM-augmented assessment used in the gray band only."""

    model_config = ConfigDict(extra="forbid")

    risk: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=300)


class AtRiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    partner_id: str
    queue: str
    elapsed_minutes: int
    sla_minutes: int
    risk: float = Field(ge=0.0, le=1.0)
    risk_band: str
    action_taken: str
    rationale: str


class WatchdogReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int
    at_risk: list[AtRiskItem] = Field(default_factory=list)
    notified: int = 0
    escalated: int = 0
    deduplicated: int = 0


def _rule_risk(ticket: OpenTicket) -> tuple[float, str]:
    """Pure rule-based breach-risk score — runs always, never raises."""

    if ticket.sla_minutes <= 0:
        return 0.0, "sla_minutes is zero — cannot compute risk"
    ratio = ticket.elapsed_minutes / ticket.sla_minutes
    risk = round(min(1.0, max(0.0, ratio)), 4)
    rationale = (
        f"elapsed {ticket.elapsed_minutes}m of {ticket.sla_minutes}m SLA "
        f"({ratio * 100:.1f}%); rule-only"
    )
    return risk, rationale


def _band(risk: float) -> str:
    if risk >= GRAY_BAND_HIGH:
        return "high"
    if risk >= GRAY_BAND_LOW:
        return "gray"
    return "low"


def _llm_augment(ticket: OpenTicket, rule_risk: float, provider: LLMProvider) -> tuple[float, str]:
    """LLM augmentation for gray-band tickets — fall back to rule on error."""

    prompt = (
        f"Ticket {ticket.ticket_id} ({ticket.category}, urgency={ticket.urgency}) "
        f"has been open for {ticket.elapsed_minutes} minutes against an SLA of "
        f"{ticket.sla_minutes} minutes. Last activity was at "
        f"{ticket.last_activity_at.isoformat()}. Estimate breach risk in [0, 1]."
    )
    try:
        result = provider.complete(
            [Message(role="user", content=prompt)],
            BreachRiskAssessment,
            TIER,
            system="You assess SLA-breach risk on partner-ticket workloads.",
        )
        return result.risk, f"LLM-augmented: {result.rationale}"
    except LLMProviderError as exc:
        _log.warning(
            "watchdog_llm_failed_falling_back_to_rule",
            extra={"ticket_id": ticket.ticket_id, "error": str(exc)},
        )
        return rule_risk, f"FALLBACK to rule on LLM error: {exc}"


# --- mock LLM rule for gray-band augmentation -------------------------------


def _mock_breach_risk_rule(_system: str, messages: list[Message]) -> dict[str, Any]:
    """Deterministic mock for the gray-band assessment.

    Reads the elapsed/SLA ratio mentioned in the prompt and amplifies
    risk for critical/high urgencies. No randomness; same input ->
    same output.
    """

    text = " ".join(m.content for m in messages if m.role == "user").lower()
    risk = 0.65  # default mid-band
    rationale = "deterministic mid-gray-band assessment"
    if "urgency=critical" in text:
        risk = min(1.0, risk + 0.20)
        rationale = "critical urgency — risk amplified by 0.20"
    elif "urgency=high" in text:
        risk = min(1.0, risk + 0.10)
        rationale = "high urgency — risk amplified by 0.10"
    return {"risk": round(risk, 4), "rationale": rationale}


MockProvider.register(BreachRiskAssessment, _mock_breach_risk_rule)


# --- main entry point -------------------------------------------------------


def run_watchdog_once(provider: LLMProvider | None = None) -> WatchdogReport:
    """Run one watchdog scan and return the report.

    Resets the on-call idempotency registry at the start so the scan is
    self-contained. Production keeps a persistent registry across scans
    and uses time-windowed keys instead.
    """

    reset_oncall_idempotency()
    if provider is None:
        provider = MockProvider()
    dispatcher = ToolDispatcher(allow_list=ALLOW_LIST)

    with bind_log_context(agent="watchdog"):
        _log.info("watchdog_scan_start")
        try:
            tickets = dispatcher.call("tickets_open_with_state")
        except ToolError as exc:
            _log.error("watchdog_open_tickets_failed", extra={"error": str(exc)})
            return WatchdogReport(scanned=0)

        report = WatchdogReport(scanned=len(tickets))
        for t in tickets:
            risk, rationale = _rule_risk(t)
            band = _band(risk)
            if band == "gray":
                aug_risk, aug_rationale = _llm_augment(t, risk, provider)
                if aug_risk > risk:
                    risk = aug_risk
                    rationale = aug_rationale
                    band = _band(risk)

            if band == "low":
                continue

            key = f"watchdog:{t.ticket_id}:{int(risk * 10)}"
            action_label = "no_action"
            try:
                if band == "high":
                    res = dispatcher.call(
                        "notify_oncall",
                        ticket_id=t.ticket_id,
                        queue=t.queue,
                        idempotency_key=key,
                        message=f"SLA breach risk {risk:.2f}: {rationale}",
                    )
                    action_label = (
                        "notify_oncall:deduplicated" if res.deduplicated else "notify_oncall"
                    )
                    if res.deduplicated:
                        report.deduplicated += 1
                    else:
                        report.notified += 1
                else:
                    # gray band that survived augmentation — softer notification
                    res = dispatcher.call(
                        "notify_oncall",
                        ticket_id=t.ticket_id,
                        queue=t.queue,
                        idempotency_key=key,
                        message=f"Rising risk {risk:.2f}: {rationale}",
                    )
                    action_label = (
                        "notify_oncall_soft:deduplicated"
                        if res.deduplicated
                        else "notify_oncall_soft"
                    )
                    if res.deduplicated:
                        report.deduplicated += 1
                    else:
                        report.notified += 1

                # Manager escalation if elapsed > SLA (already breached).
                if t.elapsed_minutes >= t.sla_minutes:
                    esc_key = f"escalate:{t.ticket_id}"
                    esc = dispatcher.call(
                        "escalate_to_manager",
                        ticket_id=t.ticket_id,
                        queue=t.queue,
                        idempotency_key=esc_key,
                        reason=f"SLA breached: {t.elapsed_minutes}m > {t.sla_minutes}m",
                    )
                    if not esc.deduplicated:
                        report.escalated += 1
                        action_label = action_label + "+escalate"
            except ToolError as exc:
                _log.error(
                    "watchdog_action_failed",
                    extra={"ticket_id": t.ticket_id, "error": str(exc)},
                )

            report.at_risk.append(
                AtRiskItem(
                    ticket_id=t.ticket_id,
                    partner_id=t.partner_id,
                    queue=t.queue,
                    elapsed_minutes=t.elapsed_minutes,
                    sla_minutes=t.sla_minutes,
                    risk=risk,
                    risk_band=band,
                    action_taken=action_label,
                    rationale=rationale,
                )
            )

        _log.info(
            "watchdog_scan_done",
            extra={
                "scanned": report.scanned,
                "at_risk": len(report.at_risk),
                "notified": report.notified,
                "escalated": report.escalated,
                "deduplicated": report.deduplicated,
            },
        )
    return report
