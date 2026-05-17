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
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from partner_ticket_agentic.agents.insights import WindowSummary, run_insights
from partner_ticket_agentic.agents.watchdog import run_watchdog_once
from partner_ticket_agentic.graph import run_pipeline
from partner_ticket_agentic.obs import new_trace_id, trace_collector
from partner_ticket_agentic.providers import make_provider
from partner_ticket_agentic.safety import (
    SafetyError,
    assert_safe_input,
    detect_prompt_injection,
)
from partner_ticket_agentic.web.simulator import record_as_dict, simulator

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


@app.post("/api/simulate/start")
def simulate_start(interval: float = 4.0, provider: str = "mock") -> JSONResponse:
    """Start the live-traffic simulator.

    Fires synthetic tickets at ``interval`` seconds (default 4) through
    the pipeline against the chosen ``provider`` (default ``mock``).
    Results land in the simulator's rolling window for the dashboard
    endpoints to read.
    """

    try:
        result = simulator.start(interval=interval, provider_name=provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/api/simulate/stop")
def simulate_stop() -> JSONResponse:
    return JSONResponse(simulator.stop())


@app.post("/api/simulate/reset")
def simulate_reset() -> JSONResponse:
    """Stop the simulator and clear its rolling window.

    Called by the dashboard when the operator switches providers —
    keeping mock and Anthropic records in the same KPI window would
    make the metrics meaningless.
    """

    return JSONResponse(simulator.reset())


@app.get("/api/simulate/status")
def simulate_status() -> JSONResponse:
    return JSONResponse(simulator.status())


@app.get("/api/stats/dashboard")
def stats_dashboard() -> JSONResponse:
    """Aggregate over the simulator's rolling window.

    KPI tiles, throughput buckets, category / urgency / HITL counts,
    confidence histogram, cache-hit mean, and the most recent runs.
    Empty payload when the simulator hasn't produced anything yet.
    """

    runs = simulator.snapshot()
    sim_status = simulator.status()

    if not runs:
        return JSONResponse(
            {
                "empty": True,
                "running": sim_status["running"],
                "interval_seconds": sim_status["interval_seconds"],
                "provider": sim_status["provider"],
            }
        )

    total = len(runs)
    total_cost = round(sum(r.cost_usd for r in runs), 4)
    total_tokens_in = sum(r.tokens_in for r in runs)
    total_tokens_out = sum(r.tokens_out for r in runs)

    # Throughput — last 2 hours bucketed into 24 x 5-minute slots.
    bucket_count = 24
    bucket_minutes = 5
    bucket_span = timedelta(minutes=bucket_minutes)
    now = datetime.now(UTC)
    earliest = now - bucket_span * bucket_count
    buckets = [0] * bucket_count
    cost_buckets = [0.0] * bucket_count
    for record in runs:
        try:
            timestamp = datetime.fromisoformat(record.started_at)
        except ValueError:
            continue
        if timestamp < earliest:
            continue
        index = int((timestamp - earliest) / bucket_span)
        if 0 <= index < bucket_count:
            buckets[index] += 1
            cost_buckets[index] += record.cost_usd

    # Distributions for the bar charts.
    cat_counts = Counter(record.category for record in runs)
    urgency_counts = Counter(record.urgency for record in runs)
    hitl_counts = Counter(record.hitl_decision for record in runs)

    # 10-bucket confidence histogram, 0.0 → 1.0.
    conf_buckets = [0] * 10
    for record in runs:
        slot = min(9, int(record.confidence * 10))
        conf_buckets[slot] += 1

    avg_duration_ms = int(sum(r.duration_ms for r in runs) / total)
    avg_cost = round(total_cost / total, 5)
    cache_hit_mean = round(sum(r.cache_hit_rate for r in runs) / total, 3)

    drafts_pending = sum(1 for r in list(runs)[-50:] if r.hitl_decision == "edited")

    # Most recent 20 runs for the live activity stream (newest first).
    recent = [record_as_dict(r) for r in list(runs)[-20:][::-1]]

    return JSONResponse(
        {
            "empty": False,
            "running": sim_status["running"],
            "interval_seconds": sim_status["interval_seconds"],
            "provider": sim_status["provider"],
            "kpis": {
                "tickets_processed": total,
                "drafts_pending": drafts_pending,
                "in_flight": 1 if sim_status["running"] else 0,
                "spend_usd": total_cost,
                "avg_duration_ms": avg_duration_ms,
                "avg_cost_per_ticket_usd": avg_cost,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "cache_hit_mean": cache_hit_mean,
            },
            "throughput_5min": buckets,
            "cost_5min_usd": [round(value, 4) for value in cost_buckets],
            "bucket_minutes": bucket_minutes,
            "bucket_count": bucket_count,
            "categories": cat_counts.most_common(),
            "urgency": dict(urgency_counts),
            "hitl_decisions": dict(hitl_counts),
            "confidence_histogram": conf_buckets,
            "recent": recent,
        }
    )


@app.get("/api/insights")
def insights(window: int = 100, provider: str = "mock") -> JSONResponse:
    """F9 Insights — synthesise the last ``window`` runs into patterns.

    Reads from the simulator's rolling window, builds a compact summary,
    and dispatches to the chosen LLM provider (mock by default — same
    deterministic floor as the rest of the system). Returns a Pydantic-
    validated ``InsightsOutput``.
    """

    if provider not in {"mock", "anthropic", "ollama"}:
        raise HTTPException(status_code=400, detail=f"unknown provider {provider!r}")

    snapshot = simulator.snapshot()
    if not snapshot:
        return JSONResponse(
            {
                "empty": True,
                "reason": "Simulator window is empty — start the simulator to feed insights.",
            }
        )

    recent = snapshot[-max(1, min(window, 500)) :]
    summary = _build_window_summary(recent)
    selected = make_provider(provider)
    output = run_insights(summary, provider=selected)
    return JSONResponse(output.model_dump(mode="json"))


def _build_window_summary(records: list[Any]) -> WindowSummary:
    """Compact the rolling-window records into the agent's input shape."""

    from collections import Counter

    if not records:
        return WindowSummary(
            window_size=0,
            time_range_minutes=0,
            categories={},
            urgency={},
            hitl={},
            partner_counts={},
            avg_confidence=0.0,
            avg_duration_ms=0,
            avg_cost_usd=0.0,
            sample_recent=[],
        )

    categories = Counter(r.category for r in records)
    urgency = Counter(r.urgency for r in records)
    hitl = Counter(r.hitl_decision for r in records)
    partner_counts = Counter(r.base_ticket_id for r in records)

    try:
        earliest = datetime.fromisoformat(records[0].started_at)
        latest = datetime.fromisoformat(records[-1].started_at)
        span_minutes = max(0, int((latest - earliest).total_seconds() / 60))
    except (ValueError, AttributeError):
        span_minutes = 0

    avg_conf = sum(r.confidence for r in records) / len(records)
    avg_dur = int(sum(r.duration_ms for r in records) / len(records))
    avg_cost = sum(r.cost_usd for r in records) / len(records)

    sample = [
        {
            "sim_ticket_id": r.sim_ticket_id,
            "category": r.category,
            "urgency": r.urgency,
            "hitl_decision": r.hitl_decision,
        }
        for r in records[-8:][::-1]
    ]

    return WindowSummary(
        window_size=len(records),
        time_range_minutes=span_minutes,
        categories=dict(categories),
        urgency=dict(urgency),
        hitl=dict(hitl),
        partner_counts=dict(partner_counts),
        avg_confidence=round(avg_conf, 3),
        avg_duration_ms=avg_dur,
        avg_cost_usd=round(avg_cost, 5),
        sample_recent=sample,
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
