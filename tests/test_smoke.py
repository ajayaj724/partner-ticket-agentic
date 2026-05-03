"""Scaffold smoke tests.

These exist so CI has something green to run from commit 1 onwards. They
verify that the package imports cleanly, that the version is exposed, and
that the ``--list`` and ``--help`` CLI surfaces work end-to-end on the seed
data without invoking any network or LLM provider. Subsequent feature
commits land their own focused test modules alongside the agent code.
"""

from __future__ import annotations

import partner_ticket_agentic
from partner_ticket_agentic.cli import build_parser, main


def test_package_imports_and_exposes_version() -> None:
    assert isinstance(partner_ticket_agentic.__version__, str)
    assert partner_ticket_agentic.__version__.count(".") == 2


def test_cli_help_does_not_crash(capsys) -> None:
    parser = build_parser()
    text = parser.format_help()
    assert "--list" in text
    assert "--ticket-id" in text
    assert "--watchdog" in text
    assert "--llm-provider" in text


def test_cli_list_prints_sample_tickets(capsys) -> None:
    rc = main(["--list"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "sample-1" in captured.out
    assert "sample-5" in captured.out


def test_cli_default_provider_is_mock() -> None:
    parser = build_parser()
    args = parser.parse_args(["--list"])
    assert args.llm_provider == "mock"
