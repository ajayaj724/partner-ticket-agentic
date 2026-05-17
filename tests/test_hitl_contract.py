"""HITL (Human-in-the-Loop) gate contract — load-bearing invariant.

The F5 Drafter is the only path to partner-facing egress. Its
``requires_approval=True`` flag is the primary technical control behind
the EU AI Act *limited risk* classification documented in
``docs/AI_ACT_ASSESSMENT.md``. Flipping it to ``False`` without re-running
the AI-Act assessment moves the system up a risk tier silently.

This file pins the invariant from two directions:

* **Behavioural** — run the drafter on diverse ticket inputs and assert
  every output has ``requires_approval=True``. Catches regressions in
  the agent's construction logic.
* **Source-level** — grep the drafter's source for the literal
  ``requires_approval=False``. Catches regressions in code review where
  the constructor literal at line 97 is mutated.

Either test failing is a release-blocker. Do not silence them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from partner_ticket_agentic.agents.drafter import DrafterOutput, run_drafter
from partner_ticket_agentic.agents.enricher import run_enricher
from partner_ticket_agentic.agents.router import run_router
from partner_ticket_agentic.agents.triage import run_triage
from partner_ticket_agentic.memory.working import TicketState
from partner_ticket_agentic.providers import MockProvider

_DIVERSE_TICKETS: list[dict[str, str]] = [
    {
        "ticket_id": "HITL-001",
        "partner_id": "P-BRU-01",
        "subject": "Circuit CIRC-44781 down since 09:14",
        "description": "Monitoring shows CIRC-44781 unreachable from 09:14 CET.",
    },
    {
        "ticket_id": "HITL-002",
        "partner_id": "P-ANT-02",
        "subject": "Reschedule field appointment APT-20460",
        "description": "Please move APT-20460 from Tuesday afternoon to Thursday morning.",
    },
    {
        "ticket_id": "HITL-003",
        "partner_id": "P-GHE-03",
        "subject": "Invoice INV-9912 dispute",
        "description": "We dispute invoice INV-9912 — line item 4 charged twice.",
    },
    {
        "ticket_id": "HITL-004",
        "partner_id": "P-LIE-04",
        "subject": "General enquiry — partner-portal access",
        "description": "Could you walk us through the new partner-portal onboarding?",
    },
    {
        "ticket_id": "HITL-005",
        "partner_id": "P-BRU-05",
        "subject": "CIRC-99921 intermittent drops",
        "description": "Circuit CIRC-99921 is dropping every ~30 minutes since this morning.",
    },
]


def _state_through_chain(ticket: dict[str, str]) -> TicketState:
    state = TicketState(
        ticket_id=ticket["ticket_id"],
        partner_id=ticket["partner_id"],
        subject=ticket["subject"],
        description=ticket["description"],
    )
    provider = MockProvider()
    triage = run_triage(state, provider)
    state = state.model_copy(update={"triage": triage.model_dump()})
    enrichment = run_enricher(state)
    state = state.model_copy(update={"enrichment": enrichment.model_dump()})
    routing = run_router(state)
    state = state.model_copy(update={"routing": routing.model_dump()})
    return state


@pytest.mark.parametrize("ticket", _DIVERSE_TICKETS, ids=[t["ticket_id"] for t in _DIVERSE_TICKETS])
def test_every_drafter_output_requires_approval(ticket: dict[str, str]) -> None:
    """Behavioural contract: every DrafterOutput must require approval.

    Holding this true across the full ticket-category surface (circuit,
    appointment, billing, enquiry, intermittent) makes silent regression
    by category-specific code-path practically impossible.
    """

    state = _state_through_chain(ticket)
    out: DrafterOutput = run_drafter(state)
    assert out.requires_approval is True, (
        f"HITL gate broken for ticket {ticket['ticket_id']} — "
        f"requires_approval={out.requires_approval}. This flips the "
        f"EU AI Act classification. Do not merge."
    )


def test_drafter_source_contains_no_requires_approval_false() -> None:
    """Source-level contract: the literal `requires_approval=False` must
    never appear in the drafter agent. This catches a copy-paste mistake
    that would defeat the behavioural test for an unsampled category.
    """

    here = Path(__file__).resolve()
    root = next(p for p in (here, *here.parents) if (p / "pyproject.toml").exists())
    drafter_src = (root / "src" / "partner_ticket_agentic" / "agents" / "drafter.py").read_text()
    # Whitespace-tolerant: `requires_approval = False`, `requires_approval=False`.
    pattern = re.compile(r"requires_approval\s*=\s*False")
    assert not pattern.search(drafter_src), (
        "Drafter source contains `requires_approval=False` literal. "
        "This flips the EU AI Act limited-risk classification. "
        "Re-run docs/AI_ACT_ASSESSMENT.md before merging."
    )


def test_drafter_output_schema_default_is_true() -> None:
    """Schema contract: the Pydantic default for requires_approval must
    be True. A `DrafterOutput()` constructed without specifying the field
    inherits the safe default.
    """

    field = DrafterOutput.model_fields["requires_approval"]
    assert field.default is True, (
        "DrafterOutput.requires_approval default is not True. "
        "An agent constructing a partial draft would now skip the HITL gate."
    )
