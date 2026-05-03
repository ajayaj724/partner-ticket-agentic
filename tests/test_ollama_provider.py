"""End-to-end test for the Ollama provider.

Skips with a clear reason when ``ollama serve`` isn't running on
``localhost:11434`` (the default), matching the contract in CLAUDE.md.
When the local server *is* reachable and the small-tier model is pulled,
this exercises the full ``format: json`` + Pydantic-validation path.
"""

from __future__ import annotations

import os

import httpx
import pytest
from pydantic import BaseModel

REQUIRED_MODEL = "llama3.2:3b"


def _ollama_has_required_model() -> bool:
    """True iff Ollama is reachable AND the small-tier model is pulled.

    Both conditions matter — a reachable server without the model would fail
    the integration test for the wrong reason. Checking /api/tags lets us
    skip cleanly with a precise message instead.
    """

    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{base}/api/tags")
            if resp.status_code != 200:
                return False
            models = resp.json().get("models") or []
            return any(m.get("name", "").startswith(REQUIRED_MODEL) for m in models)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_has_required_model(),
    reason=(
        f"Ollama not reachable on localhost:11434 or model {REQUIRED_MODEL!r} "
        "not pulled; local provider test skipped."
    ),
)


class _IsCircuitOutage(BaseModel):
    is_outage: bool
    note: str


def test_ollama_provider_returns_validated_pydantic_object() -> None:
    from partner_ticket_agentic.providers import Message, OllamaProvider, Tier

    provider = OllamaProvider()
    out = provider.complete(
        [Message(role="user", content="Circuit CIRC-44781 has been unreachable since 09:14.")],
        _IsCircuitOutage,
        Tier.SMALL,
        system="Decide whether the message reports a circuit outage.",
    )
    assert isinstance(out, _IsCircuitOutage)
    assert out.is_outage is True
