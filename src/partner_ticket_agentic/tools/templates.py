"""Template + compliance tools used by F5 Drafter."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.registry import ToolError, register_tool


class DraftTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    subject: str
    body: str
    tone: str = "professional"


_TEMPLATES: dict[str, DraftTemplate] = {
    "circuit_down": DraftTemplate(
        template_id="TPL-001",
        subject="[Acknowledged] Circuit {circuit_id} outage — investigating",
        body=(
            "Hello {partner_name},\n\n"
            "Thank you for reporting the outage on {circuit_id}. "
            "Our NOC team has been notified and is investigating. We will follow up "
            "with a status update within {sla_minutes} minutes.\n\n"
            "Reference: ticket {ticket_id}.\n\n"
            "Best regards,\nThe NOC team"
        ),
    ),
    "throughput_degraded": DraftTemplate(
        template_id="TPL-002",
        subject="[Investigating] Performance degradation on {circuit_id}",
        body=(
            "Hello {partner_name},\n\n"
            "We have received your report of degraded throughput on {circuit_id}. "
            "We are pulling the last 24h metrics and will run a latency probe. "
            "Expect an update within {sla_minutes} minutes.\n\n"
            "Reference: ticket {ticket_id}.\n\n"
            "Best regards,\nThe NOC team"
        ),
    ),
    "appointment_request": DraftTemplate(
        template_id="TPL-003",
        subject="[Acknowledged] Appointment update request {appointment_id}",
        body=(
            "Hello {partner_name},\n\n"
            "Thank you for the appointment update request. "
            "We will identify alternative slots and confirm with you within "
            "{sla_minutes} minutes.\n\n"
            "Reference: ticket {ticket_id}.\n\n"
            "Best regards,\nDispatch"
        ),
    ),
    "billing": DraftTemplate(
        template_id="TPL-004",
        subject="[Acknowledged] Invoice query {invoice_id}",
        body=(
            "Hello {partner_name},\n\n"
            "We have received your billing query regarding {invoice_id}. "
            "Finance Ops will compare against your contracted rate card and respond "
            "within {sla_minutes} minutes.\n\n"
            "Reference: ticket {ticket_id}.\n\n"
            "Best regards,\nFinance Ops"
        ),
    ),
    "provisioning": DraftTemplate(
        template_id="TPL-005",
        subject="[Acknowledged] Provisioning request {ticket_id}",
        body=(
            "Hello {partner_name},\n\n"
            "Thank you for the provisioning request. We will validate the site "
            "address and capacity, and propose a delivery window within "
            "{sla_minutes} minutes.\n\n"
            "Reference: ticket {ticket_id}.\n\n"
            "Best regards,\nProvisioning"
        ),
    ),
    "other": DraftTemplate(
        template_id="TPL-099",
        subject="[Received] Ticket {ticket_id}",
        body=(
            "Hello {partner_name},\n\n"
            "Thank you for your message. We have logged ticket {ticket_id} and "
            "will route it to the appropriate team within {sla_minutes} minutes.\n\n"
            "Best regards,\nFront Office"
        ),
    ),
}


@register_tool("template_lookup", description="Fetch a draft template by triage category.")
def template_lookup(*, category: str) -> DraftTemplate:
    if category not in _TEMPLATES:
        raise ToolError(f"no template registered for category {category!r}")
    return _TEMPLATES[category]


# --- compliance filter --------------------------------------------------------


class ComplianceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flags: list[str] = Field(default_factory=list)
    blocked: bool = False


# Sensitive patterns scanned in outbound drafts.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    (
        "password_in_body",
        re.compile(r"\b(password|wachtwoord|mot de passe)\s*[:=]\s*\S+", re.IGNORECASE),
    ),
    (
        "secret_token",
        re.compile(r"\b(api[_-]?key|secret|token)\s*[:=]\s*[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    ),
    ("national_id_be", re.compile(r"\b\d{2}\.\d{2}\.\d{2}-\d{3}\.\d{2}\b")),
)

_FORBIDDEN_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "guarantee_uptime",
        re.compile(r"\b(?:we|i)\s+guarantee\s+(?:100%\s+)?uptime\b", re.IGNORECASE),
    ),
    ("we_will_compensate", re.compile(r"\bwe\s+will\s+compensate\b", re.IGNORECASE)),
)


@register_tool(
    "compliance_filter",
    description="Scan a draft for PII / secrets / forbidden commitments — block on hit.",
)
def compliance_filter(*, subject: str, body: str) -> ComplianceResult:
    flags: list[str] = []
    text = f"{subject}\n{body}"
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            flags.append(f"pii:{label}")
    for label, pattern in _FORBIDDEN_PHRASES:
        if pattern.search(text):
            flags.append(f"forbidden:{label}")
    blocked = bool(flags)
    return ComplianceResult(flags=flags, blocked=blocked)
