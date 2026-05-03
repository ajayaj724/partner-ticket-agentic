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
from dataclasses import dataclass
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
    :class:`ToolNotAllowedError` if violated) and then logs the dispatch
    with latency for the trace. Errors raised by the tool itself surface
    as :class:`ToolError`.
    """

    allow_list: ToolAllowList

    def call(self, tool_name: str, /, **kwargs: Any) -> Any:
        self.allow_list.check(tool_name)
        tool = ToolRegistry.get(tool_name)
        started = time.perf_counter()
        try:
            result = tool.handler(**kwargs)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _log.warning(
                "tool_failed",
                extra={
                    "tool": tool_name,
                    "agent": self.allow_list.agent,
                    "latency_ms": latency_ms,
                    "error": str(exc),
                },
            )
            raise ToolError(f"tool {tool_name!r} raised: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log.info(
            "tool_call",
            extra={
                "tool": tool_name,
                "agent": self.allow_list.agent,
                "latency_ms": latency_ms,
                "outcome": "success",
            },
        )
        return result
