/**
 * /how-it-works — developer-facing walkthrough.
 *
 * Companion page to the live demo at /. Where the demo shows the system
 * running, this page explains how it's built: the tech stack, the
 * request flow with code excerpts, and where to find every component in
 * the repo. All file references link to the public GitHub mirror.
 */

import Link from "next/link";
import {
  ArrowLeft,
  Code,
  ExternalLink,
  GitBranch,
  Layers,
} from "lucide-react";

const REPO = "https://github.com/ajayantony/partner-ticket-agentic";

function gh(path: string, line?: number, line2?: number): string {
  const branch = "main";
  const range = line ? `#L${line}${line2 ? `-L${line2}` : ""}` : "";
  return `${REPO}/blob/${branch}/${path}${range}`;
}

// --------------------------------------------------------------------
// Tech stack — flat tables. Each row: choice + version + why + ruled-out.

type StackRow = { name: string; version?: string; why: string; ruled?: string };

const BACKEND_CORE: StackRow[] = [
  {
    name: "Python", version: "3.14",
    why: "Latest stable. The ML / agent-framework ecosystem lives in Python.",
    ruled: "Older 3.11 (works, but locks us out of newer typing).",
  },
  {
    name: "uv", version: "0.5+",
    why: "10–100× faster than pip; reproducible lockfile; first-class Python-version management.",
    ruled: "Poetry (slower, heavier, no built-in Python switcher).",
  },
  {
    name: "LangGraph", version: "1.x (StateGraph)",
    why: "Real state machine — conditional edges, parallel fan-out, join semantics.",
    ruled: "Hand-rolled controller; LangChain chains (too linear); Airflow / Temporal (DAG decided at deploy, not by LLM output).",
  },
  {
    name: "Pydantic", version: "v2",
    why: "Typed I/O at every agent boundary. v2 has a Rust core — 5–50× faster than v1.",
    ruled: "Free-text LLM output; dataclasses (no validation); hand-rolled JSON Schema.",
  },
  {
    name: "FastAPI", version: "0.115+",
    why: "Pydantic-native, async, OpenAPI for free. Frontend proxies /api/* here.",
    ruled: "Flask (sync-only, no schema); Django (overkill for an API).",
  },
];

const AI_SURFACE: StackRow[] = [
  {
    name: "Provider abstraction",
    why: "One LLMProvider interface, three concrete implementations behind it. Same agent code runs against all three.",
  },
  {
    name: "Mock provider",
    why: "Deterministic if/elif rules — reviewer reads the rules and predicts the output. Default path is offline + free.",
  },
  {
    name: "Ollama provider", version: "llama3.2:3b",
    why: "Local LLM on the developer's machine. Proves prompts work on a small open-source model.",
  },
  {
    name: "Anthropic provider", version: "Claude Haiku 4.5",
    why: "Cloud LLM with tool-use loop + prompt caching (cache_control: ephemeral) for repeat-call savings.",
  },
  {
    name: "Memory · 3 tiers",
    why: "Working (TicketState, in-process) · episodic (SQLite at ~/.ptag/episodic.db) · long-term (FAISS + BM25 hybrid).",
  },
  {
    name: "Retrieval", version: "FAISS + BM25 hybrid",
    why: "Vector similarity merged with keyword ranking. Top-3 candidates reach the consumer — naive prompt-stuffing would burn 10–100× the tokens.",
  },
  {
    name: "MCP server",
    why: "Anthropic's Model Context Protocol — publishes the tool registry to external orchestrators without coupling.",
  },
];

const FRONTEND: StackRow[] = [
  {
    name: "Next.js", version: "16.2",
    why: "App Router, Server Components, Turbopack. Same-origin /api/* proxy to FastAPI keeps CORS out of the picture.",
  },
  { name: "React", version: "19.2", why: "Bundled with Next 16 App Router." },
  { name: "TypeScript", version: "5", why: "Strict typing on the frontend matches the Pydantic discipline on the backend." },
  { name: "Tailwind CSS", version: "v4", why: "Token-driven; the theme tokens live in globals.css." },
  { name: "motion", version: "12 (formerly framer-motion)", why: "Stroke-in animation on the topology, staggered agent-card reveals, the progress bar." },
  { name: "lucide-react", version: "1.x", why: "Consistent stroke-weight icons." },
];

// --------------------------------------------------------------------
// Code walkthroughs — each is a tight excerpt + file reference.

type Excerpt = {
  title: string;
  blurb: string;
  path: string;
  line1: number;
  line2?: number;
  code: string;
};

