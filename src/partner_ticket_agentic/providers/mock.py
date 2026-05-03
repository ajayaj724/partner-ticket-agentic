"""Deterministic mock LLM provider.

CLAUDE.md is explicit: the mock provider is the **default**, must run
offline with no API keys, and its behaviour must be a deterministic
if/elif rule a reviewer can read and predict. No hashing, no randomness,
no time-based branching. This module enforces that contract.

Each agent registers a rule keyed by its Pydantic output schema. When the
mock is asked to ``complete`` against that schema, it dispatches to the
registered rule, which inspects the messages and returns a Python dict
(validated by Pydantic on the way out). If no rule is registered the mock
raises a clear error rather than guess — guessing is exactly what a real
LLM would do and exactly what the deterministic contract forbids.

Rules are registered by the agent module itself (see e.g.
``agents/triage.py``) at import time, which keeps the rule next to the
schema for easy review.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from partner_ticket_agentic.obs import get_logger
from partner_ticket_agentic.providers.base import (
    ApprovedModelRegistry,
    LLMProviderError,
    Message,
    Tier,
    load_approved_models,
)

T = TypeVar("T", bound=BaseModel)

_log = get_logger("providers.mock")

# Rule signature: receive the system prompt (or empty string) and the
# message list, return a dict that satisfies the schema. Returning a dict
# (rather than a model instance) keeps rules independent of the schema's
# constructor signature; the dispatcher validates via ``model_validate``.
RuleFn = Callable[[str, list[Message]], dict[str, Any]]


class MockProvider:
    """In-process LLM stand-in with deterministic per-schema rules."""

    name = "mock"
    _rules: ClassVar[dict[str, RuleFn]] = {}

    def __init__(self, registry: ApprovedModelRegistry | None = None) -> None:
        # The registry is consulted only so the trace shows a "model_id"
        # field for parity with real providers. Mock never calls anything.
        self._registry = registry or load_approved_models()

    # ---- public API ---------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        schema: type[T],
        tier: Tier,
        *,
        system: str | None = None,
        trace_id: str | None = None,
    ) -> T:
        model_id = self._registry.resolve(self.name, tier)
        schema_name = schema.__name__
        rule = self._rules.get(schema_name)
        if rule is None:
            raise LLMProviderError(
                f"mock provider has no rule registered for schema {schema_name!r}; "
                f"add one via MockProvider.register({schema_name}, fn) — "
                "deterministic behaviour requires explicit rules per agent."
            )
        started = time.perf_counter()
        try:
            raw = rule(system or "", messages)
            instance = schema.model_validate(raw)
        except ValidationError as exc:
            raise LLMProviderError(
                f"mock rule for {schema_name!r} produced output that failed schema validation: {exc}"
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log.info(
            "llm_call",
            extra={
                "provider": self.name,
                "model_id": model_id,
                "tier": tier.value,
                "schema": schema_name,
                "latency_ms": latency_ms,
                "trace_id": trace_id,
                "outcome": "success",
            },
        )
        return instance

    # ---- registration -------------------------------------------------------

    @classmethod
    def register(cls, schema: type[BaseModel], fn: RuleFn) -> None:
        """Register a deterministic rule for the given Pydantic schema.

        Idempotent: re-registering the same schema replaces the rule. This
        is convenient for tests that want to override a rule for a single
        case without touching the agent module.
        """

        cls._rules[schema.__name__] = fn

    @classmethod
    def is_registered(cls, schema: type[BaseModel]) -> bool:
        return schema.__name__ in cls._rules

    @classmethod
    def clear_rules(cls) -> None:
        """Drop all registered rules. Test-only — not used in normal runs."""

        cls._rules.clear()


__all__ = ["MockProvider", "RuleFn"]
