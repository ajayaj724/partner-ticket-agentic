"""Tests for the seed-backed tools (CRM, inventory, ticket history, runbook search)."""

from __future__ import annotations

import pytest

from partner_ticket_agentic.tools.crm import Partner, crm_lookup_partner
from partner_ticket_agentic.tools.inventory import CircuitInfo, inventory_lookup_circuit
from partner_ticket_agentic.tools.registry import ToolError
from partner_ticket_agentic.tools.runbook import (
    RunbookHitModel,
    cross_encode_rerank,
    runbook_search,
)
from partner_ticket_agentic.tools.ticket_history import ticket_history_recent


class TestCrm:
    def test_lookup_known_partner(self) -> None:
        partner = crm_lookup_partner(partner_id="P-1001")
        assert isinstance(partner, Partner)
        assert partner.name == "BrusselsNet BV"
        assert partner.tier == "gold"
        assert "CIRC-44781" in partner.active_circuits

    def test_lookup_unknown_partner_raises(self) -> None:
        with pytest.raises(ToolError):
            crm_lookup_partner(partner_id="P-9999")


class TestInventory:
    def test_lookup_known_circuit(self) -> None:
        info = inventory_lookup_circuit(circuit_id="CIRC-44781")
        assert isinstance(info, CircuitInfo)
        assert info.owning_partner_id == "P-1001"
        assert info.status == "down"

    def test_lookup_degraded_circuit(self) -> None:
        info = inventory_lookup_circuit(circuit_id="CIRC-44782")
        assert info.status == "degraded"

    def test_lookup_unknown_circuit_raises(self) -> None:
        with pytest.raises(ToolError):
            inventory_lookup_circuit(circuit_id="CIRC-NONE")


class TestTicketHistory:
    def test_known_partner_returns_history(self) -> None:
        rows = ticket_history_recent(partner_id="P-1001", limit=5)
        assert len(rows) >= 1
        assert all(t.resolved for t in rows)

    def test_unknown_partner_returns_empty(self) -> None:
        rows = ticket_history_recent(partner_id="P-NEW")
        assert rows == []

    def test_limit_is_respected(self) -> None:
        rows = ticket_history_recent(partner_id="P-1001", limit=1)
        assert len(rows) == 1


class TestRunbookSearch:
    def test_circuit_outage_query(self) -> None:
        result = runbook_search(query="circuit outage from partner", k=3)
        assert result.query == "circuit outage from partner"
        assert result.hits, "expected at least one runbook hit"
        assert isinstance(result.hits[0], RunbookHitModel)
        assert result.hits[0].category == "circuit_down"

    def test_empty_query_returns_zeros(self) -> None:
        result = runbook_search(query="", k=3)
        assert result.hits  # FAISS still returns hits, just with low scores

    def test_rerank_promotes_query_aligned_categories(self) -> None:
        hits = runbook_search(query="invoice mismatch billing", k=4).hits
        reranked = cross_encode_rerank(query="invoice mismatch billing", hits=hits)
        # billing_discrepancy should rerank above the others.
        assert reranked[0].category == "billing_discrepancy"
