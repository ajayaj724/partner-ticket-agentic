"""Tool registry and per-agent dispatcher.

Tools are pure(-ish) Python functions: typed Pydantic-validated inputs,
typed Pydantic outputs, idempotent for read-only operations and
idempotency-keyed for side-effecting ones (DESIGN.md §2). The registry
keeps them keyed by name; the dispatcher enforces an agent's
:class:`ToolAllowList` and emits one structured-log line per call so the
trace shows exactly which tool fired with what latency.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from partner_ticket_agentic.obs import get_logger
from partner_ticket_agentic.safety import ToolAllowList

_log = get_logger("tools")


class ToolError(RuntimeError):
    """Raised when a tool call fails for a non-allow-list reason.

    The dispatcher catches this and lets the calling agent decide whether
    to fall back or surface — DESIGN.md §2 "fail closed" — but never to
    silently swallow.
    """


# Forward-declare the policy types so the ToolDispatcher dataclass below
# can reference them. Importing at the top would create a circular
# dependency with tools/policy.py (which imports ToolError from here).
from partner_ticket_agentic.tools.policy import (  # noqa: E402
    DEFAULT_BREAKERS,
    RetryPolicy,
)


@dataclass(frozen=True, slots=True)
class Tool:
    """One registered tool: name, handler, and a one-line description."""

    name: str
    handler: Callable[..., Any]
    description: str


class ToolRegistry:
    """Process-wide registry of available tools, keyed by name."""

    _tools: ClassVar[dict[str, Tool]] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        if tool.name in cls._tools:
            existing = cls._tools[tool.name]
            if existing.handler is not tool.handler:
                raise ValueError(f"tool {tool.name!r} already registered with a different handler")
            return
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Tool:
        try:
            return cls._tools[name]
        except KeyError as exc:
            raise ToolError(f"unknown tool {name!r}; not in registry") from exc

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._tools)


def register_tool(
    name: str, *, description: str = ""
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: register the wrapped function as a named tool."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        ToolRegistry.register(
            Tool(name=name, handler=fn, description=description or fn.__doc__ or "")
        )
        return fn

    return deco


@dataclass(frozen=True, slots=True)
class ToolDispatcher:
    """Per-agent dispatcher that enforces a :class:`ToolAllowList`.

    Construct with ``ToolDispatcher(allow_list=...)`` once per agent run.
    Every :meth:`call` first checks the allow-list (raising
    :class:`ToolNotAllowedError` if violated), then consults the
    process-wide circuit breaker for the named tool, then runs the
    handler with exponential-backoff retries from :class:`RetryPolicy`.
    Errors raised by the tool itself surface as :class:`ToolError`;
    fast-fails from an open circuit surface as
    :class:`CircuitOpenError`.

    Read-only tools are safe to retry by nature. Side-effecting tools
    take an ``idempotency_key`` parameter (per DESIGN.md §2), so
    retries deduplicate at the tool layer.
    """

    allow_list: ToolAllowList
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def call(self, tool_name: str, /, **kwargs: Any) -> Any:
        self.allow_list.check(tool_name)
        tool = ToolRegistry.get(tool_name)
        breaker = DEFAULT_BREAKERS.for_tool(tool_name)
        breaker.check_or_raise()

        last_exc: Exception | None = None
        for attempt in range(self.retry_policy.max_retries + 1):
            started = time.perf_counter()
            try:
                result = tool.handler(**kwargs)
            except Exception as exc:
                last_exc = exc
                latency_ms = int((time.perf_counter() - started) * 1000)
                _log.warning(
                    "tool_failed",
                    extra={
                        "tool": tool_name,
                        "agent": self.allow_list.agent,
                        "latency_ms": latency_ms,
                        "attempt": attempt + 1,
                        "max_attempts": self.retry_policy.max_retries + 1,
                        "error": str(exc),
                    },
                )
                if attempt < self.retry_policy.max_retries:
                    delay = self.retry_policy.backoff_base_s * (2**attempt)
                    time.sleep(delay)
                    continue
                breaker.record_failure()
                raise ToolError(f"tool {tool_name!r} raised: {exc}") from exc
            else:
                latency_ms = int((time.perf_counter() - started) * 1000)
                _log.info(
                    "tool_call",
                    extra={
                        "tool": tool_name,
                        "agent": self.allow_list.agent,
                        "latency_ms": latency_ms,
                        "attempt": attempt + 1,
                        "outcome": "success",
                    },
                )
                breaker.record_success()
                return result
        # Unreachable — the loop always exits via return or raise — but
        # keeps mypy happy on the function's terminal control flow.
        raise ToolError(  # pragma: no cover
            f"tool {tool_name!r} exhausted retries without raising"
        ) from last_exc
