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
