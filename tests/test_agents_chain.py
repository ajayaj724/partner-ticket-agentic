"""Tests for F3 Router, F4 Knowledge, and F5 Drafter — the rest of the chain."""

from __future__ import annotations

from typing import Any

import pytest

from partner_ticket_agentic.agents.drafter import (
    ALLOW_LIST as DRAFTER_ALLOW,
)
from partner_ticket_agentic.agents.drafter import (
    run_drafter,
)
from partner_ticket_agentic.agents.enricher import run_enricher
from partner_ticket_agentic.agents.knowledge import (
    ALLOW_LIST as KNOWLEDGE_ALLOW,
)
from partner_ticket_agentic.agents.knowledge import (
    run_knowledge,
)
from partner_ticket_agentic.agents.router import (
    ALLOW_LIST as ROUTER_ALLOW,
)
from partner_ticket_agentic.agents.router import (
    run_router,
)
from partner_ticket_agentic.agents.triage import run_triage
from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.providers import MockProvider


def _state_through_enricher(**overrides: Any) -> TicketState:
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
    state = state.model_copy(update={"triage": triage.model_dump()})
    enrichment = run_enricher(state)
    state = state.model_copy(update={"enrichment": enrichment.model_dump()})
    return state


# ---- F3 Router --------------------------------------------------------------


class TestRouter:
    def test_circuit_outage_routes_to_noc_l2(self) -> None:
        state = _state_through_enricher()
        out = run_router(state)
        assert out.queue == "NOC-L2"
        assert out.assignee.queue == "NOC-L2"
        assert out.sla_minutes == 30  # gold tier critical
        assert out.confidence >= 0.8

    def test_appointment_routes_to_dispatch(self) -> None:
        state = _state_through_enricher(
            ticket_id="T-200",
            partner_id="P-1002",
            subject="Reschedule appointment APT-20460",
            description="Move APT-20460 from Tuesday afternoon to Thursday morning.",
        )
        out = run_router(state)
        assert out.queue == "DISPATCH"

    def test_billing_routes_to_fin_ops(self) -> None:
        state = _state_through_enricher(
            ticket_id="T-300",
            partner_id="P-1003",
            subject="Invoice INV-2026-0412 looks wrong",
            description="April invoice INV-2026-0412 shows EUR 1,240 but contract rate is 980.",
        )
        out = run_router(state)
        assert out.queue == "FIN-OPS"

    def test_low_triage_confidence_routes_to_review(self) -> None:
        state = TicketState(
            ticket_id="T-X",
            partner_id="P-1001",
            subject="?",
            description="The thing isn't doing the thing.",
            triage={
                "category": "other",
                "urgency": "low",
                "entities": {"circuits": [], "appointments": [], "invoices": [], "locations": []},
                "confidence": 0.5,
                "rationale": "no match",
            },
            enrichment={
                "partner_profile": {
                    "partner_id": "P-1001",
                    "name": "BrusselsNet BV",
                    "tier": "gold",
                    "primary_contact": "ops@brusselsnet.example.be",
                    "primary_phone": "+32 2 555 0101",
                    "active_circuits": [],
                },
                "asset_state": [],
                "recent_tickets": [],
                "relevant_runbooks": [],
                "unavailable": [],
            },
        )
        out = run_router(state)
        assert out.queue == "REVIEW"

    def test_allow_list_pinned(self) -> None:
        assert ROUTER_ALLOW.tools == frozenset(
            {"directory_resolve_assignee", "queue_workload_snapshot", "sla_policy_for_partner"}
        )


# ---- F4 Knowledge ------------------------------------------------------------


