"""Compile-time topology contract for the LangGraph state machine.

This file pins the orchestration shape as a typed test. The intent isn't
to re-test the runtime behaviour — `test_graph.py` already exercises the
request path. The intent is to fail CI loudly on subtle topology drift:
a node added, an edge re-wired, a conditional predicate's named branches
renamed.

If this test fails, two things to check before "updating the snapshot":

1. Was the topology change intentional? If yes, update the expected sets
   below and re-run the eval suite (`python -m partner_ticket_agentic.evals`)
   to confirm the change didn't tank precision/recall.
2. Did the change widen the AI Act blast radius? Re-read
   `docs/AI_ACT_ASSESSMENT.md` §5 re-evaluation triggers before merging.

The expected shape mirrors `docs/DESIGN.md` §2 and the figure in
`docs/concepts/03-orchestration.html`. Keep these three in sync.

REQ: CI-01 (Phase 1, v1.1 production-readiness gap closure)
"""

from __future__ import annotations

from typing import Any

import pytest

from partner_ticket_agentic.graph import build_graph

EXPECTED_NODES: frozenset[str] = frozenset(
    {
        "triage",
        "linker",
        "enricher",
        "router",
        "knowledge",
        "route_decision",
        "scheduler",
        "drafter",
    }
)
"""Eight nodes — the F0 Intake is inline in `run_pipeline`, F8 Watchdog is a
sidecar, F9 Insights is a cross-stream sidecar — none of those live in the
graph itself."""


# Each entry is (source, target). START and END are LangGraph sentinels and
# must appear by name in the compiled graph. The `route_decision`
# conditional fans out to scheduler / drafter; both branches are listed as
# edges here because they exist in the compiled topology.
EXPECTED_FIXED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        # START → parallel fan-out
        ("__start__", "triage"),
        ("__start__", "linker"),
        # parallel fan-in
        ("triage", "enricher"),
        ("linker", "enricher"),
        # Enricher → parallel fan-out
        ("enricher", "router"),
        ("enricher", "knowledge"),
        # parallel fan-in into the route-decision passthrough
        ("router", "route_decision"),
        ("knowledge", "route_decision"),
        # Scheduler → Drafter → END (when conditional fires scheduler branch)
        ("scheduler", "drafter"),
        ("drafter", "__end__"),
    }
)
"""Ten fixed edges. The two conditional branches from route_decision
(scheduler, drafter) are validated separately below — LangGraph models
those as a single conditional-edge node with named branches, not as
discrete edges in the same shape."""


EXPECTED_CONDITIONAL_BRANCHES: frozenset[str] = frozenset({"scheduler", "drafter"})
"""The route_decision conditional fans out to exactly these two named
branches. Adding a third branch (e.g. `linker_recheck`) requires bumping
this set deliberately and updating DESIGN.md §2."""


def _extract_topology(compiled: Any) -> tuple[set[str], set[tuple[str, str]]]:
    """Pull the nodes and fixed edges out of a compiled LangGraph runnable.

    LangGraph's compiled graph exposes `get_graph()` returning a
    Mermaid-style structure with `.nodes` (dict keyed by node id) and
    `.edges` (list of edge objects with `.source` / `.target`). We rely on
    the public introspection API rather than any private attribute.
    """

    g = compiled.get_graph()
    nodes = {n for n in g.nodes if n not in {"__start__", "__end__"}}
    edges = {(e.source, e.target) for e in g.edges if not getattr(e, "conditional", False)}
    return nodes, edges


def test_node_set_is_exactly_eight_named_agents() -> None:
    """Graph contains exactly the eight expected in-graph nodes — no more,
    no fewer. Sidecars (Watchdog, Insights) and intake (F0) are deliberately
    not in this set."""

    compiled = build_graph()
    nodes, _ = _extract_topology(compiled)
    assert nodes == EXPECTED_NODES, (
        f"Node set drift detected.\n"
        f"  Added:   {nodes - EXPECTED_NODES}\n"
        f"  Removed: {EXPECTED_NODES - nodes}\n"
        f"If the change is intentional, update EXPECTED_NODES and re-run the "
        f"eval suite. Then re-read docs/AI_ACT_ASSESSMENT.md §5 triggers."
    )


