"use client";

/**
 * Engineering-schematic topology of the 8-agent LangGraph.
 *
 * Renders the StateGraph from src/partner_ticket_agentic/graph.py as a
 * blueprint-style diagram. Nodes carry data-state={"idle"|"active"|"complete"}
 * so the CSS in globals.css can colour them in. Edges are drawn in with a
 * stroke-dasharray reveal once the page mounts.
 */

import { motion } from "motion/react";

export type AgentId =
  | "intake"
  | "triage"
  | "linker"
  | "enricher"
  | "router"
  | "knowledge"
  | "scheduler"
  | "drafter"
  | "watchdog";

export type NodeState = "idle" | "active" | "complete" | "skipped";

type Node = {
  id: AgentId;
  label: string;
  code: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

const NODES: Node[] = [
  { id: "intake",    label: "Intake",    code: "F0", x: 360, y:  20, w: 120, h: 44 },
  { id: "triage",    label: "Triage",    code: "F1", x: 200, y: 120, w: 140, h: 56 },
  { id: "linker",    label: "Linker",    code: "F7", x: 500, y: 120, w: 140, h: 56 },
  { id: "enricher",  label: "Enricher",  code: "F2", x: 350, y: 220, w: 140, h: 56 },
  { id: "router",    label: "Router",    code: "F3", x: 180, y: 320, w: 140, h: 56 },
  { id: "knowledge", label: "Knowledge", code: "F4", x: 520, y: 320, w: 140, h: 56 },
  { id: "scheduler", label: "Scheduler", code: "F6", x: 180, y: 420, w: 140, h: 56 },
  { id: "drafter",   label: "Drafter",   code: "F5", x: 350, y: 520, w: 200, h: 64 },
  { id: "watchdog",  label: "Watchdog",  code: "F8", x: 700, y: 520, w: 140, h: 56 },
];

type Edge = { from: AgentId; to: AgentId; conditional?: boolean };

const EDGES: Edge[] = [
  { from: "intake",    to: "triage" },
  { from: "intake",    to: "linker" },
  { from: "triage",    to: "enricher" },
  { from: "linker",    to: "enricher" },
  { from: "enricher",  to: "router" },
  { from: "enricher",  to: "knowledge" },
  { from: "router",    to: "scheduler", conditional: true },
  { from: "router",    to: "drafter",   conditional: true },
  { from: "knowledge", to: "drafter" },
  { from: "scheduler", to: "drafter" },
];

function nodeById(id: AgentId): Node {
  const n = NODES.find((x) => x.id === id);
  if (!n) throw new Error(`unknown node ${id}`);
  return n;
}

function edgePath(e: Edge): string {
  const a = nodeById(e.from);
  const b = nodeById(e.to);
  const ax = a.x + a.w / 2;
  const ay = a.y + a.h;
  const bx = b.x + b.w / 2;
  const by = b.y;
  // Right-angle bracket through the midpoint — feels drafted, not generic.
  const my = (ay + by) / 2;
  return `M ${ax} ${ay} L ${ax} ${my} L ${bx} ${my} L ${bx} ${by}`;
}

export type TopologyProps = {
  /** Per-node visual state. */
  states: Partial<Record<AgentId, NodeState>>;
  /** Animate edge stroke-in once on mount. */
  drawIn?: boolean;
};

export function Topology({ states, drawIn = true }: TopologyProps) {
  return (
    <svg
      viewBox="0 0 880 620"
      className="w-full h-auto"
      role="img"
      aria-label="Eight-agent LangGraph topology"
    >
      <defs>
        <marker
          id="arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-ink)" />
        </marker>
      </defs>

      {/* Edges */}
      <g fill="none" stroke="var(--color-ink-soft)" strokeWidth="1.2">
        {EDGES.map((e, i) => (
          <motion.path
            key={`${e.from}-${e.to}`}
            d={edgePath(e)}
            strokeDasharray={e.conditional ? "5 4" : undefined}
            markerEnd="url(#arrow)"
            initial={drawIn ? { pathLength: 0, opacity: 0 } : false}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.15 + i * 0.07, ease: "easeOut" }}
          />
        ))}
      </g>

      {/* Nodes */}
      <g>
        {NODES.map((n, i) => {
          const state = states[n.id] ?? "idle";
          return (
            <motion.g
              key={n.id}
              data-state={state}
              initial={drawIn ? { opacity: 0, y: -4 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.05 + i * 0.06 }}
            >
              <rect
                x={n.x}
                y={n.y}
                width={n.w}
                height={n.h}
                rx={0}
                fill="var(--color-paper)"
                stroke="var(--color-ink)"
                strokeWidth={1.4}
              />
              {/* Corner ticks — drafted detail */}
              {[
                [n.x, n.y],
                [n.x + n.w, n.y],
                [n.x, n.y + n.h],
                [n.x + n.w, n.y + n.h],
              ].map(([cx, cy], j) => (
                <circle key={j} cx={cx} cy={cy} r={1.6} fill="var(--color-ink)" />
              ))}
              <text
                x={n.x + n.w / 2}
                y={n.y + n.h / 2 - 4}
                textAnchor="middle"
                dominantBaseline="middle"
                fontFamily="var(--font-display), serif"
                fontStyle="italic"
                fontSize="18"
                fill="var(--color-ink)"
              >
                {n.label}
              </text>
              <text
                x={n.x + n.w / 2}
                y={n.y + n.h / 2 + 14}
                textAnchor="middle"
                dominantBaseline="middle"
                fontFamily="var(--font-mono), monospace"
                fontSize="9.5"
                letterSpacing="0.18em"
                fill="var(--color-muted)"
              >
                {n.code}
              </text>
            </motion.g>
          );
        })}
      </g>

      {/* Annotations — "FIG. 1" feel */}
      <g
        fontFamily="var(--font-mono), monospace"
        fontSize="9.5"
        letterSpacing="0.18em"
        fill="var(--color-muted)"
      >
        <text x="20" y="180">PARALLEL FAN-OUT</text>
        <text x="20" y="320">JOIN @ ENRICHER</text>
        <text x="20" y="440">CONDITIONAL · ON-SITE</text>
        <text x="20" y="540">HITL · APPROVAL GATE</text>
        <text x="700" y="600">SIDECAR</text>
      </g>

      {/* Figure caption */}
      <text
        x="440"
        y="610"
        textAnchor="middle"
        fontFamily="var(--font-mono), monospace"
        fontSize="9.5"
        letterSpacing="0.22em"
        fill="var(--color-muted)"
      >
        FIG. 1 — LANGGRAPH STATE MACHINE · 8 SPECIALISTS · 1 HITL GATE
      </text>
    </svg>
  );
}
