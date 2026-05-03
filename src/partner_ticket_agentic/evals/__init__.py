"""Eval harness for the partner-ticketing agentic platform.

Each eval set is a JSONL golden under ``evals/`` keyed by feature
(``triage_categories.jsonl``, ``routing_decisions.jsonl``,
``duplicate_pairs.jsonl``, ``runbook_relevance.jsonl``,
``breach_replay.jsonl``). The runner is wired in
:mod:`partner_ticket_agentic.evals.__main__` and is exercised in CI on every
push so precision/recall regressions surface before merge.
"""

from __future__ import annotations
