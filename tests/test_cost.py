"""Tests for the cost-estimation + per-ticket ledger module."""

from __future__ import annotations

from partner_ticket_agentic.cost import (
    CostLedger,
    bind_ledger,
    current_ledger,
    estimate_cost,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_deterministic_for_same_text(self) -> None:
        a = estimate_tokens("circuit CIRC-44781 is down")
        b = estimate_tokens("circuit CIRC-44781 is down")
        assert a == b

    def test_scales_with_length(self) -> None:
        short = estimate_tokens("hi")
        long = estimate_tokens("the quick brown fox jumps over the lazy dog " * 10)
        assert long > short

    def test_minimum_one_token(self) -> None:
        assert estimate_tokens("") >= 1
        assert estimate_tokens("x") >= 1


class TestEstimateCost:
    def test_anthropic_haiku_input_output(self) -> None:
        breakdown = estimate_cost(
            "anthropic",
            "claude-haiku-4-5-20251001",
            tokens_in=1_000_000,
            tokens_out=0,
        )
        assert breakdown.usd == 1.0  # $1 per 1M input tokens

    def test_anthropic_caching_discount(self) -> None:
        # 1M cached input tokens at haiku rate = 10% of $1 = $0.10
        breakdown = estimate_cost(
            "anthropic",
            "claude-haiku-4-5-20251001",
            tokens_in=0,
            tokens_out=0,
            cached_input_tokens=1_000_000,
        )
        assert breakdown.usd == 0.10
        assert breakdown.cache_hit is True

    def test_mock_is_always_free(self) -> None:
        breakdown = estimate_cost("mock", "mock-small", tokens_in=10_000_000, tokens_out=10_000_000)
        assert breakdown.usd == 0.0

    def test_ollama_is_always_free(self) -> None:
        breakdown = estimate_cost(
            "ollama", "llama3.2:3b", tokens_in=10_000_000, tokens_out=10_000_000
        )
        assert breakdown.usd == 0.0

    def test_unknown_model_falls_back_to_zero(self) -> None:
        breakdown = estimate_cost(
            "anthropic", "claude-future-99", tokens_in=1_000_000, tokens_out=1_000_000
        )
        assert breakdown.usd == 0.0


class TestCostLedger:
    def test_summary_empty_ledger(self) -> None:
        ledger = CostLedger()
        s = ledger.summary()
        assert s["calls"] == 0
        assert s["cost_usd"] == 0.0
        assert s["by_agent"] == {}

    def test_summary_rolls_up_across_agents(self) -> None:
        ledger = CostLedger()
        b1 = estimate_cost(
            "anthropic", "claude-haiku-4-5-20251001", tokens_in=10_000, tokens_out=2_000
        )
        b2 = estimate_cost("anthropic", "claude-sonnet-4-6", tokens_in=5_000, tokens_out=1_000)
        ledger.record(
            agent="triage", provider="anthropic", model="claude-haiku-4-5-20251001", breakdown=b1
        )
        ledger.record(
            agent="drafter", provider="anthropic", model="claude-sonnet-4-6", breakdown=b2
        )
        s = ledger.summary()
        assert s["calls"] == 2
        assert s["tokens_in"] == 15_000
        assert s["tokens_out"] == 3_000
        assert s["cost_usd"] > 0.0
        assert set(s["by_agent"].keys()) == {"triage", "drafter"}
        assert s["by_agent"]["triage"]["calls"] == 1


class TestLedgerContextVar:
    def test_current_ledger_is_none_by_default(self) -> None:
        assert current_ledger() is None

    def test_bind_ledger_makes_it_current(self) -> None:
        ledger = CostLedger()
        with bind_ledger(ledger) as bound:
            assert current_ledger() is bound is ledger
        assert current_ledger() is None

    def test_bind_ledger_is_nestable(self) -> None:
        outer, inner = CostLedger(), CostLedger()
        with bind_ledger(outer):
            assert current_ledger() is outer
            with bind_ledger(inner):
                assert current_ledger() is inner
            assert current_ledger() is outer
        assert current_ledger() is None
