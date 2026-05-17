"""Live-traffic simulator for the dashboard.

Runs a background thread that fires synthetic tickets at a configurable
interval, routes them through the real pipeline against the mock provider
(no $$, deterministic), and keeps a rolling in-memory window of results.
The ``/api/stats/dashboard`` endpoint aggregates over this window so the
frontend can render KPIs, charts, and a live activity stream.

The simulator is intentionally simple: one thread, one ring buffer, a
single module-level instance. The HTTP layer owns its lifecycle through
``/api/simulate/{start,stop,status}``.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Any

from partner_ticket_agentic.graph import run_pipeline
from partner_ticket_agentic.obs import new_trace_id
from partner_ticket_agentic.providers import make_provider

# Weighted ticket-pick distribution so the dashboard category histogram
# looks plausible. Higher weight → more frequent in the stream. Circuit
# outages dominate (matches real telecom-NOC traffic shape); the catch-all
# OTHER ticket is rare.
_TICKET_WEIGHTS: dict[str, int] = {
    # circuit_down (heaviest — outages drive most real ops traffic)
    "sample-1": 5,
    "sample-6": 5,
    "sample-12": 4,
    "sample-13": 3,
    "sample-17": 4,
    # throughput_degraded
    "sample-5": 3,
    "sample-7": 3,
    "sample-11": 3,
    # appointment_request
    "sample-2": 3,
    "sample-8": 2,
    "sample-14": 2,
    # billing
    "sample-3": 2,
    "sample-9": 2,
    "sample-15": 2,
    # provisioning
    "sample-4": 1,
    "sample-10": 1,
    # OTHER / ambiguous
    "sample-16": 1,
}

# Baseline HITL decision distribution — what we'd expect for an "average"
# ticket. Per-ticket weights get adjusted below by confidence, category,
# and urgency so the dashboard's HITL bar reflects plausible operator
# behaviour rather than a flat random distribution.
_HITL_WEIGHTS_BASELINE: dict[str, int] = {"approved": 78, "edited": 16, "rejected": 6}

# Tighter bands when triage confidence is low — operators trust the draft
# less, so they edit or reject more often.
_HITL_WEIGHTS_LOW_CONF: dict[str, int] = {"approved": 40, "edited": 35, "rejected": 25}
_HITL_WEIGHTS_MED_CONF: dict[str, int] = {"approved": 65, "edited": 25, "rejected": 10}

# Per-category multipliers — applied on top of the confidence band. These
# encode plausible operator instincts:
#
# * billing → numbers must be right; operators edit more (compliance check)
# * provisioning → long-term commitments; high edit rate
# * appointment_request → templated and low-stakes; mostly approve
# * circuit_down → urgent, fast approval, but high-stakes when it's wrong
_CATEGORY_BIAS: dict[str, dict[str, float]] = {
    "billing": {"approved": 0.85, "edited": 1.7, "rejected": 1.0},
    "provisioning": {"approved": 0.85, "edited": 1.6, "rejected": 1.0},
    "appointment_request": {"approved": 1.2, "edited": 0.55, "rejected": 0.4},
    "circuit_down": {"approved": 1.05, "edited": 1.0, "rejected": 0.9},
    "throughput_degraded": {"approved": 1.0, "edited": 1.1, "rejected": 1.0},
    "other": {"approved": 0.85, "edited": 1.3, "rejected": 1.2},
}

# Urgency multipliers — critical tickets tend to be approved fast (no time
# for fussing) but never rejected outright.
_URGENCY_BIAS: dict[str, dict[str, float]] = {
    "critical": {"approved": 1.15, "edited": 0.9, "rejected": 0.5},
    "high": {"approved": 1.05, "edited": 1.0, "rejected": 0.9},
    "medium": {"approved": 1.0, "edited": 1.0, "rejected": 1.0},
    "low": {"approved": 0.9, "edited": 1.1, "rejected": 1.2},
}

# Synthetic dollar cost per run, sampled from this range. The real cost
# ledger reports $0 on the mock provider; the dashboard needs movement on
# its budget-burndown chart, so we overlay an "as-if-Anthropic-Haiku"
# amount. Documented honestly on the dashboard.
_SYNTHETIC_COST_USD_RANGE: tuple[float, float] = (0.0015, 0.0085)

# +/- noise added to the (deterministic) mock confidence so the
# confidence histogram has shape rather than being a single column.
_CONFIDENCE_NOISE: float = 0.06


@dataclass
class RunRecord:
    """One row in the simulator's rolling history."""

    trace_id: str
    base_ticket_id: str
    sim_ticket_id: str
    category: str
    urgency: str
    confidence: float
    queue: str
    sla_minutes: int
    runbook_id: str
    hitl_decision: str
    tokens_in: int
    tokens_out: int
    cache_hit_rate: float
    cost_usd: float
    duration_ms: int
    started_at: str
    scheduler_used: bool


