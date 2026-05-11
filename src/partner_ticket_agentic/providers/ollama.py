"""Ollama-backed LLM provider for local OSS models.

Targets a running ``ollama serve`` on ``http://localhost:11434`` (the
default). Uses the ``/api/chat`` endpoint with ``format: "json"`` to coax
JSON output, then validates against the requested Pydantic schema with a
single retry on validation failure (the second attempt restates the
schema explicitly in the system prompt).

The ``tests/test_ollama_provider.py`` integration test is skipped with a
clear reason when the local server isn't reachable, matching the
contract in CLAUDE.md ("same skip-with-reason pattern if not available").
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from partner_ticket_agentic.cost import (
    current_ledger,
    estimate_cost,
    estimate_tokens,
)
from partner_ticket_agentic.obs import current_log_context, get_logger
from partner_ticket_agentic.providers.base import (
    ApprovedModelRegistry,
    LLMProviderError,
    Message,
    Tier,
    load_approved_models,
)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT = 60.0

_log = get_logger("providers.ollama")


class OllamaProvider:
    """Local OSS provider talking to ``ollama serve``.

    Initialisation pings ``/api/tags`` so an unreachable server fails fast
    with :class:`LLMProviderError` rather than later inside ``complete``.
    The platform's ``make_provider`` falls back to mock on that failure.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        registry: ApprovedModelRegistry | None = None,
        client: httpx.Client | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        ping: bool = True,
    ) -> None:
        self._base_url = (base_url or os.environ.get("OLLAMA_HOST") or _DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._registry = registry or load_approved_models()
        if ping:
            try:
                resp = self._client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
            except Exception as exc:
                if self._owns_client:
                    self._client.close()
                raise LLMProviderError(f"cannot reach Ollama at {self._base_url}: {exc}") from exc

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
        json_schema = schema.model_json_schema()
        schema_hint = json.dumps(json_schema, indent=2, sort_keys=True)
        base_system = (system or "") + (
            "\n\nReply with a single JSON object that satisfies this JSON Schema "
            f"and nothing else:\n```json\n{schema_hint}\n```"
        )

        body = self._build_body(model_id, base_system, messages)
        instance, latency_ms, raw = self._call_and_parse(body, schema)
        if instance is None:
            # one retry with a stricter system prompt restating the schema name
            stricter = base_system + (
                f"\n\nThe object MUST be a valid {schema.__name__}. "
                "Do not include comments, prose, or markdown fences."
            )
            body = self._build_body(model_id, stricter, messages)
            instance, latency_ms_retry, raw = self._call_and_parse(body, schema)
            latency_ms += latency_ms_retry
            if instance is None:
                raise LLMProviderError(
                    f"ollama produced output that failed {schema.__name__} validation twice; "
                    f"raw payload was: {raw[:500]!r}"
                )

        # Ollama doesn't return usage; estimate deterministically.
        input_text = (system or "") + "\n".join(m.content for m in messages)
        output_text = instance.model_dump_json()
        tokens_in = estimate_tokens(input_text)
        tokens_out = estimate_tokens(output_text)
        breakdown = estimate_cost(self.name, model_id, tokens_in=tokens_in, tokens_out=tokens_out)

        agent = current_log_context().get("agent", "unknown")
        ledger = current_ledger()
        if ledger is not None:
            ledger.record(agent=str(agent), provider=self.name, model=model_id, breakdown=breakdown)

        _log.info(
            "llm_call",
            extra={
                "provider": self.name,
                "model_id": model_id,
                "tier": tier.value,
                "schema": schema.__name__,
                "latency_ms": latency_ms,
                "trace_id": trace_id,
                "outcome": "success",
                **breakdown.to_log_fields(),
            },
        )
        return instance

    # ---- internals ----------------------------------------------------------

    def _build_body(self, model_id: str, system: str, messages: list[Message]) -> dict[str, Any]:
        chat_messages: list[dict[str, str]] = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        for m in messages:
            chat_messages.append({"role": m.role, "content": m.content})
        return {
            "model": model_id,
            "messages": chat_messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }

    def _call_and_parse(self, body: dict[str, Any], schema: type[T]) -> tuple[T | None, int, str]:
        started = time.perf_counter()
        try:
            resp = self._client.post(f"{self._base_url}/api/chat", json=body)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise LLMProviderError(f"ollama API call failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        message = payload.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError(
                f"ollama returned empty content; raw payload: {json.dumps(payload)[:500]}"
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None, latency_ms, content
        try:
            return schema.model_validate(parsed), latency_ms, content
        except ValidationError:
            return None, latency_ms, content


__all__ = ["OllamaProvider"]
