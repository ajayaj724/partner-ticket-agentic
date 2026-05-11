"""Tests for per-tenant token + USD budgets (deck slide 17)."""

from __future__ import annotations

import pytest

from partner_ticket_agentic.cost import (
    BudgetCap,
    BudgetExceededError,
    BudgetRegistry,
    BudgetState,
    bind_budget,
    current_budget,
    load_budgets,
)


class TestBudgetState:
    def test_would_exceed_returns_kind_when_tokens_cap_breached(self) -> None:
        state = BudgetState(partner_id="P-X", cap=BudgetCap(max_tokens=100, max_usd=10.0))
        assert state.would_exceed(tokens=50, usd=1.0) is None
        state.record(tokens=80, usd=1.0)
        assert state.would_exceed(tokens=50, usd=1.0) == "tokens"

    def test_would_exceed_returns_kind_when_usd_cap_breached(self) -> None:
        state = BudgetState(partner_id="P-X", cap=BudgetCap(max_tokens=10_000, max_usd=0.10))
        state.record(tokens=100, usd=0.08)
        assert state.would_exceed(tokens=100, usd=0.05) == "usd"

    def test_zero_cap_means_unlimited(self) -> None:
        state = BudgetState(partner_id="P-X", cap=BudgetCap(max_tokens=0, max_usd=0.0))
        # No cap set → would_exceed always False even for huge calls.
        assert state.would_exceed(tokens=10_000_000, usd=1000.0) is None

    def test_record_fires_threshold_alerts_once(self) -> None:
        state = BudgetState(
            partner_id="P-X",
            cap=BudgetCap(max_tokens=100, max_usd=10.0),
            alert_thresholds=(0.70, 0.90, 1.00),
        )
        # First record crosses 70% on tokens.
        fired = state.record(tokens=75, usd=0.0)
        assert ("tokens", 0.70) in fired
        # Second record crosses 90%.
        fired = state.record(tokens=20, usd=0.0)
        assert ("tokens", 0.90) in fired
        # Third record reaches 100%.
        fired = state.record(tokens=5, usd=0.0)
        assert ("tokens", 1.00) in fired
        # Each threshold fires only once per kind.
        fired = state.record(tokens=10, usd=0.0)
        assert all(t != ("tokens", 0.70) for t in fired)


class TestBudgetRegistry:
    def test_explicit_partner_override_wins_over_tier(self) -> None:
        reg = BudgetRegistry(
            partners={"P-1001": BudgetCap(max_tokens=999, max_usd=9.99)},
            defaults_by_tier={"gold": BudgetCap(max_tokens=50_000, max_usd=0.50)},
            alert_thresholds=(0.7, 0.9, 1.0),
        )
        cap = reg.cap_for("P-1001", tier="gold")
        assert cap.max_tokens == 999

    def test_tier_default_used_when_no_explicit_override(self) -> None:
        reg = BudgetRegistry(
            partners={},
            defaults_by_tier={"silver": BudgetCap(max_tokens=20_000, max_usd=0.20)},
            alert_thresholds=(0.7, 0.9, 1.0),
        )
        cap = reg.cap_for("P-NEW", tier="silver")
        assert cap.max_tokens == 20_000

    def test_unknown_partner_and_tier_returns_unlimited(self) -> None:
        reg = BudgetRegistry(partners={}, defaults_by_tier={}, alert_thresholds=(0.7, 0.9, 1.0))
        cap = reg.cap_for("P-NEW", tier=None)
        assert cap.max_tokens == 0
        assert cap.max_usd == 0.0


class TestLoadBudgets:
    def test_loads_shipped_yaml(self) -> None:
        reg = load_budgets()
        gold = reg.defaults_by_tier["gold"]
        assert gold.max_tokens == 50_000
        # P-1001 has an explicit override in budgets.yaml.
        explicit = reg.cap_for("P-1001", tier="gold")
        assert explicit.max_tokens == 50_000


class TestBudgetContextVar:
    def test_current_budget_is_none_by_default(self) -> None:
        assert current_budget() is None

    def test_bind_budget_makes_it_current(self) -> None:
        state = BudgetState(partner_id="P-X", cap=BudgetCap(max_tokens=100, max_usd=1.0))
        with bind_budget(state) as bound:
            assert current_budget() is bound is state
        assert current_budget() is None


class TestProviderBudgetEnforcement:
    def test_mock_provider_raises_when_budget_exceeded(self) -> None:
        from pydantic import BaseModel

        from partner_ticket_agentic.providers import Message, MockProvider, Tier

        class ToyOutput(BaseModel):
            label: str

        # 1-token cap — even the smallest mock call should exceed it.
        state = BudgetState(partner_id="P-X", cap=BudgetCap(max_tokens=1, max_usd=0.0))

        # Snapshot the existing rule registry so this test doesn't wipe
        # the rules other tests rely on (agent modules register rules at
        # import time; clear_rules would orphan the rest of the suite).
        snapshot = dict(MockProvider._rules)
        MockProvider.register(ToyOutput, lambda _s, _m: {"label": "x"})

        try:
            with bind_budget(state), pytest.raises(BudgetExceededError) as exc:
                MockProvider().complete(
                    [Message(role="user", content="hello")], ToyOutput, Tier.SMALL
                )
            assert exc.value.partner_id == "P-X"
            assert exc.value.kind == "tokens"
        finally:
            MockProvider._rules.clear()
            MockProvider._rules.update(snapshot)
