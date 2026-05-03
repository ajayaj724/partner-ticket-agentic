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