class TestKnowledge:
    def test_circuit_outage_returns_circuit_runbook(self) -> None:
        state = _state_through_enricher()
        out = run_knowledge(state)
        assert out.top_runbook is not None
        assert out.top_runbook.category == "circuit_down"
        assert out.suggested_steps, "expected suggested steps for circuit_down"
        assert out.citation == out.top_runbook.runbook_id
        assert out.confidence > 0.0

    def test_billing_returns_billing_runbook(self) -> None:
        state = _state_through_enricher(
            ticket_id="T-301",
            partner_id="P-1003",
            subject="Invoice INV-2026-0412 looks wrong",
            description="April invoice INV-2026-0412 shows EUR 1,240 but contract rate is 980.",
        )
        out = run_knowledge(state)
        assert out.top_runbook is not None
        assert out.top_runbook.category == "billing_discrepancy"

    def test_low_score_falls_back_to_no_high_confidence(self) -> None:
        state = _state_through_enricher(
            ticket_id="T-Z",
            partner_id="P-1001",
            subject="zzz",
            description="zzzz zzz zz zzz",
        )
        out = run_knowledge(state)
        # Either no steps (below threshold) or a fallback_reason set.
        if out.fallback_reason is None:
            assert out.suggested_steps  # high-confidence path
        else:
            assert out.suggested_steps == []

    def test_allow_list_pinned(self) -> None:
        assert KNOWLEDGE_ALLOW.tools == frozenset({"runbook_search", "cross_encode_rerank"})


# ---- F5 Drafter --------------------------------------------------------------


class TestDrafter:
    def _state_through_chain(self) -> TicketState:
        state = _state_through_enricher()
        routing = run_router(state)
        return state.model_copy(update={"routing": routing.model_dump()})

    def test_draft_includes_circuit_id_and_partner_name(self) -> None:
        state = self._state_through_chain()
        out = run_drafter(state)
        assert "CIRC-44781" in out.subject + out.body
        assert "BrusselsNet BV" in out.body
        assert out.requires_approval is True
        assert out.blocked is False

    def test_appointment_draft_uses_appointment_template(self) -> None:
        state = TicketState(
            ticket_id="T-200",
            partner_id="P-1002",
            subject="Reschedule appointment APT-20460",
            description="Move APT-20460 from Tuesday afternoon to Thursday morning.",
        )
        triage = run_triage(state, MockProvider())
        state = state.model_copy(update={"triage": triage.model_dump()})
        enrichment = run_enricher(state)
        state = state.model_copy(update={"enrichment": enrichment.model_dump()})
        routing = run_router(state)
        state = state.model_copy(update={"routing": routing.model_dump()})
        out = run_drafter(state)
        assert out.template_id == "TPL-003"
        assert "APT-20460" in out.subject + out.body

    def test_compliance_filter_blocks_pii_in_template_context(self) -> None:
        # Inject a forbidden phrase via partner name to simulate a PII leak.
        state = self._state_through_chain()
        bad_enrichment = state.enrichment.copy()  # type: ignore[union-attr]
        bad_enrichment["partner_profile"] = dict(bad_enrichment["partner_profile"])  # type: ignore[arg-type]
        bad_enrichment["partner_profile"]["name"] = "ACME password: hunter2"
        state = state.model_copy(update={"enrichment": bad_enrichment})
        out = run_drafter(state)
        assert out.blocked is True
        assert any(flag.startswith("pii:") for flag in out.compliance_flags)
        assert out.rationale.startswith("BLOCKED:")

    def test_drafter_always_requires_approval(self) -> None:
        state = self._state_through_chain()
        out = run_drafter(state)
        assert out.requires_approval is True

    def test_allow_list_pinned(self) -> None:
        assert DRAFTER_ALLOW.tools == frozenset({"template_lookup", "compliance_filter"})


# ---- Cross-agent contract ---------------------------------------------------


def test_full_chain_produces_full_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state_through_enricher()
    routing = run_router(state)
    state = state.model_copy(update={"routing": routing.model_dump()})
    knowledge = run_knowledge(state)
    state = state.model_copy(update={"knowledge": knowledge.model_dump()})
    draft = run_drafter(state)
    state = state.model_copy(update={"draft": draft.model_dump()})

    assert state.triage and state.triage["category"] == "circuit_down"
    assert state.enrichment and state.enrichment["partner_profile"]["partner_id"] == "P-1001"
    assert state.routing and state.routing["queue"] == "NOC-L2"
    assert state.knowledge and state.knowledge["top_runbook"]["category"] == "circuit_down"
    assert state.draft and state.draft["template_id"] == "TPL-001"
