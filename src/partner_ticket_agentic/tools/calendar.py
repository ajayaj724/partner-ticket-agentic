"""Calendar / address / travel-time tools used by F6 Scheduler.

Deterministic seeds — the demo run picks the same slots every time so the
panel can compare runs. Production swaps these for the real dispatch
calendar, geocoder, and routing service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from partner_ticket_agentic.tools.crm import _load_partners
from partner_ticket_agentic.tools.registry import ToolError, register_tool


class TimeSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engineer_id: str
    starts_at: datetime
    ends_at: datetime
    region: str


class PartnerAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_id: str
    city: str
    region: str


class TravelEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_region: str
    to_region: str
    minutes: int


class SlotScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engineer_id: str
    starts_at: datetime
    ends_at: datetime
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


# A small fixed roster of engineers per region. Demo only.
_REGION_OF_PARTNER = {
    "P-1001": ("Brussels", "BR"),
    "P-1002": ("Antwerp", "AN"),
    "P-1003": ("Liège", "LI"),
    "P-1004": ("Ghent", "GH"),
}

_ENGINEER_REGIONS: dict[str, str] = {
    "eng-100": "BR",
    "eng-101": "AN",
    "eng-102": "GH",
    "eng-103": "LI",
}

# Fixed travel-time matrix in minutes — symmetric, demo-only.
_TRAVEL_MIN: dict[tuple[str, str], int] = {
    ("BR", "BR"): 5,
    ("BR", "AN"): 50,
    ("BR", "GH"): 60,
    ("BR", "LI"): 90,
    ("AN", "AN"): 5,
    ("AN", "GH"): 70,
    ("AN", "LI"): 110,
    ("GH", "GH"): 5,
    ("GH", "LI"): 130,
    ("LI", "LI"): 5,
}


def _travel(a: str, b: str) -> int:
    return _TRAVEL_MIN.get((a, b)) or _TRAVEL_MIN.get((b, a)) or 60


# Deterministic next-business-day seed — generated from a fixed reference
# date so demo output is stable across runs. Production reads the live
# calendar.
_SLOT_REFERENCE = datetime(2026, 5, 5, 8, 0, tzinfo=UTC)


# Per-engineer slot-time offset in minutes — explicit table so the demo
# is reproducible across processes (Python's hash() is randomised by
# PYTHONHASHSEED and would shuffle slots between runs).
_OFFSET_BY_ENG: dict[str, int] = {
    "eng-100": 0,
    "eng-101": 12,
    "eng-102": 24,
    "eng-103": 6,
}


def _seed_slots(engineer_id: str, region: str) -> list[TimeSlot]:
    out: list[TimeSlot] = []
    base = _SLOT_REFERENCE
    offset_min = _OFFSET_BY_ENG.get(engineer_id, 0)
    for day in range(5):
        for hour in (9, 11, 14, 16):
            starts = base + timedelta(days=day, hours=hour - base.hour, minutes=offset_min)
            ends = starts + timedelta(hours=2)
            out.append(
                TimeSlot(
                    engineer_id=engineer_id,
                    starts_at=starts,
                    ends_at=ends,
                    region=region,
                )
            )
    return out


@register_tool(
    "engineer_calendar_available_slots",
    description="List available 2-hour slots for engineers in the given region.",
)
def engineer_calendar_available_slots(*, region: str, limit: int = 8) -> list[TimeSlot]:
    out: list[TimeSlot] = []
    for eng_id, eng_region in _ENGINEER_REGIONS.items():
        if eng_region != region:
            continue
        out.extend(_seed_slots(eng_id, eng_region))
    out.sort(key=lambda s: s.starts_at)
    return out[:limit]


@register_tool(
    "partner_address_lookup",
    description="Partner's primary address / region from CRM.",
)
def partner_address_lookup(*, partner_id: str) -> PartnerAddress:
    partners = _load_partners()
    if partner_id not in partners:
        raise ToolError(f"partner {partner_id!r} not in CRM")
    city, region = _REGION_OF_PARTNER.get(partner_id, ("Brussels", "BR"))
    return PartnerAddress(partner_id=partner_id, city=city, region=region)


@register_tool(
    "travel_time_estimate",
    description="Travel-time estimate (minutes) between two regions.",
)
def travel_time_estimate(*, from_region: str, to_region: str) -> TravelEstimate:
    minutes = _travel(from_region, to_region)
    return TravelEstimate(from_region=from_region, to_region=to_region, minutes=minutes)


@register_tool(
    "slot_score",
    description="Score a slot against partner urgency and travel time. Deterministic.",
)
def slot_score(
    *,
    slot: TimeSlot,
    partner_address: PartnerAddress,
    urgency: str,
) -> SlotScore:
    travel = _travel(slot.region, partner_address.region)
    # Score: closer-in-time + lower-travel-time is better. Urgency scales
    # the time-proximity weight.
    days_out = (slot.starts_at - _SLOT_REFERENCE).days
    urgency_weight = {"critical": 0.5, "high": 0.35, "medium": 0.2, "low": 0.1}.get(urgency, 0.2)
    proximity = max(0.0, 1.0 - days_out * 0.18)
    travel_pen = max(0.0, 1.0 - travel / 180.0)
    score = round(min(1.0, urgency_weight * 2 * proximity + 0.5 * travel_pen), 4)
    rationale = f"days_out={days_out}, travel={travel}m, urgency={urgency}, score={score}"
    return SlotScore(
        engineer_id=slot.engineer_id,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        score=score,
        rationale=rationale,
    )
