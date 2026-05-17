"use client";

/**
 * Partner-Ticketing Agentic Platform — control surface.
 *
 * Light-theme "engineering schematic". Picks a ticket, picks a provider,
 * runs it through the LangGraph and shows what each agent produced.
 * Drafter is the F5 HITL gate — its card has the Approve/Edit/Reject
 * action row that real operators would click.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowRight,
  Check,
  Loader2,
  Pencil,
  X,
  ShieldAlert,
  Cpu,
  Code,
  Layers,
  Activity,
} from "lucide-react";

import {
  listTickets,
  runTicket,
  type PipelineState,
  type Provider,
  type RunResponse,
  type Ticket,
} from "@/lib/api";
import { Topology, type AgentId, type NodeState } from "@/components/topology";

// ------------------------------------------------------------------
// Helpers

const PROVIDERS: { id: Provider; label: string; note: string }[] = [
  { id: "mock",      label: "mock",      note: "offline · deterministic" },
  { id: "ollama",    label: "ollama",    note: "local · llama3.2" },
  { id: "anthropic", label: "anthropic", note: "claude · prompt cache" },
];

// Stage ordering for the run-animation. Each stage activates one or more
// nodes for ~delay ms, then marks them complete. The `story` text is the
// plain-English narration shown to non-expert viewers so they can follow
// what each agent is doing in real time.
const STAGES: { delay: number; nodes: AgentId[]; story: string }[] = [
  { delay: 280, nodes: ["intake"],
    story: "Intake is scanning the ticket for sensitive data and prompt-injection patterns at the edge." },
  { delay: 380, nodes: ["triage", "linker"],
    story: "Triage is classifying category and urgency. Linker is searching for duplicate tickets — they run in parallel." },
  { delay: 320, nodes: ["enricher"],
    story: "Enricher is pulling the partner profile, asset state, and recent ticket history." },
  { delay: 380, nodes: ["router", "knowledge"],
    story: "Router is picking the queue and SLA. Knowledge is retrieving the relevant runbook — also in parallel." },
  { delay: 320, nodes: ["scheduler"],
    story: "Scheduler is proposing on-site appointment slots. (Skipped for tickets that don't need a technician.)" },
  { delay: 280, nodes: ["drafter"],
    story: "Drafter is composing the outbound message and checking compliance. Pausing for your approval next." },
];

// Plain-English subtitles for the agent cards so non-experts can follow
// without knowing what the F-codes mean.
const AGENT_SUBTITLES: Record<string, string> = {
  F1: "Classifies the ticket — category, urgency, confidence",
  F2: "Pulls partner profile, assets, recent history",
  F3: "Picks the queue, assignee, and SLA",
  F4: "Finds the right runbook (BM25 + vector search)",
  F7: "Detects duplicate / related tickets",
  F6: "Proposes on-site appointment slots",
};

const fmtUsd = (n: number) => `$${n.toFixed(4)}`;
const fmtNum = (n: number) => n.toLocaleString();

// REQ OBS-02 · save the full run response (ticket + state + trace + provider)
// to disk as JSON. The same payload the CLI's `--export-trace` flag emits, so
// operators have parity between the two surfaces. File name embeds the
// ticket_id + trace_id so saved files don't collide across runs.
function downloadTrace(res: RunResponse): void {
  const ticketId = res.ticket?.ticket_id ?? "unknown";
  const traceId = res.state?.trace_id ?? "no-trace-id";
  const blob = new Blob([JSON.stringify(res, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `trace-${ticketId}-${traceId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke after a tick so the click handler has fully processed.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// Plain-English wrap-up shown after the pipeline finishes. Pulls out the
// fields a non-expert viewer would actually care about and stitches them
// into one sentence — what category, who got assigned, did we skip the
// scheduler, is a draft waiting for review.
function summariseRun(res: RunResponse): string {
  const s = res.state;
  const cat = s.triage?.category ?? "—";
  const urg = s.triage?.urgency ?? "—";
  const queue = s.routing?.queue ?? "—";
  const sla = s.routing?.sla_minutes;
  const slots = s.schedule?.proposed_slots?.length ?? 0;
  const blocked = s.draft?.blocked === true;
  const parts: string[] = [];
  parts.push(`Classified as ${cat} / ${urg}, routed to ${queue}${sla ? ` (${sla} min SLA)` : ""}.`);
  if (slots > 0) parts.push(`Scheduler proposed ${slots} appointment slot${slots === 1 ? "" : "s"}.`);
  else parts.push("Scheduler skipped — no on-site work needed.");
  if (blocked) parts.push("Draft is BLOCKED on compliance — needs your edit before send.");
  else parts.push("Draft is ready — choose Approve, Edit, or Reject below.");
  return parts.join(" ");
}

// ------------------------------------------------------------------
// Page

export default function Page() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [ticketId, setTicketId] = useState<string>("");
  // UI default is Ollama: when the panel hits this page, the three LLM-bound
  // agents (Triage, Watchdog, Insights) should call a real local model, not the
  // deterministic if/elif mock. Mock stays available behind the provider
  // chip for CI / fallback / offline demo.
  const [provider, setProvider] = useState<Provider>("ollama");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nodeStates, setNodeStates] = useState<Partial<Record<AgentId, NodeState>>>({});
  const [hitlChoice, setHitlChoice] = useState<"approve" | "edit" | "reject" | null>(null);
  const [narration, setNarration] = useState<string>(
    "Pick a ticket and an LLM provider, then press Run to send it through the pipeline.",
  );
  const [runProgress, setRunProgress] = useState<number>(0);

  // Section refs so we can auto-scroll the viewport to follow the
  // pipeline's progress instead of forcing the user to manually chase
  // each new section.
  const narratorRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<HTMLDivElement>(null);
  const hitlRef = useRef<HTMLDivElement>(null);

  function scrollTo(ref: React.RefObject<HTMLDivElement | null>) {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Bootstrap ticket list
  useEffect(() => {
    listTickets()
      .then((rows) => {
        setTickets(rows);
        if (rows.length && !ticketId) setTicketId(rows[0].ticket_id);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onRun() {
    if (!ticketId || running) return;
    setRunning(true);
    setResult(null);
    setError(null);
    setHitlChoice(null);
    setNodeStates({});
    setNarration("Pipeline starting…");
    setRunProgress(0);

    // Auto-scroll viewport to the live-commentary strip so the user can
    // follow the narration without having to scroll manually.
    requestAnimationFrame(() => scrollTo(narratorRef));

    // Animate the stages while the real fetch happens in parallel.
    const fetchP = runTicket(ticketId, provider);
    let acc: Partial<Record<AgentId, NodeState>> = {};
    for (let i = 0; i < STAGES.length; i++) {
      const stage = STAGES[i];
      acc = { ...acc, ...Object.fromEntries(stage.nodes.map((n) => [n, "active"])) };
      setNodeStates({ ...acc });
      setNarration(stage.story);
      await new Promise((r) => setTimeout(r, stage.delay));
      acc = { ...acc, ...Object.fromEntries(stage.nodes.map((n) => [n, "complete"])) };
      setNodeStates({ ...acc });
      setRunProgress(Math.round(((i + 1) / STAGES.length) * 100));
    }

    try {
      const res = await fetchP;
      setResult(res);
      if (!res.state.schedule?.proposed_slots?.length) {
        setNodeStates((s) => ({ ...s, scheduler: "skipped" }));
      }
      setNarration(summariseRun(res));
      // Walk the viewport through the result: agent cards first, then
      // the HITL gate where the operator actually acts.
      setTimeout(() => scrollTo(cardsRef), 250);
      setTimeout(() => scrollTo(hitlRef), 2200);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setNarration("Something failed — see the error block above.");
    } finally {
      setRunning(false);
      setTimeout(() => setRunProgress(0), 1200);
    }
  }

  const selectedTicket = useMemo(
    () => tickets.find((t) => t.ticket_id === ticketId),
    [tickets, ticketId],
  );

  const state = result?.state;

  return (
    <div className="min-h-full">
      {/* Sticky pipeline-progress bar — visible at the very top of the
          viewport whenever a run is in flight. Fills accent-amber as
          stages complete; dissolves a moment after the run finishes. */}
      <motion.div
        aria-hidden
        className="fixed top-0 left-0 right-0 z-50 h-[3px] bg-[color:var(--color-line-soft)]"
        animate={{ opacity: running || runProgress > 0 ? 1 : 0 }}
        transition={{ duration: 0.25 }}
      >
        <motion.div
          className="h-full bg-[color:var(--color-accent)]"
          animate={{ width: `${runProgress}%` }}
          transition={{ duration: 0.3, ease: "easeOut" }}
        />
      </motion.div>

      <Header />

      <main className="mx-auto max-w-[1240px] px-8 pb-32">
        {/* ============================ Hero */}
        <section className="grid grid-cols-12 gap-8 pt-16 pb-12">
          <div className="col-span-12 lg:col-span-8">
            <div className="callout mb-5">Capgemini · Blue Harvest · Reference Implementation</div>
            <h1 className="display text-[clamp(48px,7vw,108px)]">
              Eight specialists,
              <br />
              <span className="display-italic">one human</span>
              <span className="display">,</span>{" "}
              <span className="display-italic">one gate.</span>
            </h1>
            <p className="mt-8 max-w-2xl text-lg leading-relaxed text-[color:var(--color-ink-soft)]">
              A real LangGraph state machine routes partner-installation tickets through
              triage, enrichment, knowledge retrieval, scheduling, and drafting — with
              an F5 approval step on every outbound message. Ollama (local llama3.2) by
              default; mock and Anthropic are opt-in via the provider switch.
            </p>
          </div>

          <aside className="col-span-12 lg:col-span-4 lg:pl-8 lg:border-l lg:border-[color:var(--color-line)]">
            <div className="callout mb-3">Margin notes</div>
            <p className="margin-note">
              The operator (a Belgian telecom) <em>files</em> tickets <em>with</em> upstream
              fiber-installation partners — supplier-side B2B field service. Every outbound
              message passes through the human gate.
            </p>
            <div className="mt-6 grid grid-cols-2 gap-3">
              <Spec k="Runtime" v="Python 3.14" />
              <Spec k="Orchestration" v="LangGraph 1.x" />
              <Spec k="Schemas" v="Pydantic v2" />
              <Spec k="Memory" v="3 tiers" />
              <Spec k="Tests" v="193 passing" />
              <Spec k="Default LLM" v="ollama · local" />
            </div>
          </aside>
        </section>

        <div className="divider-strong mb-12" />

        {/* ============================ Controls */}
        <section className="grid grid-cols-12 gap-8 mb-10">
          <div className="col-span-12 lg:col-span-8">
            <div className="callout mb-3">§ 01 · Controls</div>
            <div className="schematic relative p-7">
              <span className="corner-bl" />
              <span className="corner-br" />

              {/* Provider */}
              <div className="mb-7">
                <div className="callout mb-2">Provider</div>
                <div className="flex flex-wrap gap-0 -ml-px">
                  {PROVIDERS.map((p) => {
                    const active = provider === p.id;
                    return (
                      <button
                        key={p.id}
                        onClick={() => setProvider(p.id)}
                        disabled={running}
                        className={[
                          "btn",
                          active ? "btn-primary" : "btn-ghost",
                          "rounded-none",
                          "border-r-0 last:border-r",
                        ].join(" ")}
                        style={{ minWidth: 160 }}
                      >
                        <span className="mono text-[12px]">{p.label}</span>
                        <span className="ml-2 text-[11px] opacity-70 mono">{p.note}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Ticket + Run */}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
                <div className="flex-1">
                  <div className="callout mb-2">Ticket</div>
                  <select
                    value={ticketId}
                    onChange={(e) => setTicketId(e.target.value)}
                    disabled={running || !tickets.length}
                    className="w-full mono text-[13px] px-3 py-2.5 bg-[color:var(--color-paper)] border border-[color:var(--color-line-strong)] focus:outline-none focus:border-[color:var(--color-ink)]"
                  >
                    {tickets.length === 0 ? (
                      <option>loading…</option>
                    ) : (
                      tickets.map((t) => (
                        <option key={t.ticket_id} value={t.ticket_id}>
                          {t.ticket_id} — {t.subject}
                        </option>
                      ))
                    )}
                  </select>
                </div>
                <button
                  onClick={onRun}
                  disabled={running || !ticketId}
                  className="btn btn-primary inline-flex items-center gap-2"
                  style={{ minWidth: 200, height: 44 }}
                >
                  {running ? (
                    <>
                      <Loader2 size={14} className="animate-spin" />
                      <span>Running…</span>
                    </>
                  ) : (
                    <>
                      <span>Run pipeline</span>
                      <ArrowRight size={14} />
                    </>
                  )}
                </button>
              </div>

              {selectedTicket && (
                <div className="mt-6 pt-5 border-t border-[color:var(--color-line)]">
                  <div className="callout mb-2">Ticket body — verbatim</div>
                  <p className="text-[15px] leading-relaxed text-[color:var(--color-ink-soft)]">
                    <span className="display-italic text-[color:var(--color-ink)]">
                      “{selectedTicket.subject}”
                    </span>
                    <br />
                    <span className="mono text-[12.5px]">{selectedTicket.description}</span>
                  </p>
                </div>
              )}

              {error && (
                <div className="mt-6 p-4 border-l-2 border-[color:var(--color-danger)] bg-[color:var(--color-danger-soft)]">
                  <div className="callout text-[color:var(--color-danger)]">Error</div>
                  <p className="mono text-[12.5px] mt-1">{error}</p>
                </div>
              )}
            </div>
          </div>

          <aside className="col-span-12 lg:col-span-4 lg:pl-8 lg:border-l lg:border-[color:var(--color-line)]">
            <div className="callout mb-3">Why this matters</div>
            <p className="margin-note">
              The same code path runs against three providers. Mock is{" "}
              <em>deterministic</em> — a reviewer can predict the output by reading
              the rules. Ollama proves the prompt works on a small local model.
              Anthropic adds tool-use and prompt caching.
            </p>
          </aside>
        </section>

        {/* ============================ Plain-English narrator */}
        <section ref={narratorRef} className="mb-10 scroll-mt-6">
          <div className="callout mb-3">Live commentary &middot; what's happening right now</div>
          <div
            className={`schematic relative p-6 flex items-center gap-5 ${
              running ? "border-[color:var(--color-accent)]" : ""
            }`}
          >
            <span className="corner-bl" />
            <span className="corner-br" />
            <div
              aria-hidden
              className={`shrink-0 w-3 h-3 rounded-full ${
                running
                  ? "bg-[color:var(--color-accent)] pulse"
                  : result
                  ? "bg-[color:var(--color-ok)]"
                  : "bg-[color:var(--color-line-strong)]"
              }`}
            />
            <p
              className="display-italic text-[clamp(18px,2vw,22px)] leading-snug text-[color:var(--color-ink)]"
              aria-live="polite"
            >
              {narration}
            </p>
          </div>
        </section>

        {/* ============================ Topology */}
        <section className="grid grid-cols-12 gap-8 mb-12">
          <div className="col-span-12 lg:col-span-8">
            <div className="callout mb-3">
              &sect; 02 &middot; Pipeline topology
              <span className="ml-2 normal-case tracking-normal text-[color:var(--color-muted)]">
                &mdash; the eight agents this ticket flows through
              </span>
            </div>
            <div className="schematic relative p-6">
              <span className="corner-bl" />
              <span className="corner-br" />
              <Topology states={nodeStates} />
            </div>
          </div>

          <aside className="col-span-12 lg:col-span-4 lg:pl-8 lg:border-l lg:border-[color:var(--color-line)] flex flex-col justify-between">
            <div>
              <div className="callout mb-3">Reading the schematic</div>
              <p className="margin-note">
                Solid lines are <em>unconditional</em> edges. Dashed lines fire only
                when the routing decision selects on-site work. The scheduler is{" "}
                <em>skipped</em> for billing or knowledge tickets.
              </p>
            </div>
            <div className="mt-8">
              <div className="callout mb-3">Run status</div>
              <div className="grid grid-cols-2 gap-2 text-[12.5px] mono">
                <Stat k="trace" v={state?.trace_id ?? "—"} />
                <Stat k="provider" v={result?.provider_resolved ?? "—"} />
                <Stat k="category" v={state?.triage?.category ?? "—"} />
                <Stat k="urgency" v={state?.triage?.urgency ?? "—"} />
              </div>
            </div>
          </aside>
        </section>

        {/* ============================ Agent outputs */}
        <div ref={cardsRef} className="scroll-mt-6">
        {/* Skeletons while the run is in flight — so the user sees a
            loading state before real cards arrive. */}
        {running && !state && (
          <section className="mb-14">
            <div className="callout mb-3">
              &sect; 03 &middot; What each agent decided
              <span className="ml-2 normal-case tracking-normal text-[color:var(--color-muted)]">
                &mdash; populating as agents finish&hellip;
              </span>
            </div>
            <div className="grid grid-cols-12 gap-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <SkeletonCard key={i} delay={i * 0.07} />
              ))}
            </div>
          </section>
        )}
        <AnimatePresence>
          {state && (
            <motion.section
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.4 }}
              className="mb-14"
            >
              <div className="callout mb-3">
                &sect; 03 &middot; What each agent decided
                <span className="ml-2 normal-case tracking-normal text-[color:var(--color-muted)]">
                  &mdash; one structured output per agent
                </span>
              </div>
              <div className="grid grid-cols-12 gap-6">
                {state.triage && (
                  <AgentCard
                    code="F1"
                    name="Triage"
                    span={4}
                    delay={0.05}
                    rows={[
                      ["category", state.triage.category],
                      ["urgency", state.triage.urgency],
                      ["confidence", state.triage.confidence.toFixed(2)],
                    ]}
                    body={state.triage.rationale}
                  />
                )}
                {state.enrichment && (
                  <AgentCard
                    code="F2"
                    name="Enricher"
                    span={4}
                    delay={0.10}
                    rows={[
                      ["partner", state.enrichment.partner_profile?.name ?? "—"],
                      ["tier", state.enrichment.partner_profile?.tier ?? "—"],
                      ["recent", String(state.enrichment.recent_tickets?.length ?? 0)],
                    ]}
                    body={
                      state.enrichment.unavailable?.length
                        ? `unavailable: ${state.enrichment.unavailable.join(", ")}`
                        : undefined
                    }
                  />
                )}
                {state.routing && (
                  <AgentCard
                    code="F3"
                    name="Router"
                    span={4}
                    delay={0.15}
                    rows={[
                      ["queue", state.routing.queue],
                      ["assignee", state.routing.assignee?.name ?? state.routing.assignee?.user_id ?? "—"],
                      ["SLA", `${state.routing.sla_minutes} min`],
                      ["confidence", state.routing.confidence.toFixed(2)],
                    ]}
                    body={state.routing.rationale}
                  />
                )}
                {state.knowledge && (
                  <AgentCard
                    code="F4"
                    name="Knowledge"
                    span={4}
                    delay={0.20}
                    rows={[
                      ["runbook", state.knowledge.top_runbook?.title ?? "—"],
                      ["citation", state.knowledge.citation ?? "—"],
                      ["confidence", state.knowledge.confidence.toFixed(2)],
                    ]}
                    body={
                      state.knowledge.fallback_reason ??
                      (state.knowledge.suggested_steps ?? []).join(" → ")
                    }
                  />
                )}
                {state.related && (
                  <AgentCard
                    code="F7"
                    name="Linker"
                    span={4}
                    delay={0.25}
                    rows={[
                      ["duplicate", state.related.is_likely_duplicate ? "yes" : "no"],
                      ["related", String(state.related.related?.length ?? 0)],
                      ["confidence", state.related.confidence.toFixed(2)],
                    ]}
                    body={state.related.rationale}
                  />
                )}
                {state.schedule && (
                  <AgentCard
                    code="F6"
                    name="Scheduler"
                    span={4}
                    delay={0.30}
                    rows={[
                      ["slots", String(state.schedule.proposed_slots?.length ?? 0)],
                      [
                        "earliest",
                        state.schedule.proposed_slots?.[0]?.starts_at?.replace("T", " ").slice(0, 16) ??
                          "—",
                      ],
                    ]}
                    body={state.schedule.fallback_reason ?? undefined}
                    skipped={!state.schedule.proposed_slots?.length}
                  />
                )}
              </div>
            </motion.section>
          )}
        </AnimatePresence>
        </div>

        {/* ============================ HITL Drafter */}
        <div ref={hitlRef} className="scroll-mt-6">
        <AnimatePresence>
          {state?.draft && (
            <motion.section
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45, delay: 0.35 }}
              className="mb-14"
            >
              <div className="callout mb-3">
                &sect; 04 &middot; Approval gate
                <span className="ml-2 normal-case tracking-normal text-[color:var(--color-muted)]">
                  &mdash; nothing reaches the partner without a human signing off (F5 Drafter)
                </span>
              </div>
              <div className="schematic relative p-8">
                <span className="corner-bl" />
                <span className="corner-br" />

                <div className="grid grid-cols-12 gap-8">
                  <div className="col-span-12 lg:col-span-8">
                    <div className="flex items-baseline gap-3 mb-1">
                      <span className="callout">Draft · template {state.draft.template_id}</span>
                      {state.draft.blocked && (
                        <span className="callout text-[color:var(--color-danger)]">BLOCKED</span>
                      )}
                      {state.draft.compliance_flags.length > 0 && !state.draft.blocked && (
                        <span className="callout text-[color:var(--color-warn)]">
                          {state.draft.compliance_flags.join(" · ")}
                        </span>
                      )}
                    </div>
                    <h3 className="display-italic text-3xl leading-tight mb-4">
                      {state.draft.subject}
                    </h3>
                    <pre className="mono text-[13px] leading-relaxed whitespace-pre-wrap text-[color:var(--color-ink-soft)] pl-4 border-l border-[color:var(--color-line)]">
{state.draft.body}
                    </pre>
                  </div>

                  <div className="col-span-12 lg:col-span-4 lg:pl-6 lg:border-l lg:border-[color:var(--color-line)]">
                    <div className="callout mb-3">Human review</div>
                    <p className="margin-note mb-5">
                      Nothing leaves this surface without a human pressing one of three keys.
                      <em> Approve</em> sends, <em>Edit</em> rewrites, <em>Reject</em> kills the draft.
                    </p>

                    <div className="flex flex-col gap-2">
                      <button
                        onClick={() => setHitlChoice("approve")}
                        className={`btn inline-flex items-center justify-center gap-2 ${
                          hitlChoice === "approve" ? "btn-primary" : ""
                        }`}
                      >
                        <Check size={14} />
                        <span>Approve · send</span>
                      </button>
                      <button
                        onClick={() => setHitlChoice("edit")}
                        className={`btn inline-flex items-center justify-center gap-2 ${
                          hitlChoice === "edit" ? "btn-accent" : ""
                        }`}
                      >
                        <Pencil size={14} />
                        <span>Edit · revise</span>
                      </button>
                      <button
                        onClick={() => setHitlChoice("reject")}
                        className={`btn inline-flex items-center justify-center gap-2 ${
                          hitlChoice === "reject"
                            ? "border-[color:var(--color-danger)] bg-[color:var(--color-danger-soft)] text-[color:var(--color-danger)]"
                            : ""
                        }`}
                      >
                        <X size={14} />
                        <span>Reject · kill</span>
                      </button>
                    </div>

                    {hitlChoice && (
                      <p className="mt-4 mono text-[12px] text-[color:var(--color-muted)]">
                        recorded: <span className="text-[color:var(--color-ink)]">{hitlChoice}</span>{" "}
                        · {new Date().toISOString().slice(11, 19)}Z
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </motion.section>
          )}
        </AnimatePresence>
        </div>

        {/* ============================ PII + cost */}
        {state && (
          <section className="grid grid-cols-12 gap-8 mb-16">
            {/* PII */}
            <div className="col-span-12 lg:col-span-5">
              <div className="callout mb-3">§ 05 · PII findings</div>
              <div className="card p-6">
                {state.pii_findings && state.pii_findings.length > 0 ? (
                  <ul className="space-y-2">
                    {state.pii_findings.map((p, i) => (
                      <li key={i} className="flex items-center gap-3 mono text-[13px]">
                        <ShieldAlert size={14} className="text-[color:var(--color-warn)]" />
                        <span className="callout">{p.kind}</span>
                        <span className="text-[color:var(--color-ink-soft)]">{p.match}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mono text-[13px] text-[color:var(--color-muted)]">
                    no PII detected at ingest.
                  </p>
                )}
              </div>
            </div>

            {/* Cost */}
            <div className="col-span-12 lg:col-span-7">
              <div className="callout mb-3">§ 06 · Cost telemetry</div>
              <div className="card p-7">
                <div className="grid grid-cols-4 gap-6">
                  <CostCell k="calls" v={state.cost ? fmtNum(state.cost.calls) : "—"} />
                  <CostCell
                    k="tokens · in"
                    v={state.cost ? fmtNum(state.cost.tokens_in) : "—"}
                  />
                  <CostCell
                    k="tokens · out"
                    v={state.cost ? fmtNum(state.cost.tokens_out) : "—"}
                  />
                  <CostCell
                    k="cache · hit"
                    v={
                      state.cost
                        ? `${Math.round(state.cost.cache_hit_rate * 100)}%`
                        : "—"
                    }
                  />
                </div>
                <div className="divider mt-6 mb-4" />
                <div className="flex items-baseline justify-between">
                  <div className="callout">spend · this run</div>
                  <span className="bignum-italic text-[color:var(--color-accent-deep)]">
                    {state.cost ? fmtUsd(state.cost.cost_usd) : "—"}
                  </span>
                </div>
                {result && (
                  <div className="divider mt-6 mb-4" />
                )}
                {result && (
                  <div className="flex items-center justify-between gap-4">
                    <div className="callout">trace · audit-of-record</div>
                    <button
                      type="button"
                      onClick={() => downloadTrace(result)}
                      className="border border-[color:var(--color-ink)] hover:bg-[color:var(--color-accent-soft)] hover:border-[color:var(--color-accent)] px-4 py-2 mono text-[11px] tracking-[0.16em] uppercase text-[color:var(--color-ink)] transition-colors cursor-pointer"
                      data-testid="download-trace-btn"
                    >
                      Download trace ↓
                    </button>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}
      </main>

      <Footer />
    </div>
  );
}

// ------------------------------------------------------------------
// Small components

function Header() {
  return (
    <header className="border-b border-[color:var(--color-line)]">
      <div className="mx-auto max-w-[1240px] px-8 py-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 border border-[color:var(--color-ink)] flex items-center justify-center mono text-[11px]">
            <span>PTA</span>
          </div>
          <div>
            <div className="mono text-[11px] tracking-[0.22em] uppercase text-[color:var(--color-muted)]">
              Partner-Ticketing
            </div>
            <div className="display-italic text-[18px] leading-none">Agentic Platform</div>
          </div>
        </div>
        <nav className="flex items-center gap-5 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)]">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
          >
            <Activity size={12} />
            <span>dashboard</span>
          </Link>
          <Link
            href="/how-it-works"
            className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]"
          >
            <Layers size={12} />
            <span>how it works</span>
          </Link>
          <a href="https://github.com/ajayantony/partner-ticket-agentic" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 hover:text-[color:var(--color-ink)]">
            <Code size={12} />
            <span>repo</span>
          </a>
          <span className="inline-flex items-center gap-1.5">
            <Cpu size={12} />
            <span>LangGraph 1.x</span>
          </span>
        </nav>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="border-t border-[color:var(--color-ink)] bg-[color:var(--color-paper-deep)]">
      <div className="mx-auto max-w-[1240px] px-8 py-8 grid grid-cols-12 gap-6 mono text-[11px] tracking-[0.18em] uppercase text-[color:var(--color-muted)]">
        <div className="col-span-12 md:col-span-4">
          <div className="text-[color:var(--color-ink)] mb-1">Ajay Antony</div>
          <div>Capgemini Blue Harvest · panel ref impl · 2026</div>
        </div>
        <div className="col-span-6 md:col-span-4">
          <div className="text-[color:var(--color-ink)] mb-1">Stack</div>
          <div>Python · LangGraph · Pydantic · FastAPI · Next 16</div>
        </div>
        <div className="col-span-6 md:col-span-4">
          <div className="text-[color:var(--color-ink)] mb-1">Default</div>
          <div>ollama · local llama3.2 · no API keys leave the machine</div>
        </div>
      </div>
    </footer>
  );
}

function Spec({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="callout">{k}</div>
      <div className="mono text-[13px] text-[color:var(--color-ink)]">{v}</div>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] tracking-[0.18em] uppercase text-[color:var(--color-muted)]">
        {k}
      </span>
      <span className="text-[color:var(--color-ink)] truncate">{v}</span>
    </div>
  );
}

function AgentCard({
  code,
  name,
  rows,
  body,
  delay,
  skipped,
}: {
  code: string;
  name: string;
  rows: [string, string][];
  body?: string;
  span?: number;
  delay: number;
  skipped?: boolean;
}) {
  const subtitle = AGENT_SUBTITLES[code];
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      className={`col-span-12 md:col-span-6 lg:col-span-4 card p-5 ${
        skipped ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="callout">{code} · {name}</span>
        {skipped && <span className="callout text-[color:var(--color-faint)]">skipped</span>}
      </div>
      {subtitle && (
        <p className="text-[12.5px] leading-snug text-[color:var(--color-muted)] mb-3">
          {subtitle}
        </p>
      )}
      <dl className="space-y-1.5 mb-3">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 text-[13px] mono">
            <dt className="text-[color:var(--color-muted)]">{k}</dt>
            <dd className="text-[color:var(--color-ink)] text-right truncate max-w-[60%]">{v}</dd>
          </div>
        ))}
      </dl>
      {body && (
        <p className="text-[13px] leading-relaxed text-[color:var(--color-ink-soft)] pt-3 border-t border-[color:var(--color-line)]">
          {body}
        </p>
      )}
    </motion.div>
  );
}

function CostCell({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="callout">{k}</span>
      <span className="display text-[28px] leading-none">{v}</span>
    </div>
  );
}

// Skeleton card shown in the §03 grid while the pipeline is running but
// no real data has arrived yet. A subtle shimmer signals "loading" without
// committing to a layout the real card might not match.
function SkeletonCard({ delay }: { delay: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay }}
      className="col-span-12 md:col-span-6 lg:col-span-4 card p-5 overflow-hidden relative"
      aria-hidden
    >
      <div className="flex items-center justify-between mb-3">
        <span className="block h-3 w-20 bg-[color:var(--color-line)] rounded-sm" />
        <span className="block h-2 w-10 bg-[color:var(--color-line-soft)] rounded-sm" />
      </div>
      <div className="space-y-2 mb-3">
        <span className="block h-2.5 w-full bg-[color:var(--color-line-soft)] rounded-sm" />
        <span className="block h-2.5 w-5/6 bg-[color:var(--color-line-soft)] rounded-sm" />
        <span className="block h-2.5 w-3/4 bg-[color:var(--color-line-soft)] rounded-sm" />
      </div>
      <div className="pt-3 border-t border-[color:var(--color-line)] space-y-1.5">
        <span className="block h-2 w-2/3 bg-[color:var(--color-line-soft)] rounded-sm" />
        <span className="block h-2 w-1/2 bg-[color:var(--color-line-soft)] rounded-sm" />
      </div>
      {/* Diagonal shimmer sweeping across the card */}
      <motion.div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "linear-gradient(110deg, transparent 30%, rgba(28,27,23,0.06) 50%, transparent 70%)",
        }}
        animate={{ x: ["-100%", "100%"] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      />
    </motion.div>
  );
}
