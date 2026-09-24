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

logger = logging.getLogger(__name__)

MOCK_TOURS_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_tours.json"


def use_mock_tours() -> bool:
    flag = (os.getenv("USE_MOCK_TOURS") or "").strip().lower()
    url = (os.getenv("SUPABASE_URL") or "").strip().lower()
    return flag in {"1", "true", "yes"} or "mock.supabase.local" in url


def fetch_mock_tours(destinations: list[str] | None = None) -> list[dict[str, Any]]:
    """Local published-tour inventory. No network. Guide assignment optional."""
    if not MOCK_TOURS_PATH.exists():
        raise FileNotFoundError(f"Mock tour file not found at {MOCK_TOURS_PATH}")
    with MOCK_TOURS_PATH.open("r", encoding="utf-8") as fh:
        tours: list[dict[str, Any]] = json.load(fh)

    if not destinations:
        logger.info("Loaded %d MOCK published tours (unfiltered)", len(tours))
        return tours

    tokens = [d.strip().casefold() for d in destinations if d.strip()]
    matched: list[dict[str, Any]] = []
    for tour in tours:
        loc = (tour.get("location") or "").casefold()
        country = (tour.get("country") or "").casefold()
        if any(token in loc or token in country or loc in token for token in tokens):
            matched.append(tour)

    logger.info(
        "Loaded %d MOCK published tours for destinations=%s",
        len(matched), destinations,
    )
    return matched


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


def fetch_live_tours(destinations: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Fetch published tours. Published is the only eligibility gate.

    Guide assignment is a label ("appointed" vs "to be appointed"), never a filter.
    If a destination has zero published tours, return none for that city —
    do not fall back to another region's catalogue.
    """
    if use_mock_tours():
        return fetch_mock_tours(destinations)

    MAX_TOURS = 60
    client = get_supabase_client()

    _TRANSPORT_ACTIVITY_TYPES = {
        "Shinkansen Tickets (bullet train)",
        "Pagoda Support",
        "Airport transfers - Custom",
        "Transfers",
    }

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
            or_conditions.append(f"country.ilike.%{dest_clean}%")
        if or_conditions:
            query = query.or_(",".join(or_conditions))

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

    assignment_by_tour: dict[int, dict] = {}
    if tour_ids:
        assignments_response = (
            client.table("guide_tour_assignments")
            .select("tour_id, guide_id, operator_id")
            .in_("tour_id", tour_ids)
            .execute()
        )
        for row in assignments_response.data or []:
            if row["tour_id"] not in assignment_by_tour:
                assignment_by_tour[row["tour_id"]] = row

    unassigned = [r["id"] for r in tour_rows if r.get("id") not in assignment_by_tour]
    if unassigned:
        logger.info(
            "Keeping %d published tour(s) with no guide allocated: %s",
            len(unassigned), unassigned,
        )

    owner_ids = [r.get("user_id") for r in tour_rows if r.get("user_id")]
    guide_ids = [a["guide_id"] for a in assignment_by_tour.values() if a.get("guide_id")]
    operator_ids = [a["operator_id"] for a in assignment_by_tour.values() if a.get("operator_id")]
    person_ids = list({*owner_ids, *guide_ids, *operator_ids})

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
    if guide_ids:
        profiles_response = (
            client.table("profiles")
            .select("user_id, specialties, daily_rate_amount, daily_rate_currency, "
                     "guide_availability_calendar, city")
            .in_("user_id", list(set(guide_ids)))
            .execute()
        )
        for row in profiles_response.data or []:
            profile_by_user_id[row["user_id"]] = row

    def _display_name(user: dict) -> str:
        first = user.get("first_name", "")
        last = user.get("last_name", "")
        return f"{first} {last}".strip()

    tours: list[dict[str, Any]] = []
    for row in tour_rows:
        tour_id = row.get("id")
        assignment = assignment_by_tour.get(tour_id, {})
        owner_id = row.get("user_id") or assignment.get("operator_id")
        guide_id = assignment.get("guide_id")
        owner = user_by_id.get(owner_id, {}) if owner_id else {}
        guide_user = user_by_id.get(guide_id, {}) if guide_id else {}
        profile = profile_by_user_id.get(guide_id, {}) if guide_id else {}

        operator_name = _display_name(owner) or "Pagoda Travel"
        guide_name = _display_name(guide_user) or None
        guide_status = "appointed" if guide_id else "to_be_appointed"

        duration_minutes, duration_display = _format_duration(
            row.get("start_time"), row.get("end_time")
        )
        price, price_display = _format_price(row.get("price_per_adult"))

        availability_calendar = profile.get("guide_availability_calendar")
        if guide_status == "to_be_appointed":
            availability_display = "Guide to be appointed"
        elif not availability_calendar:
            availability_display = "Availability on request"
        else:
            availability_display = "See guide calendar for availability"

        tours.append({
            "tour_id": str(tour_id),
            "tour_name": row.get("name", "Unnamed Tour"),
            "description": (row.get("description") or "")[:300],
            "location": row.get("location", "Japan") or "Japan",
            "country": row.get("country", "Japan"),
            "duration_minutes": duration_minutes,
            "duration_display": duration_display,
            "operator": {
                "id": owner_id,
                "name": operator_name,
                "email": owner.get("email"),
            },
            "guide": {
                "id": guide_id,
                "name": guide_name,
                "status": guide_status,
            },
            "price": price,
            "price_display": price_display,
            "availability": None,
            "availability_display": availability_display,
            "tour_type": row.get("activity_type", "other"),
        })

    logger.info(
        "Fetched %d published tours (%d with guide assignments, destinations=%s)",
        len(tours), len(assignment_by_tour), destinations,
    )
    return tours