const EXCERPTS: Excerpt[] = [
  {
    title: "The LangGraph state machine",
    blurb:
      "All seven in-graph agents wired up. START fans out to Triage + Linker in parallel; both join at Enricher; Enricher fans out to Router + Knowledge; the conditional edge picks between Scheduler and Drafter.",
    path: "src/partner_ticket_agentic/graph.py",
    line1: 62, line2: 97,
    code: `graph = StateGraph(TicketState)
graph.add_node("triage",    _triage)
graph.add_node("linker",    linker_node)
graph.add_node("enricher",  enricher_node)
graph.add_node("router",    router_node)
graph.add_node("knowledge", knowledge_node)
graph.add_node("scheduler", scheduler_node)
graph.add_node("drafter",   drafter_node)

graph.add_edge(START,      "triage")
graph.add_edge(START,      "linker")
graph.add_edge("triage",   "enricher")
graph.add_edge("linker",   "enricher")
graph.add_edge("enricher", "router")
graph.add_edge("enricher", "knowledge")

graph.add_conditional_edges(
    "route_decision", _route_decision,
    {"scheduler": "scheduler", "drafter": "drafter"},
)
graph.add_edge("scheduler", "drafter")
graph.add_edge("drafter",    END)`,
  },
  {
    title: "Provider abstraction & model-tier registry",
    blurb:
      "One typed Protocol every provider implements. The `tier` argument (small / medium / large) resolves to a concrete model via config/approved_models.yaml — an unapproved (provider, tier) pair raises rather than silently degrades.",
    path: "src/partner_ticket_agentic/providers/base.py",
    line1: 21, line2: 73,
    code: `class Tier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

@runtime_checkable
class LLMProvider(Protocol):
    name: str
    def complete(
        self,
        messages: list[Message],
        schema: type[T],
        tier: Tier,
        *,
        system: str | None = None,
        trace_id: str | None = None,
    ) -> T: ...`,
  },
  {
    title: "Approved-model mapping",
    blurb:
      "Tier → model id per provider. Adding a new model requires editing this file, which makes the model surface auditable for compliance.",
    path: "config/approved_models.yaml",
    line1: 18, line2: 34,
    code: `providers:
  anthropic:
    small:  claude-haiku-4-5-20251001
    medium: claude-sonnet-4-6
    large:  claude-opus-4-7
  ollama:
    small:  llama3.2:3b
    medium: llama3.1:8b
    large:  llama3.3:70b
  mock:
    small:  mock-small
    medium: mock-medium
    large:  mock-large`,
  },
  {
    title: "Triage agent — declared tier",
    blurb:
      "Each agent that calls the LLM declares the tier it needs at its call site. Triage is classification — the cheapest model is enough.",
    path: "src/partner_ticket_agentic/agents/triage.py",
    line1: 37,
    code: `TIER = Tier.SMALL    # classification — small model is enough`,
  },
  {
    title: "Tool allow-listing — security as code, not convention",
    blurb:
      "Each agent carries a per-agent ToolAllowList. Calling a tool not on the list raises ToolNotAllowedError. The prompt cannot talk the model into using a tool it isn't allowed to use.",
    path: "src/partner_ticket_agentic/agents/drafter.py",
    line1: 23,
    code: `ALLOW_LIST = ToolAllowList.of("drafter", "template_lookup", "compliance_filter")`,
  },
  {
    title: "Retry + circuit breaker — bounded blast radius",
    blurb:
      "Every tool call goes through a dispatcher that applies exponential-backoff retries from a RetryPolicy. Read-only tools retry by nature; side-effecting tools take an idempotency key so retries are safe.",
    path: "src/partner_ticket_agentic/tools/registry.py",
    line1: 119, line2: 143,
    code: `for attempt in range(self.retry_policy.max_retries + 1):
    try:
        return handler(**kwargs)
    except ToolError as e:
        if attempt < self.retry_policy.max_retries:
            delay = self.retry_policy.backoff_base_s * (2**attempt)
            time.sleep(delay)
            continue
        raise`,
  },
  {
    title: "FastAPI surface — what the frontend hits",
    blurb:
      "Six endpoints. /api/tickets lists the samples; /api/run/{id}?provider=… executes the pipeline; /api/watchdog runs the F8 sidecar; /api/inject demos the injection filter; /api/topology returns the static graph layout.",
    path: "src/partner_ticket_agentic/web/app.py",
    line1: 52, line2: 66,
    code: `app = FastAPI(title="Partner-Ticketing Agentic Platform — Web UI")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")

@app.get("/api/tickets")
def list_tickets() -> JSONResponse:
    return JSONResponse(_load_sample_tickets())

@app.get("/api/run/{ticket_id}")
def run_ticket(ticket_id: str, provider: str = "mock") -> JSONResponse:
    ...`,
  },
  {
    title: "Next.js proxy to FastAPI",
    blurb:
      "Same-origin /api/* requests from the browser are rewritten to the FastAPI backend on :8000. CORS becomes a non-issue; production deploys the same rewrite at the edge.",
    path: "frontend/next.config.ts",
    line1: 1, line2: 19,
    code: `const FASTAPI_ORIGIN = process.env.FASTAPI_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: \`\${FASTAPI_ORIGIN}/api/:path*\`,
      },
    ];
  },
};`,
  },
  {
    title: "Mock provider — deterministic rule dispatch",
    blurb:
      "The mock has no model. Each agent registers a per-schema rule that takes the prompt and returns a dict; Pydantic validates the dict the same way it validates real-LLM output. If no rule is registered, the mock raises rather than guessing — guessing is what a real LLM does and is exactly what determinism forbids.",
    path: "src/partner_ticket_agentic/providers/mock.py",
    line1: 68, line2: 100,
    code: `def complete(self, messages, schema, tier, *, system=None, trace_id=None):
    model_id = self._registry.resolve(self.name, tier)
    schema_name = schema.__name__
    rule = self._rules.get(schema_name)
    if rule is None:
        raise LLMProviderError(
            f"mock provider has no rule registered for schema {schema_name!r}; "
            f"add one via MockProvider.register({schema_name}, fn) — "
            "deterministic behaviour requires explicit rules per agent."
        )
    with span("llm_call", provider=self.name, model=model_id,
              tier=tier.value, schema=schema_name):
        raw = rule(system or "", messages)
        return schema.model_validate(raw)`,
  },
  {
    title: "A real mock rule — F1 Triage",
    blurb:
      "Read this function and you can predict the Triage card on the demo page for any sample ticket. Keyword classifier + entity extractor + a small confidence bonus when entities are present. No randomness, no time-based branching, no hashing.",
    path: "src/partner_ticket_agentic/agents/triage.py",
    line1: 194, line2: 220,
    code: `def _triage_rule(_system: str, messages: list[Message]) -> dict[str, Any]:
    """Deterministic mock rule for TriageOutput — no LLM call."""
    user_text = "\\n".join(m.content for m in messages if m.role == "user")
    category, urgency, confidence, hits = _classify_keywords(user_text)
    entities = _extract_entities(user_text)
    bonus = 0.0
    if entities.circuits or entities.appointments or entities.invoices:
        bonus = 0.03
    confidence = min(1.0, confidence + bonus)
    return {
        "category": category.value,
        "urgency": urgency.value,
        "entities": entities.model_dump(),
        "confidence": round(confidence, 4),
        "rationale": _build_rationale(category, hits, entities),
    }

MockProvider.register(TriageOutput, _triage_rule)`,
  },
  {
    title: "Simulator tick — one synthetic ticket through the pipeline",
    blurb:
      "The /dashboard page's live stream is this loop. Daemon thread, weighted-random ticket pick, real run_pipeline() call (same code path as the demo), schema-validated state, ring-buffered for the stats endpoint. Stop is interruptible — the sleep wakes every 200 ms to re-check.",
    path: "src/partner_ticket_agentic/web/simulator.py",
    line1: 142, line2: 195,
    code: `while True:
    with self._lock:
        if not self.running:
            return

    base = rnd.choice(weighted_pool)
    with self._lock:
        self.counter += 1
        sim_id = f"SIM-{self.counter:04d}"
    ticket = dict(base); ticket["ticket_id"] = sim_id

    t0 = time.monotonic()
    try:
        state = run_pipeline(ticket, provider=provider,
                             trace_id=new_trace_id())
    except Exception:
        self._sleep_with_check(); continue
    duration_ms = int((time.monotonic() - t0) * 1000)

    record = self._record_from_state(
        rnd=rnd, base=base, sim_id=sim_id,
        state=state, duration_ms=duration_ms,
    )
    with self._lock:
        self._runs.append(record)

    self._sleep_with_check()`,
  },
  {
    title: "Dashboard stats — aggregating the ring buffer",
    blurb:
      "Every 2 seconds the /dashboard page hits /api/stats/dashboard. The endpoint walks the simulator's deque and emits KPIs, 24-bucket throughput, category and HITL distributions, a 10-bucket confidence histogram, and the most recent 20 runs. No database — pure in-process aggregation. Swap the source to Postgres in production and nothing else changes.",
    path: "src/partner_ticket_agentic/web/app.py",
    line1: 134, line2: 184,
    code: `runs = simulator.snapshot()
sim_status = simulator.status()

# Throughput — last 2 hours bucketed into 24 × 5-min slots.
bucket_count = 24; bucket_minutes = 5
bucket_span = timedelta(minutes=bucket_minutes)
now = datetime.now(timezone.utc)
earliest = now - bucket_span * bucket_count
buckets = [0] * bucket_count
cost_buckets = [0.0] * bucket_count
for record in runs:
    timestamp = datetime.fromisoformat(record.started_at)
    if timestamp < earliest: continue
    index = int((timestamp - earliest) / bucket_span)
    buckets[index] += 1
    cost_buckets[index] += record.cost_usd

# Distributions, histogram, KPIs …
cat_counts   = Counter(r.category       for r in runs)
hitl_counts  = Counter(r.hitl_decision  for r in runs)
conf_buckets = [0] * 10
for r in runs:
    conf_buckets[min(9, int(r.confidence * 10))] += 1`,
  },
];