def test_fixed_edges_match_exactly() -> None:
    """The ten non-conditional edges form the expected DAG shape: two parallel
    fan-outs (Triage&Linker → Enricher, Router&Knowledge → route_decision)
    and a single linear tail (scheduler → drafter → END)."""

    compiled = build_graph()
    _, edges = _extract_topology(compiled)
    missing = EXPECTED_FIXED_EDGES - edges
    extra = edges - EXPECTED_FIXED_EDGES
    assert not missing and not extra, (
        f"Edge set drift detected.\n"
        f"  Missing: {sorted(missing)}\n"
        f"  Extra:   {sorted(extra)}\n"
        f"If the change is intentional, update EXPECTED_FIXED_EDGES and "
        f"re-read DESIGN.md §2 to keep the spec in sync."
    )


def test_conditional_edge_has_exactly_two_named_branches() -> None:
    """The single conditional edge from `route_decision` must fan out to
    exactly `scheduler` and `drafter`. A third branch (or a renamed branch)
    silently shifts request-path behaviour and must fail the build."""

    compiled = build_graph()
    g = compiled.get_graph()
    # LangGraph models conditional fan-outs as edges with `conditional=True`
    # and a `data` attribute holding the branch label.
    conditional_targets = {
        e.target
        for e in g.edges
        if getattr(e, "conditional", False) and e.source == "route_decision"
    }
    assert conditional_targets == EXPECTED_CONDITIONAL_BRANCHES, (
        f"Conditional-edge branches at `route_decision` drifted.\n"
        f"  Added:   {conditional_targets - EXPECTED_CONDITIONAL_BRANCHES}\n"
        f"  Removed: {EXPECTED_CONDITIONAL_BRANCHES - conditional_targets}\n"
        f"The conditional fan-out is the *only* runtime branching in the graph; "
        f"adding a branch is a load-bearing architectural change."
    )


def test_parallel_fan_outs_are_two_and_intact() -> None:
    """Two parallel fan-out structures exist:

    1. START → {Triage, Linker} (then they join at Enricher).
    2. Enricher → {Router, Knowledge} (then they join at route_decision).

    Each parallel pair must have exactly two outgoing edges from the
    same source.
    """

    compiled = build_graph()
    _, edges = _extract_topology(compiled)

    out_from_start = {tgt for src, tgt in edges if src == "__start__"}
    assert out_from_start == {"triage", "linker"}, (
        f"Fan-out from START must be exactly Triage & Linker. Got: {out_from_start}"
    )

    out_from_enricher = {tgt for src, tgt in edges if src == "enricher"}
    assert out_from_enricher == {"router", "knowledge"}, (
        f"Fan-out from Enricher must be exactly Router & Knowledge. Got: {out_from_enricher}"
    )


def test_graph_compiles_without_error() -> None:
    """Sanity: the compile path itself returns a runnable. This is the
    "fail-on-syntax-error" backstop — if `build_graph()` raises, every
    other test in this file becomes meaningless."""

    compiled = build_graph()
    assert compiled is not None
    assert hasattr(compiled, "invoke") or hasattr(compiled, "stream"), (
        "Compiled graph must expose the LangGraph runnable interface."
    )


@pytest.mark.parametrize(
    "node",
    sorted(EXPECTED_NODES),
)
def test_every_expected_node_is_reachable(node: str) -> None:
    """Each expected node appears in the compiled graph — parametrised so a
    missing node names itself in the failure output."""

    compiled = build_graph()
    nodes, _ = _extract_topology(compiled)
    assert node in nodes, f"Expected node {node!r} not present in compiled graph"
