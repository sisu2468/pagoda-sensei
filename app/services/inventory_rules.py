"""
Inventory rules for Sensei.

Published is the only eligibility gate. These helpers never invent tours.
They hide airport-transfer products and apply host-agency guide visibility.
"""

from __future__ import annotations

from typing import Any

TRANSFER_ACTIVITY_TYPES = frozenset(
    {
        "transfer",
        "transfers",
        "airport transfers",
        "airport transfers - custom",
        "airport transfer",
        "transferz",
    }
)

_TRANSFER_NAME_MARKERS = (
    "transferz",
    "airport transfer",
    "airport transfers",
    "airport pickup",
    "airport drop",
    "haneda hotel pickup",
    "narita hotel pickup",
)


def is_airport_transfer(tour: dict[str, Any]) -> bool:
    """
    True for Transferz / airport-transfer catalogue rows.

    Partners must not sell airport transfers as tours. Sensei must not
    mix those rows into sightseeing recommendations, and must not create
    a Transferz booking from intake flight notes.
    """
    activity = str(tour.get("tour_type") or "").strip().casefold()
    if activity in TRANSFER_ACTIVITY_TYPES:
        return True

    text = " ".join(
        [
            str(tour.get("tour_name") or ""),
            str(tour.get("description") or ""),
        ]
    ).casefold()
    if any(marker in text for marker in _TRANSFER_NAME_MARKERS):
        return True
    if "airport" in text and "transfer" in text:
        return True
    return False


def _all_guides(tour: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tour.get("guides")
    if isinstance(raw, list) and raw:
        return [g for g in raw if isinstance(g, dict)]
    guide = tour.get("guide")
    if isinstance(guide, dict) and (guide.get("id") or guide.get("name") or guide.get("status")):
        return [guide]
    return []


def guide_visible_to_agency(guide: dict[str, Any], host_agency_id: str | None) -> bool:
    """
    Host-agency exclusivity: Fora only sees Fora's introduced guides.

    A guide with no introduced_by_agency_id is visible to every agency.
    Missing exclusivity metadata is a no-op so live inventory is not dropped.
    """
    if not host_agency_id or not str(host_agency_id).strip():
        return True
    exclusive = (
        guide.get("introduced_by_agency_id")
        or guide.get("host_agency_id")
        or guide.get("exclusive_to_agency_id")
    )
    if not exclusive:
        return True
    return str(exclusive).strip().casefold() == str(host_agency_id).strip().casefold()


def visible_guides(
    tour: dict[str, Any],
    host_agency_id: str | None = None,
) -> list[dict[str, Any]]:
    """Guides an advisor at this host agency may see on the tour card."""
    return [
        g for g in _all_guides(tour) if guide_visible_to_agency(g, host_agency_id)
    ]


def primary_guide(
    tour: dict[str, Any],
    host_agency_id: str | None = None,
) -> dict[str, Any]:
    """
    Card primary guide. If the only appointed guide is exclusive to another
    agency, the tour stays eligible with “Guide to be appointed”.
    """
    for guide in visible_guides(tour, host_agency_id):
        status = str(guide.get("status") or "").casefold()
        if guide.get("id") or status == "appointed":
            payload = dict(guide)
            payload.setdefault("status", "appointed")
            return payload
    return {"id": None, "name": None, "status": "to_be_appointed"}


def tour_matches_guide(
    tour: dict[str, Any],
    *,
    guide_id: str | None = None,
    guide_name: str | None = None,
) -> bool:
    """Match against assigned guides, including exclusive ones (search is explicit)."""
    guides = _all_guides(tour)
    if not guides:
        return False
    if guide_id:
        want = str(guide_id).strip()
        return any(str(g.get("id") or "") == want for g in guides)
    token = (guide_name or "").strip().casefold()
    if not token:
        return False
    return any(token in str(g.get("name") or "").casefold() for g in guides)


def find_guide_in_inventory(
    tours: list[dict[str, Any]],
    query: str | None,
) -> tuple[str | None, str | None]:
    """
    Second entry: search-by-guide-name.

    Returns (guide_id, guide_name) when the query names a real assigned guide.
    Does not guess a person from a city or experience phrase.
    """
    if not query or not query.strip():
        return None, None
    text = query.strip()
    folded = text.casefold()

    prefixed = None
    for prefix in ("tours by ", "tours from ", "guide ", "by "):
        if folded.startswith(prefix):
            prefixed = text[len(prefix) :].strip()
            break

    candidates: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for tour in tours:
        for guide in _all_guides(tour):
            name = str(guide.get("name") or "").strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            candidates.append((str(guide["id"]) if guide.get("id") else None, name))

    search = (prefixed or text).casefold()
    # Prefer an exact / full-name hit so “food tour in Osaka” does not match a guide.
    hits = [
        (gid, name)
        for gid, name in candidates
        if len(name) >= 4 and name.casefold() in search
    ]
    if len(hits) == 1:
        return hits[0]
    if prefixed and len(hits) >= 1:
        return hits[0]
    return None, None


def public_guide_fields(guide: dict[str, Any]) -> dict[str, Any]:
    """Advisor-safe guide payload. Never include daily_rate / net price."""
    return {
        "id": guide.get("id"),
        "name": guide.get("name"),
        "status": guide.get("status") or ("appointed" if guide.get("id") else "to_be_appointed"),
        "city": guide.get("city"),
        "specialties": guide.get("specialties"),
        "availability_display": guide.get("availability_display")
        or (
            "Guide to be appointed"
            if (guide.get("status") == "to_be_appointed" or not guide.get("id"))
            else "Availability on request"
        ),
        "introduced_by_agency_id": guide.get("introduced_by_agency_id")
        or guide.get("host_agency_id"),
    }
