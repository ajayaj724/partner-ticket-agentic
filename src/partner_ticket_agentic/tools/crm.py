"""CRM lookup tool — read-only access to the partners directory.

Backed by ``data/partners.json`` for the demo. Production would point this
at the real CRM (Salesforce, internal partner registry, etc.) — the
function signature stays identical, only the implementation moves.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.registry import ToolError, register_tool


class Partner(BaseModel):
    """Partner record returned by :func:`crm_lookup_partner`."""

    model_config = ConfigDict(extra="forbid")

    partner_id: str
    name: str
    tier: str = Field(description="Service tier — gold/silver/bronze.")
    primary_contact: str
    primary_phone: str
    active_circuits: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _load_partners() -> dict[str, dict[str, Any]]:
    path = _seed_path()
    with path.open("r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return {r["partner_id"]: r for r in rows}


def _seed_path() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "data" / "partners.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("data/partners.json not found")


@register_tool("crm_lookup_partner", description="Fetch a partner record by partner_id.")
def crm_lookup_partner(*, partner_id: str) -> Partner:
    """Return the :class:`Partner` for ``partner_id``.

    Raises :class:`ToolError` for unknown IDs — the F2 Enricher catches
    this and proceeds without the partner profile rather than failing the
    whole flow (per DESIGN.md §3 F2 "if any tool fails after retries, omit
    that section but continue").
    """

    rows = _load_partners()
    row = rows.get(partner_id)
    if row is None:
        raise ToolError(f"partner {partner_id!r} not found in CRM")
    return Partner(**row)
