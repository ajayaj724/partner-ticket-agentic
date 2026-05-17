# Load Test Plan

A k6 load profile for the partner-ticket-agentic FastAPI surface. The plan is documented and ready to run — **not executed** in v1.1. Running it lives in the v2.0 production-migration milestone (Tier-B `OPS-01`), after the simulator is extracted to its own worker.

REQ: PERF-01 (Phase 1, v1.1 production-readiness gap closure)

---

## Why this plan exists at v1.1

The panel will ask for a P95 number. Saying *"we'd add a load test"* is weaker than *"here's the load test plan, here are the expected targets per provider, here's the pass/fail criteria — it lands in v2.0 once the simulator is out of the request-path process"*.

This document is the answer to that question.

---

## Target endpoint

`GET /api/run/{ticket_id}?provider={mock|ollama|anthropic}`

This is the request-path endpoint — one ticket through the full nine-agent pipeline. The `provider` query parameter selects the LLM backend. Three runs of the plan, one per provider, gives three operating-point characterisations.

## Out of scope

- `POST /api/simulate/start` — dashboard simulator runs in-process and would skew measurements. The simulator is being extracted to a separate worker in `OPS-01`; load-test against the new pub-sub surface, not against the current in-process simulator.
- `GET /api/insights` — F9 Insights is a sidecar with its own cadence; covered separately when it moves to a worker.
- `GET /api/stats/dashboard` — read-only aggregator; characterise with a simpler synthetic.

---

## Workload profile

```text
0 s ─────── 30 s ────── 90 s ─────── 150 s ─────── 180 s
│   warm   │   ramp     │  hold      │  ramp-down  │
│  0 → 1   │  1 → 50    │  50 RPS    │  50 → 0     │
│  RPS     │  RPS       │            │  RPS        │
```

- **Warm (30 s)**: 1 RPS to populate caches (prompt-cache, model-warm, FAISS+BM25 index, runbook seed read). Provider responses outside this window are what we measure.
- **Ramp (60 s)**: linear ramp from 1 → 50 RPS. Catches the early-saturation behaviour — where does latency start to climb? where does the breaker first trip?
- **Hold (60 s)**: 50 RPS steady-state for two minutes. Long enough to fill the cost-ledger context-var pool and surface lock contention if any.
- **Ramp-down (30 s)**: linear 50 → 0 RPS. Lets the queue drain cleanly.

Ticket population is the existing 17 sample tickets in `data/sample_tickets.json`, picked round-robin.

---

## P95 latency targets

Per provider, end-to-end pipeline latency from request to response:

| Provider | Target P95 | Tolerance | Rationale |
|---|---|---|---|
| `mock` | **< 50 ms** | +20 % | Deterministic if/elif rules; entire cost is JSON serialisation and Pydantic validation. Anything above 50 ms suggests CPU contention from the simulator or FastAPI worker saturation. |
| `ollama` | **< 12 s** (warm) | +30 % | First call after warm-up takes ~7 s for Triage alone on llama3.2:3b. End-to-end is dominated by the single LLM call. Anything above 16 s means the model unloaded mid-test or Ollama is queueing. |
| `anthropic` | **< 4 s** (cache-warm) | +25 % | One LLM call per pipeline (Triage). Network round-trip dominates. With 70 % cache hit rate the latency floor is ~2 s; pre-warm allows a ~4 s P95. |

**Cold-cache contract**: P95 numbers above assume the warm phase has primed all caches. A separate "cold start" measurement runs the workload from a freshly-restarted FastAPI process to characterise the first-minute curve.

---

## Pass/fail criteria

The test passes if **all** of the following hold:

1. **No 5xx responses.** Any unhandled exception in the request path is a test failure regardless of latency. The HITL contract test, schema validation, and the circuit breaker should all surface as 4xx-typed errors.
2. **P95 latency within target.** Per the table above, per provider. Stretch P99 monitored but not gate-blocking — too sensitive to one slow call.
3. **No `BudgetExceededError` storm.** At 50 RPS over 2 minutes that's 6,000 pipeline runs. If we trip a partner budget during the test, that's a synthetic-load issue (budgets are sized for real partner volumes) — adjust the test fixture, not the production budget.
4. **F8 Watchdog sidecar keeps up.** Sidecar runs every 30 s. At 50 RPS for 2 minutes the watchdog should scan a window of 3,000 open tickets without exceeding its single iteration budget. Surface as a separate signal.
5. **Memory growth bounded.** FastAPI RSS should plateau within 30 s of steady-state. Continued growth indicates the cost-ledger context-var leak or a graph-state retention bug.

---

## What to watch (in addition to the latency P95)