// --------------------------------------------------------------------
// File index — every important path in the repo, grouped by area.

type IndexEntry = { path: string; what: string };
type IndexSection = { heading: string; entries: IndexEntry[] };

const FILE_INDEX: IndexSection[] = [
  {
    heading: "Orchestration & state",
    entries: [
      { path: "src/partner_ticket_agentic/graph.py", what: "LangGraph StateGraph wiring — the topology" },
      { path: "src/partner_ticket_agentic/memory/working.py", what: "TicketState — working-memory shape" },
      { path: "src/partner_ticket_agentic/memory/episodic.py", what: "SQLite episodic memory" },
    ],
  },
  {
    heading: "Agents (one file each)",
    entries: [
      { path: "src/partner_ticket_agentic/agents/triage.py", what: "F1 — category, urgency, confidence" },
      { path: "src/partner_ticket_agentic/agents/enricher.py", what: "F2 — partner profile, asset state" },
      { path: "src/partner_ticket_agentic/agents/router.py", what: "F3 — queue, assignee, SLA" },
      { path: "src/partner_ticket_agentic/agents/knowledge.py", what: "F4 — hybrid retrieval (FAISS + BM25)" },
      { path: "src/partner_ticket_agentic/agents/drafter.py", what: "F5 — template-driven outbound + compliance flags (HITL gate)" },
      { path: "src/partner_ticket_agentic/agents/scheduler.py", what: "F6 — appointment slot proposal" },
      { path: "src/partner_ticket_agentic/agents/linker.py", what: "F7 — duplicate / related ticket detection" },
      { path: "src/partner_ticket_agentic/agents/watchdog.py", what: "F8 — SLA-risk sidecar scanner" },
    ],
  },
  {
    heading: "LLM providers",
    entries: [
      { path: "src/partner_ticket_agentic/providers/base.py", what: "Protocol, Tier enum, approved-model registry" },
      { path: "src/partner_ticket_agentic/providers/mock.py", what: "Deterministic if/elif rules" },
      { path: "src/partner_ticket_agentic/providers/ollama.py", what: "HTTP client for local Ollama server" },
      { path: "src/partner_ticket_agentic/providers/anthropic.py", what: "Anthropic SDK + tool-use loop + prompt cache" },
      { path: "config/approved_models.yaml", what: "Tier → model-id mapping per provider" },
    ],
  },
  {
    heading: "Tools, safety & cost",
    entries: [
      { path: "src/partner_ticket_agentic/tools/registry.py", what: "Tool dispatcher with retry + circuit breaker" },
      { path: "src/partner_ticket_agentic/tools/policy.py", what: "RetryPolicy + CircuitBreaker config" },
      { path: "src/partner_ticket_agentic/safety.py", what: "ToolAllowList + injection scan + PII detection" },
      { path: "src/partner_ticket_agentic/cost.py", what: "PRICING table + CostLedger + per-tenant budgets" },
      { path: "config/budgets.yaml", what: "Per-tenant budgets with 70/90/100% alert thresholds" },
    ],
  },
  {
    heading: "Web surface",
    entries: [
      { path: "src/partner_ticket_agentic/web/app.py", what: "FastAPI endpoints" },
      { path: "src/partner_ticket_agentic/mcp_server.py", what: "MCP tool-registry server (stdio)" },
      { path: "frontend/src/app/page.tsx", what: "Demo control surface (this app's home)" },
      { path: "frontend/src/app/dashboard/page.tsx", what: "Operations dashboard + live simulator UI + hand-rolled SVG charts" },
      { path: "frontend/src/app/how-it-works/page.tsx", what: "This page" },
      { path: "frontend/src/components/topology.tsx", what: "8-node SVG state-machine diagram" },
      { path: "frontend/next.config.ts", what: "Same-origin /api/* proxy to FastAPI" },
      { path: "src/partner_ticket_agentic/web/simulator.py", what: "Live-traffic simulator — ring buffer, weighted ticket pick, provider switch" },
      { path: "data/sample_tickets.json", what: "17 seeded sample tickets across all 5 categories + Belgian partner contexts" },
    ],
  },
  {
    heading: "Tests, evals & ops scripts",
    entries: [
      { path: "tests/", what: "pytest — 193 passing" },
      { path: "evals/", what: "Per-feature eval entries (F1–F8)" },
      { path: "scripts/up.sh", what: "Start Ollama + FastAPI + Next.js, open browser" },
      { path: "scripts/down.sh", what: "Stop backend + frontend" },
      { path: "scripts/smoke.sh", what: "End-to-end provider verification" },
      { path: "scripts/preflight.sh", what: "Day-before environment check" },
    ],
  },
  {
    heading: "Design docs",
    entries: [
      { path: "docs/DESIGN.md", what: "Feature spec — F1–F8, principles, demo plan" },
      { path: "docs/AI_ACT_ASSESSMENT.md", what: "EU AI Act risk classification + GDPR controls" },
      { path: "docs/concepts/", what: "Plain-English explainer series (00–08)" },
    ],
  },
];

