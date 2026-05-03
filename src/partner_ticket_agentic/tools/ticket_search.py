"""Vector-similarity search over a partner's recent tickets — F7 Linker.

Demo implementation: index the seeded ticket history per partner using the
same deterministic FNV-1a feature-hashed embedder the long-term runbook
index uses. Production swaps in a real ticket store + dense embedder
without changing the function signature.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.memory.longterm import EMBED_DIM, embed_text
from partner_ticket_agentic.tools.registry import ToolError, register_tool
from partner_ticket_agentic.tools.ticket_history import _SEED_HISTORY


class TicketHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    partner_id: str
    category: str
    summary: str
    similarity: float = Field(ge=-1.0, le=1.0)
    status: Literal["open", "investigating", "resolved", "closed"]


class TicketSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[TicketHit] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple[TicketHit, np.ndarray]]]:
    """Per-partner index keyed by partner_id."""

    out: dict[str, list[tuple[TicketHit, np.ndarray]]] = {}
    for partner_id, summaries in _SEED_HISTORY.items():
        rows: list[tuple[TicketHit, np.ndarray]] = []
        for s in summaries:
            text = f"{s.category} {s.summary}"
            vec = embed_text(text, EMBED_DIM).astype(np.float32)
            hit = TicketHit(
                ticket_id=s.ticket_id,
                partner_id=partner_id,
                category=s.category,
                summary=s.summary,
                similarity=0.0,
                status="resolved" if s.resolved else "open",
            )
            rows.append((hit, vec))
        out[partner_id] = rows
    return out


@register_tool(
    "ticket_search_recent",
    description="Vector similarity over a partner's recent tickets — F7 Linker tool.",
)
def ticket_search_recent(*, partner_id: str, query: str, k: int = 3) -> TicketSearchResult:
    rows = _index().get(partner_id, [])
    if not rows:
        return TicketSearchResult(query=query, hits=[])
    q = embed_text(query, EMBED_DIM).astype(np.float32)
    scored = []
    for hit, vec in rows:
        sim = float(np.dot(q, vec))
        scored.append(
            TicketHit(
                ticket_id=hit.ticket_id,
                partner_id=hit.partner_id,
                category=hit.category,
                summary=hit.summary,
                similarity=sim,
                status=hit.status,
            )
        )
    scored.sort(key=lambda h: h.similarity, reverse=True)
    return TicketSearchResult(query=query, hits=scored[:k])


@register_tool(
    "ticket_status_lookup",
    description="Return the current status of a ticket by ID. Tenant-scoped.",
)
def ticket_status_lookup(*, ticket_id: str, partner_id: str) -> TicketHit:
    for rows in _index().values():
        for hit, _ in rows:
            if hit.ticket_id == ticket_id and hit.partner_id == partner_id:
                return hit
    raise ToolError(f"ticket {ticket_id!r} for partner {partner_id!r} not found")
