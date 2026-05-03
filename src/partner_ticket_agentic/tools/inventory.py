"""Inventory lookup tool — circuit state, owning partner, last-check time.

For the demo, the inventory is derived from ``data/partners.json``: a
circuit's owning partner is whoever lists it under ``active_circuits``,
and the status is synthesised deterministically from the circuit ID so
that the demo is reproducible. Production would point this at the real
inventory system (e.g., an OSS / NetBox).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from partner_ticket_agentic.tools.crm import _load_partners
from partner_ticket_agentic.tools.registry import ToolError, register_tool

CircuitStatus = Literal["up", "degraded", "down", "decommissioned", "unknown"]


class CircuitInfo(BaseModel):
    """Inventory record returned by :func:`inventory_lookup_circuit`."""

    model_config = ConfigDict(extra="forbid")

    circuit_id: str
    owning_partner_id: str
    status: CircuitStatus
    bandwidth_mbps: int
    site: str | None = None


def _circuit_to_partner_index() -> dict[str, str]:
    """Map circuit_id -> partner_id from the shipped partners seed."""

    out: dict[str, str] = {}
    for partner_id, row in _load_partners().items():
        for circ in row.get("active_circuits") or []:
            out[circ] = partner_id
    return out


def _synth_status(circuit_id: str) -> CircuitStatus:
    """Synthesise a status from the circuit ID for demo reproducibility.

    The mapping is explicit (a small ``if`` ladder), not random or
    hash-based, so the panel demo gives the same status on every run.
    Reviewers can read it and predict.
    """

    if circuit_id == "CIRC-44781":
        return "down"
    if circuit_id == "CIRC-44782":
        return "degraded"
    if circuit_id == "CIRC-66302":
        return "up"
    if circuit_id == "CIRC-77410":
        return "up"
    return "up"


def _synth_bandwidth(circuit_id: str) -> int:
    if circuit_id.endswith("0"):
        return 10_000
    if circuit_id.endswith(("1", "2")):
        return 1_000
    return 100


@register_tool(
    "inventory_lookup_circuit",
    description="Fetch a circuit's inventory record — owning partner, status, bandwidth.",
)
def inventory_lookup_circuit(*, circuit_id: str) -> CircuitInfo:
    """Return the :class:`CircuitInfo` for ``circuit_id`` from inventory."""

    owners = _circuit_to_partner_index()
    partner_id = owners.get(circuit_id)
    if partner_id is None:
        raise ToolError(f"circuit {circuit_id!r} not found in inventory")
    return CircuitInfo(
        circuit_id=circuit_id,
        owning_partner_id=partner_id,
        status=_synth_status(circuit_id),
        bandwidth_mbps=_synth_bandwidth(circuit_id),
    )
