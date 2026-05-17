/**
 * API client for the FastAPI backend.
 *
 * In development, Next.js's `rewrites()` proxies `/api/*` to
 * `localhost:8000`, so we can use same-origin fetches. In production
 * the same proxy can be deployed at the edge.
 */

export type Ticket = {
  ticket_id: string;
  partner_id: string;
  subject: string;
  description: string;
  submitted_at?: string;
};

export type TriageOutput = {
  category: string;
  urgency: string;
  confidence: number;
  rationale?: string;
  entities?: {
    circuits?: string[];
    appointments?: string[];
    invoices?: string[];
    locations?: string[];
  };
};

export type Slot = {
  engineer_id: string;
  starts_at: string;
  ends_at: string;
  score: number;
};

export type CostSummary = {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cached_input_tokens: number;
  cost_usd: number;
  cache_hit_rate: number;
  by_agent?: Record<
    string,
    { calls: number; tokens_in: number; tokens_out: number; cost_usd: number }
  >;
};

export type PipelineState = {
  ticket_id: string;
  partner_id: string;
  subject: string;
  description: string;
  trace_id?: string;
  provider?: string;
  triage?: TriageOutput;
  enrichment?: {
    partner_profile?: { partner_id: string; name: string; tier: string };
    asset_state?: unknown[];
    recent_tickets?: unknown[];
    relevant_runbooks?: unknown[];
    unavailable?: string[];
  };
  routing?: {
    queue: string;
    assignee?: { user_id: string; name?: string };
    sla_minutes: number;
    confidence: number;
    rationale?: string;
  };
  knowledge?: {
    top_runbook?: { runbook_id: string; title: string };
    citation?: string;
    confidence: number;
    suggested_steps?: string[];
    fallback_reason?: string | null;
  };
  related?: {
    is_likely_duplicate: boolean;
    related?: Array<{ ticket_id: string; similarity: number; status: string }>;
    confidence: number;
    rationale?: string;
  };
  schedule?: {
    proposed_slots?: Slot[];
    fallback_reason?: string | null;
  };
  draft?: {
    template_id: string;
    subject: string;
    body: string;
    compliance_flags: string[];
    blocked: boolean;
    requires_approval: boolean;
  };
  pii_findings?: Array<{ kind: string; match: string }>;
  cost?: CostSummary;
};

export type TraceEntry = Record<string, unknown> & {
  ts?: string;
  level?: string;
  logger?: string;
  message?: string;
};

export type RunResponse = {
  ticket: Ticket;
  state: PipelineState;
  trace: TraceEntry[];
  provider_resolved: string;
};

export type Provider = "mock" | "ollama" | "anthropic";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${path} → HTTP ${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export async function listTickets(): Promise<Ticket[]> {
  return getJson<Ticket[]>("/api/tickets");
}

export async function runTicket(
  ticketId: string,
  provider: Provider = "mock",
): Promise<RunResponse> {
  return getJson<RunResponse>(
    `/api/run/${encodeURIComponent(ticketId)}?provider=${provider}`,
  );
}

export async function runWatchdog(provider: Provider = "mock"): Promise<{
  scanned: number;
  at_risk: Array<{
    ticket_id: string;
    queue: string;
    elapsed_minutes: number;
    sla_minutes: number;
    risk: number;
    risk_band: string;
    action_taken: string;
  }>;
  notified: number;
  escalated: number;
  deduplicated: number;
}> {
  return getJson(`/api/watchdog?provider=${provider}`);
}

export async function injectionCheck(text: string): Promise<{
  rejected: boolean;
  matches: Array<{ pattern: string; match: string }>;
}> {
  const res = await fetch("/api/inject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`inject → HTTP ${res.status}`);
  return res.json();
}

// ====== Dashboard / live simulator ==========================================

export type SimulatorStatus = {
  running: boolean;
  interval_seconds: number;
  provider: Provider;
  started_at: string | null;
  tickets_processed: number;
  history_size: number;
};

export type DashboardRunRecord = {
  trace_id: string;
  base_ticket_id: string;
  sim_ticket_id: string;
  category: string;
  urgency: string;
  confidence: number;
  queue: string;
  sla_minutes: number;
  runbook_id: string;
  hitl_decision: "approved" | "edited" | "rejected";
  tokens_in: number;
  tokens_out: number;
  cache_hit_rate: number;
  cost_usd: number;
  duration_ms: number;
  started_at: string;
  scheduler_used: boolean;
};

export type DashboardStats =
  | { empty: true; running: boolean; interval_seconds: number; provider: Provider }
  | {
      empty: false;
      running: boolean;
      interval_seconds: number;
      provider: Provider;
      kpis: {
        tickets_processed: number;
        drafts_pending: number;
        in_flight: number;
        spend_usd: number;
        avg_duration_ms: number;
        avg_cost_per_ticket_usd: number;
        tokens_in: number;
        tokens_out: number;
        cache_hit_mean: number;
      };
      throughput_5min: number[];
      cost_5min_usd: number[];
      bucket_minutes: number;
      bucket_count: number;
      categories: Array<[string, number]>;
      urgency: Record<string, number>;
      hitl_decisions: Record<string, number>;
      confidence_histogram: number[];
      recent: DashboardRunRecord[];
    };

export async function getDashboardStats(): Promise<DashboardStats> {
  return getJson<DashboardStats>("/api/stats/dashboard");
}

export async function getSimulatorStatus(): Promise<SimulatorStatus> {
  return getJson<SimulatorStatus>("/api/simulate/status");
}

export async function startSimulator(
  intervalSeconds: number,
  provider: Provider = "mock",
): Promise<{ running: boolean; provider: Provider }> {
  const res = await fetch(
    `/api/simulate/start?interval=${encodeURIComponent(intervalSeconds)}&provider=${provider}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`simulate/start → HTTP ${res.status}`);
  return res.json();
}

export async function stopSimulator(): Promise<{ running: boolean }> {
  const res = await fetch("/api/simulate/stop", { method: "POST" });
  if (!res.ok) throw new Error(`simulate/stop → HTTP ${res.status}`);
  return res.json();
}

export async function resetSimulator(): Promise<{
  cleared_records: number;
  cleared_counter: number;
  running: boolean;
}> {
  const res = await fetch("/api/simulate/reset", { method: "POST" });
  if (!res.ok) throw new Error(`simulate/reset → HTTP ${res.status}`);
  return res.json();
}

// ====== F9 Insights agent ===================================================

export type Insight = {
  kind: "trend" | "anomaly" | "segment" | "recommendation";
  title: string;
  detail: string;
  severity: "info" | "warn" | "alert";
  confidence: number;
  evidence_ids: string[];
};

export type InsightsResponse =
  | { empty: true; reason: string }
  | {
      empty?: false;
      window_size: number;
      generated_at: string;
      summary: string;
      insights: Insight[];
    };

export async function getInsights(
  provider: Provider = "mock",
  window: number = 100,
): Promise<InsightsResponse> {
  return getJson<InsightsResponse>(
    `/api/insights?provider=${provider}&window=${window}`,
  );
}
