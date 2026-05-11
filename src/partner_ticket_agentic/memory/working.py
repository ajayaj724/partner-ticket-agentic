"""LangGraph state object — the working-memory tier.

The state is the single dict-shaped value passed between nodes in the
LangGraph StateGraph. Each agent reads a few fields and writes its own
output slot; LangGraph handles the merge. All slots are optional because
agents run in different orders depending on routing (F7 in parallel with
F1, F6 conditional on triage category, etc.) and the state is built up
incrementally.

Per-agent output schemas live in their respective ``agents/<name>.py``
modules and are stored here as ``dict[str, Any]`` until they land —
keeping this module agnostic of the agent surface. Once all agents are
present, callers can interpret each slot via the agent's own model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TicketState(BaseModel):
    """LangGraph working-memory state for a single ticket flow."""

    model_config = ConfigDict(extra="forbid")

    # ---- inputs ------------------------------------------------------------
    ticket_id: str
    partner_id: str
    subject: str
    description: str
    submitted_at: str | None = None

    # ---- run metadata ------------------------------------------------------
    trace_id: str | None = None
    provider: str | None = None
    safety_findings: list[str] = Field(default_factory=list)

    # ---- per-agent output slots (populated as agents run) ------------------
    triage: dict[str, Any] | None = None
    enrichment: dict[str, Any] | None = None
    routing: dict[str, Any] | None = None
    knowledge: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    related: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None

    # ---- fallback markers --------------------------------------------------
    fell_back: list[str] = Field(default_factory=list)

    # ---- cost telemetry (slide 17 of the deck) -----------------------------
    # Populated by the graph after the pipeline runs. Same shape as
    # ``cost.CostLedger.summary()`` — kept dict-typed so this module stays
    # independent of the cost module's class.
    cost: dict[str, Any] | None = None

    @classmethod
    def from_ticket(cls, ticket: dict[str, Any]) -> TicketState:
        """Construct an initial state from a ticket dict (e.g., seed data)."""

        return cls(
            ticket_id=ticket["ticket_id"],
            partner_id=ticket["partner_id"],
            subject=ticket["subject"],
            description=ticket["description"],
            submitted_at=ticket.get("submitted_at"),
        )
