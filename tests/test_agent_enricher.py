"""Tests for the F2 Enricher agent.

Covers parallel tool dispatch, the per-tool fail-soft contract from
DESIGN.md §3 F2, the LangGraph node wrapper's state-update shape, and
the allow-list invariant.
"""

from __future__ import annotations

from typing import Any

import pytest

from partner_ticket_agentic.agents.enricher import (
    ALLOW_LIST,
    EnrichmentOutput,
    enricher_node,
    run_enricher,
)
from partner_ticket_agentic.agents.triage import run_triage
from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.providers import MockProvider


def _state_with_triage(**overrides: Any) -> TicketState:
    base = {
        "ticket_id": "T-100",
        "partner_id": "P-1001",
        "subject": "Circuit CIRC-44781 down since 09:14",
        "description": (
            "Our monitoring shows circuit CIRC-44781 is unreachable. "
            "Customers in Brussels-Centre are affected."
        ),
    }
    base.update(overrides)
    state = TicketState(**base)
    triage = run_triage(state, MockProvider())
    return state.model_copy(update={"triage": triage.model_dump()})


def test_enricher_populates_partner_circuit_history_runbooks() -> None:
    state = _state_with_triage()
    out = run_enricher(state)
    assert isinstance(out, EnrichmentOutput)
    assert out.partner_profile is not None
    assert out.partner_profile.partner_id == "P-1001"
    assert out.recent_tickets, "P-1001 has seeded ticket history"
    assert out.relevant_runbooks, "runbook_search should return at least one hit"
    assert any(rb.category == "circuit_down" for rb in out.relevant_runbooks)
    # asset_state populated for the extracted circuit
    assert any(c.circuit_id == "CIRC-44781" for c in out.asset_state)


def test_enricher_marks_unavailable_for_unknown_partner() -> None:
    state = TicketState(
        ticket_id="T-200",
        partner_id="P-NONEXISTENT",
        subject="Generic ticket",
        description="No circuits referenced.",
        triage={
            "category": "other",
            "urgency": "low",
            "entities": {"circuits": [], "appointments": [], "invoices": [], "locations": []},
            "confidence": 0.5,
            "rationale": "no match",
        },
    )
    out = run_enricher(state)
    assert "partner_profile" in out.unavailable
    assert out.partner_profile is None
    assert out.recent_tickets == []  # unknown partner returns empty seed history


def test_enricher_marks_unavailable_for_unknown_circuit() -> None:
    state = TicketState(
        ticket_id="T-201",
        partner_id="P-1001",
        subject="ghost circuit",
        description="ref to CIRC-99999",
        triage={
            "category": "other",
            "urgency": "low",
            "entities": {
                "circuits": ["CIRC-99999"],
                "appointments": [],
                "invoices": [],
                "locations": [],
            },
            "confidence": 0.5,
            "rationale": "no match",
        },
    )
    out = run_enricher(state)
    assert any(label.startswith("asset_state[CIRC-99999]") for label in out.unavailable)


def test_enricher_node_returns_state_update() -> None:
    state = _state_with_triage()
    update = enricher_node(state)
    assert "enrichment" in update
    enrichment = update["enrichment"]
    assert enrichment["partner_profile"]["partner_id"] == "P-1001"


def test_allow_list_is_pinned_to_design_doc() -> None:
    """Lock the F2 tool surface to DESIGN.md §3 F2."""
    assert ALLOW_LIST.agent == "enricher"
    assert ALLOW_LIST.tools == frozenset(
        {
            "crm_lookup_partner",
            "inventory_lookup_circuit",
            "ticket_history_recent",
            "runbook_search",
        }
    )


def test_enricher_does_not_run_disallowed_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if someone hands the dispatcher a wider allow-list, the agent's pinned list governs."""

    state = _state_with_triage()

    seen_tools: list[str] = []
    from partner_ticket_agentic.tools.registry import ToolDispatcher

    real_call = ToolDispatcher.call

    def spy_call(self: ToolDispatcher, tool_name: str, **kwargs: Any) -> Any:
        seen_tools.append(tool_name)
        return real_call(self, tool_name, **kwargs)

    monkeypatch.setattr(ToolDispatcher, "call", spy_call)
    run_enricher(state)
    assert set(seen_tools).issubset(ALLOW_LIST.tools)
