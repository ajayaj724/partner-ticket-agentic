"""Command-line interface for the partner-ticketing agentic platform.

Exposes the entry points described in ``docs/DESIGN.md`` Section 6 (Demo
Plan). For the scaffold milestone the CLI implements ``--list`` end-to-end
(reading the seed data under ``data/sample_tickets.json``) and registers
placeholder handlers for ``--ticket-id``, ``--watchdog``, ``--inject``, and
``--llm-provider`` that subsequent commits replace with real implementations.
Keeping the surface stable from the start means the demo invocations in the
README and the design doc remain valid as features land.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from partner_ticket_agentic import __version__


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


def _cmd_not_yet_implemented(name: str) -> int:
    print(
        f"[scaffold] '{name}' is not yet implemented in this commit; "
        "subsequent commits add the request/response pipeline (F1-F5), "
        "F6 Scheduler, F7 Linker, and F8 Watchdog.",
        file=sys.stderr,
    )
    return 2


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
        return _cmd_not_yet_implemented(f"--ticket-id {args.ticket_id}")
    if args.watchdog:
        return _cmd_not_yet_implemented("--watchdog")
    if args.inject is not None:
        return _cmd_not_yet_implemented("--inject")

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
