"""Safety primitives: instruction-injection filter and tool allow-list.

DESIGN.md §2 commits to two non-negotiables enforced *in code*: every agent
operates over a fixed, audited tool surface (the **allow-list**); and any
free-text input that could be a jailbreak attempt is filtered before it
reaches an LLM. This module is where both live.

The allow-list is a typed gate: callers pass a tool name through
:meth:`ToolAllowList.check`, and an unauthorised tool raises
:class:`ToolNotAllowedError` rather than silently succeeding. That makes
the policy enforceable in tests and visible in stack traces during a
review. The injection filter ships a small heuristic detector that flags
the most common jailbreak vectors — enough to demonstrate the architecture
and the exit point in the design where a production filter would land.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


class SafetyError(Exception):
    """Base class for safety-policy violations raised within the platform."""


class ToolNotAllowedError(SafetyError):
    """Raised when an agent attempts to invoke a tool outside its allow-list.

    The error message names the calling agent, the tool that was attempted,
    and the set of tools that *were* permitted, so the violation is
    self-describing in a stack trace.
    """

    def __init__(self, agent: str, tool: str, allowed: Iterable[str]) -> None:
        allowed_sorted = sorted(allowed)
        super().__init__(f"agent {agent!r} attempted tool {tool!r}; allowed: {allowed_sorted}")
        self.agent = agent
        self.tool = tool
        self.allowed = tuple(allowed_sorted)


@dataclass(frozen=True, slots=True)
class ToolAllowList:
    """Per-agent allow-list of tool names.

    Construct with ``ToolAllowList(agent="triage", tools={...})`` and call
    :meth:`check` on every dispatch. ``frozen=True`` plus tuple storage
    means the list can't be mutated after construction; the only way to
    grant a new tool is to re-create the allow-list.
    """

    agent: str
    tools: frozenset[str]

    @classmethod
    def of(cls, agent: str, *tools: str) -> ToolAllowList:
        return cls(agent=agent, tools=frozenset(tools))

    def check(self, tool: str) -> None:
        """Raise :class:`ToolNotAllowedError` unless ``tool`` is in the list."""

        if tool not in self.tools:
            raise ToolNotAllowedError(self.agent, tool, self.tools)

    def __contains__(self, tool: object) -> bool:
        return isinstance(tool, str) and tool in self.tools


# Patterns flagged as likely prompt-injection attempts. The list is small and
# explicit on purpose: a reviewer must be able to read it and understand what
# the filter does. Production deployments would extend this with a managed
# service (Anthropic prompt-injection classifier, Lakera, etc.).
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore (the )?(previous|prior|above) (instructions?|prompt)\b", re.IGNORECASE),
    re.compile(
        r"\bdisregard (the )?(previous|prior|above|system) (instructions?|prompt)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bforget (the )?(previous|prior|above|system) (instructions?|prompt)\b", re.IGNORECASE
    ),
    re.compile(r"\b(reveal|print|show|leak)\s+(your )?system prompt\b", re.IGNORECASE),
    re.compile(
        r"\b(you are|act as) (now )?(a|an) (developer|admin|root|superuser)\b", re.IGNORECASE
    ),
    re.compile(r"\bdeveloper mode\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>"),
)


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    """A single matched injection pattern with the substring that triggered it."""

    pattern: str
    match: str

    def __str__(self) -> str:
        return f"{self.pattern!r} matched on {self.match!r}"


def detect_prompt_injection(text: str) -> list[InjectionFinding]:
    """Return a list of injection findings for the given text.

    Empty list means "looks clean by these heuristics." A non-empty list is
    a *suspicion*, not a guaranteed attack — callers decide whether to
    block (e.g., the demo CLI's ``--inject`` flag exits non-zero) or
    sanitise and proceed. The function is pure and side-effect free.
    """

    if not text:
        return []
    findings: list[InjectionFinding] = []
    for pat in _INJECTION_PATTERNS:
        m = pat.search(text)
        if m is not None:
            findings.append(InjectionFinding(pattern=pat.pattern, match=m.group(0)))
    return findings


def assert_safe_input(text: str) -> None:
    """Raise :class:`SafetyError` if ``text`` matches any injection pattern.

    Used at the ingest boundary — before a partner's free-text description
    is handed to the triage agent — to fail closed on obvious jailbreak
    attempts. The exception carries the matched patterns so the caller can
    log them for audit.
    """

    findings = detect_prompt_injection(text)
    if findings:
        rendered = "; ".join(str(f) for f in findings)
        raise SafetyError(f"input failed prompt-injection filter: {rendered}")


# --- PII detection + redaction (deck slide 18) -------------------------------
#
# Belgian-telecom defaults: e-mail addresses, phone numbers (international
# and national), IBAN (BE-prefixed), and IPv4 addresses. The patterns are
# deliberately tight — the cost of a false-positive on a ticket description
# is a redacted token in the log; the cost of a false-negative is a PII
# leak in audit storage. Production deployments would layer a managed
# detector (Presidio, AWS Comprehend, Google DLP) on top.

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Belgian phone: +32 ... or 0... with 2-3 digit area code + 6-7 digit body.
    ("phone_be", re.compile(r"(?:\+32[\s.-]?\d|0\d)(?:[\s.-]?\d){7,9}")),
    # International phone: +<country code 1-3> followed by 7-12 digits.
    ("phone_intl", re.compile(r"\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}")),
    # Belgian IBAN: BE + 2 check digits + 12 digits (with optional spaces).
    ("iban_be", re.compile(r"\bBE\d{2}(?:[\s]?\d{4}){3}\b")),
    # IPv4 — useful for telecom network leaks.
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


@dataclass(frozen=True, slots=True)
class PIIFinding:
    """A single PII match with its category and the substring that triggered it."""

    kind: str
    match: str

    def __str__(self) -> str:
        return f"{self.kind}={self.match!r}"


def detect_pii(text: str) -> list[PIIFinding]:
    """Return all PII findings for ``text`` — empty list means "no PII".

    Pure and side-effect free. ``redact_pii_for_logging`` consumes this
    function for the actual masking. Used at the ingest boundary (in
    :func:`partner_ticket_agentic.graph.run_pipeline`) so the trace
    records what was detected before the agents see it.
    """

    if not text:
        return []
    findings: list[PIIFinding] = []
    for kind, pat in _PII_PATTERNS:
        for m in pat.finditer(text):
            findings.append(PIIFinding(kind=kind, match=m.group(0)))
    return findings


def redact_pii_for_logging(text: str) -> tuple[str, list[PIIFinding]]:
    """Return ``(redacted_text, findings)`` — replace every PII span with a tag.

    Used for log records and trace exports. Agents still receive the
    original text (per DESIGN.md §4.2 — they need real content to
    operate); only the audit surface sees the masked version. The
    replacement tag preserves the *kind* of PII so a reviewer reading the
    log knows what was redacted without seeing the value.
    """

    if not text:
        return text, []
    findings = detect_pii(text)
    redacted = text
    # Apply longest matches first to avoid partial overlap collisions
    # (e.g., an IBAN containing what looks like a phone fragment).
    for f in sorted(findings, key=lambda x: -len(x.match)):
        redacted = redacted.replace(f.match, f"[REDACTED:{f.kind}]")
    return redacted, findings
