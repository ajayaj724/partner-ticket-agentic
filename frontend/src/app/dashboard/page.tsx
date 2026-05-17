"use client";

/**
 * /dashboard — operations + AI quality + cost view.
 *
 * Reads from /api/stats/dashboard (which aggregates over the in-memory
 * simulator window) and renders KPIs, throughput, distributions, a live
 * activity stream, and the F8 watchdog panel. The "live mode" toggle
 * controls the backend simulator's lifecycle.
 *
 * Charts are hand-rolled SVG to match the engineering-schematic aesthetic
 * without committing to a charting library.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Brain,
  ChevronRight,
  Code,
  Layers,
  Lightbulb,
  Pause,
  Play,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";

import {
  getDashboardStats,
  getInsights,
  getSimulatorStatus,
  resetSimulator,
  runWatchdog,
  startSimulator,
  stopSimulator,
  type DashboardStats,
  type DashboardRunRecord,
  type Insight,
  type InsightsResponse,
  type Provider,
} from "@/lib/api";

const POLL_MS = 2000;
const DEFAULT_INTERVAL_S = 3;

const fmtUsd = (n: number, places = 4) => `$${n.toFixed(places)}`;
const fmtNum = (n: number) => n.toLocaleString();
const fmtMs = (n: number) => `${n}ms`;

const HITL_ORDER: Array<"approved" | "edited" | "rejected"> = [
  "approved",
  "edited",
  "rejected",
];
const HITL_COLOR: Record<string, string> = {
  approved: "var(--color-ok)",
  edited: "var(--color-warn)",
  rejected: "var(--color-danger)",
};

const URGENCY_BORDER: Record<string, string> = {
  critical: "var(--color-danger)",
  high: "var(--color-warn)",
  medium: "var(--color-line-strong)",
  low: "var(--color-line-strong)",
  normal: "var(--color-line-strong)",
};

type Toast = {
  id: string;
  ticket: DashboardRunRecord;
  expires: number;
};

// ---------------------------------------------------------------------------
// Page

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [interval, setInterval_] = useState(DEFAULT_INTERVAL_S);
  const [provider, setProvider] = useState<Provider>("mock");
  const [watchdog, setWatchdog] = useState<Awaited<ReturnType<typeof runWatchdog>> | null>(null);
  const [watchdogBusy, setWatchdogBusy] = useState(false);
  const [insights, setInsights] = useState<InsightsResponse | null>(null);
  const [insightsBusy, setInsightsBusy] = useState(false);
  const pollingRef = useRef<number | null>(null);
  const insightsTimerRef = useRef<number | null>(null);

  // Toast + flash state. Tracking seen ticket IDs so we only notify on
  // *new* records, not on every poll. On the first load we silently
  // remember everything that's already there — otherwise the user gets
  // 20 toasts in a row.
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [flashIds, setFlashIds] = useState<Set<string>>(new Set());
  const seenIds = useRef<Set<string>>(new Set());
  const seededRef = useRef<boolean>(false);
  const prevTicketCountRef = useRef<number>(0);
  const [kpiPulseKey, setKpiPulseKey] = useState<number>(0);

  const refresh = useCallback(async () => {
    try {
      const next = await getDashboardStats();
      setStats(next);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // Detect newly-arrived records on every stats update; spawn toasts +
  // mark rows for the activity-stream flash + pulse the KPI tile.
  useEffect(() => {
    if (!stats || stats.empty) return;

    if (!seededRef.current) {
      // First load — remember everything quietly, don't spam toasts.
      for (const record of stats.recent) seenIds.current.add(record.sim_ticket_id);
      seededRef.current = true;
      prevTicketCountRef.current = stats.kpis.tickets_processed;
      return;
    }

    // KPI pulse if the count went up
    if (stats.kpis.tickets_processed > prevTicketCountRef.current) {
      prevTicketCountRef.current = stats.kpis.tickets_processed;
      setKpiPulseKey((k) => k + 1);
    }

    // Find records we haven't seen yet (stats.recent is newest-first)
    const fresh: DashboardRunRecord[] = [];
    for (const record of stats.recent) {
      if (!seenIds.current.has(record.sim_ticket_id)) {
        fresh.push(record);
        seenIds.current.add(record.sim_ticket_id);
      }
    }
    if (fresh.length === 0) return;

    // Spawn toasts (oldest first so the visual order is newest-on-top
    // when prepended)
    const now = Date.now();
    setToasts((prev) => {
      const incoming: Toast[] = fresh
        .slice()
        .reverse()
        .map((record) => ({
          id: record.sim_ticket_id,
          ticket: record,
          expires: now + 3500,
        }));
      return [...incoming, ...prev].slice(0, 4);
    });

    // Mark these IDs for a brief flash on the activity-stream row
    setFlashIds((prev) => {
      const next = new Set(prev);
      for (const record of fresh) next.add(record.sim_ticket_id);
      return next;
    });
    // Clear the flash markers after a beat
    const flashIds = fresh.map((r) => r.sim_ticket_id);
    window.setTimeout(() => {
      setFlashIds((prev) => {
        const next = new Set(prev);
        for (const id of flashIds) next.delete(id);
        return next;
      });
    }, 1400);
  }, [stats]);

  // Toast cleanup loop
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setToasts((prev) => prev.filter((t) => t.expires > now));
    }, 400);
    return () => window.clearInterval(timer);
  }, [toasts.length]);

  // Poll continuously — cheap, in-process.
  useEffect(() => {
    refresh();
    pollingRef.current = window.setInterval(refresh, POLL_MS);
    return () => {
      if (pollingRef.current !== null) window.clearInterval(pollingRef.current);
    };
  }, [refresh]);

  const running = stats && !stats.empty ? stats.running : false;

  const handleToggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (running) await stopSimulator();
      else await startSimulator(interval, provider);
      const fresh = await getSimulatorStatus();
      // Update the running flag immediately rather than wait for the next poll.
      setStats((prev) =>
        prev
          ? ({ ...prev, running: fresh.running, provider: fresh.provider } as DashboardStats)
          : prev,
      );
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const refreshInsights = useCallback(async () => {
    setInsightsBusy(true);
    try {
      const next = await getInsights(provider);
      setInsights(next);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInsightsBusy(false);
    }
  }, [provider]);

  // Auto-refresh insights every 12 s while the simulator is producing data.
  // 12 s matches roughly four 3-second simulator ticks — meaningful window
  // shift, not aggressive enough to hammer the LLM.
  //
  // Split into two effects intentionally. Earlier we kept the timer in one
  // effect that depended on `stats` — stats updates every 2 s via the dashboard
  // poll, which would clear and re-create the interval every poll, so the 12 s
  // tick never fired. Two effects keep the timer alive across stats updates.
  const refreshInsightsRef = useRef(refreshInsights);
  refreshInsightsRef.current = refreshInsights;
  const hasData = Boolean(stats && !stats.empty);

  // Effect 1 — first refresh when data first appears.
  useEffect(() => {
    if (hasData && insights === null) {
      refreshInsightsRef.current();
    }
  }, [hasData, insights]);

  // Effect 2 — periodic refresh. Owns the timer; survives stats updates
  // because it only re-runs when the producing/not-producing state flips.
  useEffect(() => {
    if (!hasData) return;
    const id = window.setInterval(() => refreshInsightsRef.current(), 12_000);
    insightsTimerRef.current = id;
    return () => {
      window.clearInterval(id);
      insightsTimerRef.current = null;
    };
  }, [hasData]);

  // Clear all simulator state + frontend caches when switching providers.
  // Surfaces a synthetic "system" toast so the operator sees what happened.
  const handleResetForSwitch = async (newProvider: Provider) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await resetSimulator();
      seenIds.current = new Set();
      seededRef.current = false;
      prevTicketCountRef.current = 0;
      setInsights(null);
      setWatchdog(null);
      setFlashIds(new Set());

      const now = Date.now();
      const sysTicket: DashboardRunRecord = {
        trace_id: `sys-reset-${now}`,
        base_ticket_id: "—",
        sim_ticket_id: `SYS · provider switch`,
        category: `cleared ${result.cleared_records} record${result.cleared_records === 1 ? "" : "s"}`,
        urgency: "info",
        confidence: 1,
        queue: `→ ${newProvider}`,
        sla_minutes: 0,
        runbook_id: "—",
        hitl_decision: "approved",
        tokens_in: 0,
        tokens_out: 0,
        cache_hit_rate: 0,
        cost_usd: 0,
        duration_ms: 0,
        started_at: new Date(now).toISOString(),
        scheduler_used: false,
      };
      setToasts((prev) => [
        { id: `sys-${now}`, ticket: sysTicket, expires: now + 4000 },
        ...prev,
      ].slice(0, 4));

      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleWatchdog = async () => {
    if (watchdogBusy) return;
    setWatchdogBusy(true);
    try {
      const result = await runWatchdog("mock");
      setWatchdog(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWatchdogBusy(false);
    }
  };

  return (
    <div className="min-h-full">
      <ToastStack toasts={toasts} />
      <DashboardHeader running={running} />

      <main className="mx-auto max-w-[1320px] px-8 pb-32">
        {/* Hero + simulator controls */}
        <section className="pt-12 pb-8 grid grid-cols-12 gap-8 items-end">
          <div className="col-span-12 lg:col-span-8">
            <div className="callout mb-3">Operations dashboard</div>
            <h1 className="display text-[clamp(36px,5vw,64px)] mb-3">
              The system, in operation
            </h1>
            <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)] max-w-3xl">
              Per-ticket detail lives on the demo page. This view is the
              operational shape — throughput, category mix, HITL decisions,
              cost burndown, confidence drift, and the F8 watchdog. Data
              comes from a live simulator that fires synthetic tickets
              through the real pipeline against the mock provider.
            </p>
          </div>
          <div className="col-span-12 lg:col-span-4">
            <SimulatorControls
              running={running}
              busy={busy}
              interval={interval}
              setInterval={setInterval_}
              provider={provider}
              setProvider={setProvider}
              activeProvider={stats?.provider ?? "mock"}
              onToggle={handleToggle}
              onResetForSwitch={handleResetForSwitch}
              ticketsProcessed={stats && !stats.empty ? stats.kpis.tickets_processed : 0}
            />
          </div>
        </section>

        {error && (
          <div className="mb-8 p-4 border-l-2 border-[color:var(--color-danger)] bg-[color:var(--color-danger-soft)]">
            <div className="callout text-[color:var(--color-danger)]">Error</div>
            <p className="mono text-[12.5px] mt-1">{error}</p>
          </div>
        )}

        {/* Empty state */}
        {stats?.empty && (
          <EmptyState running={running} onStart={handleToggle} />
        )}

        {/* Full dashboard */}
        {stats && !stats.empty && (
          <>
            {/* ============================== KPI tiles */}
            <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
              <KpiTile
                label="Tickets processed"
                value={fmtNum(stats.kpis.tickets_processed)}
                sub="since simulator start"
                live={running}
                pulseKey={kpiPulseKey}
              />
              <KpiTile
                label="Drafts pending (edit)"
                value={fmtNum(stats.kpis.drafts_pending)}
                sub="last 50 runs"
              />
              <KpiTile
                label="Avg latency"
                value={fmtMs(stats.kpis.avg_duration_ms)}
                sub="end-to-end pipeline"
              />
              <KpiTile
                label="Spend (synthetic)"
                value={fmtUsd(stats.kpis.spend_usd, 4)}
                sub={`${fmtUsd(stats.kpis.avg_cost_per_ticket_usd, 5)} / ticket`}
                accent
              />
            </section>

            {/* ============================== Throughput + categories */}
            <section className="grid grid-cols-12 gap-6 mb-10">
              <div className="col-span-12 lg:col-span-8 card p-6">
                <div className="flex items-baseline justify-between mb-4">
                  <h3 className="display text-[18px]">
                    Throughput &middot; last 2 hours
                  </h3>
                  <span className="callout">
                    {stats.bucket_count} &times; {stats.bucket_minutes}-min buckets
                  </span>
                </div>
                <ThroughputChart
                  buckets={stats.throughput_5min}
                  costBuckets={stats.cost_5min_usd}
                  bucketMinutes={stats.bucket_minutes}
                />
              </div>

              <div className="col-span-12 lg:col-span-4 card p-6">
                <h3 className="display text-[18px] mb-4">Categories</h3>
                <CategoryBars data={stats.categories} />
              </div>
            </section>

            {/* ============================== HITL + Confidence + Urgency */}
            <section className="grid grid-cols-12 gap-6 mb-10">
              <div className="col-span-12 md:col-span-4 card p-6">
                <h3 className="display text-[18px] mb-3">HITL decisions</h3>
                <p className="text-[12.5px] text-[color:var(--color-muted)] mb-4">
                  Operator action on each generated draft.
                </p>
                <HitlBar decisions={stats.hitl_decisions} />
              </div>

              <div className="col-span-12 md:col-span-4 card p-6">
                <h3 className="display text-[18px] mb-3">Confidence (Triage)</h3>
                <p className="text-[12.5px] text-[color:var(--color-muted)] mb-4">
                  Distribution of model confidence, 0.0 &rarr; 1.0.
                </p>
                <ConfidenceHistogram buckets={stats.confidence_histogram} />
              </div>

              <div className="col-span-12 md:col-span-4 card p-6">
                <h3 className="display text-[18px] mb-3">Urgency mix</h3>
                <p className="text-[12.5px] text-[color:var(--color-muted)] mb-4">
                  Triage-assigned urgency band.
                </p>
                <UrgencyMix urgency={stats.urgency} />
              </div>
            </section>

            {/* ============================== Cost + activity stream */}
            <section className="grid grid-cols-12 gap-6 mb-10">
              <div className="col-span-12 lg:col-span-5 card p-6">
                <h3 className="display text-[18px] mb-2">Cost burndown</h3>
                <p className="text-[12.5px] text-[color:var(--color-muted)] mb-4">
                  Spend per 5-min bucket. Synthetic at-Anthropic-Haiku rate.
                </p>
                <CostBurndown
                  buckets={stats.cost_5min_usd}
                  bucketMinutes={stats.bucket_minutes}
                />
                <div className="mt-4 grid grid-cols-3 gap-3 text-[12.5px]">
                  <Tiny
                    label="tokens in"
                    value={fmtNum(stats.kpis.tokens_in)}
                  />
                  <Tiny
                    label="tokens out"
                    value={fmtNum(stats.kpis.tokens_out)}
                  />
                  <Tiny
                    label="cache hit"
                    value={`${Math.round(stats.kpis.cache_hit_mean * 100)}%`}
                  />
                </div>
              </div>

              <div className="col-span-12 lg:col-span-7 card p-6">
                <div className="flex items-baseline justify-between mb-4">
                  <h3 className="display text-[18px]">Live activity</h3>
                  <span className="callout inline-flex items-center gap-1.5">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        running
                          ? "bg-[color:var(--color-accent)] pulse"
                          : "bg-[color:var(--color-line-strong)]"
                      }`}
                    />
                    {running ? "streaming" : "paused"}
                  </span>
                </div>
                <ActivityStream rows={stats.recent} flashIds={flashIds} />
              </div>
            </section>

            {/* ============================== F9 Insights */}
            <InsightsSection
              insights={insights}
              busy={insightsBusy}
              onRefresh={refreshInsights}
              activeProvider={stats.provider}
            />

            {/* ============================== F8 Watchdog */}
            <section className="card p-6 mb-10">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
                <div>
                  <h3 className="display text-[18px] mb-1">
                    F8 Watchdog &middot; sidecar SLA scan
                  </h3>
                  <p className="text-[13px] text-[color:var(--color-muted)]">
                    Out-of-band scan. Surfaces tickets at risk of breaching their SLA.
                  </p>
                </div>
                <button
                  onClick={handleWatchdog}
                  disabled={watchdogBusy}
                  className="btn btn-ghost inline-flex items-center gap-2"
                >
                  <ShieldAlert size={14} />
                  <span>{watchdogBusy ? "Scanning…" : "Run scan now"}</span>
                </button>
              </div>
              <WatchdogPanel report={watchdog} />
            </section>
          </>
        )}
      </main>

      <DashboardFooter />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header / footer

function DashboardHeader({ running }: { running: boolean }) {
  return (
    <header className="border-b border-[color:var(--color-line)]">
      <div className="mx-auto max-w-[1320px] px-8 py-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 border border-[color:var(--color-ink)] flex items-center justify-center mono text-[11px]">
            PTA
          </div>
          <div>
            <div className="mono text-[11px] tracking-[0.22em] uppercase text-[color:var(--color-muted)]">
              Partner-Ticketing
            </div>
            <div className="display text-[18px] leading-none">Dashboard</div>
          </div>
        </div>
        <nav className="flex items-center gap-5 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)]">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
          >
            <ArrowLeft size={12} />
            <span>demo</span>
          </Link>
          <Link
            href="/how-it-works"
            className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
          >
            <Layers size={12} />
            <span>how it works</span>
          </Link>
          <a
            href="https://github.com/ajayantony/partner-ticket-agentic"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
          >
            <Code size={12} />
            <span>repo</span>
          </a>
          <span className="inline-flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${
                running
                  ? "bg-[color:var(--color-accent)] pulse"
                  : "bg-[color:var(--color-line-strong)]"
              }`}
            />
            {running ? "live" : "idle"}
          </span>
        </nav>
      </div>
    </header>
  );
}

