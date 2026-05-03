"""Tests for the tool registry and per-agent dispatcher."""

from __future__ import annotations

import pytest

from partner_ticket_agentic.safety import ToolAllowList, ToolNotAllowedError
from partner_ticket_agentic.tools.registry import (
    ToolDispatcher,
    ToolError,
    ToolRegistry,
    register_tool,
)


@register_tool("__test_echo", description="Echo for tests.")
def _echo(*, payload: str) -> str:
    return payload[::-1]


@register_tool("__test_boom", description="Always raises for tests.")
def _boom(**_: object) -> object:
    raise RuntimeError("kaboom")


def test_registry_lists_registered_tools() -> None:
    names = ToolRegistry.names()
    assert "__test_echo" in names
    assert "crm_lookup_partner" in names
    assert "inventory_lookup_circuit" in names
    assert "ticket_history_recent" in names
    assert "runbook_search" in names


def test_dispatcher_calls_allowed_tool() -> None:
    allow = ToolAllowList.of("test", "__test_echo")
    dispatcher = ToolDispatcher(allow_list=allow)
    assert dispatcher.call("__test_echo", payload="abc") == "cba"


def test_dispatcher_rejects_disallowed_tool() -> None:
    allow = ToolAllowList.of("test", "__test_echo")
    dispatcher = ToolDispatcher(allow_list=allow)
    with pytest.raises(ToolNotAllowedError):
        dispatcher.call("crm_lookup_partner", partner_id="P-1001")


def test_dispatcher_wraps_tool_errors_as_tool_error() -> None:
    allow = ToolAllowList.of("test", "__test_boom")
    dispatcher = ToolDispatcher(allow_list=allow)
    with pytest.raises(ToolError) as exc:
        dispatcher.call("__test_boom")
    assert "kaboom" in str(exc.value)


def test_unknown_tool_raises_tool_error() -> None:
    allow = ToolAllowList.of("test", "ghost_tool")
    dispatcher = ToolDispatcher(allow_list=allow)
    with pytest.raises(ToolError):
        dispatcher.call("ghost_tool")