// --------------------------------------------------------------------
// Components

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="grid grid-cols-12 gap-3 py-2">
      <span className="col-span-12 md:col-span-5 text-[color:var(--color-ink)]">{k}</span>
      <span className="col-span-12 md:col-span-7 text-[color:var(--color-ink-soft)]">{v}</span>
    </div>
  );
}

function StackTable({ title, rows }: { title: string; rows: StackRow[] }) {
  return (
    <div className="schematic relative p-6 mb-6">
      <span className="corner-bl" />
      <span className="corner-br" />
      <div className="callout mb-4">{title}</div>
      <div className="divide-y divide-[color:var(--color-line)]">
        {rows.map((r) => (
          <div key={r.name} className="grid grid-cols-12 gap-4 py-3 text-[13.5px]">
            <div className="col-span-12 md:col-span-3">
              <div className="font-semibold text-[color:var(--color-ink)]">{r.name}</div>
              {r.version && (
                <div className="mono text-[11px] text-[color:var(--color-muted)] mt-0.5">
                  {r.version}
                </div>
              )}
            </div>
            <div className="col-span-12 md:col-span-9 leading-relaxed text-[color:var(--color-ink-soft)]">
              {r.why}
              {r.ruled && (
                <div className="mt-1 text-[12.5px] text-[color:var(--color-muted)]">
                  <span className="mono uppercase tracking-[0.1em]">ruled out:</span> {r.ruled}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CodeExcerpt({ excerpt }: { excerpt: Excerpt }) {
  return (
    <div className="mb-8">
      <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2 mb-2">
        <h3 className="display text-[20px] text-[color:var(--color-ink)]">{excerpt.title}</h3>
        <a
          href={gh(excerpt.path, excerpt.line1, excerpt.line2)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 mono text-[11px] tracking-[0.08em] text-[color:var(--color-muted)] hover:text-[color:var(--color-accent)]"
        >
          <Code size={11} />
          <span>
            {excerpt.path}:{excerpt.line1}
            {excerpt.line2 ? `–${excerpt.line2}` : ""}
          </span>
          <ExternalLink size={10} />
        </a>
      </div>
      <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] mb-3 max-w-3xl">
        {excerpt.blurb}
      </p>
      <pre className="mono text-[12.5px] leading-relaxed bg-[color:var(--color-ink)] text-[color:var(--color-paper-deep)] p-5 overflow-x-auto whitespace-pre">
{excerpt.code}
      </pre>
    </div>
  );
}

function FileIndex({ section }: { section: IndexSection }) {
  return (
    <div className="mb-8">
      <h3 className="callout mb-3">{section.heading}</h3>
      <div className="divide-y divide-[color:var(--color-line)] border border-[color:var(--color-line)]">
        {section.entries.map((e) => (
          <a
            key={e.path}
            href={gh(e.path)}
            target="_blank"
            rel="noreferrer"
            className="grid grid-cols-12 gap-4 px-4 py-2.5 text-[13px] hover:bg-[color:var(--color-paper-deep)] transition-colors"
          >
            <span className="col-span-12 md:col-span-5 mono text-[12px] text-[color:var(--color-accent-deep)] truncate">
              {e.path}
            </span>
            <span className="col-span-12 md:col-span-7 text-[color:var(--color-ink-soft)]">
              {e.what}
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// Page

export default function HowItWorksPage() {
  return (
    <div className="min-h-full">
      {/* Header — same identity as the homepage but with a back link */}
      <header className="border-b border-[color:var(--color-line)]">
        <div className="mx-auto max-w-[1100px] px-8 py-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 border border-[color:var(--color-ink)] flex items-center justify-center mono text-[11px]">
              PTA
            </div>
            <div>
              <div className="mono text-[11px] tracking-[0.22em] uppercase text-[color:var(--color-muted)]">
                Partner-Ticketing
              </div>
              <div className="display text-[18px] leading-none">How it works</div>
            </div>
          </div>
          <nav className="flex items-center gap-5 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)]">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
            >
              <ArrowLeft size={12} />
              <span>back to demo</span>
            </Link>
            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
            >
              <Code size={12} />
              <span>repo</span>
            </a>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1100px] px-8 pb-32">
        {/* Hero */}
        <section className="pt-16 pb-12">
          <div className="callout mb-5">Developer walkthrough</div>
          <h1 className="display text-[clamp(40px,5.5vw,72px)] mb-6">
            How the application works
          </h1>
          <p className="text-[17px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl">
            A complete tour of the stack — what we chose, why, and where each
            piece lives in the repository. Every code excerpt links to the
            exact file and line range on GitHub.
          </p>
          <div className="mt-8 flex flex-wrap gap-3 text-[12px] mono uppercase tracking-[0.12em] text-[color:var(--color-muted)]">
            <span className="inline-flex items-center gap-1.5">
              <Layers size={12} /> 3 layers
            </span>
            <span className="text-[color:var(--color-line-strong)]">·</span>
            <span className="inline-flex items-center gap-1.5">
              <GitBranch size={12} /> 7 specialists + 1 sidecar
            </span>
            <span className="text-[color:var(--color-line-strong)]">·</span>
            <span className="inline-flex items-center gap-1.5">
              <Code size={12} /> Python · TypeScript
            </span>
          </div>
        </section>

        <div className="divider-strong mb-12" />

        {/* Section 1: tech stack */}
        <section className="mb-16">
          <div className="callout mb-3">&sect; 01 &middot; Technology stack</div>
          <h2 className="display text-[32px] mb-3">
            One stack, three concerns
          </h2>
          <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-8">
            Backend handles the request path and the agent pipeline. The AI
            surface is a thin abstraction with three concrete providers behind
            it. Frontend is a Next.js App-Router project that proxies
            <code className="mono text-[12px] bg-[color:var(--color-bg)] px-1.5 py-0.5">/api/*</code>
            same-origin to the backend.
          </p>

          <StackTable title="Backend core" rows={BACKEND_CORE} />
          <StackTable title="AI surface" rows={AI_SURFACE} />
          <StackTable title="Frontend" rows={FRONTEND} />
        </section>

        {/* Section 2: how a request flows */}
        <section className="mb-16">
          <div className="callout mb-3">&sect; 02 &middot; How a request flows</div>
          <h2 className="display text-[32px] mb-3">
            Trace a ticket from browser to partner
          </h2>
          <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-6">
            What happens when you press <strong>Run pipeline</strong> on the
            demo page. Each step links to the exact lines that execute it.
          </p>

          <ol className="space-y-4 mb-10 max-w-3xl">
            {[
              ["1.", <>The browser POSTs <code className="mono text-[12.5px]">/api/run/sample-1?provider=mock</code>.</>],
              ["2.", <>Next.js rewrites the path same-origin to FastAPI at <code className="mono text-[12.5px]">localhost:8000</code> — see <a className="underline" href={gh("frontend/next.config.ts", 9, 16)} target="_blank" rel="noreferrer">next.config.ts</a>.</>],
              ["3.", <>FastAPI&apos;s <code className="mono text-[12.5px]">/api/run/&#123;id&#125;</code> endpoint loads the ticket, scans for PII and prompt-injection at the edge, then calls <code className="mono text-[12.5px]">run_pipeline()</code>.</>],
              ["4.", <>The LangGraph state machine fires. Triage and Linker run in parallel; Enricher joins their results; Router and Knowledge run in parallel; the conditional edge picks Scheduler vs Drafter.</>],
              ["5.", <>Each LLM-bound agent calls <code className="mono text-[12.5px]">provider.complete(messages, schema, tier)</code>. The chosen tier maps to a concrete model id via the approved-models registry.</>],
              ["6.", <>The Drafter composes the outbound message + compliance flags. Output is returned with <code className="mono text-[12.5px]">requires_approval=True</code> — the gate.</>],
              ["7.", <>The frontend renders agent cards, the HITL gate, and the cost telemetry. The human picks Approve / Edit / Reject. Nothing leaves the system without that click.</>],
            ].map(([num, content], i) => (
              <li key={i} className="grid grid-cols-12 gap-3 text-[15px]">
                <span className="col-span-1 mono text-[color:var(--color-accent-deep)] font-semibold">
                  {num}
                </span>
                <span className="col-span-11 leading-relaxed text-[color:var(--color-ink-soft)]">
                  {content}
                </span>
              </li>
            ))}
          </ol>
        </section>

        {/* Section 3: key code excerpts */}
        <section className="mb-16">
          <div className="callout mb-3">&sect; 03 &middot; Key code excerpts</div>
          <h2 className="display text-[32px] mb-3">
            The load-bearing snippets
          </h2>
          <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-10">
            Eight excerpts that carry the architectural intent. Each one
            answers a question a panel reviewer would ask.
          </p>

          {EXCERPTS.map((excerpt) => (
            <CodeExcerpt key={excerpt.title} excerpt={excerpt} />
          ))}
        </section>

        {/* Section 4: live simulator + dashboard */}
        <section className="mb-16">
          <div className="callout mb-3">&sect; 04 &middot; Live simulator &amp; dashboard</div>
          <h2 className="display text-[32px] mb-3">
            The system, under continuous load
          </h2>
          <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-6">
            The <Link className="underline" href="/dashboard">/dashboard</Link> page shows
            the system <em>operating</em>, not just executing one ticket. A
            background thread fires synthetic tickets through the real
            pipeline at a configurable interval; the dashboard polls every
            two seconds and renders the aggregate. Provider is switchable —
            the same architecture supports mock, Ollama, or Anthropic
            without any pipeline change.
          </p>

          <h3 className="display text-[20px] mb-3 mt-8">How a single tick works</h3>
          <ol className="space-y-2 mb-6 max-w-3xl text-[15px] leading-relaxed text-[color:var(--color-ink-soft)]">
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">1.</span>
              Pick a sample ticket via weighted random (circuit-down dominates so the histogram looks like real ops traffic).
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">2.</span>
              Rename the ticket <code className="mono text-[12.5px]">SIM-NNNN</code> so it's distinct in the live activity stream.
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">3.</span>
              Call <code className="mono text-[12.5px]">run_pipeline()</code> against the chosen provider. <strong>Same code path as the demo page</strong> — Triage + Linker in parallel, join at Enricher, Router + Knowledge in parallel, conditional to Scheduler / Drafter.
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">4.</span>
              Capture the real <code className="mono text-[12.5px]">TicketState</code> — category, urgency, confidence, queue, runbook, tokens, latency.
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">5.</span>
              Overlay synthetic-but-plausible fields the mock can&apos;t produce: HITL decision (biased by confidence, category, and urgency &mdash; not a flat random pick), dollar cost (when on mock), small noise on confidence so the histogram has shape.
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">6.</span>
              Append to a 500-record ring buffer. The dashboard endpoint walks this buffer to aggregate KPIs, throughput, distributions, and the recent stream.
            </li>
          </ol>

          <h3 className="display text-[20px] mb-3 mt-8">Real vs synthetic</h3>
          <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-4">
            Defensible framing for the panel: <strong>the pipeline is real,
            the gaps are synthetic substitutes for missing infrastructure</strong>.
            What&apos;s real and what&apos;s overlaid:
          </p>
          <div className="card p-5 mb-6 max-w-3xl">
            <div className="divide-y divide-[color:var(--color-line)] text-[13.5px]">
              <Row k="Category, urgency, queue, runbook, schedule" v="real — emitted by the actual agents" />
              <Row k="Latency, tokens in / out" v="real — wall-clock + provider-reported" />
              <Row k="Confidence (Triage)" v="real value + ±0.06 noise (so the 10-bucket histogram has shape)" />
              <Row k="HITL decision" v="synthetic — biased by confidence band × category × urgency (e.g. billing edits more, appointments approve more, critical never rejects)" />
              <Row k="Cost (USD)" v="real when provider=anthropic · synthetic otherwise (mock reports $0)" />
              <Row k="Cache-hit rate" v="real when anthropic · 0% otherwise" />
              <Row k="F8 Watchdog scan" v="real — calls the same watchdog as the per-ticket demo" />
            </div>
          </div>

          <h3 className="display text-[20px] mb-3 mt-8">How HITL decisions get their shape</h3>
          <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-3">
            The simulator can&apos;t fire real <em>Approve / Edit / Reject</em>
            buttons, so the HITL outcome is synthesised. A flat 78/16/6 pick
            would make the dashboard&apos;s HITL bar move uniformly across all
            categories &mdash; which isn&apos;t how operators behave. Instead
            we bias the pick in three multiplicative layers:
          </p>
          <ol className="space-y-2 mb-4 max-w-3xl text-[14px] leading-relaxed text-[color:var(--color-ink-soft)]">
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">L1</span>
              <strong>Confidence band</strong> chooses the baseline.
              <code className="mono text-[12.5px]"> conf&lt;0.6 </code>
              → operators don&apos;t trust the draft (40/35/25);
              <code className="mono text-[12.5px]"> conf&lt;0.75 </code>
              → mild distrust (65/25/10); higher → standard 78/16/6.
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">L2</span>
              <strong>Category</strong> multiplier tilts the baseline.
              Billing &amp; provisioning edit at ~1.6&times; baseline (numbers and
              commitments must be verified); appointment requests approve at
              ~1.2&times; (templated, low-stakes).
            </li>
            <li>
              <span className="mono text-[color:var(--color-accent-deep)] font-semibold mr-2">L3</span>
              <strong>Urgency</strong> finishes the tilt.
              <code className="mono text-[12.5px]"> critical </code>
              gets <em>approved</em> ×1.15 and <em>rejected</em> ×0.5 &mdash; ops
              teams approve fast under pressure and almost never reject outright.
            </li>
          </ol>
          <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-6">
            The full picker is{" "}
            <a
              className="underline"
              href={gh("src/partner_ticket_agentic/web/simulator.py", 268, 305)}
              target="_blank"
              rel="noreferrer"
            >
              <code className="mono text-[12.5px]">_pick_hitl()</code> in simulator.py
            </a>{" "}
            — about 30 lines, no randomness inside the bias logic itself,
            entirely driven by data tables a reviewer can inspect.
            In production the function is replaced with a query against the
            ticket&apos;s real Approve/Edit/Reject button-press events.
          </p>

          <h3 className="display text-[20px] mb-3 mt-8">Why mock is the default</h3>
          <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-3">
            Three reasons:
          </p>
          <ul className="list-disc pl-6 space-y-1.5 max-w-3xl text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] mb-6">
            <li><strong>Speed</strong> — ~25 ms per tick versus 5-7 s on Ollama and 0.5-2 s on Anthropic. The dashboard fills up quickly.</li>
            <li><strong>Determinism</strong> — same input gives the same output, so the category distribution is stable over a 20-minute panel demo.</li>
            <li><strong>Cost</strong> — $0. A 30-minute simulator run on Anthropic at 3-second interval would burn ~$1.20 in tokens; on mock, $0.</li>
          </ul>
          <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl">
            For the demo where it matters — proving real-LLM behaviour — use Ollama on the demo page (one ticket, watch the local model think). For real cost-accounting and prompt-cache behaviour, use Anthropic. The dashboard supports all three; switch via the dropdown.
          </p>
        </section>

        {/* Section 5: file index */}
        <section className="mb-16">
          <div className="callout mb-3">&sect; 05 &middot; File index</div>
          <h2 className="display text-[32px] mb-3">
            Where to find every component
          </h2>
          <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl mb-8">
            Grouped by area. Click any row to open the file on GitHub.
          </p>

          {FILE_INDEX.map((section) => (
            <FileIndex key={section.heading} section={section} />
          ))}
        </section>

        {/* Back link */}
        <div className="text-center pt-8 border-t border-[color:var(--color-line)]">
          <Link
            href="/"
            className="inline-flex items-center gap-2 btn btn-ghost"
          >
            <ArrowLeft size={14} />
            <span>Back to the live demo</span>
          </Link>
        </div>
      </main>

      <footer className="border-t border-[color:var(--color-ink)] bg-[color:var(--color-paper-deep)]">
        <div className="mx-auto max-w-[1100px] px-8 py-8 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)] flex justify-between flex-wrap gap-4">
          <span>Ajay Antony &middot; Capgemini Blue Harvest reference impl &middot; 2026</span>
          <a href={REPO} target="_blank" rel="noreferrer" className="hover:text-[color:var(--color-ink)]">
            github.com/ajayantony/partner-ticket-agentic
          </a>
        </div>
      </footer>
    </div>
  );
}
