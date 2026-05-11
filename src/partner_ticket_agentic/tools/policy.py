"""Retry policy + circuit breaker for the tool layer.

Closes the slide-13 deck commitment "Retry with backoff + circuit
breaker". Wraps each :class:`ToolDispatcher` call: transient errors get
exponential-backoff retries up to ``max_retries``; if a tool fails
``failure_threshold`` times consecutively, its circuit opens and
subsequent calls fast-fail with :class:`CircuitOpenError` for the
``cooldown_s`` window. The first probe after the window puts the
breaker in HALF_OPEN — a single trial that decides whether to close
the circuit or re-open it.

Side-effecting tools are safe to retry because they take an
``idempotency_key`` (the design-doc contract from §2). Read-only tools
are always safe to retry by nature.

The breaker state is process-local. A production deployment would
share it across workers via Redis or a sidecar — out of scope for the
demo, but the API is shaped to make that swap a one-class rewrite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from partner_ticket_agentic.tools.registry import ToolError


class CircuitOpenError(ToolError):
    """Raised when a tool's circuit is open and the call must fast-fail."""


class CircuitState(StrEnum):
    """Three-state circuit-breaker FSM."""

    CLOSED = "closed"  # normal — calls go through
    OPEN = "open"  # tripped — fast-fail until cooldown elapses
    HALF_OPEN = "half_open"  # probing — single trial to decide next state


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configuration for the dispatcher's retry behaviour.

    ``max_retries=0`` disables retries entirely (the policy passes
    through to the underlying call). ``backoff_base_s`` is the
    geometric base; attempt *n* sleeps ``backoff_base_s * 2**(n-1)``.
    """

    max_retries: int = 2
    backoff_base_s: float = 0.05

    @classmethod
    def disabled(cls) -> RetryPolicy:
        return cls(max_retries=0)


@dataclass
class CircuitBreaker:
    """Per-tool circuit-breaker state.

    State machine:

    * ``CLOSED`` (default) — track consecutive failures. After
      ``failure_threshold`` failures, open the circuit.
    * ``OPEN`` — every :meth:`check_or_raise` raises
      :class:`CircuitOpenError` until ``cooldown_s`` elapses.
    * ``HALF_OPEN`` — exactly one call gets through. Success closes the
      circuit; failure re-opens it for another ``cooldown_s``.
    """

    tool: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    _now: ClassVar = staticmethod(time.monotonic)

    def check_or_raise(self) -> None:
        """Inspect the breaker; raise :class:`CircuitOpenError` if open.

        Side-effect: transitions OPEN -> HALF_OPEN when the cooldown
        has elapsed. HALF_OPEN passes through (the caller is the probe).
        """

        if self.state is CircuitState.OPEN:
            if self._now() - self.opened_at >= self.cooldown_s:
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitOpenError(
                    f"tool {self.tool!r} circuit is open; "
                    f"cooldown ends in "
                    f"{self.cooldown_s - (self._now() - self.opened_at):.1f}s"
                )

    def record_success(self) -> None:
        """Mark a successful call. Resets failure count and closes the circuit."""

        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at = 0.0

    def record_failure(self) -> None:
        """Mark a failed call. Trips the circuit at the failure threshold.

        From HALF_OPEN, a single failure re-opens the circuit
        immediately (the probe failed). From CLOSED, the breaker waits
        for ``failure_threshold`` consecutive failures.
        """

        self.consecutive_failures += 1
        if (
            self.state is CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN
            self.opened_at = self._now()


@dataclass
class BreakerRegistry:
    """Process-local pool of :class:`CircuitBreaker` instances keyed by tool name.

    Constructed lazily — the first :meth:`for_tool` call creates the
    breaker with default settings. Tests can drop the whole registry
    with :meth:`reset` to keep test ordering hermetic.
    """

    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    default_failure_threshold: int = 5
    default_cooldown_s: float = 30.0

    def for_tool(self, tool: str) -> CircuitBreaker:
        if tool not in self.breakers:
            self.breakers[tool] = CircuitBreaker(
                tool=tool,
                failure_threshold=self.default_failure_threshold,
                cooldown_s=self.default_cooldown_s,
            )
        return self.breakers[tool]

    def reset(self) -> None:
        self.breakers.clear()


# Default process-wide registry. Tests reach in to reset it.
DEFAULT_BREAKERS = BreakerRegistry()
