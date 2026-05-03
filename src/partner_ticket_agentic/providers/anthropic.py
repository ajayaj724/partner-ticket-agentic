"""Anthropic-backed LLM provider with tool-use forced to a single emit-tool.

CLAUDE.md requires the Anthropic provider to use **tool-use plus structured
outputs** to enforce the JSON schema, rather than relying on the model to
return well-formed JSON in free text. The trick:

1. Convert the requested Pydantic schema to JSON Schema.
2. Define one tool whose ``input_schema`` *is* that JSON Schema.
3. Set ``tool_choice={"type": "tool", "name": ...}`` so the model can only
   reply by calling that tool.
4. Validate the tool's ``input`` payload through ``model.model_validate``
   on the way out.

That gives two layers of enforcement: Anthropic's tool-call validation
(which rejects malformed payloads server-side) and Pydantic's
:class:`ValidationError` on the client. If either trips, the provider
raises :class:`LLMProviderError` — never a partial dict.

Initialisation is permissive: missing ``ANTHROPIC_API_KEY`` simply raises
:class:`LLMProviderError`, which the public ``make_provider`` factory
catches and falls back to the mock provider. The integration test in
``tests/test_anthropic_provider.py`` skips with a clear reason when the
key isn't present.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from partner_ticket_agentic.obs import get_logger
from partner_ticket_agentic.providers.base import (
    ApprovedModelRegistry,
    LLMProviderError,
    Message,
    Tier,
    load_approved_models,
)

if TYPE_CHECKING:
    from anthropic import Anthropic

T = TypeVar("T", bound=BaseModel)

_log = get_logger("providers.anthropic")


class AnthropicProvider:
    """Production-shaped Anthropic provider — tool-use with forced JSON."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        registry: ApprovedModelRegistry | None = None,
        client: Anthropic | None = None,
        max_tokens: int = 1024,
    ) -> None:
        if client is None:
            try:
                from anthropic import Anthropic as _Anthropic
            except ImportError as exc:
                raise LLMProviderError("anthropic SDK is not installed") from exc
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMProviderError(
                    "ANTHROPIC_API_KEY is not set; cannot initialise AnthropicProvider"
                )
            self._client = _Anthropic(api_key=key)
        else:
            self._client = client
        self._registry = registry or load_approved_models()
        self._max_tokens = max_tokens

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
        tool_name = f"emit_{schema.__name__.lower()}"
        json_schema = schema.model_json_schema()
        tool_def: dict[str, Any] = {
            "name": tool_name,
            "description": (
                f"Emit a {schema.__name__} object that satisfies the input schema. "
                "Reply ONLY by calling this tool — never in free text."
            ),
            "input_schema": _strip_pydantic_metadata(json_schema),
        }
        anthropic_messages = [{"role": m.role, "content": m.content} for m in messages]

        started = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=model_id,
                max_tokens=self._max_tokens,
                system=system or "",
                tools=[tool_def],
                tool_choice={"type": "tool", "name": tool_name},
                messages=anthropic_messages,
            )
        except Exception as exc:
            raise LLMProviderError(f"anthropic API call failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        tool_use = _extract_tool_use(response, tool_name)
        try:
            instance = schema.model_validate(tool_use)
        except ValidationError as exc:
            raise LLMProviderError(
                f"anthropic returned tool-use payload that failed schema validation: {exc}"
            ) from exc

        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None) if usage else None
        tokens_out = getattr(usage, "output_tokens", None) if usage else None
        _log.info(
            "llm_call",
            extra={
                "provider": self.name,
                "model_id": model_id,
                "tier": tier.value,
                "schema": schema.__name__,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "trace_id": trace_id,
                "outcome": "success",
            },
        )
        return instance


def _strip_pydantic_metadata(json_schema: dict[str, Any]) -> dict[str, Any]:
    """Remove Pydantic-only keys that Anthropic's tool-input validator rejects.

    Pydantic emits ``$defs`` and ``title`` fields that the Anthropic tool
    schema validator tolerates inconsistently across SDK versions. We
    inline ``$defs`` and drop ``title`` recursively to keep the tool
    definition minimal and portable.
    """

    schema = dict(json_schema)
    defs = schema.pop("$defs", None)

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and defs:
                ref = node["$ref"]
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    name = ref.removeprefix("#/$defs/")
                    target = defs.get(name)
                    if target is not None:
                        merged = {k: v for k, v in node.items() if k != "$ref"}
                        merged.update(_resolve(target))
                        return merged
            return {k: _resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


def _extract_tool_use(response: Any, expected_tool: str) -> dict[str, Any]:
    """Pull the tool_use block matching ``expected_tool`` out of a response."""

    content = getattr(response, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "tool_use":
            continue
        block_name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if block_name != expected_tool:
            continue
        block_input = getattr(block, "input", None)
        if block_input is None and isinstance(block, dict):
            block_input = block.get("input")
        if isinstance(block_input, dict):
            return block_input
    raise LLMProviderError(
        f"anthropic response did not contain a tool_use for {expected_tool!r}; "
        "the model declined to use the structured-output tool."
    )


__all__ = ["AnthropicProvider"]
