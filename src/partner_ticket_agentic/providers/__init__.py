"""LLM provider abstraction for the agentic platform.

DESIGN.md §4.1 commits to three provider implementations behind one
:class:`LLMProvider` interface: a deterministic ``mock`` (default), an
``anthropic`` provider that uses tool-use to force JSON output, and an
``ollama`` provider that talks to a local Ollama server. The choice is
runtime via ``--llm-provider``, and the platform falls back to ``mock`` if
the chosen provider fails to initialise.

This package wires up the public surface (interface, factory, tier enum)
and re-exports the concrete provider classes. Agents only ever depend on
:class:`LLMProvider` and :class:`Tier` — never on a concrete provider —
so swapping providers is a pure config change.
"""

from __future__ import annotations

from partner_ticket_agentic.providers.anthropic import AnthropicProvider
from partner_ticket_agentic.providers.base import (
    ApprovedModelRegistry,
    LLMProvider,
    LLMProviderError,
    Message,
    Tier,
    load_approved_models,
)
from partner_ticket_agentic.providers.mock import MockProvider
from partner_ticket_agentic.providers.ollama import OllamaProvider

__all__ = [
    "AnthropicProvider",
    "ApprovedModelRegistry",
    "LLMProvider",
    "LLMProviderError",
    "Message",
    "MockProvider",
    "OllamaProvider",
    "Tier",
    "load_approved_models",
    "make_provider",
]


def make_provider(name: str) -> LLMProvider:
    """Construct a provider by short name (``mock``, ``anthropic``, ``ollama``).

    Falls back to :class:`MockProvider` if the requested provider fails to
    initialise — matching the contract in DESIGN.md §4.1 ("Falls back to
    mock if the chosen provider fails to initialise"). A warning is logged
    so the trace makes the fallback visible rather than silent.
    """

    name = name.lower().strip()
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        try:
            return AnthropicProvider()
        except LLMProviderError:
            from partner_ticket_agentic.obs import get_logger

            get_logger("providers").warning("anthropic_init_failed_falling_back_to_mock")
            return MockProvider()
    if name == "ollama":
        try:
            return OllamaProvider()
        except LLMProviderError:
            from partner_ticket_agentic.obs import get_logger

            get_logger("providers").warning("ollama_init_failed_falling_back_to_mock")
            return MockProvider()
    raise ValueError(f"unknown provider {name!r}; expected one of: mock, anthropic, ollama")
