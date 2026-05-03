"""Agent surface for the partner-ticketing platform.

Each agent module owns one feature from the catalogue (F1-F8) and exposes:

* A Pydantic *output* schema (e.g., :class:`TriageOutput`) — the validated
  JSON contract the agent emits.
* A *node function* suitable for use as a LangGraph node — takes
  :class:`~partner_ticket_agentic.memory.working.TicketState` and returns a
  partial state update.
* A registered mock rule (via :class:`MockProvider.register`) so the
  default offline path is deterministic and reviewer-readable.

Importing an agent module is the registration trigger for its mock rule.
The package's ``__init__`` imports each agent so a plain ``import
partner_ticket_agentic.agents`` is enough to wire every rule.
"""

from __future__ import annotations

from partner_ticket_agentic.agents.triage import (
    TriageOutput,
    run_triage,
    triage_node,
)

__all__ = [
    "TriageOutput",
    "run_triage",
    "triage_node",
]
