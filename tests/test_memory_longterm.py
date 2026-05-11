"""Tests for the long-term memory tier (FAISS over runbooks).

Verifies the deterministic embedder and the retrieval contract: same
input always yields same vector; the index returns the runbook whose
text actually matches the query above unrelated runbooks.
"""

from __future__ import annotations

import numpy as np

from partner_ticket_agentic.memory.longterm import (
    EMBED_DIM,
    LongTermMemory,
    embed_text,
)


def test_embedder_is_deterministic() -> None:
    a = embed_text("circuit CIRC-44781 outage")
    b = embed_text("circuit CIRC-44781 outage")
    assert a.shape == (EMBED_DIM,)
    assert np.allclose(a, b)


def test_embedder_is_unit_norm_for_nonempty_text() -> None:
    v = embed_text("partner circuit down")
    assert np.linalg.norm(v) == 1.0 or np.isclose(np.linalg.norm(v), 1.0)


def test_embedder_returns_zero_vector_for_empty_text() -> None:
    v = embed_text("")
    assert np.allclose(v, np.zeros(EMBED_DIM))


def test_long_term_memory_retrieves_relevant_runbook_first() -> None:
    ltm = LongTermMemory.from_seed()
    hits = ltm.search("circuit outage from partner", k=3)
    assert hits, "expected at least one hit"
    # The runbook seeded under category 'circuit_down' should be the top match.
    assert hits[0].category == "circuit_down"
    # All hits cite a runbook id and have a score in [-1, 1].
    for hit in hits:
        assert hit.runbook_id.startswith("RB-")
        assert -1.0 <= hit.score <= 1.0


def test_long_term_memory_clamps_k_to_corpus_size() -> None:
    ltm = LongTermMemory.from_seed()
    hits = ltm.search("invoice", k=999)
    assert len(hits) <= 8  # we have 8 seed runbooks
    assert all(h.runbook_id for h in hits)


def test_long_term_memory_billing_query_finds_billing_runbook() -> None:
    ltm = LongTermMemory.from_seed()
    hits = ltm.search("invoice INV-2026-0412 mismatch with contract rate", k=3)
    top_categories = [h.category for h in hits]
    assert "billing_discrepancy" in top_categories


def test_bm25_mode_returns_lexical_match() -> None:
    """BM25 should rank exact-term matches highest."""

    ltm = LongTermMemory.from_seed()
    hits = ltm.search("appointment reschedule request", k=3, mode="bm25")
    # The runbook explicitly titled "Appointment reschedule request" should
    # win the lexical match.
    assert hits[0].category == "appointment_change"
    # BM25 scores are non-negative and unbounded above (not in [-1, 1]).
    assert hits[0].score >= 0.0


def test_hybrid_mode_combines_signals() -> None:
    """Hybrid mode should produce a blended score that respects both signals."""

    ltm = LongTermMemory.from_seed()
    hybrid = ltm.search("circuit CIRC-44781 outage", k=3, mode="hybrid")
    dense = ltm.search("circuit CIRC-44781 outage", k=3, mode="dense")
    bm25 = ltm.search("circuit CIRC-44781 outage", k=3, mode="bm25")
    # All three should find the circuit-down runbook in the top-3.
    for results in (hybrid, dense, bm25):
        cats = [h.category for h in results]
        assert "circuit_down" in cats
    # Hybrid scores are in [0, 1] after min-max normalisation.
    for h in hybrid:
        assert 0.0 <= h.score <= 1.0


def test_alpha_one_matches_dense_ranking() -> None:
    """alpha=1.0 should produce the same ranking as mode='dense'."""

    ltm = LongTermMemory.from_seed()
    dense = ltm.search("circuit outage", k=4, mode="dense")
    blended = ltm.search("circuit outage", k=4, mode="hybrid", alpha=1.0)
    assert [h.runbook_id for h in dense] == [h.runbook_id for h in blended]


def test_alpha_zero_matches_bm25_ranking() -> None:
    """alpha=0.0 should produce the same ranking as mode='bm25'."""

    ltm = LongTermMemory.from_seed()
    bm25 = ltm.search("invoice mismatch", k=4, mode="bm25")
    blended = ltm.search("invoice mismatch", k=4, mode="hybrid", alpha=0.0)
    assert [h.runbook_id for h in bm25] == [h.runbook_id for h in blended]
