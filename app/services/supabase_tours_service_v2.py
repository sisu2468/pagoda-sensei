"""
Supabase Integration Service — CORRECTED for Pagoda Travel Pro schema

This replaces the earlier version that was connected to the wrong
Supabase project. The real production schema uses:

  tour                    — the actual tour data (name, location, price,
                             start_time/end_time, status)
  guide_tour_assignments  — join table: tour_id -> guide_id, operator_id
  users                   — guide/operator name, email
  profiles                — guide bio, specialties, daily_rate,
                             guide_availability_calendar (jsonb)

Price is real (price_per_adult / price_per_child, in JPY based on
sample data). Availability comes from the guide's
guide_availability_calendar on their profile, not a per-tour field.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from app.services.inventory_rules import (
    is_excluded_catalogue,
    location_matches_city,
    tour_matches_guide,
)

logger = logging.getLogger(__name__)

MOCK_TOURS_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_tours.json"

_TRANSPORT_ACTIVITY_TYPES = {
    "Shinkansen Tickets (bullet train)",
    "Pagoda Support",
    "Airport transfers - Custom",
    "Airport transfers",
    "Airport transfer",
    "Transfers",
    "Transferz",
    "Special Accommodations",
    "Special Accommodation",
    "Special Accomodations",
}


def use_mock_tours() -> bool:
    flag = (os.getenv("USE_MOCK_TOURS") or "").strip().lower()
    url = (os.getenv("SUPABASE_URL") or "").strip().lower()
    return flag in {"1", "true", "yes"} or "mock.supabase.local" in url


def _normalize_mock_tour(tour: dict[str, Any]) -> dict[str, Any]:
    """Ensure mock rows have guides[] and never expose a net rate."""
    row = dict(tour)
    guides = row.get("guides")
    if not isinstance(guides, list) or not guides:
        guide = row.get("guide") or {}
        row["guides"] = [guide] if guide else []
    return row


def fetch_mock_tours(
    destinations: list[str] | None = None,
    guide_id: str | None = None,
    guide_name: str | None = None,
) -> list[dict[str, Any]]:
    """Local published-tour inventory. No network. Guide assignment optional."""
    if not MOCK_TOURS_PATH.exists():
        raise FileNotFoundError(f"Mock tour file not found at {MOCK_TOURS_PATH}")
    with MOCK_TOURS_PATH.open("r", encoding="utf-8") as fh:
        tours: list[dict[str, Any]] = json.load(fh)

    published: list[dict[str, Any]] = []
    for raw in tours:
        tour = _normalize_mock_tour(raw)
        if is_excluded_catalogue(tour):
            continue
        if (guide_id or guide_name) and not tour_matches_guide(
            tour, guide_id=guide_id, guide_name=guide_name
        ):
            continue
        if destinations:
            if not any(location_matches_city(tour.get("location"), d) for d in destinations):
                continue
        published.append(tour)

    logger.info(
        "Loaded %d MOCK published tours (destinations=%s guide=%s)",
        len(published), destinations, guide_id or guide_name,
    )
    return published


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment"
        )
    if use_mock_tours():
        raise EnvironmentError(
            "USE_MOCK_TOURS is on — not connecting to Supabase. "
            "Set USE_MOCK_TOURS=false and real SUPABASE_URL / SUPABASE_ANON_KEY for live data."
        )

    logger.info("Initialising Supabase client for %s", url)
    return create_client(url, key)


def _format_duration(start_time: str | None, end_time: str | None) -> tuple[int | None, str]:
    """Compute duration in minutes from start_time/end_time strings (HH:MM:SS)."""
    if not start_time or not end_time:
        return None, "Duration not specified"

    try:
        t1 = datetime.strptime(start_time, "%H:%M:%S").time()
        t2 = datetime.strptime(end_time, "%H:%M:%S").time()

        minutes = (t2.hour * 60 + t2.minute) - (t1.hour * 60 + t1.minute)
        if minutes < 0:
            # Overnight tour (e.g. 15:00 -> 10:00 next day)
            minutes += 24 * 60

        if minutes < 60:
            return minutes, f"{minutes} min"
        hours = minutes / 60
        if hours == int(hours):
            return minutes, f"{int(hours)} hour{'s' if hours != 1 else ''}"
        return minutes, f"{hours:.1f} hours"
    except ValueError:
        return None, "Duration not specified"


def _format_price(price_per_adult: Any) -> tuple[float | None, str]:
    """Format price, handling null/zero cases."""
    if price_per_adult is None:
        return None, "Contact operator for pricing"
    try:
        price = float(price_per_adult)
        if price <= 0:
            return None, "Contact operator for pricing"
        return price, f"¥{price:,.0f} per adult"
    except (ValueError, TypeError):
        return None, "Contact operator for pricing"


def fetch_live_tours(
    destinations: list[str] | None = None,
    guide_id: str | None = None,
    guide_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch published tours. Published is the only eligibility gate.

    Guide assignment is a label ("appointed" vs "to be appointed"), never a filter.
    If a destination has zero published tours, return none for that city —
    do not fall back to another region's catalogue.
    Airport-transfer / Transferz catalogue rows are excluded.
    """
    if use_mock_tours():
        return fetch_mock_tours(
            destinations, guide_id=guide_id, guide_name=guide_name
        )

    MAX_TOURS = 60
    client = get_supabase_client()

    query = (
        client.table("tour")
        .select("id, name, location, country, description, start_time, end_time, "
                "price_per_adult, price_per_child, status, activity_type, user_id")
        .eq("status", "published")
        .not_.in_("activity_type", list(_TRANSPORT_ACTIVITY_TYPES))
    )

    if destinations:
        or_conditions = []
        for dest in destinations:
            dest_clean = dest.strip().replace(",", "").replace("%", "")
            if not dest_clean:
                continue
            or_conditions.append(f"location.ilike.%{dest_clean}%")
        if or_conditions:
            query = query.or_(",".join(or_conditions))

    filter_guide_id = guide_id
    filter_guide_name = guide_name
    name_matched_ids: list[str] = []
    if filter_guide_name and not filter_guide_id:
        token = filter_guide_name.strip().replace(",", "").replace("%", "")
        if token:
            users_by_name = (
                client.table("users")
                .select("id, first_name, last_name")
                .or_(f"first_name.ilike.%{token}%,last_name.ilike.%{token}%")
                .execute()
            )
            name_matched_ids = [r["id"] for r in (users_by_name.data or []) if r.get("id")]

    lookup_guide_ids = [filter_guide_id] if filter_guide_id else name_matched_ids
    if lookup_guide_ids:
        guide_assignments = (
            client.table("guide_tour_assignments")
            .select("tour_id, guide_id")
            .in_("guide_id", lookup_guide_ids)
            .execute()
        )
        extra_tour_ids = [
            r["tour_id"] for r in (guide_assignments.data or []) if r.get("tour_id")
        ]
        if extra_tour_ids and not destinations:
            query = query.in_("id", extra_tour_ids)

    tours_response = query.execute()
    tour_rows = tours_response.data or []

    if destinations and not tour_rows:
        logger.info(
            "No published tours for destinations=%s — returning empty inventory "
            "(no unfiltered fallback)",
            destinations,
        )
        return []

    if len(tour_rows) > MAX_TOURS:
        by_location: dict[str, list[dict]] = {}
        for row in tour_rows:
            loc = (row.get("location") or "Unknown").strip()
            by_location.setdefault(loc, []).append(row)

        capped: list[dict] = []
        locations = list(by_location.keys())
        idx = 0
        while len(capped) < MAX_TOURS and any(by_location[loc] for loc in locations):
            loc = locations[idx % len(locations)]
            if by_location[loc]:
                capped.append(by_location[loc].pop(0))
            idx += 1
        tour_rows = capped
        logger.info(
            "Capped tour set from %d to %d, distributed across %d locations",
            len(tours_response.data or []), len(tour_rows), len(locations),
        )

    tour_ids = [row["id"] for row in tour_rows if row.get("id")]

    assignments_by_tour: dict[Any, list[dict]] = {}
    if tour_ids:
        assignments_response = (
            client.table("guide_tour_assignments")
            .select("tour_id, guide_id, operator_id")
            .in_("tour_id", tour_ids)
            .execute()
        )
        for row in assignments_response.data or []:
            assignments_by_tour.setdefault(row["tour_id"], []).append(row)

    unassigned = [r["id"] for r in tour_rows if r.get("id") not in assignments_by_tour]
    if unassigned:
        logger.info(
            "Keeping %d published tour(s) with no guide allocated: %s",
            len(unassigned), unassigned,
        )

    owner_ids = [r.get("user_id") for r in tour_rows if r.get("user_id")]
    assigned_guide_ids = [
        a["guide_id"]
        for rows in assignments_by_tour.values()
        for a in rows
        if a.get("guide_id")
    ]
    operator_ids = [
        a["operator_id"]
        for rows in assignments_by_tour.values()
        for a in rows
        if a.get("operator_id")
    ]
    person_ids = list({*owner_ids, *assigned_guide_ids, *operator_ids})

    user_by_id: dict[str, dict] = {}
    if person_ids:
        users_response = (
            client.table("users")
            .select("id, first_name, last_name, email")
            .in_("id", person_ids)
            .execute()
        )
        for row in users_response.data or []:
            user_by_id[row["id"]] = row

    profile_by_user_id: dict[str, dict] = {}
    if assigned_guide_ids:
        profiles_response = (
            client.table("profiles")
            .select("user_id, specialties, guide_availability_calendar, city")
            .in_("user_id", list(set(assigned_guide_ids)))
            .execute()
        )
        for row in profiles_response.data or []:
            profile_by_user_id[row["user_id"]] = row

    def _display_name(user: dict) -> str:
        first = user.get("first_name", "")
        last = user.get("last_name", "")
        return f"{first} {last}".strip()

    def _guide_record(assigned_guide_id: Any) -> dict[str, Any]:
        guide_user = user_by_id.get(assigned_guide_id, {}) if assigned_guide_id else {}
        profile = profile_by_user_id.get(assigned_guide_id, {}) if assigned_guide_id else {}
        availability_calendar = profile.get("guide_availability_calendar")
        if not assigned_guide_id:
            availability_display = "Guide to be appointed"
        elif not availability_calendar:
            availability_display = "Availability on request"
        else:
            availability_display = "See guide calendar for availability"
        return {
            "id": assigned_guide_id,
            "name": _display_name(guide_user) or None,
            "status": "appointed" if assigned_guide_id else "to_be_appointed",
            "city": profile.get("city"),
            "specialties": profile.get("specialties"),
            "availability_display": availability_display,
            "introduced_by_agency_id": profile.get("introduced_by_agency_id")
            or profile.get("host_agency_id"),
        }

    tours: list[dict[str, Any]] = []
    for row in tour_rows:
        row_id = row.get("id")
        assignments = assignments_by_tour.get(row_id, [])
        owner_id = row.get("user_id") or (assignments[0].get("operator_id") if assignments else None)
        owner = user_by_id.get(owner_id, {}) if owner_id else {}

        guides = [
            _guide_record(a.get("guide_id"))
            for a in assignments
            if a.get("guide_id")
        ]
        primary = guides[0] if guides else {
            "id": None,
            "name": None,
            "status": "to_be_appointed",
            "city": None,
            "specialties": None,
            "availability_display": "Guide to be appointed",
            "introduced_by_agency_id": None,
        }

        duration_minutes, duration_display = _format_duration(
            row.get("start_time"), row.get("end_time")
        )
        price, price_display = _format_price(row.get("price_per_adult"))

        formatted = {
            "tour_id": str(row_id),
            "tour_name": row.get("name", "Unnamed Tour"),
            "description": (row.get("description") or "")[:300],
            "location": row.get("location", "Japan") or "Japan",
            "country": row.get("country", "Japan"),
            "duration_minutes": duration_minutes,
            "duration_display": duration_display,
            "operator": {
                "id": owner_id,
                "name": _display_name(owner) or "Pagoda Travel",
                "email": owner.get("email"),
            },
            "guide": primary,
            "guides": guides,
            "price": price,
            "price_display": price_display,
            "availability": None,
            "availability_display": primary.get("availability_display"),
            "tour_type": row.get("activity_type", "other"),
        }
        if is_excluded_catalogue(formatted):
            continue
        if (filter_guide_id or filter_guide_name) and not tour_matches_guide(
            formatted, guide_id=filter_guide_id, guide_name=filter_guide_name
        ):
            continue
        tours.append(formatted)

    logger.info(
        "Fetched %d published tours (%d with guide assignments, destinations=%s)",
        len(tours), len(assignments_by_tour), destinations,
    )
    return tours
