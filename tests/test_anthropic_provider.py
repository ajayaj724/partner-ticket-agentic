"""End-to-end test for the Anthropic provider.

Skips with a clear reason when ``ANTHROPIC_API_KEY`` is not present,
matching the contract in CLAUDE.md. When the key *is* present, this
exercises the full tool-use + structured-output path against the live
API: a tiny Pydantic schema, one message, and an assertion on the
returned object.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ,
    reason="ANTHROPIC_API_KEY is not set; live Anthropic provider test skipped.",
)


class _SentimentOutput(BaseModel):
    sentiment: str
    confidence: float


def test_anthropic_provider_returns_validated_pydantic_object() -> None:
    from partner_ticket_agentic.providers import AnthropicProvider, Message, Tier

    provider = AnthropicProvider()
    out = provider.complete(
        [Message(role="user", content="The circuit is back online and the partner is relieved.")],
        _SentimentOutput,
        Tier.SMALL,
        system="Classify the sentiment of the input. confidence is in [0, 1].",
    )
    assert isinstance(out, _SentimentOutput)
    assert out.sentiment.lower() in {"positive", "negative", "neutral", "mixed"}
    assert 0.0 <= out.confidence <= 1.0
