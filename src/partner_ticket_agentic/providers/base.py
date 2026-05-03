"""Provider-interface primitives: tier enum, message shape, base protocol.

This module deliberately knows nothing about HTTP, the Anthropic SDK, or
Ollama — it only defines the contract that every concrete provider must
implement. Keeping the abstractions narrow makes tests cheap (mock the
contract, not the world) and makes swapping providers a one-line change.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Tier(StrEnum):
    """Logical model tier — small/medium/large.

    Matches DESIGN.md §4.1's "model routing, not model monoculture":
    classification and extraction land on ``SMALL``, summaries and drafts
    on ``MEDIUM``, ambiguous reasoning on ``LARGE``. Each provider maps
    these to its concrete model IDs via ``config/approved_models.yaml``.
    """

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Message(BaseModel):
    """One turn in a chat-style prompt — role + content."""

    role: str
    content: str


class LLMProviderError(RuntimeError):
    """Raised when a provider cannot satisfy a ``complete`` call.

    Wraps transport failures, model rejections, schema-validation errors,
    and approval-list violations. The platform's fallback semantics
    (``make_provider`` swapping to mock on init failure) catch this class
    explicitly.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """The single interface every provider implements.

    ``complete`` takes a list of messages, a Pydantic schema class, and a
    tier. The contract is: it returns either a validated instance of
    ``schema`` or it raises :class:`LLMProviderError`. Providers must not
    return free-text or unvalidated dicts — schema enforcement is the
    provider's job, per DESIGN.md §2 ("Schema-first I/O").
    """

    name: str

    def complete(
        self,
        messages: list[Message],
        schema: type[T],
        tier: Tier,
        *,
        system: str | None = None,
        trace_id: str | None = None,
    ) -> T: ...


# --- approved-models registry -------------------------------------------------


class ApprovedModelRegistry(BaseModel):
    """In-memory view of ``config/approved_models.yaml``.

    Wraps a ``provider -> tier -> model_id`` mapping with a single
    :meth:`resolve` accessor. Providers consult the registry in their
    ``complete`` paths; an unapproved (provider, tier) pair raises
    :class:`LLMProviderError` so the violation is visible in the trace
    rather than degraded silently.
    """

    providers: dict[str, dict[str, str]]

    def resolve(self, provider: str, tier: Tier) -> str:
        prov = self.providers.get(provider)
        if prov is None:
            raise LLMProviderError(
                f"provider {provider!r} not in approved-model registry; "
                "edit config/approved_models.yaml to add it."
            )
        model = prov.get(tier.value)
        if model is None:
            raise LLMProviderError(
                f"tier {tier.value!r} not approved for provider {provider!r}; "
                "edit config/approved_models.yaml to add it."
            )
        return model


def _default_config_path() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "config" / "approved_models.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("config/approved_models.yaml not found; expected at the project root.")


def load_approved_models(path: str | Path | None = None) -> ApprovedModelRegistry:
    """Load the approved-models registry from a YAML file.

    Defaults to ``<project root>/config/approved_models.yaml``, which the
    repo ships. Tests can pass a temporary path with their own approval
    list to exercise the rejection paths without touching the shipped
    config.
    """

    yaml_path = Path(path) if path is not None else _default_config_path()
    with yaml_path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ValueError(f"{yaml_path}: expected top-level 'providers:' mapping")
    return ApprovedModelRegistry(providers=providers)