function DashboardFooter() {
  return (
    <footer className="border-t border-[color:var(--color-ink)] bg-[color:var(--color-paper-deep)]">
      <div className="mx-auto max-w-[1320px] px-8 py-8 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)] flex justify-between flex-wrap gap-3">
        <span>
          Synthetic stream &middot; mock provider &middot; in-process simulator
        </span>
        <span>Costs are at-Anthropic-Haiku rates for shape only</span>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Simulator controls

const PROVIDER_NOTES: Record<Provider, { sub: string; warn?: string }> = {
  mock: {
    sub: "Deterministic if/elif — offline, ~25 ms / tick, $0",
  },
  ollama: {
    sub: "Local llama3.2:3b via Ollama — ~5–7 s / tick, $0 (GPU only)",
    warn: "Ollama must be running on :11434 with the model pulled",
  },
  anthropic: {
    sub: "Cloud Claude (Haiku) — ~0.5–2 s / tick, real $$ per call",
    warn: "Requires ANTHROPIC_API_KEY env var on the backend",
  },
};

function SimulatorControls({
  running,
  busy,
  interval,
  setInterval,
  provider,
  setProvider,
  activeProvider,
  onToggle,
  onResetForSwitch,
  ticketsProcessed,
}: {
  running: boolean;
  busy: boolean;
  interval: number;
  setInterval: (n: number) => void;
  provider: Provider;
  setProvider: (p: Provider) => void;
  activeProvider: Provider;
  onToggle: () => void;
  onResetForSwitch: (p: Provider) => void;
  ticketsProcessed: number;
}) {
  // Show a confirm banner when:
  //   - the local provider selection differs from what the simulator is currently using
  //   - and there's existing data that the switch would mix
  const pendingSwitch =
    !running && provider !== activeProvider && ticketsProcessed > 0;
  const note = PROVIDER_NOTES[provider];
  const PROVIDERS: { id: Provider; label: string }[] = [
    { id: "mock", label: "mock" },
    { id: "ollama", label: "ollama" },
    { id: "anthropic", label: "anthropic" },
  ];
  return (
    <div className="schematic relative p-5">
      <span className="corner-bl" />
      <span className="corner-br" />
      <div className="callout mb-3">Live simulator</div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="display text-[28px] leading-none mb-1">
            {ticketsProcessed}
          </div>
          <div className="text-[12px] text-[color:var(--color-muted)]">
            tickets processed
            {running && (
              <>
                {" · "}
                <span className="text-[color:var(--color-accent)]">
                  via {activeProvider}
                </span>
              </>
            )}
          </div>
        </div>
        <button
          onClick={onToggle}
          disabled={busy}
          className={`btn inline-flex items-center gap-2 ${
            running ? "btn-ghost" : "btn-primary"
          }`}
          style={{ minWidth: 120 }}
        >
          {running ? <Pause size={14} /> : <Play size={14} />}
          <span>{running ? "Pause" : "Start"}</span>
        </button>
      </div>

      <div className="mb-4">
        <span className="callout">Provider</span>
        <div className="mt-2 flex -ml-px">
          {PROVIDERS.map((p) => {
            const active = provider === p.id;
            return (
              <button
                key={p.id}
                onClick={() => setProvider(p.id)}
                disabled={running || busy}
                className={[
                  "btn flex-1 rounded-none border-r-0 last:border-r mono text-[12px]",
                  active ? "btn-primary" : "btn-ghost",
                ].join(" ")}
                title={
                  running
                    ? "Pause the simulator before switching providers"
                    : `Switch to ${p.label}`
                }
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <p className="text-[11.5px] text-[color:var(--color-muted)] mt-2 leading-snug">
          {note.sub}
          {note.warn && (
            <>
              <br />
              <span className="text-[color:var(--color-warn)]">{note.warn}</span>
            </>
          )}
        </p>

        <AnimatePresence>
          {pendingSwitch && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.25 }}
              className="overflow-hidden"
            >
              <div
                className="mt-3 p-3 border-l-[3px] text-[12px] leading-relaxed"
                style={{
                  borderLeftColor: "var(--color-warn)",
                  backgroundColor: "var(--color-warn-soft)",
                  color: "var(--color-ink)",
                }}
              >
                <div className="font-semibold mb-1">
                  Switch from {activeProvider} &rarr; {provider}?
                </div>
                <p className="text-[color:var(--color-ink-soft)] mb-2.5">
                  Mixing {activeProvider} and {provider} metrics in the same
                  window makes the KPIs meaningless. Clearing{" "}
                  <span className="mono">{ticketsProcessed}</span> existing{" "}
                  {activeProvider} record{ticketsProcessed === 1 ? "" : "s"} and
                  starting fresh.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => onResetForSwitch(provider)}
                    disabled={busy}
                    className="btn btn-primary py-1 px-3 text-[11px]"
                  >
                    Clear &amp; switch
                  </button>
                  <button
                    onClick={() => setProvider(activeProvider)}
                    disabled={busy}
                    className="btn btn-ghost py-1 px-3 text-[11px]"
                  >
                    Keep {activeProvider}
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <label className="block">
        <span className="callout">Interval (seconds)</span>
        <div className="flex items-center gap-3 mt-2">
          <input
            type="range"
            min={1}
            max={10}
            step={0.5}
            value={interval}
            onChange={(e) => setInterval(parseFloat(e.target.value))}
            className="flex-1 accent-[color:var(--color-accent)]"
            disabled={running || busy}
          />
          <span className="mono text-[13px] w-12 text-right">
            {interval.toFixed(1)}s
          </span>
        </div>
      </label>
      <p className="text-[11.5px] text-[color:var(--color-muted)] mt-3 leading-snug">
        Provider and interval are locked while running. Pause to change them.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state

function EmptyState({ running, onStart }: { running: boolean; onStart: () => void }) {
  return (
    <div className="card p-10 text-center mb-10">
      <Sparkles size={28} className="mx-auto text-[color:var(--color-accent)] mb-3" />
      <h3 className="display text-[24px] mb-2">No data yet</h3>
      <p className="text-[14px] text-[color:var(--color-ink-soft)] mb-5 max-w-md mx-auto">
        Start the simulator to fire synthetic tickets through the pipeline.
        The dashboard fills in as the stream produces records.
      </p>
      {!running && (
        <button onClick={onStart} className="btn btn-primary inline-flex items-center gap-2">
          <Play size={14} />
          <span>Start live stream</span>
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// KPI tile

function KpiTile({
  label,
  value,
  sub,
  live,
  accent,
  pulseKey,
}: {
  label: string;
  value: string;
  sub: string;
  live?: boolean;
  accent?: boolean;
  pulseKey?: number;
}) {
  return (
    <motion.div
      animate={
        pulseKey !== undefined && pulseKey > 0
          ? {
              backgroundColor: [
                "var(--color-paper)",
                "var(--color-accent-soft)",
                "var(--color-paper)",
              ],
            }
          : undefined
      }
      transition={{ duration: 0.9, ease: "easeOut" }}
      key={`kpi-${label}-${pulseKey ?? 0}`}
      className={`card p-5 ${
        accent ? "border-[color:var(--color-accent)]" : ""
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="callout">{label}</span>
        {live && (
          <span className="inline-flex items-center gap-1 text-[10px] mono uppercase tracking-[0.1em] text-[color:var(--color-accent)]">
            <span className="w-1.5 h-1.5 rounded-full bg-[color:var(--color-accent)] pulse" />
            live
          </span>
        )}
      </div>
      <motion.div
        key={`v-${value}`}
        initial={
          pulseKey !== undefined && pulseKey > 0
            ? { scale: 1.08, color: "var(--color-accent-deep)" }
            : { scale: 1 }
        }
        animate={{ scale: 1, color: accent ? "var(--color-accent-deep)" : "var(--color-ink)" }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        className="display text-[32px] leading-none mb-1"
      >
        {value}
      </motion.div>
      <div className="text-[11.5px] text-[color:var(--color-muted)]">{sub}</div>
    </motion.div>
  );
}

function Tiny({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="callout">{label}</div>
      <div className="mono text-[14px] text-[color:var(--color-ink)] mt-0.5">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Throughput chart — area + bars hybrid

function ThroughputChart({
  buckets,
  costBuckets,
  bucketMinutes,
}: {
  buckets: number[];
  costBuckets: number[];
  bucketMinutes: number;
}) {
  const width = 800;
  const height = 180;
  const padding = { top: 12, right: 16, bottom: 28, left: 30 };
  const max = Math.max(1, ...buckets);
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const barW = innerW / buckets.length;

  const pts = buckets.map((v, i) => {
    const x = padding.left + i * barW + barW / 2;
    const y = padding.top + innerH - (v / max) * innerH;
    return { x, y, v };
  });
  const areaPath = [
    `M ${padding.left} ${padding.top + innerH}`,
    ...pts.map((p) => `L ${p.x} ${p.y}`),
    `L ${padding.left + innerW} ${padding.top + innerH}`,
    "Z",
  ].join(" ");
  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");

  const tickLabels = Array.from({ length: 5 }).map((_, i) => {
    const idx = Math.round(((buckets.length - 1) * i) / 4);
    const minsAgo = (buckets.length - 1 - idx) * bucketMinutes;
    return {
      x: padding.left + idx * barW + barW / 2,
      label: minsAgo === 0 ? "now" : `-${minsAgo}m`,
    };
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      shapeRendering="geometricPrecision"
    >
      {/* Grid lines */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <line
          key={f}
          x1={padding.left}
          x2={padding.left + innerW}
          y1={padding.top + innerH - f * innerH}
          y2={padding.top + innerH - f * innerH}
          stroke="var(--color-line)"
          strokeWidth={1}
          strokeDasharray={f === 0 ? undefined : "2 3"}
        />
      ))}
      {/* Y axis labels */}
      {[0, 0.5, 1].map((f) => {
        const value = Math.round(f * max);
        return (
          <text
            key={f}
            x={padding.left - 6}
            y={padding.top + innerH - f * innerH + 3}
            textAnchor="end"
            fontFamily="var(--font-mono), monospace"
            fontSize={9.5}
            fill="var(--color-muted)"
          >
            {value}
          </text>
        );
      })}
      {/* Area + line */}
      <motion.path
        d={areaPath}
        fill="var(--color-accent-soft)"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
      />
      <motion.path
        d={linePath}
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth={1.8}
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.6 }}
      />
      {/* Dots */}
      {pts.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={p.v > 0 ? 2.2 : 0}
          fill="var(--color-accent)"
        />
      ))}
      {/* X axis ticks */}
      {tickLabels.map((t, i) => (
        <text
          key={i}
          x={t.x}
          y={height - 6}
          textAnchor="middle"
          fontFamily="var(--font-mono), monospace"
          fontSize={9.5}
          fill="var(--color-muted)"
        >
          {t.label}
        </text>
      ))}
      {/* Caption */}
      <text
        x={padding.left}
        y={padding.top - 2}
        fontFamily="var(--font-mono), monospace"
        fontSize={9.5}
        fill="var(--color-muted)"
      >
        tickets / 5 min
      </text>
      {/* Hidden but accessible: cost-per-bucket totals on hover would go here */}
      {costBuckets.length > 0 && null}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Category horizontal bars

function CategoryBars({ data }: { data: Array<[string, number]> }) {
  const total = data.reduce((s, [, n]) => s + n, 0) || 1;
  return (
    <div className="space-y-3">
      {data.map(([cat, n]) => {
        const pct = (n / total) * 100;
        return (
          <div key={cat}>
            <div className="flex justify-between text-[12.5px] mb-1">
              <span className="text-[color:var(--color-ink)]">{cat}</span>
              <span className="mono text-[color:var(--color-muted)]">
                {n} &middot; {pct.toFixed(0)}%
              </span>
            </div>
            <div className="h-2 bg-[color:var(--color-line-soft)] overflow-hidden">
              <motion.div
                className="h-full bg-[color:var(--color-accent)]"
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.4 }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// HITL decisions — single stacked bar

function HitlBar({ decisions }: { decisions: Record<string, number> }) {
  const total = HITL_ORDER.reduce((s, k) => s + (decisions[k] ?? 0), 0) || 1;
  return (
    <div>
      <div className="h-8 flex overflow-hidden">
        {HITL_ORDER.map((k) => {
          const v = decisions[k] ?? 0;
          const pct = (v / total) * 100;
          return (
            <motion.div
              key={k}
              className="h-full"
              style={{ backgroundColor: HITL_COLOR[k] }}
              initial={{ width: 0 }}
              animate={{ width: `${pct}%` }}
              transition={{ duration: 0.4 }}
              title={`${k}: ${v}`}
            />
          );
        })}
      </div>
      <div className="mt-3 space-y-1.5">
        {HITL_ORDER.map((k) => {
          const v = decisions[k] ?? 0;
          const pct = (v / total) * 100;
          return (
            <div key={k} className="flex items-center text-[12.5px]">
              <span
                className="w-2.5 h-2.5 mr-2 inline-block"
                style={{ backgroundColor: HITL_COLOR[k] }}
              />
              <span className="text-[color:var(--color-ink)] mr-auto capitalize">
                {k}
              </span>
              <span className="mono text-[color:var(--color-muted)]">
                {v} &middot; {pct.toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confidence histogram — 10 vertical columns

function ConfidenceHistogram({ buckets }: { buckets: number[] }) {
  const max = Math.max(1, ...buckets);
  return (
    <div>
      <div className="flex items-end gap-1 h-24">
        {buckets.map((v, i) => {
          const h = (v / max) * 100;
          const isLow = i < 4;
          return (
            <motion.div
              key={i}
              className="flex-1"
              style={{
                backgroundColor: isLow
                  ? "var(--color-warn)"
                  : "var(--color-ink-soft)",
              }}
              initial={{ height: 0 }}
              animate={{ height: `${h}%` }}
              transition={{ duration: 0.4, delay: i * 0.02 }}
              title={`${(i / 10).toFixed(1)}-${((i + 1) / 10).toFixed(1)}: ${v}`}
            />
          );
        })}
      </div>
      <div className="flex justify-between mt-2 text-[10.5px] mono text-[color:var(--color-muted)]">
        <span>0.0</span>
        <span>0.5</span>
        <span>1.0</span>
      </div>
      <p className="text-[11px] text-[color:var(--color-muted)] mt-2 leading-snug">
        Amber columns mark confidence &lt; 0.4 &mdash; those tickets escalate
        to a human queue rather than auto-route.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Urgency mix — donut-ish radial

function UrgencyMix({ urgency }: { urgency: Record<string, number> }) {
  const ORDER = ["critical", "high", "normal", "low"];
  const COLOR: Record<string, string> = {
    critical: "var(--color-danger)",
    high: "var(--color-warn)",
    normal: "var(--color-ok)",
    low: "var(--color-line-strong)",
  };
  const total = ORDER.reduce((s, k) => s + (urgency[k] ?? 0), 0) || 1;
  return (
    <div className="space-y-2.5">
      {ORDER.map((k) => {
        const v = urgency[k] ?? 0;
        const pct = (v / total) * 100;
        return (
          <div key={k} className="flex items-center gap-3 text-[13px]">
            <span
              className="w-2.5 h-2.5 inline-block"
              style={{ backgroundColor: COLOR[k] }}
            />
            <span className="text-[color:var(--color-ink)] capitalize w-20">
              {k}
            </span>
            <div className="flex-1 h-1.5 bg-[color:var(--color-line-soft)] overflow-hidden">
              <motion.div
                className="h-full"
                style={{ backgroundColor: COLOR[k] }}
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.4 }}
              />
            </div>
            <span className="mono text-[color:var(--color-muted)] w-10 text-right">
              {v}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cost burndown chart

function CostBurndown({
  buckets,
  bucketMinutes,
}: {
  buckets: number[];
  bucketMinutes: number;
}) {
  const width = 540;
  const height = 140;
  const padding = { top: 8, right: 12, bottom: 22, left: 38 };
  const cumulative: number[] = [];
  let acc = 0;
  for (const v of buckets) {
    acc += v;
    cumulative.push(acc);
  }
  const max = Math.max(0.0001, ...cumulative);
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const pts = cumulative.map((v, i) => {
    const x = padding.left + (i / Math.max(1, cumulative.length - 1)) * innerW;
    const y = padding.top + innerH - (v / max) * innerH;
    return { x, y };
  });
  const areaPath = [
    `M ${padding.left} ${padding.top + innerH}`,
    ...pts.map((p) => `L ${p.x} ${p.y}`),
    `L ${padding.left + innerW} ${padding.top + innerH}`,
    "Z",
  ].join(" ");
  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const oldest = (buckets.length - 1) * bucketMinutes;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto">
      {[0, 0.5, 1].map((f) => {
        const value = max * f;
        return (
          <g key={f}>
            <line
              x1={padding.left}
              x2={padding.left + innerW}
              y1={padding.top + innerH - f * innerH}
              y2={padding.top + innerH - f * innerH}
              stroke="var(--color-line)"
              strokeWidth={1}
              strokeDasharray={f === 0 ? undefined : "2 3"}
            />
            <text
              x={padding.left - 4}
              y={padding.top + innerH - f * innerH + 3}
              textAnchor="end"
              fontFamily="var(--font-mono), monospace"
              fontSize={9.5}
              fill="var(--color-muted)"
            >
              {fmtUsd(value, 3)}
            </text>
          </g>
        );
      })}
      <motion.path d={areaPath} fill="var(--color-accent-soft)" />
      <motion.path
        d={linePath}
        fill="none"
        stroke="var(--color-accent-deep)"
        strokeWidth={1.8}
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.6 }}
      />
      <text
        x={padding.left}
        y={height - 6}
        fontFamily="var(--font-mono), monospace"
        fontSize={9.5}
        fill="var(--color-muted)"
      >
        -{oldest}m
      </text>
      <text
        x={padding.left + innerW}
        y={height - 6}
        textAnchor="end"
        fontFamily="var(--font-mono), monospace"
        fontSize={9.5}
        fill="var(--color-muted)"
      >
        now
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Activity stream

function ActivityStream({
  rows,
  flashIds,
}: {
  rows: DashboardRunRecord[];
  flashIds: Set<string>;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-[13px] text-[color:var(--color-muted)]">
        No runs yet. Start the simulator to see live traffic.
      </p>
    );
  }
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleTimeString("en-GB", { hour12: false }).slice(0, 8);
  return (
    <div className="divide-y divide-[color:var(--color-line)] -mx-2">
      {rows.map((r) => {
        const decisionColor = HITL_COLOR[r.hitl_decision] ?? "var(--color-ink)";
        const isNew = flashIds.has(r.sim_ticket_id);
        return (
          <motion.div
            key={r.trace_id || r.sim_ticket_id}
            initial={{ opacity: 0, x: -8 }}
            animate={
              isNew
                ? {
                    opacity: 1,
                    x: 0,
                    backgroundColor: [
                      "var(--color-accent-soft)",
                      "rgba(240, 216, 207, 0.4)",
                      "rgba(0,0,0,0)",
                    ],
                  }
                : { opacity: 1, x: 0 }
            }
            transition={
              isNew
                ? { duration: 1.2, ease: "easeOut" }
                : { duration: 0.25 }
            }
            className="grid grid-cols-12 gap-2 px-2 py-2 text-[12.5px] items-center"
          >
            <span className="col-span-2 mono text-[color:var(--color-muted)]">
              {fmtTime(r.started_at)}
            </span>
            <span className="col-span-2 mono text-[color:var(--color-accent-deep)]">
              {r.sim_ticket_id}
            </span>
            <span className="col-span-3 text-[color:var(--color-ink)] truncate">
              {r.category}
            </span>
            <span className="col-span-1 mono text-[color:var(--color-muted)]">
              {r.urgency}
            </span>
            <span className="col-span-1 mono text-[color:var(--color-muted)] text-right">
              {fmtMs(r.duration_ms)}
            </span>
            <span className="col-span-2 mono text-[color:var(--color-muted)] text-right">
              {fmtUsd(r.cost_usd, 4)}
            </span>
            <span
              className="col-span-1 mono text-[10px] uppercase tracking-[0.06em] text-right"
              style={{ color: decisionColor }}
            >
              {r.hitl_decision.slice(0, 3)}
            </span>
          </motion.div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// F8 Watchdog panel

// ---------------------------------------------------------------------------
// F9 Insights section — what the LLM saw across the recent window

const INSIGHT_KIND_META: Record<
  Insight["kind"],
  { icon: typeof Brain; label: string; color: string }
> = {
  trend: { icon: TrendingUp, label: "Trend", color: "var(--color-accent-deep)" },
  anomaly: { icon: AlertTriangle, label: "Anomaly", color: "var(--color-danger)" },
  segment: { icon: Users, label: "Segment", color: "var(--color-ink-soft)" },
  recommendation: { icon: Lightbulb, label: "Recommendation", color: "var(--color-warn)" },
};

const INSIGHT_SEVERITY_BG: Record<Insight["severity"], string> = {
  info: "var(--color-paper)",
  warn: "var(--color-warn-soft)",
  alert: "var(--color-danger-soft)",
};

function InsightsSection({
  insights,
  busy,
  onRefresh,
  activeProvider,
}: {
  insights: InsightsResponse | null;
  busy: boolean;
  onRefresh: () => void;
  activeProvider: Provider;
}) {
  if (insights === null) {
    return null;
  }
  if (insights.empty) {
    return (
      <section className="card p-6 mb-10">
        <div className="flex items-center gap-2 mb-2">
          <Brain size={16} className="text-[color:var(--color-accent)]" />
          <h3 className="display text-[18px]">F9 Insights</h3>
        </div>
        <p className="text-[13px] text-[color:var(--color-muted)]">
          {insights.reason}
        </p>
      </section>
    );
  }
  const generated = new Date(insights.generated_at);
  return (
    <section className="card p-6 mb-10">
      <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <Brain size={18} className="text-[color:var(--color-accent)]" />
          <h3 className="display text-[18px]">F9 Insights</h3>
          <span className="callout">
            &middot; AI synthesis over last {insights.window_size}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11.5px] mono text-[color:var(--color-muted)]">
          <span>
            via {activeProvider} &middot; refreshed{" "}
            {generated.toLocaleTimeString("en-GB", { hour12: false }).slice(0, 8)}
          </span>
          <button
            onClick={onRefresh}
            disabled={busy}
            className="btn btn-ghost inline-flex items-center gap-1.5 py-1 px-3"
            title="Re-run the insights agent now"
          >
            <RefreshCw size={11} className={busy ? "animate-spin" : ""} />
            <span>{busy ? "Thinking…" : "Refresh"}</span>
          </button>
        </div>
      </div>

      <p className="text-[14px] leading-relaxed text-[color:var(--color-ink-soft)] mb-5 max-w-4xl">
        {insights.summary}
      </p>

      {insights.insights.length === 0 ? (
        <p className="text-[13px] text-[color:var(--color-muted)]">
          No insights this round.
        </p>
      ) : (
        <div className="grid grid-cols-12 gap-4">
          {insights.insights.map((insight, i) => (
            <InsightCard key={`${insight.title}-${i}`} insight={insight} delay={i * 0.05} />
          ))}
        </div>
      )}
    </section>
  );
}

function InsightCard({ insight, delay }: { insight: Insight; delay: number }) {
  const meta = INSIGHT_KIND_META[insight.kind];
  const Icon = meta.icon;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay }}
      className="col-span-12 md:col-span-6 lg:col-span-4 p-4 border-l-[3px]"
      style={{
        borderLeftColor: meta.color,
        backgroundColor: INSIGHT_SEVERITY_BG[insight.severity],
      }}
    >
      <div className="flex items-center justify-between mb-2">
        <span
          className="inline-flex items-center gap-1.5 mono text-[10px] tracking-[0.1em] uppercase"
          style={{ color: meta.color }}
        >
          <Icon size={11} />
          <span>{meta.label}</span>
        </span>
        <span
          className="mono text-[10px] uppercase tracking-[0.08em] text-[color:var(--color-muted)]"
        >
          {insight.severity} &middot; {Math.round(insight.confidence * 100)}%
        </span>
      </div>
      <h4 className="text-[14px] font-semibold leading-snug text-[color:var(--color-ink)] mb-1.5">
        {insight.title}
      </h4>
      <p className="text-[12.5px] leading-relaxed text-[color:var(--color-ink-soft)] mb-2.5">
        {insight.detail}
      </p>
      {insight.evidence_ids.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {insight.evidence_ids.slice(0, 6).map((id) => (
            <span
              key={id}
              className="mono text-[10px] px-1.5 py-0.5 bg-[color:var(--color-paper)] border border-[color:var(--color-line)] text-[color:var(--color-muted)]"
            >
              {id}
            </span>
          ))}
          {insight.evidence_ids.length > 6 && (
            <span className="mono text-[10px] text-[color:var(--color-muted)]">
              +{insight.evidence_ids.length - 6}
            </span>
          )}
        </div>
      )}
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Toast stack — slides notifications in from the bottom-right corner whenever
// a new ticket arrives. Max 4 visible; each auto-dismisses after ~3.5s.

function ToastStack({ toasts }: { toasts: Toast[] }) {
  return (
    <div
      aria-live="polite"
      className="fixed bottom-6 right-6 z-[60] flex flex-col gap-2.5 pointer-events-none"
    >
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastCard({ toast }: { toast: Toast }) {
  const t = toast.ticket;
  const borderColor =
    URGENCY_BORDER[t.urgency] ?? "var(--color-line-strong)";
  const decisionColor = HITL_COLOR[t.hitl_decision] ?? "var(--color-ink)";
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleTimeString("en-GB", { hour12: false }).slice(0, 8);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 40, scale: 0.96 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      exit={{ opacity: 0, x: 40, scale: 0.96 }}
      transition={{ type: "spring", stiffness: 320, damping: 28 }}
      style={{ borderLeftColor: borderColor }}
      className="pointer-events-auto w-[320px] bg-[color:var(--color-paper)] border border-[color:var(--color-line-strong)] border-l-[3px] shadow-[0_8px_24px_-6px_rgba(28,27,23,0.18)] p-3.5"
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="mono text-[10.5px] tracking-[0.1em] uppercase text-[color:var(--color-muted)]">
          New ticket &middot; {fmtTime(t.started_at)}
        </span>
        <span
          className="mono text-[10px] uppercase tracking-[0.08em]"
          style={{ color: decisionColor }}
        >
          {t.hitl_decision}
        </span>
      </div>
      <div className="mono text-[12.5px] text-[color:var(--color-accent-deep)] mb-1.5">
        {t.sim_ticket_id}
      </div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13.5px] font-semibold text-[color:var(--color-ink)] truncate">
          {t.category}
        </span>
        <span
          className="mono text-[10.5px] uppercase tracking-[0.08em] shrink-0"
          style={{ color: borderColor }}
        >
          {t.urgency}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11.5px] text-[color:var(--color-muted)] mono">
        <span>queue {t.queue}</span>
        <span>{t.duration_ms} ms</span>
        <span>{fmtUsd(t.cost_usd, 4)}</span>
      </div>
      {/* Progress bar that counts down the toast's lifetime */}
      <motion.div
        initial={{ width: "100%" }}
        animate={{ width: "0%" }}
        transition={{ duration: 3.5, ease: "linear" }}
        className="mt-2.5 h-[2px]"
        style={{ backgroundColor: borderColor }}
      />
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// F8 Watchdog panel

function WatchdogPanel({
  report,
}: {
  report: Awaited<ReturnType<typeof runWatchdog>> | null;
}) {
  if (!report) {
    return (
      <p className="text-[13px] text-[color:var(--color-muted)]">
        No scan yet. Press <em>Run scan now</em> to fire the F8 watchdog
        against the current set of tickets.
      </p>
    );
  }
  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-5">
        <Tiny label="scanned" value={fmtNum(report.scanned)} />
        <Tiny label="at-risk" value={fmtNum(report.at_risk.length)} />
        <Tiny label="notified" value={fmtNum(report.notified)} />
        <Tiny label="escalated" value={fmtNum(report.escalated)} />
        <Tiny label="deduplicated" value={fmtNum(report.deduplicated)} />
      </div>
      {report.at_risk.length === 0 ? (
        <p className="text-[13px] text-[color:var(--color-muted)]">
          No tickets are at risk of breaching SLA.
        </p>
      ) : (
        <div className="divide-y divide-[color:var(--color-line)] border-t border-[color:var(--color-line)]">
          {report.at_risk.map((entry) => {
            const bandColor =
              entry.risk_band === "high"
                ? "var(--color-danger)"
                : entry.risk_band === "med"
                ? "var(--color-warn)"
                : "var(--color-muted)";
            return (
              <div
                key={entry.ticket_id}
                className="grid grid-cols-12 gap-2 py-2.5 text-[12.5px] items-center"
              >
                <span className="col-span-2 mono text-[color:var(--color-accent-deep)]">
                  {entry.ticket_id}
                </span>
                <span className="col-span-2 text-[color:var(--color-ink)]">
                  {entry.queue}
                </span>
                <span className="col-span-3 mono text-[color:var(--color-muted)]">
                  {entry.elapsed_minutes}m / {entry.sla_minutes}m
                </span>
                <span
                  className="col-span-1 mono text-[11px] uppercase"
                  style={{ color: bandColor }}
                >
                  {entry.risk_band}
                </span>
                <span className="col-span-2 mono text-[color:var(--color-muted)]">
                  risk {entry.risk.toFixed(2)}
                </span>
                <span className="col-span-2 mono text-[color:var(--color-muted)] inline-flex items-center gap-1 justify-end">
                  <ChevronRight size={11} />
                  {entry.action_taken}
                </span>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-4 inline-flex items-center gap-2 text-[11.5px] text-[color:var(--color-muted)]">
        <Activity size={12} />
        F8 runs out-of-band — it does not block the request path.
      </div>
    </div>
  );
}
