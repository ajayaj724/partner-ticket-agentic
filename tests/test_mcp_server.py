"""Tests for the MCP-server exposure of the tool registry.

The ``mcp`` extras are optional — these tests skip cleanly if the SDK
isn't installed. When it is, they cover the SDK-independent surface
(``list_tool_descriptors``, ``call_tool_by_name``, schema derivation)
and a smoke test of the server-construction path.
"""

from __future__ import annotations

import pytest

# Import the tool modules so their @register_tool decorators run before the
# MCP server enumerates tools.
import partner_ticket_agentic.tools  # noqa: F401
from partner_ticket_agentic.mcp_server import (
    _annotation_to_schema,
    call_tool_by_name,
    list_tool_descriptors,
    tool_to_mcp_schema,
)
from partner_ticket_agentic.tools.registry import ToolRegistry


class TestAnnotationToSchema:
    def test_primitives(self) -> None:
        assert _annotation_to_schema(str) == {"type": "string"}
        assert _annotation_to_schema(int) == {"type": "integer"}
        assert _annotation_to_schema(float) == {"type": "number"}
        assert _annotation_to_schema(bool) == {"type": "boolean"}

    def test_list_of_strings(self) -> None:
        schema = _annotation_to_schema(list[str])
        assert schema == {"type": "array", "items": {"type": "string"}}

    def test_optional(self) -> None:
        # ``str | None`` is the most common project shape; the schema
        # should resolve to the non-None branch.
        schema = _annotation_to_schema(str | None)
        assert schema == {"type": "string"}

    def test_dict(self) -> None:
        schema = _annotation_to_schema(dict[str, int])
        assert schema["type"] == "object"


class TestToolDescriptors:
    def test_every_registered_tool_surfaces(self) -> None:
        descriptors = list_tool_descriptors()
        names_in_descriptors = {d["name"] for d in descriptors}
        # Spot-check a few of the tools from F2 / F3 / F4 / F6 / F8.
        for expected in [
            "crm_lookup_partner",
            "directory_resolve_assignee",
            "runbook_search",
            "engineer_calendar_available_slots",
            "notify_oncall",
        ]:
            assert expected in names_in_descriptors, f"missing {expected!r} in MCP surface"
        assert names_in_descriptors == set(ToolRegistry.names())

    def test_descriptors_carry_input_schema(self) -> None:
        for d in list_tool_descriptors():
            assert d["input_schema"]["type"] == "object"
            assert "properties" in d["input_schema"]

    def test_schema_derives_required_from_default_less_params(self) -> None:
        # crm_lookup_partner(partner_id: str) -> ...
        crm = ToolRegistry.get("crm_lookup_partner")
        schema = tool_to_mcp_schema(crm)
        assert "partner_id" in schema["properties"]
        assert "partner_id" in schema.get("required", [])


class TestCallToolByName:
    def test_round_trip_through_registry(self) -> None:
        # Fetching a known seeded partner should succeed and return a dict-
        # like with the expected name.
        result = call_tool_by_name("crm_lookup_partner", {"partner_id": "P-1001"})
        # The tool returns a Pydantic model — verify by attr access.
        assert getattr(result, "name", None) == "BrusselsNet BV"
        assert getattr(result, "tier", None) == "gold"


class TestServerConstruction:
    def test_build_server_smoke(self) -> None:
        mcp = pytest.importorskip(
            "mcp", reason="install [mcp] extras to run server-construction test"
        )
        del mcp
        from partner_ticket_agentic.mcp_server import build_server

        server = build_server()
        # The Anthropic SDK's Server exposes the registered handlers via
        # private attributes. We don't depend on those — just verify the
        # object is constructed without raising and carries the right name.
        assert server.name == "partner-ticket-agentic"
