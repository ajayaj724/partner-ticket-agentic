"""Command-line interface for the partner-ticketing agentic platform.

Exposes the entry points described in ``docs/DESIGN.md`` Section 6 (Demo
Plan): ``--list``, ``--ticket-id``, ``--watchdog --once``, and
``--inject``. The provider is selectable at runtime via
``--llm-provider mock|anthropic|ollama``; the default is ``mock`` so the
demo runs offline with no API keys. ``--export-trace PATH`` dumps the
full structured trace for the run to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from partner_ticket_agentic import __version__
from partner_ticket_agentic.obs import new_trace_id, trace_collector
from partner_ticket_agentic.providers import make_provider
from partner_ticket_agentic.safety import SafetyError, assert_safe_input


def _project_root() -> Path:
    """Best-effort resolution of the repository root.

    The CLI is invoked from a checkout (``python -m partner_ticket_agentic``)
    rather than as an installed package in production, so we walk upwards
    from this file to find a directory that contains both ``data/`` and
    ``pyproject.toml``. Falls back to the current working directory.
    """

    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "data" / "sample_tickets.json").exists() and (
            parent / "pyproject.toml"
        ).exists():
            return parent
    return Path.cwd()


def _load_sample_tickets() -> list[dict[str, Any]]:
    path = _project_root() / "data" / "sample_tickets.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON array")
    return data


def _cmd_list(_: argparse.Namespace) -> int:
    tickets = _load_sample_tickets()
    if not tickets:
        print("(no sample tickets found in data/sample_tickets.json)", file=sys.stderr)
        return 1
    print(f"Found {len(tickets)} sample tickets:")
    print()
    for t in tickets:
        print(f"  {t['ticket_id']}  partner={t['partner_id']:<6}  {t['subject']}")
    print()
    print("Run a single ticket through the pipeline:")
    print("  python -m partner_ticket_agentic --ticket-id <id>")
    return 0


def _ticket_by_id(ticket_id: str) -> dict[str, Any] | None:
    for t in _load_sample_tickets():
        if t["ticket_id"] == ticket_id:
            return t
    return None


def _print_pipeline_summary(state: Any) -> None:
    """Pretty-print the terminal :class:`TicketState` for the demo."""

    triage = state.triage or {}
    enrichment = state.enrichment or {}
    routing = state.routing or {}
    knowledge = state.knowledge or {}
    related = state.related or {}
    schedule = state.schedule or {}
    draft = state.draft or {}
    profile = enrichment.get("partner_profile") or {}
    top_runbook = (knowledge.get("top_runbook") or {}) if knowledge else {}

    def line(label: str, value: Any) -> None:
        print(f"  {label:<22}{value}")

    print()
    print(f"== Pipeline result for {state.ticket_id} ==")
    line("provider", state.provider)
    line("trace_id", state.trace_id)
    print()
    print("F1 Triage:")
    line("category", triage.get("category"))
    line("urgency", triage.get("urgency"))
    line("confidence", triage.get("confidence"))
    line("entities.circuits", (triage.get("entities") or {}).get("circuits"))
    print()
    print("F2 Enrichment:")
    line("partner", f"{profile.get('name')} (tier={profile.get('tier')})" if profile else None)
    line("recent_tickets", len(enrichment.get("recent_tickets") or []))
    line("relevant_runbooks", len(enrichment.get("relevant_runbooks") or []))
    line("unavailable", enrichment.get("unavailable") or "-")
    print()
    print("F3 Routing:")
    line("queue", routing.get("queue"))
    line("assignee", (routing.get("assignee") or {}).get("user_id"))
    line("sla_minutes", routing.get("sla_minutes"))
    print()
    print("F4 Knowledge:")
    line("top_runbook", top_runbook.get("runbook_id") if top_runbook else "(none)")
    line("citation", knowledge.get("citation"))
    line("confidence", knowledge.get("confidence"))
    print()
    print("F7 Linker:")
    line("is_likely_duplicate", related.get("is_likely_duplicate"))
    line("related", len(related.get("related") or []))
    line("confidence", related.get("confidence"))
    print()
    if schedule and (schedule.get("proposed_slots") or []):
        print("F6 Scheduler:")
        for slot in (schedule.get("proposed_slots") or [])[:3]:
            line(
                slot.get("engineer_id", "?"), f"{slot.get('starts_at')} (score {slot.get('score')})"
            )
        print()
    print("F5 Drafter (HITL — requires_approval):")
    line("template_id", draft.get("template_id"))
    line("blocked", draft.get("blocked"))
    line("compliance_flags", draft.get("compliance_flags"))
    print()
    print("Subject:", draft.get("subject"))
    print("Body:")
    print((draft.get("body") or "").rstrip())
    print()


def _cmd_ticket(args: argparse.Namespace) -> int:
    ticket = _ticket_by_id(args.ticket_id)
    if ticket is None:
        print(
            f"unknown ticket-id {args.ticket_id!r}; run --list to see options",
            file=sys.stderr,
        )
        return 2
    try:
        assert_safe_input(ticket["description"])
    except SafetyError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 3

    from partner_ticket_agentic.graph import run_pipeline

    provider = make_provider(args.llm_provider)
    if args.export_trace:
        with trace_collector() as buf:
            state = run_pipeline(ticket, provider=provider, trace_id=new_trace_id())
        Path(args.export_trace).write_text(json.dumps(buf, indent=2, default=str))
        print(f"(trace written to {args.export_trace})", file=sys.stderr)
    else:
        state = run_pipeline(ticket, provider=provider, trace_id=new_trace_id())
    _print_pipeline_summary(state)
    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    """Demo run: try to submit a ticket whose description is a prompt-injection.

    Per DESIGN.md §6 demo run #5, this should be rejected at the safety
    boundary before reaching any agent. We exit non-zero on rejection so
    the panel sees the gate enforced visibly.
    """

    text = args.inject
    try:
        assert_safe_input(text)
    except SafetyError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 4
    print("(input passed the prompt-injection filter — would proceed to triage)")
    return 0


def _cmd_watchdog(args: argparse.Namespace) -> int:
    """Run one F8 Watchdog scan and print the report."""

    from partner_ticket_agentic.agents.watchdog import run_watchdog_once

    provider = make_provider(args.llm_provider)
    report = run_watchdog_once(provider=provider)
    print()
    print("== F8 Watchdog scan ==")
    print(f"  scanned        {report.scanned}")
    print(f"  at_risk        {len(report.at_risk)}")
    print(f"  notified       {report.notified}")
    print(f"  escalated      {report.escalated}")
    print(f"  deduplicated   {report.deduplicated}")
    print()
    if not report.at_risk:
        print("(no tickets at risk)")
        return 0
    print("At-risk tickets:")
    for item in report.at_risk:
        print(
            f"  {item.ticket_id:<12} {item.queue:<14} "
            f"elapsed={item.elapsed_minutes}m / sla={item.sla_minutes}m "
            f"risk={item.risk:.2f} band={item.risk_band:<4} "
            f"action={item.action_taken}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="partner-ticket-agentic",
        description=(
            "Reference agentic platform for partner-ticketing in a telecom "
            "context. See docs/DESIGN.md for the full specification."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--list", action="store_true", help="List sample tickets from data/sample_tickets.json."
    )
    actions.add_argument(
        "--ticket-id", metavar="ID", help="Run a single ticket through the agent pipeline."
    )
    actions.add_argument(
        "--watchdog", action="store_true", help="Run the F8 SLA Escalation Watchdog."
    )
    actions.add_argument(
        "--inject",
        metavar="TEXT",
        help="Submit a ticket containing the given text (used to demo the prompt-injection filter).",
    )

    parser.add_argument(
        "--llm-provider",
        choices=("mock", "anthropic", "ollama"),
        default="mock",
        help="LLM provider to use. Default: mock (deterministic, offline).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="With --watchdog, run a single scan and exit.",
    )
    parser.add_argument(
        "--export-trace",
        metavar="PATH",
        help="Write the trace for the run to PATH as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return _cmd_list(args)
    if args.ticket_id is not None:
        return _cmd_ticket(args)
    if args.watchdog:
        return _cmd_watchdog(args)
    if args.inject is not None:
        return _cmd_inject(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
