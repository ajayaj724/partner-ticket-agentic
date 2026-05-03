"""FastAPI app for the web UI.

Endpoints:

* ``GET /``                — serves the single-page UI.
* ``GET /api/tickets``     — list of seeded sample tickets.
* ``GET /api/run/{id}``    — run one ticket through the full pipeline; returns
  ``{state, trace}`` as JSON. Synchronous; the pipeline finishes in well under
  a second on the mock provider, so the frontend animates the trace events
  client-side rather than streaming.
* ``GET /api/watchdog``    — run one F8 scan; returns the WatchdogReport.
* ``POST /api/inject``     — exercise the prompt-injection filter.

The app binds to ``127.0.0.1`` only (loopback). It's a localhost demo, not a
deployable web service — DESIGN.md §6 demo scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from partner_ticket_agentic.agents.watchdog import run_watchdog_once
from partner_ticket_agentic.graph import run_pipeline
from partner_ticket_agentic.obs import new_trace_id, trace_collector
from partner_ticket_agentic.providers import make_provider
from partner_ticket_agentic.safety import (
    SafetyError,
    assert_safe_input,
    detect_prompt_injection,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_sample_tickets() -> list[dict[str, Any]]:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "data" / "sample_tickets.json"
        if candidate.exists():
            with candidate.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    return []


app = FastAPI(title="Partner-Ticketing Agentic Platform — Web UI")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/api/tickets")
def list_tickets() -> JSONResponse:
    return JSONResponse(_load_sample_tickets())


@app.get("/api/run/{ticket_id}")
def run_ticket(ticket_id: str, provider: str = "mock") -> JSONResponse:
    tickets = _load_sample_tickets()
    ticket = next((t for t in tickets if t["ticket_id"] == ticket_id), None)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id!r}")

    try:
        assert_safe_input(ticket["description"])
    except SafetyError as exc:
        return JSONResponse({"rejected": True, "reason": str(exc)}, status_code=400)

    if provider not in {"mock", "anthropic", "ollama"}:
        raise HTTPException(status_code=400, detail=f"unknown provider {provider!r}")

    selected = make_provider(provider)
    with trace_collector() as buf:
        state = run_pipeline(ticket, provider=selected, trace_id=new_trace_id())

    return JSONResponse(
        {
            "ticket": ticket,
            # mode="json" coerces datetimes (F6 slot times) and other
            # non-JSON-native types into their JSON representations.
            "state": state.model_dump(mode="json"),
            "trace": buf,
            "provider_resolved": selected.name,
        }
    )


@app.get("/api/watchdog")
def watchdog(provider: str = "mock") -> JSONResponse:
    if provider not in {"mock", "anthropic", "ollama"}:
        raise HTTPException(status_code=400, detail=f"unknown provider {provider!r}")
    selected = make_provider(provider)
    report = run_watchdog_once(provider=selected)
    return JSONResponse(report.model_dump(mode="json"))


class InjectRequest(BaseModel):
    text: str


@app.post("/api/inject")
def inject(req: InjectRequest) -> JSONResponse:
    findings = detect_prompt_injection(req.text)
    if not findings:
        return JSONResponse({"rejected": False, "matches": []})
    return JSONResponse(
        {
            "rejected": True,
            "matches": [{"pattern": f.pattern, "match": f.match} for f in findings],
        }
    )


@app.get("/api/topology")
def topology() -> JSONResponse:
    """Return the static graph topology so the frontend can render the DAG."""

    return JSONResponse(
        {
            "nodes": [
                {"id": "start", "label": "START", "kind": "terminal"},
                {"id": "triage", "label": "F1 Triage", "kind": "agent", "tier": "small"},
                {"id": "linker", "label": "F7 Linker", "kind": "agent", "tier": "procedural"},
                {"id": "enricher", "label": "F2 Enricher", "kind": "agent", "tier": "procedural"},
                {"id": "router", "label": "F3 Router", "kind": "agent", "tier": "procedural"},
                {"id": "knowledge", "label": "F4 Knowledge", "kind": "agent", "tier": "small"},
                {"id": "route_decision", "label": "route?", "kind": "decision"},
                {"id": "scheduler", "label": "F6 Scheduler", "kind": "agent", "tier": "procedural"},
                {"id": "drafter", "label": "F5 Drafter (HITL)", "kind": "agent", "tier": "medium"},
                {"id": "end", "label": "END", "kind": "terminal"},
                {"id": "watchdog", "label": "F8 Watchdog", "kind": "scheduled"},
            ],
            "edges": [
                {"from": "start", "to": "triage", "kind": "parallel"},
                {"from": "start", "to": "linker", "kind": "parallel"},
                {"from": "triage", "to": "enricher"},
                {"from": "linker", "to": "enricher"},
                {"from": "enricher", "to": "router", "kind": "parallel"},
                {"from": "enricher", "to": "knowledge", "kind": "parallel"},
                {"from": "router", "to": "route_decision"},
                {"from": "knowledge", "to": "route_decision"},
                {"from": "route_decision", "to": "scheduler", "kind": "conditional"},
                {"from": "route_decision", "to": "drafter", "kind": "conditional"},
                {"from": "scheduler", "to": "drafter"},
                {"from": "drafter", "to": "end"},
            ],
        }
    )
