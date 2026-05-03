"""Runbook search tool — wraps the long-term-memory FAISS index.

Hybrid retrieval (BM25 + dense) is the production pattern from DESIGN.md
§3 F4. The demo uses dense-only retrieval over the deterministic
feature-hashed embeddings in :mod:`partner_ticket_agentic.memory.longterm`,
which keeps the demo offline and reproducible. The F4 Knowledge agent
optionally re-ranks via :func:`cross_encode_rerank` (also a deterministic
stand-in here).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.longterm import LongTermMemory, RunbookHit
from partner_ticket_agentic.tools.registry import register_tool


class RunbookSearchResult(BaseModel):
    """Wrapper so the registry returns a Pydantic model (not a dataclass list)."""

    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[RunbookHitModel] = Field(default_factory=list)


class RunbookHitModel(BaseModel):
    """Pydantic mirror of :class:`RunbookHit` — same fields, validated."""

    model_config = ConfigDict(extra="forbid")

    runbook_id: str
    title: str
    category: str
    score: float
    summary: str
    owner_queue: str
    sla_minutes: int

    @classmethod
    def from_hit(cls, hit: RunbookHit) -> RunbookHitModel:
        return cls(
            runbook_id=hit.runbook_id,
            title=hit.title,
            category=hit.category,
            score=hit.score,
            summary=hit.summary,
            owner_queue=hit.owner_queue,
            sla_minutes=hit.sla_minutes,
        )


# Forward-ref resolution — allows RunbookHitModel after RunbookSearchResult.
RunbookSearchResult.model_rebuild()


@lru_cache(maxsize=1)
def _ltm() -> LongTermMemory:
    return LongTermMemory.from_seed()


@register_tool(
    "runbook_search",
    description="Top-k runbook matches for a query against the long-term FAISS index.",
)
def runbook_search(*, query: str, k: int = 3) -> RunbookSearchResult:
    """Return the top-``k`` runbooks whose vector is closest to ``query``."""

    hits = _ltm().search(query, k=k)
    return RunbookSearchResult(
        query=query,
        hits=[RunbookHitModel.from_hit(h) for h in hits],
    )


@register_tool(
    "cross_encode_rerank",
    description="Deterministic stand-in for a cross-encoder reranker over hits.",
)
def cross_encode_rerank(*, query: str, hits: list[RunbookHitModel]) -> list[RunbookHitModel]:
    """Boost hits whose category-or-title shares a token with the query.

    This is a deterministic stand-in for a real cross-encoder (e.g.,
    bge-reranker, ColBERT). The point is not to be a great reranker but
    to demonstrate the F4 architecture: retrieve, then rerank, with a
    surface that swaps cleanly in production.
    """

    if not hits:
        return hits
    query_tokens = {tok.lower() for tok in query.split()}

    def boost(h: RunbookHitModel) -> float:
        title_tokens = {tok.lower() for tok in h.title.split()}
        category_tokens = {tok.lower() for tok in h.category.replace("_", " ").split()}
        overlap = len(query_tokens & (title_tokens | category_tokens))
        return h.score + 0.1 * overlap

    return sorted(hits, key=boost, reverse=True)
