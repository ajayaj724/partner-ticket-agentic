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

from partner_ticket_agentic.agents.drafter import (
    DrafterOutput,
    drafter_node,
    run_drafter,
)
from partner_ticket_agentic.agents.enricher import (
    EnrichmentOutput,
    enricher_node,
    run_enricher,
)
from partner_ticket_agentic.agents.knowledge import (
    KnowledgeOutput,
    knowledge_node,
    run_knowledge,
)
from partner_ticket_agentic.agents.linker import (
    LinkerOutput,
    linker_node,
    run_linker,
)
from partner_ticket_agentic.agents.router import (
    RoutingOutput,
    router_node,
    run_router,
)
from partner_ticket_agentic.agents.scheduler import (
    SchedulerOutput,
    run_scheduler,
    scheduler_node,
)
from partner_ticket_agentic.agents.triage import (
    TriageOutput,
    run_triage,
    triage_node,
)
from partner_ticket_agentic.agents.watchdog import (
    AtRiskItem,
    WatchdogReport,
    run_watchdog_once,
)

__all__ = [
    "AtRiskItem",
    "DrafterOutput",
    "EnrichmentOutput",
    "KnowledgeOutput",
    "LinkerOutput",
    "RoutingOutput",
    "SchedulerOutput",
    "TriageOutput",
    "WatchdogReport",
    "drafter_node",
    "enricher_node",
    "knowledge_node",
    "linker_node",
    "router_node",
    "run_drafter",
    "run_enricher",
    "run_knowledge",
    "run_linker",
    "run_router",
    "run_scheduler",
    "run_triage",
    "run_watchdog_once",
    "scheduler_node",
    "triage_node",
]
