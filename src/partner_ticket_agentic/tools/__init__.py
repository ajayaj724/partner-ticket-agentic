"""Tool surface for the agentic platform.

Each tool module owns one capability (CRM lookup, inventory lookup, ticket
history, runbook search, ...) with a typed signature. Tools register
themselves with the global :class:`ToolRegistry` at import time. Agents
dispatch via a :class:`ToolDispatcher` constructed with their per-agent
:class:`~partner_ticket_agentic.safety.ToolAllowList`, so an agent that
attempts a tool outside its allow-list raises
:class:`~partner_ticket_agentic.safety.ToolNotAllowedError` rather than
silently succeeding (DESIGN.md §2 "Tool allow-listing per agent").

Importing this package eagerly imports every tool module so their
registration side effects run before any agent dispatches.
"""

from __future__ import annotations

# Tool-implementation modules — import for the registration side effect.
from partner_ticket_agentic.tools import (  # noqa: F401
    calendar,
    crm,
    directory,
    inventory,
    runbook,
    templates,
    ticket_history,
    ticket_search,
)
from partner_ticket_agentic.tools.registry import (
    Tool,
    ToolDispatcher,
    ToolRegistry,
    register_tool,
)

__all__ = [
    "Tool",
    "ToolDispatcher",
    "ToolRegistry",
    "register_tool",
]
