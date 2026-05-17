"""F9 Insights agent — cross-stream synthesis over the recent window.

Where F1-F8 act per-ticket, F9 reads a *window* of completed runs and
surfaces patterns no operator would catch by eye: trending categories,
partner concentration, HITL anomalies, model-quality drift. This is
exactly the work an LLM is good at and a workflow engine is not.

Following the same shape as the other agents:

* :class:`InsightsOutput` is the Pydantic contract — same enforcement
  on real and mock providers.
* :func:`_insights_rule` is the deterministic mock — reads the JSON
  summary in the user message and applies a small set of clearly
  documented patterns.
* :func:`run_insights` is the public entrypoint, called from the
  ``/api/insights`` endpoint.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.obs import get_logger, span
from partner_ticket_agentic.providers import LLMProvider, Message, MockProvider, Tier

_log = get_logger("agents.insights")

TIER = Tier.MEDIUM
"""Synthesis benefits from a slightly larger model — DESIGN.md §4.1
"medium for summaries and drafts, large for ambiguous reasoning"."""


# --- schemas ----------------------------------------------------------------


class WindowSummary(BaseModel):
    """Compact view of the recent simulator window — the LLM's input.

    The agent serialises this as JSON in the user message; both the
    deterministic mock rule and a real LLM consume the same shape.
    """

    model_config = ConfigDict(extra="forbid")

    window_size: int = Field(ge=0)
    time_range_minutes: int = Field(ge=0)
    categories: dict[str, int]
    urgency: dict[str, int]
    hitl: dict[str, int]
    partner_counts: dict[str, int]
    avg_confidence: float = Field(ge=0, le=1)
    avg_duration_ms: int = Field(ge=0)
    avg_cost_usd: float = Field(ge=0)
    sample_recent: list[dict[str, Any]] = Field(default_factory=list)


class Insight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["trend", "anomaly", "segment", "recommendation"]
    title: str = Field(max_length=120)
    detail: str = Field(max_length=320)
    severity: Literal["info", "warn", "alert"]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class InsightsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_size: int
    generated_at: datetime
    summary: str = Field(max_length=420)
    insights: list[Insight] = Field(default_factory=list, max_length=6)


# --- system prompt for real providers ---------------------------------------


_SYSTEM_PROMPT = (
    "You are the F9 Insights agent for a Belgian-telecom partner-ticketing "
    "platform. You receive a structured JSON summary of the last N "
    "processed tickets and emit up to six high-signal insights covering "
    "trends, anomalies, partner segments, and recommendations.\n\n"
    "Each insight has: kind (trend|anomaly|segment|recommendation), title "
    "(one line, <=120 chars), detail (one paragraph, <=320 chars), severity "
    "(info|warn|alert), confidence (0-1), evidence_ids (up to 10 ticket IDs "
    "from the input that support the observation; never invent IDs).\n\n"
    "Focus on:\n"
    "- patterns a busy ops lead would want surfaced\n"
    "- anomalies that warrant intervention\n"
    "- partner concentration or risk segments\n"
    "- model-quality signals (confidence drift, latency spikes)\n\n"
    "Also emit a 2-3 sentence executive summary.\n"
    "Return ONLY valid JSON matching the InsightsOutput schema."
)


# --- deterministic mock rule ------------------------------------------------


def _insights_rule(_system: str, messages: list[Message]) -> dict[str, Any]:
    """Deterministic mock rule for :class:`InsightsOutput` — no LLM call.

    Reads the JSON ``WindowSummary`` from the user message and applies
    a documented set of patterns. A reviewer can read this function and
    predict the output for any input.

    Patterns checked (in order):

    1. **Trending category** — any category > 50% of the window
    2. **Partner concentration** — any partner > 40% of the window
    3. **HITL reject hot** — reject rate > 10%
    4. **Low avg confidence** — mean confidence < 0.70
    5. **High avg latency** — mean duration > 5000 ms
    6. **Stable operations** — fallback when nothing else fires
    """

    user_text = "\n".join(m.content for m in messages if m.role == "user")
    try:
        summary = WindowSummary.model_validate_json(user_text)
    except Exception as exc:
        # stdlib Logger.warning doesn't accept arbitrary kwargs — route the
        # error through `extra=` (matches the watchdog.py pattern). The
        # contextlib.suppress wrapper guarantees a logger misconfiguration
        # cannot escalate a recoverable parse error into a 500.
        with contextlib.suppress(Exception):
            _log.warning("insights_rule_bad_input", extra={"error": str(exc)})
        return _empty_output("Could not parse window summary.").model_dump(mode="json")

    insights: list[dict[str, Any]] = []
    total = max(summary.window_size, 1)
    sample_ids = [
        r.get("sim_ticket_id", "") for r in summary.sample_recent if r.get("sim_ticket_id")
    ]

    # 1. Trending category
    if summary.categories:
        top_cat, top_count = max(summary.categories.items(), key=lambda kv: kv[1])
        share = top_count / total
        if share > 0.5:
            insights.append(
                {
                    "kind": "trend",
                    "title": f"{top_cat} dominates the recent window",
                    "detail": (
                        f"{top_count} of {total} tickets ({share:.0%}) are {top_cat}. "
                        "If this share holds for another window, raise it with the "
                        "partner ops lead to check for an underlying network event."
                    ),
                    "severity": "warn" if share > 0.7 else "info",
                    "confidence": round(min(0.95, 0.6 + share / 2), 2),
                    "evidence_ids": sample_ids[:5],
                }
            )

    # 2. Partner concentration
    if summary.partner_counts:
        top_partner, top_partner_count = max(summary.partner_counts.items(), key=lambda kv: kv[1])
        share = top_partner_count / total
        if share > 0.4:
            insights.append(
                {
                    "kind": "segment",
                    "title": f"Partner {top_partner} is generating {share:.0%} of recent traffic",
                    "detail": (
                        f"{top_partner_count} of {total} tickets came from {top_partner}. "
                        "Worth checking whether their tier or their network needs attention. "
                        "Compare to their normal share before escalating."
                    ),
                    "severity": "warn" if share > 0.6 else "info",
                    "confidence": round(min(0.92, 0.55 + share / 2), 2),
                    "evidence_ids": sample_ids[:5],
                }
            )

    # 3. HITL reject hot
    total_hitl = sum(summary.hitl.values()) or 1
    rejects = summary.hitl.get("rejected", 0)
    reject_rate = rejects / total_hitl
    if reject_rate > 0.10:
        insights.append(
            {
                "kind": "anomaly",
                "title": f"HITL reject rate is {reject_rate:.0%}",
                "detail": (
                    f"{rejects} of {total_hitl} drafts were rejected by operators. "
                    "Could mean the Drafter's templates are misaligned with the current "
                    "ticket mix, or there's a compliance flag spike. Review the rejected "
                    "drafts for a pattern."
                ),
                "severity": "alert" if reject_rate > 0.2 else "warn",
                "confidence": 0.80,
                "evidence_ids": sample_ids[:5],
            }
        )

    # 4. Low avg confidence
    if summary.window_size >= 5 and summary.avg_confidence < 0.70:
        insights.append(
            {
                "kind": "anomaly",
                "title": f"Triage confidence is averaging {summary.avg_confidence:.2f}",
                "detail": (
                    "Low average confidence across the window suggests the small-tier model "
                    "is struggling on the current ticket mix. Consider raising the tier for "
                    "ambiguous categories or expanding the rule set."
                ),
                "severity": "warn",
                "confidence": 0.78,
                "evidence_ids": sample_ids[:5],
            }
        )

    # 5. High avg latency
    if summary.avg_duration_ms > 5000:
        insights.append(
            {
                "kind": "recommendation",
                "title": f"Pipeline latency is averaging {summary.avg_duration_ms} ms",
                "detail": (
                    "Real-LLM call cost (likely Ollama or Anthropic) is dominating end-to-end "
                    "latency. Consider model-tier downshift on Triage, or warm the model before "
                    "the panel demo."
                ),
                "severity": "info",
                "confidence": 0.72,
                "evidence_ids": sample_ids[:3],
            }
        )

    # 6. Fallback — stable operations
    if not insights:
        insights.append(
            {
                "kind": "trend",
                "title": "Operations look steady",
                "detail": (
                    f"No category dominates, partner traffic is spread out, HITL reject rate is "
                    f"healthy ({reject_rate:.0%}), and confidence averages {summary.avg_confidence:.2f}. "
                    "Nothing requires attention in the current window."
                ),
                "severity": "info",
                "confidence": 0.85,
                "evidence_ids": sample_ids[:3],
            }
        )

    insights = insights[:6]

    summary_text = _exec_summary(summary, reject_rate)
    output = {
        "window_size": summary.window_size,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary_text,
        "insights": insights,
    }
    return output


def _exec_summary(summary: WindowSummary, reject_rate: float) -> str:
    if summary.window_size == 0:
        return "No completed tickets in the current window. Start the simulator to begin."
    top_cat = "—"
    if summary.categories:
        top_cat = max(summary.categories.items(), key=lambda kv: kv[1])[0]
    return (
        f"Last {summary.window_size} tickets · {top_cat} most common · "
        f"HITL approve/edit/reject = "
        f"{summary.hitl.get('approved', 0)}/{summary.hitl.get('edited', 0)}/"
        f"{summary.hitl.get('rejected', 0)} ({reject_rate:.0%} reject). "
        f"Avg confidence {summary.avg_confidence:.2f}, avg latency "
        f"{summary.avg_duration_ms} ms."
    )


def _empty_output(reason: str) -> InsightsOutput:
    return InsightsOutput(
        window_size=0,
        generated_at=datetime.now(UTC),
        summary=reason,
        insights=[],
    )


MockProvider.register(InsightsOutput, _insights_rule)


# --- public agent API -------------------------------------------------------


def run_insights(window: WindowSummary, provider: LLMProvider) -> InsightsOutput:
    """Generate insights for the given window via the chosen provider.

    Same contract as every other agent: the provider's ``complete``
    returns a validated ``InsightsOutput`` or raises. Schema rejection
    contains the failure to this agent's boundary.
    """

    with span("agent_run", agent="insights", window_size=window.window_size):
        messages = [Message(role="user", content=window.model_dump_json())]
        try:
            return provider.complete(
                messages,
                InsightsOutput,
                TIER,
                system=_SYSTEM_PROMPT,
                trace_id=None,
            )
        except Exception as exc:
            # stdlib Logger.warning doesn't accept arbitrary kwargs — route the
            # error through `extra=` (matches the watchdog.py pattern). The
            # contextlib.suppress wrapper guarantees this safety-net path
            # never raises — never let insights kill the dashboard.
            with contextlib.suppress(Exception):
                _log.warning("insights_failed", extra={"error": str(exc)})
            return _empty_output(f"Insights generation failed: {exc!s}")
