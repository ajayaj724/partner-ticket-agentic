"""Three-tier memory subsystem for the agentic platform.

DESIGN.md §4.2 commits to a strict separation:

* **Working memory** is the LangGraph state object, scoped to one ticket
  flow. Defined in :mod:`partner_ticket_agentic.memory.working`.
* **Episodic memory** is per-partner, persisted across runs in SQLite at
  ``~/.ptag/episodic.db``. Defined in :mod:`partner_ticket_agentic.memory.episodic`.
* **Long-term memory** is organisational knowledge — a FAISS vector index
  over the runbook corpus plus structured facts in SQLite. Defined in
  :mod:`partner_ticket_agentic.memory.longterm`.

Never conflate them. Agents that reach across tiers (e.g., the Enricher
querying both episodic and long-term) hold one client per tier rather
than a god object.
"""

from __future__ import annotations

from partner_ticket_agentic.memory.episodic import EpisodicEntry, EpisodicStore
from partner_ticket_agentic.memory.longterm import LongTermMemory, RunbookHit
from partner_ticket_agentic.memory.working import TicketState

__all__ = [
    "EpisodicEntry",
    "EpisodicStore",
    "LongTermMemory",
    "RunbookHit",
    "TicketState",
]
