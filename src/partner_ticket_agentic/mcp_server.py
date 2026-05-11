"""Model Context Protocol (MCP) server exposing the tool registry.

The panel deck (slide 13) commits to "Surfaced via MCP for reuse across
agents". This module makes that statement real: every tool registered
via :func:`partner_ticket_agentic.tools.registry.register_tool` is
re-exported as a first-class MCP tool over the official Anthropic
Python SDK. An MCP-aware client — Claude Desktop, the ``mcp`` CLI, or
any other agent — can discover and invoke the same tool surface the
platform's own agents use.

Input schemas are derived from each tool handler's
:class:`inspect.Signature`, mapping Python type annotations to JSON
Schema. Required parameters are those without defaults; optional ones
carry their default through to the schema.

Boots on stdio via ``python -m partner_ticket_agentic --mcp``. Imports
``mcp`` lazily so the core install stays independent of the SDK — the
extras group is ``[mcp]``.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, get_args, get_origin

from partner_ticket_agentic.tools.registry import Tool, ToolRegistry

if TYPE_CHECKING:
    from mcp.server import Server


def _annotation_to_schema(ann: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment.

    Best-effort, not exhaustive — handles the primitives, list/dict, and
    optional unions that this project's tools actually use. Anything we
    can't classify falls back to ``"type": "string"`` so the surface
    stays usable even when the annotation is exotic.
    """

    if ann is inspect.Parameter.empty or ann is Any:
        return {"type": "string"}
    if ann is str:
        return {"type": "string"}
    if ann is int:
        return {"type": "integer"}
    if ann is float:
        return {"type": "number"}
    if ann is bool:
        return {"type": "boolean"}

    origin = get_origin(ann)
    args = get_args(ann)

    # Optional[T] / T | None → schema for T with nullable hint
    if origin is type(None):
        return {"type": "null"}
    if origin is None and args:
        # bare type with args — odd; fall back
        return {"type": "string"}

    # Union (typing.Union or PEP 604 X | Y) — pick the first non-None branch.
    if origin is not None and origin.__name__ in {"Union", "UnionType"}:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _annotation_to_schema(non_none[0])
        return {"type": "string"}

    if origin in (list, tuple, set, frozenset):
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if origin is dict:
        return {"type": "object", "additionalProperties": True}

    return {"type": "string"}


def tool_to_mcp_schema(tool: Tool) -> dict[str, Any]:
    """Build the MCP ``inputSchema`` JSON for one registered tool.

    Walks the handler's signature: each parameter becomes a property,
    parameters without a default land in ``required``. Annotations are
    mapped via :func:`_annotation_to_schema`.
    """

    sig = inspect.signature(tool.handler)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _annotation_to_schema(param.annotation)
        if param.default is param.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def list_tool_descriptors() -> list[dict[str, Any]]:
    """Public, SDK-independent listing of the tools we'd expose over MCP.

    Used both by the MCP server itself and by tests that want to verify
    the surface without spinning up an actual server. Returns one dict
    per tool with ``name``, ``description``, and ``input_schema``.
    """

    descriptors = []
    for name in ToolRegistry.names():
        tool = ToolRegistry.get(name)
        descriptors.append(
            {
                "name": tool.name,
                "description": tool.description.strip() or f"Invoke the {tool.name} tool.",
                "input_schema": tool_to_mcp_schema(tool),
            }
        )
    return descriptors


def call_tool_by_name(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch a registered tool by name with kwargs.

    Used by the MCP ``call_tool`` handler and by tests. Bypasses the
    per-agent allow-list deliberately — MCP exposure is "tool surface
    available to any consumer", and the agent-level enforcement lives
    in :class:`partner_ticket_agentic.tools.registry.ToolDispatcher`.
    A production deployment would layer an MCP-side policy on top.
    """

    tool = ToolRegistry.get(name)
    return tool.handler(**arguments)


def build_server() -> Server:
    """Construct and return the MCP server with all tools registered.

    Imports :mod:`mcp` lazily — callers without the ``[mcp]`` extras
    installed get an :class:`ImportError` they can convert into a
    "install the extras" message.
    """

    from mcp.server import Server
    from mcp.types import TextContent
    from mcp.types import Tool as MCPTool

    server = Server("partner-ticket-agentic")

    @server.list_tools()
    async def _list_tools() -> list[MCPTool]:
        return [
            MCPTool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["input_schema"],
            )
            for d in list_tool_descriptors()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = call_tool_by_name(name, arguments or {})
            payload = _json_safe(result)
        except Exception as exc:
            return [TextContent(type="text", text=f"ERROR: {exc}")]
        import json

        return [TextContent(type="text", text=json.dumps(payload, default=str))]

    return server


def _json_safe(obj: Any) -> Any:
    """Best-effort conversion of tool results to JSON-serialisable types."""

    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list | tuple):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    return obj


async def serve_stdio() -> int:
    """Run the MCP server over stdio. Used by the ``--mcp`` CLI flag."""

    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
    return 0
