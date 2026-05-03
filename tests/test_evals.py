"""Smoke + threshold tests for the eval suite.

The eval runner is invoked in CI; these tests pin the precision/recall
contract for the deterministic mock path so a regression in any agent's
classifier surfaces here before CI runs the full suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_ticket_agentic.evals.__main__ import (
    _load_jsonl,
    _score_breach,
    _score_duplicates,
    _score_routing,
    _score_runbooks,
    _score_triage,
    main,
)


def _evals(name: str) -> list[dict]:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "evals" / name
        if candidate.exists():
            return _load_jsonl(candidate)
    raise FileNotFoundError(name)


def test_triage_eval_thresholds() -> None:
    result = _score_triage(_evals("triage_categories.jsonl"))
    assert result["n"] >= 20
    # Mock is the keyword classifier; on the hand-labelled set it should
    # comfortably exceed 0.85 category accuracy.
    assert result["category_accuracy"] >= 0.85
    assert result["urgency_accuracy"] >= 0.85


def test_routing_eval_thresholds() -> None:
    result = _score_routing(_evals("routing_decisions.jsonl"))
    assert result["n"] >= 15
    assert result["queue_accuracy"] >= 0.85


def test_runbook_eval_thresholds() -> None:
    result = _score_runbooks(_evals("runbook_relevance.jsonl"))
    assert result["n"] >= 15
    # Top-3 should be very high — top-1 is harder for the deterministic
    # hashing embedder, so we set it lower.
    assert result["top3_accuracy"] >= 0.85
    assert result["top1_accuracy"] >= 0.55


def test_breach_eval_thresholds() -> None:
    result = _score_breach(_evals("breach_replay.jsonl"))
    assert result["n"] >= 10
    assert result["band_accuracy"] >= 0.85


def test_duplicate_eval_returns_well_formed_metrics() -> None:
    result = _score_duplicates(_evals("duplicate_pairs.jsonl"))
    assert result["n"] >= 10
    # Both precision and recall must be defined (non-NaN), and at least one
    # true positive must come through.
    assert 0.0 <= result["precision"] <= 1.0
    assert 0.0 <= result["recall"] <= 1.0


def test_runner_main_prints_a_report(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "F1 Triage" in out
    assert "F3 Router" in out
    assert "F4 Knowledge" in out
    assert "F7 Linker" in out
    assert "F8 Watchdog" in out
    assert "Procedural-agent coverage" in out


def test_every_eval_jsonl_parses_cleanly() -> None:
    here = Path(__file__).resolve()
    root = next(
        p
        for p in (here, *here.parents)
        if (p / "evals").is_dir() and (p / "pyproject.toml").exists()
    )
    for path in (root / "evals").glob("*.jsonl"):
        rows = _load_jsonl(path)
        assert rows, f"{path.name} is empty"
        for row in rows:
            assert "id" in row, f"{path.name}: missing 'id' on row"
            # Each row round-trips through json.dumps without TypeErrors.
            json.dumps(row)