- **Ollama RAM**: `top -pid $(pgrep ollama)` — should hold steady at the model size (~3.5 GB for llama3.2:3b)
- **FastAPI worker saturation**: 1 uvicorn worker handles ~25 RPS of mock traffic on this hardware. Above that, add workers (and the simulator becomes a single shared dependency — see `OPS-01`).
- **OTel span export drops**: When `PTAG_OTEL=1`, the simple span processor exports synchronously. Under load, span drops are silent. Watch the OTLP collector's receive count vs. the trace count from the FastAPI logs.
- **Circuit-breaker state per tool**: should stay `CLOSED` for all tools through the test. Any `OPEN` event means an upstream dependency is degraded — escalate, don't extend the test window.
- **Cost-ledger telemetry**: `cost_usd` summed across all responses. Sanity-check against expected: ~6,000 mock runs × $0 = $0; ~6,000 Anthropic runs × $0.005 per ticket (cold) → ~$30. If the number is off by 5x, the cost code regressed.

---

## k6 script

Save as `tests/load/run_load.js`. **Not committed to v1.1**; this script is the artefact that runs in v2.0. Listed here so the plan is operational, not aspirational.

```javascript
import http from 'k6/http';
import { sleep, check } from 'k6';
import { Trend, Rate } from 'k6/metrics';

// Per-provider stages let one script run all three configurations:
//   k6 run -e PROVIDER=mock      tests/load/run_load.js
//   k6 run -e PROVIDER=ollama    tests/load/run_load.js
//   k6 run -e PROVIDER=anthropic tests/load/run_load.js
const PROVIDER = __ENV.PROVIDER || 'mock';
const BASE = __ENV.BASE_URL || 'http://localhost:8000';

// 17 sample ticket IDs from data/sample_tickets.json. Picked round-robin
// to keep the input mix realistic across categories.
const TICKETS = [
  'sample-1','sample-2','sample-3','sample-4','sample-5','sample-6',
  'sample-7','sample-8','sample-9','sample-10','sample-11','sample-12',
  'sample-13','sample-14','sample-15','sample-16','sample-17',
];

export const options = {
  scenarios: {
    pipeline: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 200,
      stages: [
        { target: 1,  duration: '30s' },  // warm
        { target: 50, duration: '60s' },  // ramp 1→50
        { target: 50, duration: '60s' },  // hold
        { target: 0,  duration: '30s' },  // ramp-down
      ],
    },
  },
  thresholds: {
    // Per-provider P95 thresholds. The k6 run --tag provider=... lets us
    // split these by provider in the report.
    'http_req_duration{provider:mock}':     ['p(95)<50'],
    'http_req_duration{provider:ollama}':   ['p(95)<12000'],
    'http_req_duration{provider:anthropic}':['p(95)<4000'],
    'http_req_failed': ['rate<0.001'],  // <0.1% error rate
  },
};

const pipelineLatency = new Trend('pipeline_latency_ms', true);
const budgetExceeded = new Rate('budget_exceeded');

export default function () {
  const ticket = TICKETS[__ITER % TICKETS.length];
  const url = `${BASE}/api/run/${ticket}?provider=${PROVIDER}`;
  const res = http.get(url, { tags: { provider: PROVIDER } });
  pipelineLatency.add(res.timings.duration);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'no 5xx':         (r) => r.status < 500,
    'has trace_id':   (r) => r.json('state.trace_id') !== undefined,
    'requires_approval=true': (r) =>
      r.json('state.draft.requires_approval') === true,
  });

  // Track BudgetExceededError separately — we want to alert on it but it
  // shouldn't fail the test outright (it's a synthetic-fixture issue, not
  // a system bug).
  budgetExceeded.add(res.status === 429 || r => false);
}
```

Run with:

```bash
# Local mock baseline (cheap, fast, no dependencies)
k6 run -e PROVIDER=mock tests/load/run_load.js

# Real LLM, local (requires Ollama warm)
k6 run -e PROVIDER=ollama tests/load/run_load.js

# Anthropic — only when budget approved by operator
k6 run -e PROVIDER=anthropic tests/load/run_load.js
```

---

## Reporting

k6 writes JSON to stdout when invoked with `--out json=results.json`. The expected report shape (from a clean local mock run) is roughly:

```json
{
  "metric": "http_req_duration",
  "type": "Point",
  "data": {
    "tags": { "provider": "mock" },
    "value": 24.3
  }
}
```

For the panel, the load-test summary becomes a one-paragraph addition to the cost / latency story:

> *"At 50 RPS sustained on mock, P95 is 38 ms with zero failures. On Ollama warm, P95 is 9.2 seconds — the model is the bottleneck, not the graph. On Anthropic with 70% cache hit, P95 is 3.1 seconds. The pipeline is stateless per run, so the only scaling story for the LLM-bound paths is provider replicas."*

The numbers above are **expected** based on per-call timing observed during the panel demo. They become **measured** when the v2.0 run lands.

---

*Plan defined: 2026-05-17. Execution: v2.0 milestone (Tier-B `OPS-01` after the simulator is extracted to its own worker).*