@dataclass
class Simulator:
    """In-process synthetic-ticket generator. Thread-safe via ``_lock``."""

    interval_seconds: float = 4.0
    history_size: int = 500
    provider_name: str = "mock"
    started_at: datetime | None = None
    running: bool = False
    counter: int = 0
    _runs: deque[RunRecord] = field(default_factory=lambda: deque(maxlen=500))
    _lock: Lock = field(default_factory=Lock)
    _thread: Thread | None = None

    # --- lifecycle ----------------------------------------------------

    def start(
        self,
        interval: float | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {
                    "running": True,
                    "interval_seconds": self.interval_seconds,
                    "provider": self.provider_name,
                    "already": True,
                }
            if interval is not None:
                self.interval_seconds = max(0.5, float(interval))
            if provider_name is not None:
                if provider_name not in {"mock", "ollama", "anthropic"}:
                    raise ValueError(f"unknown provider {provider_name!r}")
                self.provider_name = provider_name
            self.running = True
            self.started_at = datetime.now(UTC)
            self._thread = Thread(target=self._loop, name="ptag-sim", daemon=True)
            self._thread.start()
        return {
            "running": True,
            "interval_seconds": self.interval_seconds,
            "provider": self.provider_name,
            "already": False,
        }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.running = False
        return {"running": False}

    def reset(self) -> dict[str, Any]:
        """Stop the simulator and clear its rolling window + counters.

        Used when switching providers — mixing mock latencies with
        Ollama latencies in the same KPI tile is confusing and makes
        the dashboard's metrics meaningless. Always returns a clean
        slate so the next ``start()`` begins from zero.
        """

        with self._lock:
            cleared = len(self._runs)
            previous_count = self.counter
            self.running = False
            self._runs.clear()
            self.counter = 0
            self.started_at = None
        return {
            "cleared_records": cleared,
            "cleared_counter": previous_count,
            "running": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "interval_seconds": self.interval_seconds,
                "provider": self.provider_name,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "tickets_processed": self.counter,
                "history_size": len(self._runs),
            }

    def snapshot(self) -> list[RunRecord]:
        with self._lock:
            return list(self._runs)

    # --- loop ---------------------------------------------------------

    def _loop(self) -> None:
        # Late import to avoid circular: app.py imports this module.
        from partner_ticket_agentic.web.app import _load_sample_tickets

        rnd = random.Random(0xCAFE)
        sample_tickets = _load_sample_tickets()
        if not sample_tickets:
            with self._lock:
                self.running = False
            return

        weighted_pool: list[dict[str, Any]] = []
        for ticket in sample_tickets:
            weight = _TICKET_WEIGHTS.get(ticket["ticket_id"], 1)
            weighted_pool.extend([ticket] * weight)

        provider = make_provider(self.provider_name)

        while True:
            with self._lock:
                if not self.running:
                    return

            base = rnd.choice(weighted_pool)
            with self._lock:
                self.counter += 1
                sim_id = f"SIM-{self.counter:04d}"
            ticket = dict(base)
            ticket["ticket_id"] = sim_id

            t0 = time.monotonic()
            try:
                state = run_pipeline(ticket, provider=provider, trace_id=new_trace_id())
            except Exception:
                self._sleep_with_check()
                continue
            duration_ms = int((time.monotonic() - t0) * 1000)

            record = self._record_from_state(
                rnd=rnd,
                base=base,
                sim_id=sim_id,
                state=state,
                duration_ms=duration_ms,
            )
            with self._lock:
                self._runs.append(record)

            self._sleep_with_check()

    def _sleep_with_check(self) -> None:
        """Sleep ``interval_seconds`` but wake every 200 ms to check stop."""

        step = 0.2
        slept = 0.0
        while slept < self.interval_seconds:
            with self._lock:
                if not self.running:
                    return
            time.sleep(step)
            slept += step

    def _record_from_state(
        self,
        *,
        rnd: random.Random,
        base: dict[str, Any],
        sim_id: str,
        state: Any,
        duration_ms: int,
    ) -> RunRecord:
        triage = state.triage or {}
        routing = state.routing or {}
        knowledge = state.knowledge or {}
        cost = state.cost or {}
        schedule = state.schedule or {}

        # Cost handling per provider:
        #   anthropic → real $$ from the cost ledger
        #   ollama    → real tokens but $0 (uses GPU minutes, not API tokens) — overlay synthetic
        #   mock      → no-op (returns $0)  — overlay synthetic so burndown moves
        real_cost = float(cost.get("cost_usd", 0.0))
        if self.provider_name == "anthropic" and real_cost > 0:
            ticket_cost = round(real_cost, 4)
        else:
            ticket_cost = round(rnd.uniform(*_SYNTHETIC_COST_USD_RANGE), 4)
        conf_noise = rnd.uniform(-_CONFIDENCE_NOISE, _CONFIDENCE_NOISE)
        confidence = max(0.0, min(1.0, float(triage.get("confidence", 0.85)) + conf_noise))

        # HITL picked AFTER confidence noise so the bias matches what the
        # dashboard will display — low-confidence tickets edit/reject more.
        hitl = _pick_hitl(
            rnd,
            confidence=confidence,
            category=str(triage.get("category", "")),
            urgency=str(triage.get("urgency", "")),
        )

        return RunRecord(
            trace_id=state.trace_id or "",
            base_ticket_id=base["ticket_id"],
            sim_ticket_id=sim_id,
            category=str(triage.get("category", "unknown")),
            urgency=str(triage.get("urgency", "normal")),
            confidence=confidence,
            queue=str(routing.get("queue", "unknown")),
            sla_minutes=int(routing.get("sla_minutes", 0)),
            runbook_id=str((knowledge.get("top_runbook") or {}).get("runbook_id", "—")),
            hitl_decision=hitl,
            tokens_in=int(cost.get("tokens_in", 0)),
            tokens_out=int(cost.get("tokens_out", 0)),
            cache_hit_rate=float(cost.get("cache_hit_rate", 0.0)),
            cost_usd=ticket_cost,
            duration_ms=duration_ms,
            started_at=datetime.now(UTC).isoformat(),
            scheduler_used=bool(schedule.get("proposed_slots")),
        )


def _weighted_choice(rnd: random.Random, weights: dict[str, int]) -> str:
    total = sum(weights.values())
    pick = rnd.randint(1, total)
    cum = 0
    for k, w in weights.items():
        cum += w
        if pick <= cum:
            return k
    return next(iter(weights))


def _pick_hitl(
    rnd: random.Random,
    *,
    confidence: float,
    category: str,
    urgency: str,
) -> str:
    """Synthesize an HITL decision biased by ticket characteristics.

    Mirrors plausible operator behaviour rather than a flat random pick:

    * **Confidence band** selects the baseline (low / mid / high).
    * **Category** multipliers tilt that baseline (billing edits more,
      appointments approve more, etc.).
    * **Urgency** finishes the tilt — critical tickets approve faster,
      low tickets reject more.

    The result is still synthetic — in production these come from real
    button presses — but the *shape* of the distribution now looks like
    a thoughtful operator team's behaviour.
    """

    if confidence < 0.6:
        base = dict(_HITL_WEIGHTS_LOW_CONF)
    elif confidence < 0.75:
        base = dict(_HITL_WEIGHTS_MED_CONF)
    else:
        base = dict(_HITL_WEIGHTS_BASELINE)

    cat_bias = _CATEGORY_BIAS.get(category, {})
    urg_bias = _URGENCY_BIAS.get(urgency, {})

    # Apply both bias maps as multiplicative scalars, floor at 1 so no
    # decision can be driven to zero (rare events should still happen).
    scaled = {
        decision: max(1, round(weight * cat_bias.get(decision, 1.0) * urg_bias.get(decision, 1.0)))
        for decision, weight in base.items()
    }
    return _weighted_choice(rnd, scaled)


def record_as_dict(r: RunRecord) -> dict[str, Any]:
    """Public helper — used by the stats endpoint to ship records over HTTP."""

    return asdict(r)


# Module-level singleton. The FastAPI app imports this directly.
simulator = Simulator()
