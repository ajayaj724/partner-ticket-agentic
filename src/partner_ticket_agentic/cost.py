"""Cost estimation + token telemetry for LLM provider calls.

Slide 17 of the panel deck commits to "Cost dashboards by partner / agent /
step" and slide 15 commits to "Token-spend metrics per tenant, agent, step".
This module is the data layer behind those claims: a small pricing table
keyed by ``(provider, model_id)``, a pure ``estimate_cost`` function that
walks the table, and a per-ticket ``CostLedger`` that rolls up agent-level
spend so the trace and the web UI can surface it.

Pricing values are the published list rates for Anthropic's January 2026
catalogue (USD per million tokens). Open-source providers (Ollama, Mock)
have ``$0`` rows because the marginal cost is the user's electricity, not
an API meter. Cache-read pricing reflects Anthropic's prompt-caching
discount (≈10% of the regular input rate); cache-write reflects the 25%
surcharge applied to the first cache fill.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# USD per **million** tokens. Conservative published rates as of Jan 2026.
# Editing this table is the only place a reviewer needs to look to audit
# what the platform thinks an API call costs.
PRICING: dict[tuple[str, str], dict[str, float]] = {
    # ---- Anthropic ----------------------------------------------------------
    ("anthropic", "claude-haiku-4-5-20251001"): {
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    ("anthropic", "claude-sonnet-4-6"): {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    ("anthropic", "claude-opus-4-7"): {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    # ---- Ollama (local, electricity-only) -----------------------------------
    ("ollama", "llama3.2:3b"): {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    ("ollama", "llama3.1:8b"): {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    ("ollama", "llama3.3:70b"): {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    },
    # ---- Mock (deterministic stub) ------------------------------------------
    ("mock", "mock-small"): {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    ("mock", "mock-medium"): {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    ("mock", "mock-large"): {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """One LLM call's token + USD numbers, ready to drop into a log record."""

    tokens_in: int
    tokens_out: int
    cached_input_tokens: int
    cache_write_tokens: int
    usd: float
    cache_hit: bool

    def to_log_fields(self) -> dict[str, Any]:
        """Flatten to the keys the JSON-line logger should emit."""

        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.usd, 6),
            "cache_hit": self.cache_hit,
        }


