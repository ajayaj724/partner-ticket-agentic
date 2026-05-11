"""Tests for retry policy + circuit breaker on the tool layer."""

from __future__ import annotations

import time

import pytest

from partner_ticket_agentic.safety import ToolAllowList
from partner_ticket_agentic.tools.policy import (
    DEFAULT_BREAKERS,
    BreakerRegistry,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RetryPolicy,
)
from partner_ticket_agentic.tools.registry import (
    ToolDispatcher,
    ToolError,
    ToolRegistry,
    register_tool,
)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    """Reset the process-wide breaker registry around each test."""

    DEFAULT_BREAKERS.reset()
    yield
    DEFAULT_BREAKERS.reset()


# ---- A handful of stub tools we can toggle from the tests ---------------
_BOOM_COUNT = {"n": 0}


@register_tool("__policy_boom", description="Always raises — for retry tests.")
def _boom() -> str:
    _BOOM_COUNT["n"] += 1
    raise RuntimeError("kaboom")


@register_tool("__policy_echo", description="Echoes value — for happy-path tests.")
def _echo(*, value: str) -> str:
    return value


class TestRetryPolicy:
    def test_disabled_policy_runs_once(self) -> None:
        policy = RetryPolicy.disabled()
        assert policy.max_retries == 0

    def test_default_policy_retries_twice(self) -> None:
        assert RetryPolicy().max_retries == 2


class TestCircuitBreaker:
    def test_closed_breaker_lets_calls_through(self) -> None:
        b = CircuitBreaker(tool="x")
        b.check_or_raise()  # does not raise
        assert b.state is CircuitState.CLOSED

    def test_opens_after_threshold_failures(self) -> None:
        b = CircuitBreaker(tool="x", failure_threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.state is CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            b.check_or_raise()

    def test_success_resets_failure_count(self) -> None:
        b = CircuitBreaker(tool="x", failure_threshold=3)
        b.record_failure()
        b.record_failure()
        b.record_success()
        assert b.consecutive_failures == 0
        assert b.state is CircuitState.CLOSED

    def test_open_transitions_to_half_open_after_cooldown(self) -> None:
        b = CircuitBreaker(tool="x", failure_threshold=1, cooldown_s=0.01)
        b.record_failure()
        assert b.state is CircuitState.OPEN
        time.sleep(0.02)
        b.check_or_raise()  # transitions to HALF_OPEN
        assert b.state is CircuitState.HALF_OPEN

    def test_half_open_failure_reopens_immediately(self) -> None:
        b = CircuitBreaker(tool="x", failure_threshold=1, cooldown_s=0.01)
        b.record_failure()
        time.sleep(0.02)
        b.check_or_raise()
        b.record_failure()  # one failure from HALF_OPEN re-opens
        assert b.state is CircuitState.OPEN

    def test_half_open_success_closes(self) -> None:
        b = CircuitBreaker(tool="x", failure_threshold=1, cooldown_s=0.01)
        b.record_failure()
        time.sleep(0.02)
        b.check_or_raise()
        b.record_success()
        assert b.state is CircuitState.CLOSED


class TestDispatcherRetry:
    def test_happy_path_no_retry(self) -> None:
        dispatcher = ToolDispatcher(allow_list=ToolAllowList.of("test", "__policy_echo"))
        assert dispatcher.call("__policy_echo", value="hi") == "hi"

    def test_retries_then_succeeds(self) -> None:
        # Make a stateful tool that fails the first call then succeeds.
        state = {"calls": 0}

        @register_tool("__policy_flake", description="Fails the first attempt, succeeds after.")
        def flake() -> str:
            state["calls"] += 1
            if state["calls"] < 2:
                raise RuntimeError("transient")
            return "ok"

        dispatcher = ToolDispatcher(
            allow_list=ToolAllowList.of("test", "__policy_flake"),
            retry_policy=RetryPolicy(max_retries=2, backoff_base_s=0.001),
        )
        assert dispatcher.call("__policy_flake") == "ok"
        assert state["calls"] == 2

    def test_exhaustion_raises_tool_error_and_records_failure(self) -> None:
        _BOOM_COUNT["n"] = 0
        dispatcher = ToolDispatcher(
            allow_list=ToolAllowList.of("test", "__policy_boom"),
            retry_policy=RetryPolicy(max_retries=1, backoff_base_s=0.001),
        )
        with pytest.raises(ToolError):
            dispatcher.call("__policy_boom")
        assert _BOOM_COUNT["n"] == 2  # initial + 1 retry
        breaker = DEFAULT_BREAKERS.for_tool("__policy_boom")
        assert breaker.consecutive_failures == 1

    def test_circuit_opens_after_repeated_failures(self) -> None:
        # Pre-trip the breaker so a single dispatcher call sees an open
        # circuit and fast-fails without invoking the tool.
        breaker = DEFAULT_BREAKERS.for_tool("__policy_boom")
        breaker.failure_threshold = 2
        breaker.record_failure()
        breaker.record_failure()  # opens

        _BOOM_COUNT["n"] = 0
        dispatcher = ToolDispatcher(
            allow_list=ToolAllowList.of("test", "__policy_boom"),
            retry_policy=RetryPolicy.disabled(),
        )
        with pytest.raises(CircuitOpenError):
            dispatcher.call("__policy_boom")
        assert _BOOM_COUNT["n"] == 0  # never invoked

    def test_tool_registry_records_handlers(self) -> None:
        assert "__policy_echo" in ToolRegistry.names()
        assert "__policy_boom" in ToolRegistry.names()


class TestBreakerRegistry:
    def test_for_tool_caches(self) -> None:
        reg = BreakerRegistry()
        a = reg.for_tool("x")
        b = reg.for_tool("x")
        assert a is b

    def test_reset_clears_state(self) -> None:
        reg = BreakerRegistry()
        reg.for_tool("x").record_failure()
        reg.reset()
        assert reg.for_tool("x").consecutive_failures == 0
