"""Structured-logging and trace-context plumbing for the agentic platform.

DESIGN.md §4.3 (Observability) commits to JSON-line logs with a ``trace_id``
on every step, plus an ``--export-trace`` flag that dumps the full trace for
a ticket. This module is the implementation: a JSON formatter, a
``ContextVar``-backed bind block, and an in-memory trace collector that the
CLI can serialise to disk.

Every agent and tool call goes through :func:`get_logger`, and every
top-level pipeline run wraps itself in :func:`bind_log_context` so the
``trace_id`` / ``ticket_id`` propagate without threading them through every
function signature.
"""

from __future__ import annotations

import json
import logging as _logging
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any, ClassVar

_EMPTY_CONTEXT: Mapping[str, Any] = MappingProxyType({})
_LOG_CONTEXT: ContextVar[Mapping[str, Any]] = ContextVar("_LOG_CONTEXT", default=_EMPTY_CONTEXT)
_TRACE_BUFFER: ContextVar[list[dict[str, Any]] | None] = ContextVar("_TRACE_BUFFER", default=None)


class _JsonLineFormatter(_logging.Formatter):
    """Emit each record as a single JSON line with bound context fields.

    The formatter merges, in order: standard fields (``ts``, ``level``,
    ``logger``, ``message``), the current ``_LOG_CONTEXT`` (``trace_id``,
    ``ticket_id``, ...), and the record's own ``extra`` payload. The
    ``extra`` payload wins on conflict so call-site fields can override
    bound context if needed.
    """

    _RESERVED: ClassVar[frozenset[str]] = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
            "taskName",
        }
    )

    def format(self, record: _logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_LOG_CONTEXT.get())
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=False)


_HANDLER_INSTALLED = False


def _ensure_handler() -> None:
    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED:
        return
    handler = _logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_JsonLineFormatter())
    root = _logging.getLogger("partner_ticket_agentic")
    root.addHandler(handler)
    root.setLevel(_logging.INFO)
    root.propagate = False
    _HANDLER_INSTALLED = True


def get_logger(name: str) -> _logging.Logger:
    """Return a logger under the project's namespace with JSON-line output.

    ``name`` is treated as a suffix of ``partner_ticket_agentic`` so callers
    pass short labels like ``"agents.triage"`` rather than the full dotted
    path. Idempotent — repeated calls share one underlying handler.
    """

    _ensure_handler()
    if name == "" or name == "partner_ticket_agentic":
        return _logging.getLogger("partner_ticket_agentic")
    return _logging.getLogger(f"partner_ticket_agentic.{name}")


def current_log_context() -> Mapping[str, Any]:
    """Return the bound log context — used by providers to read the calling agent.

    Each agent wraps its run in :func:`bind_log_context(agent="...", ...)`,
    so a provider can read ``current_log_context().get("agent")`` to know
    which agent issued the LLM call without changing the provider signature.
    """

    return _LOG_CONTEXT.get()


def new_trace_id() -> str:
    """Mint a fresh trace ID. UUID4 hex truncated to 16 chars for log brevity."""

    return uuid.uuid4().hex[:16]


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[None]:
    """Push fields onto the per-call log context for the duration of the block.

    Nested binds compose — inner fields shadow outer ones, and the previous
    context is restored on exit. Used by the pipeline runner to attach
    ``trace_id`` and ``ticket_id`` to every log line for one ticket.
    """

    previous = _LOG_CONTEXT.get()
    merged = {**previous, **fields}
    token = _LOG_CONTEXT.set(merged)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


@contextmanager
def trace_collector() -> Iterator[list[dict[str, Any]]]:
    """Capture every log record emitted within the block as JSON dicts.

    Used by ``--export-trace`` to serialise a ticket's full trace to disk.
    Operates by attaching a transient handler to the project logger; on
    exit, the handler is removed and the buffer is yielded for the caller
    to dump. Re-entrant: a nested collector receives only its own slice.
    """

    _ensure_handler()
    buffer: list[dict[str, Any]] = []
    token = _TRACE_BUFFER.set(buffer)

    class _BufferHandler(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            try:
                line = _JsonLineFormatter().format(record)
                buffer.append(json.loads(line))
            except Exception:
                pass

    handler = _BufferHandler()
    project_logger = _logging.getLogger("partner_ticket_agentic")
    project_logger.addHandler(handler)
    try:
        yield buffer
    finally:
        project_logger.removeHandler(handler)
        _TRACE_BUFFER.reset(token)
