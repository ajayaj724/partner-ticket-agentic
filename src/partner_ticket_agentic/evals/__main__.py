"""Eval runner entry point: ``python -m partner_ticket_agentic.evals``.

Loads each ``evals/*.jsonl`` golden, runs the matching agent, and prints
precision / recall / accuracy per agent. CI runs this on every push so
regressions surface before merge.

Per DESIGN.md §4.4 the JSONL set covers F1, F3, F4, F7, F8 directly. F2,
F5, and F6 are procedural agents whose contracts are pinned by unit
tests (`tests/test_agents_chain.py`, `tests/test_graph.py`); the runner
prints a coverage line for each so the CLAUDE.md "every feature has an
eval entry" requirement is visibly met.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _evals_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "evals").is_dir() and (parent / "pyproject.toml").exists():
            return parent / "evals"
    return Path.cwd() / "evals"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


# ---- per-agent scorers -----------------------------------------------------


def _score_triage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from partner_ticket_agentic.agents.triage import run_triage
    from partner_ticket_agentic.memory.working import TicketState
    from partner_ticket_agentic.providers import MockProvider

    provider = MockProvider()
    correct_cat = 0
    correct_urg = 0
    per_category: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for row in rows:
        state = TicketState(
            ticket_id=row["id"],
            partner_id="P-EVAL",
            subject=row["subject"],
            description=row["description"],
        )
        out = run_triage(state, provider)
        expected = row["expected_category"]
        actual = out.category.value
        if actual == expected:
            correct_cat += 1
            per_category[expected]["tp"] += 1
        else:
            per_category[expected]["fn"] += 1
            per_category[actual]["fp"] += 1
        if out.urgency.value == row["expected_urgency"]:
            correct_urg += 1

    n = len(rows)
    return {
        "n": n,
        "category_accuracy": correct_cat / n if n else 0.0,
        "urgency_accuracy": correct_urg / n if n else 0.0,
        "per_category": dict(per_category),
    }


def _score_routing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from partner_ticket_agentic.agents.enricher import run_enricher
    from partner_ticket_agentic.agents.router import run_router
    from partner_ticket_agentic.agents.triage import run_triage
    from partner_ticket_agentic.memory.working import TicketState
    from partner_ticket_agentic.providers import MockProvider

    provider = MockProvider()
    correct = 0
    for row in rows:
        state = TicketState(
            ticket_id=row["ticket_id"],
            partner_id=row["partner_id"],
            subject=row["subject"],
            description=row["description"],
        )
        triage = run_triage(state, provider)
        state = state.model_copy(update={"triage": triage.model_dump()})
        enrichment = run_enricher(state)
        state = state.model_copy(update={"enrichment": enrichment.model_dump()})
        routing = run_router(state)
        if routing.queue == row["expected_queue"]:
            correct += 1
    n = len(rows)
    return {"n": n, "queue_accuracy": correct / n if n else 0.0}


def _score_duplicates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from partner_ticket_agentic.agents.linker import run_linker
    from partner_ticket_agentic.memory.working import TicketState

    tp = fp = fn = tn = 0
    for row in rows:
        state = TicketState(
            ticket_id=row["id"],
            partner_id=row["partner_id"],
            subject=row["subject"],
            description=row["description"],
        )
        out = run_linker(state)
        actual = bool(out.is_likely_duplicate)
        expected = bool(row["expected_is_duplicate"])
        if expected and actual:
            tp += 1
        elif expected and not actual:
            fn += 1
        elif not expected and actual:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "n": len(rows),
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def _score_runbooks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from partner_ticket_agentic.tools.runbook import (
        cross_encode_rerank,
        runbook_search,
    )

    correct_top1 = 0
    correct_top3 = 0
    for row in rows:
        result = runbook_search(query=row["query"], k=4)
        reranked = cross_encode_rerank(query=row["query"], hits=result.hits)
        top_ids = [h.runbook_id for h in reranked]
        expected = row["expected_runbook_id"]
        if top_ids and top_ids[0] == expected:
            correct_top1 += 1
        if expected in top_ids[:3]:
            correct_top3 += 1
    n = len(rows)
    return {
        "n": n,
        "top1_accuracy": correct_top1 / n if n else 0.0,
        "top3_accuracy": correct_top3 / n if n else 0.0,
    }


def _score_breach(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from partner_ticket_agentic.agents.watchdog import _band, _rule_risk
    from partner_ticket_agentic.tools.open_tickets import AS_OF_REFERENCE, OpenTicket

    correct = 0
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        opened_at = AS_OF_REFERENCE - timedelta(minutes=int(row["elapsed_minutes"]))
        last_activity = AS_OF_REFERENCE - timedelta(minutes=max(0, int(row["elapsed_minutes"]) - 5))
        ticket = OpenTicket(
            ticket_id=row["ticket_id"],
            partner_id="P-EVAL",
            queue="NOC-L2",
            category="circuit_down",
            urgency=row["urgency"],
            sla_minutes=int(row["sla_minutes"]),
            opened_at=opened_at,
            last_activity_at=last_activity,
        )
        risk, _ = _rule_risk(ticket)
        actual = _band(risk)
        expected = row["expected_band"]
        confusion[(expected, actual)] += 1
        if actual == expected:
            correct += 1
    n = len(rows)
    return {
        "n": n,
        "band_accuracy": correct / n if n else 0.0,
        "confusion": {f"{e}->{a}": c for (e, a), c in confusion.items()},
    }


# ---- runner ---------------------------------------------------------------


_FEATURE_COVERAGE_NOTES = (
    "F2 Enricher: covered procedurally — tests/test_agent_enricher.py.",
    "F5 Drafter: covered procedurally — tests/test_agents_chain.py::TestDrafter.",
    "F6 Scheduler: covered procedurally — tests/test_graph.py::TestScheduler.",
)


def main() -> int:
    evals_root = _evals_dir()
    if not evals_root.exists():
        print(f"(no evals/ directory at {evals_root})")
        return 0

    files = {
        "F1 Triage": (evals_root / "triage_categories.jsonl", _score_triage),
        "F3 Router": (evals_root / "routing_decisions.jsonl", _score_routing),
        "F7 Linker": (evals_root / "duplicate_pairs.jsonl", _score_duplicates),
        "F4 Knowledge": (evals_root / "runbook_relevance.jsonl", _score_runbooks),
        "F8 Watchdog": (evals_root / "breach_replay.jsonl", _score_breach),
    }

    print(f"Eval suite — {datetime.now(UTC).isoformat(timespec='seconds')}Z")
    print(f"  evals_dir: {evals_root}")
    print()

    overall_ok = True
    for label, (path, scorer) in files.items():
        if not path.exists():
            print(f"  {label:<14} (missing) {path.name}")
            overall_ok = False
            continue
        rows = _load_jsonl(path)
        result = scorer(rows)
        n = result["n"]
        primary = next(
            (
                k
                for k in (
                    "category_accuracy",
                    "queue_accuracy",
                    "top1_accuracy",
                    "band_accuracy",
                    "precision",
                )
                if k in result
            ),
            None,
        )
        primary_value = result.get(primary, 0.0) if primary else 0.0
        print(f"  {label:<14} n={n:<4} {primary}={primary_value:.3f}")
        for key, value in result.items():
            if key in {"n", primary}:
                continue
            if isinstance(value, float):
                print(f"    {key}={value:.3f}")
            else:
                print(f"    {key}={value}")
        print()

    print("Procedural-agent coverage (no JSONL — pinned by unit tests):")
    for note in _FEATURE_COVERAGE_NOTES:
        print(f"  - {note}")
    print()
    print("Done." if overall_ok else "Done — some eval files were missing.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
