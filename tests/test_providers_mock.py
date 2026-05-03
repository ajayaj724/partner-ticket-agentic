"""Tests for the deterministic mock provider and the approved-models registry."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from partner_ticket_agentic.providers import (
    LLMProviderError,
    Message,
    MockProvider,
    Tier,
    load_approved_models,
    make_provider,
)


class ToyOutput(BaseModel):
    label: str
    score: float


@pytest.fixture(autouse=True)
def _reset_rules() -> None:
    """Each test gets a clean rule registry — the registry is class-level."""
    MockProvider.clear_rules()
    yield
    MockProvider.clear_rules()


class TestMockProvider:
    def test_unregistered_schema_raises(self) -> None:
        provider = MockProvider()
        with pytest.raises(LLMProviderError) as exc:
            provider.complete([Message(role="user", content="hi")], ToyOutput, Tier.SMALL)
        assert "no rule registered" in str(exc.value)

    def test_registered_rule_dispatches_and_validates(self) -> None:
        def rule(_system: str, _messages: list[Message]) -> dict[str, object]:
            return {"label": "circuit_down", "score": 0.92}

        MockProvider.register(ToyOutput, rule)
        provider = MockProvider()
        out = provider.complete(
            [Message(role="user", content="circuit is down")],
            ToyOutput,
            Tier.SMALL,
        )
        assert isinstance(out, ToyOutput)
        assert out.label == "circuit_down"
        assert out.score == pytest.approx(0.92)

    def test_rule_output_is_validated_against_schema(self) -> None:
        def bad_rule(_s: str, _m: list[Message]) -> dict[str, object]:
            return {"label": "x", "score": "not-a-float"}

        MockProvider.register(ToyOutput, bad_rule)
        provider = MockProvider()
        with pytest.raises(LLMProviderError) as exc:
            provider.complete([Message(role="user", content="x")], ToyOutput, Tier.SMALL)
        assert "schema validation" in str(exc.value)
        # ensure the underlying ValidationError chained
        assert isinstance(exc.value.__cause__, ValidationError)

    def test_register_is_idempotent(self) -> None:
        def first(_s: str, _m: list[Message]) -> dict[str, object]:
            return {"label": "first", "score": 0.0}

        def second(_s: str, _m: list[Message]) -> dict[str, object]:
            return {"label": "second", "score": 1.0}

        MockProvider.register(ToyOutput, first)
        MockProvider.register(ToyOutput, second)
        out = MockProvider().complete([Message(role="user", content="x")], ToyOutput, Tier.SMALL)
        assert out.label == "second"


class TestApprovedModelsRegistry:
    def test_loads_shipped_yaml(self) -> None:
        registry = load_approved_models()
        assert registry.resolve("anthropic", Tier.SMALL).startswith("claude-haiku")
        assert registry.resolve("anthropic", Tier.MEDIUM).startswith("claude-sonnet")
        assert registry.resolve("anthropic", Tier.LARGE).startswith("claude-opus")
        assert registry.resolve("ollama", Tier.SMALL) == "llama3.2:3b"
        assert registry.resolve("mock", Tier.SMALL) == "mock-small"

    def test_unapproved_provider_raises(self) -> None:
        registry = load_approved_models()
        with pytest.raises(LLMProviderError):
            registry.resolve("openai", Tier.SMALL)


class TestFactory:
    def test_make_mock_returns_mock(self) -> None:
        provider = make_provider("mock")
        assert provider.name == "mock"

    def test_unknown_provider_name_raises(self) -> None:
        with pytest.raises(ValueError):
            make_provider("magic")

    def test_anthropic_falls_back_to_mock_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        provider = make_provider("anthropic")
        assert provider.name == "mock"

    def test_ollama_falls_back_to_mock_when_unreachable(self, monkeypatch) -> None:
        # Point at a port that is almost certainly closed.
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
        provider = make_provider("ollama")
        assert provider.name == "mock"