def estimate_tokens(text: str) -> int:
    """Deterministic English-text token estimator (~4 chars per token).

    Used by Mock and Ollama where the provider doesn't report usage. The
    output is fully deterministic — same text yields the same count on every
    run, which is the contract CLAUDE.md sets for the demo path. Real models
    in production would replace this with their tokenizer's actual count.
    """

    return max(1, len(text) // 4)


def estimate_cost(
    provider: str,
    model: str,
    *,
    tokens_in: int,
    tokens_out: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> CostBreakdown:
    """Compute USD for one call against ``PRICING``.

    ``tokens_in`` is the *uncached* input tokens — cached tokens are billed
    at the cache-read rate, not the full input rate. ``cache_write_tokens``
    is the first-fill surcharge bucket; on a cold cache, the entire system
    prompt lands here.
    """

    rate = PRICING.get((provider, model))
    if rate is None:
        # Unknown model — fall back to $0 rather than crash the trace.
        rate = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

    usd = (
        (tokens_in / 1_000_000) * rate["input"]
        + (tokens_out / 1_000_000) * rate["output"]
        + (cached_input_tokens / 1_000_000) * rate["cache_read"]
        + (cache_write_tokens / 1_000_000) * rate["cache_write"]
    )
    return CostBreakdown(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        usd=usd,
        cache_hit=cached_input_tokens > 0,
    )


@dataclass
class CostLedger:
    """Roll up multiple LLM calls into a per-ticket total.

    The graph wires one ledger per ticket flow. Each agent's ``llm_call``
    appends a :class:`CostBreakdown`; at the end of the pipeline the
    ledger's :meth:`summary` becomes a field on the final state and gets
    serialised to the trace.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, *, agent: str, provider: str, model: str, breakdown: CostBreakdown) -> None:
        self.calls.append(
            {
                "agent": agent,
                "provider": provider,
                "model": model,
                **breakdown.to_log_fields(),
            }
        )

    def summary(self) -> dict[str, Any]:
        if not self.calls:
            return {
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cached_input_tokens": 0,
                "cost_usd": 0.0,
                "cache_hit_rate": 0.0,
                "by_agent": {},
            }

        tokens_in = sum(c["tokens_in"] for c in self.calls)
        tokens_out = sum(c["tokens_out"] for c in self.calls)
        cached = sum(c["cached_input_tokens"] for c in self.calls)
        cost = sum(c["cost_usd"] for c in self.calls)
        hits = sum(1 for c in self.calls if c["cache_hit"])

        by_agent: dict[str, dict[str, Any]] = {}
        for c in self.calls:
            agg = by_agent.setdefault(
                c["agent"],
                {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
            )
            agg["calls"] += 1
            agg["tokens_in"] += c["tokens_in"]
            agg["tokens_out"] += c["tokens_out"]
            agg["cost_usd"] = round(agg["cost_usd"] + c["cost_usd"], 6)

        return {
            "calls": len(self.calls),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_input_tokens": cached,
            "cost_usd": round(cost, 6),
            "cache_hit_rate": round(hits / len(self.calls), 3),
            "by_agent": by_agent,
        }


# --- contextvar plumbing so providers can record without explicit args ------

_LEDGER: ContextVar[CostLedger | None] = ContextVar("_LEDGER", default=None)


@contextmanager
def bind_ledger(ledger: CostLedger) -> Iterator[CostLedger]:
    """Push a :class:`CostLedger` onto the contextvar for the call site.

    The graph wraps each pipeline run in this block; provider ``complete``
    paths consult :func:`current_ledger` and append their breakdown. Keeps
    agents and tools unaware of the ledger — they only ever see ``state``.
    """

    token = _LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER.reset(token)


def current_ledger() -> CostLedger | None:
    """Return the ledger bound by the enclosing :func:`bind_ledger`, or ``None``."""

    return _LEDGER.get()


# --- per-tenant token + USD budgets (slide 17 of the deck) -----------------


class BudgetExceededError(RuntimeError):
    """Raised by a provider when a call would push a partner over budget.

    Carries the partner_id, the cap that was crossed, and the running
    total so the trace shows exactly which threshold tripped. Caught
    by the graph layer and surfaced as a fail-closed agent fallback.
    """

    def __init__(self, partner_id: str, kind: str, used: float, cap: float) -> None:
        super().__init__(f"partner {partner_id!r} over budget on {kind}: used {used} / cap {cap}")
        self.partner_id = partner_id
        self.kind = kind  # "tokens" or "usd"
        self.used = used
        self.cap = cap


@dataclass(frozen=True, slots=True)
class BudgetCap:
    """One partner's per-ticket token + USD ceiling."""

    max_tokens: int
    max_usd: float


@dataclass
class BudgetState:
    """Running consumption + alert-threshold bookkeeping for one ticket flow."""

    partner_id: str
    cap: BudgetCap
    used_tokens: int = 0
    used_usd: float = 0.0
    # Fractions already alerted so we don't spam — once we've crossed 70%
    # for tokens, we don't emit the 70%-tokens alert again on later calls.
    fired_token_alerts: set[float] = field(default_factory=set)
    fired_usd_alerts: set[float] = field(default_factory=set)
    alert_thresholds: tuple[float, ...] = (0.70, 0.90, 1.00)

    def would_exceed(self, *, tokens: int, usd: float) -> str | None:
        """Return ``"tokens"`` / ``"usd"`` if this call would breach the cap.

        Returns ``None`` if the call fits. The graph layer maps the
        return value into a :class:`BudgetExceededError`.
        """

        if self.cap.max_tokens > 0 and self.used_tokens + tokens > self.cap.max_tokens:
            return "tokens"
        if self.cap.max_usd > 0 and self.used_usd + usd > self.cap.max_usd:
            return "usd"
        return None

    def record(self, *, tokens: int, usd: float) -> list[tuple[str, float]]:
        """Add a call's consumption; return any newly-crossed thresholds.

        Each entry in the returned list is ``(kind, fraction)`` where
        ``kind`` is ``"tokens"`` or ``"usd"`` and ``fraction`` is the
        threshold (0.70, 0.90, 1.00). The caller logs these — INFO at
        0.70, WARNING at 0.90, ERROR at 1.00.
        """

        self.used_tokens += tokens
        self.used_usd += usd
        fired: list[tuple[str, float]] = []
        for t in self.alert_thresholds:
            if (
                self.cap.max_tokens > 0
                and self.used_tokens >= self.cap.max_tokens * t
                and t not in self.fired_token_alerts
            ):
                self.fired_token_alerts.add(t)
                fired.append(("tokens", t))
            if (
                self.cap.max_usd > 0
                and self.used_usd >= self.cap.max_usd * t
                and t not in self.fired_usd_alerts
            ):
                self.fired_usd_alerts.add(t)
                fired.append(("usd", t))
        return fired


_BUDGET: ContextVar[BudgetState | None] = ContextVar("_BUDGET", default=None)


@contextmanager
def bind_budget(state: BudgetState) -> Iterator[BudgetState]:
    """Bind a :class:`BudgetState` for the call site (graph wraps pipeline runs)."""

    token = _BUDGET.set(state)
    try:
        yield state
    finally:
        _BUDGET.reset(token)


def current_budget() -> BudgetState | None:
    """Return the budget bound by the enclosing :func:`bind_budget`."""

    return _BUDGET.get()


@dataclass(frozen=True, slots=True)
class BudgetRegistry:
    """Loaded view of ``config/budgets.yaml`` — per-partner + tier defaults."""

    partners: dict[str, BudgetCap]
    defaults_by_tier: dict[str, BudgetCap]
    alert_thresholds: tuple[float, ...]

    def cap_for(self, partner_id: str, tier: str | None = None) -> BudgetCap:
        """Resolve a budget cap: explicit partner override, else tier default.

        If neither is configured, returns an "unlimited" cap (zeros) so
        the check is a no-op. Production deployments would likely fail
        closed instead of silently allowing unbounded spend.
        """

        if partner_id in self.partners:
            return self.partners[partner_id]
        if tier and tier in self.defaults_by_tier:
            return self.defaults_by_tier[tier]
        return BudgetCap(max_tokens=0, max_usd=0.0)


def _default_budgets_path() -> Any:
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "config" / "budgets.yaml"
        if candidate.exists():
            return candidate
    return None


def load_budgets(path: Any = None) -> BudgetRegistry:
    """Load ``config/budgets.yaml`` into a :class:`BudgetRegistry`.

    Missing file → empty registry (every cap_for returns unlimited).
    That keeps the demo defensible if the config is absent.
    """

    import yaml

    yaml_path = path if path is not None else _default_budgets_path()
    if yaml_path is None:
        return BudgetRegistry(partners={}, defaults_by_tier={}, alert_thresholds=(0.70, 0.90, 1.00))
    from pathlib import Path

    with Path(yaml_path).open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    partners = {
        pid: BudgetCap(
            max_tokens=int(cfg.get("max_tokens", 0)),
            max_usd=float(cfg.get("max_usd", 0.0)),
        )
        for pid, cfg in (raw.get("partners") or {}).items()
    }
    defaults = {
        tier: BudgetCap(
            max_tokens=int(cfg.get("max_tokens", 0)),
            max_usd=float(cfg.get("max_usd", 0.0)),
        )
        for tier, cfg in (raw.get("defaults") or {}).items()
    }
    thresholds = tuple(raw.get("alert_thresholds") or (0.70, 0.90, 1.00))
    return BudgetRegistry(partners=partners, defaults_by_tier=defaults, alert_thresholds=thresholds)
