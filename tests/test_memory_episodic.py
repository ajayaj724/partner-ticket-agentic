"""Tests for the SQLite-backed episodic-memory tier."""

from __future__ import annotations

from partner_ticket_agentic.memory import EpisodicStore


def test_record_and_recent_round_trip() -> None:
    store = EpisodicStore(":memory:")
    store.record(
        partner_id="P-1001",
        ticket_id="T-A",
        category="circuit_down",
        urgency="critical",
        summary="CIRC-44781 outage; resolved in 47m",
    )
    store.record(
        partner_id="P-1001",
        ticket_id="T-B",
        category="performance_degradation",
        urgency="high",
        summary="2% packet loss; cleared after carrier reroute",
    )
    store.record(
        partner_id="P-1002",
        ticket_id="T-C",
        category="appointment_request",
        urgency="medium",
        summary="rescheduled to next week",
    )

    p1001 = store.recent("P-1001")
    assert [e.ticket_id for e in p1001] == ["T-B", "T-A"]
    assert all(e.partner_id == "P-1001" for e in p1001)

    assert store.count("P-1001") == 2
    assert store.count("P-1002") == 1
    assert store.count() == 3


def test_recent_respects_limit() -> None:
    store = EpisodicStore(":memory:")
    for i in range(5):
        store.record(
            partner_id="P-X",
            ticket_id=f"T-{i}",
            category="general_query",
            summary=f"summary {i}",
        )
    recent = store.recent("P-X", limit=3)
    assert [e.ticket_id for e in recent] == ["T-4", "T-3", "T-2"]


def test_recent_for_unknown_partner_is_empty() -> None:
    store = EpisodicStore(":memory:")
    assert store.recent("P-NONE") == []
