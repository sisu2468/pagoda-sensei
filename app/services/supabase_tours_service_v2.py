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

import logging
import os
from datetime import datetime, time
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment"
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
    Fetch published tours joined with their assigned guide/operator
    (via guide_tour_assignments -> users -> profiles).

    When `destinations` is given, filters at the DB level to tours whose
    location or country matches (case-insensitive, partial) any of the
    requested destinations. This keeps payload size well under the
    Claude API context/rate limits — sending all 299+ tours in one
    request can exceed them. Falls back to unfiltered (capped) fetch if no
    destinations are given or the filter matches nothing.

    Result is capped at MAX_TOURS, distributed evenly across matched
    destinations where possible so no single city crowds out the rest.
    """
    MAX_TOURS = 60
    client = get_supabase_client()

    # Activity types handled deterministically by itinerary_writer transfer logic —
    # exclude them from the AI inventory so they never appear as day activities.
    _TRANSPORT_ACTIVITY_TYPES = {
        "Shinkansen Tickets (bullet train)",
        "Pagoda Support",
        "Airport transfers - Custom",
        "Transfers",
    }

    # 1. Fetch published tours, optionally filtered by destination
    query = (
        client.table("tour")
        .select("id, name, location, country, description, start_time, end_time, "
                "price_per_adult, price_per_child, status, activity_type")
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

    # Fallback: destination filter matched nothing (e.g. spelling mismatch) —
    # rather than return zero tours, fetch unfiltered and cap.
    if destinations and not tour_rows:
        logger.warning(
            "Destination filter matched 0 tours for %s — falling back to unfiltered fetch",
            destinations,
        )
        tours_response = (
            client.table("tour")
            .select("id, name, location, country, description, start_time, end_time, "
                    "price_per_adult, price_per_child, status, activity_type")
            .eq("status", "published")
            .not_.in_("activity_type", list(_TRANSPORT_ACTIVITY_TYPES))
            .execute()
        )
        tour_rows = tours_response.data or []

    # Cap total tours, distributing roughly evenly across locations present
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

    # 2. Fetch guide assignments for these tours
    assignment_by_tour: dict[int, dict] = {}
    if tour_ids:
        assignments_response = (
            client.table("guide_tour_assignments")
            .select("tour_id, guide_id, operator_id")
            .in_("tour_id", tour_ids)
            .execute()
        )
        for row in assignments_response.data or []:
            # Keep first assignment per tour (a tour could have multiple guides)
            if row["tour_id"] not in assignment_by_tour:
                assignment_by_tour[row["tour_id"]] = row

    # Drop tours with no assignment — the AI would suggest them but the writer
    # unconditionally skips them, causing empty days. Filter here so the AI
    # only ever sees bookable tours.
    unassigned = [r["id"] for r in tour_rows if r.get("id") not in assignment_by_tour]
    if unassigned:
        logger.info(
            "Filtered out %d tour(s) with no guide_tour_assignments: %s",
            len(unassigned), unassigned,
        )
    tour_rows = [r for r in tour_rows if r.get("id") in assignment_by_tour]

    guide_ids = list({a["guide_id"] for a in assignment_by_tour.values() if a.get("guide_id")})

    # 3. Fetch guide names from users
    user_by_id: dict[str, dict] = {}
    if guide_ids:
        users_response = (
            client.table("users")
            .select("id, first_name, last_name, email")
            .in_("id", guide_ids)
            .execute()
        )
        for row in users_response.data or []:
            user_by_id[row["id"]] = row

    # 4. Fetch guide profiles (availability, specialties, daily rate)
    profile_by_user_id: dict[str, dict] = {}
    if guide_ids:
        profiles_response = (
            client.table("profiles")
            .select("user_id, specialties, daily_rate_amount, daily_rate_currency, "
                     "guide_availability_calendar, city")
            .in_("user_id", guide_ids)
            .execute()
        )
        for row in profiles_response.data or []:
            profile_by_user_id[row["user_id"]] = row

    # 5. Assemble tour-first records
    tours: list[dict[str, Any]] = []
    for row in tour_rows:
        tour_id = row.get("id")
        assignment = assignment_by_tour.get(tour_id, {})
        guide_id = assignment.get("guide_id")
        user = user_by_id.get(guide_id, {}) if guide_id else {}
        profile = profile_by_user_id.get(guide_id, {}) if guide_id else {}

        first = user.get("first_name", "")
        last = user.get("last_name", "")
        operator_name = f"{first} {last}".strip() or "Pagoda Travel"

        duration_minutes, duration_display = _format_duration(
            row.get("start_time"), row.get("end_time")
        )
        price, price_display = _format_price(row.get("price_per_adult"))

        availability_calendar = profile.get("guide_availability_calendar")
        availability_display = (
            "Availability on request"
            if not availability_calendar
            else "See guide calendar for availability"
        )

        tours.append({
            "tour_id": str(tour_id),
            "tour_name": row.get("name", "Unnamed Tour"),
            "description": (row.get("description") or "")[:300],
            "location": row.get("location", "Japan") or "Japan",
            "country": row.get("country", "Japan"),
            "duration_minutes": duration_minutes,
            "duration_display": duration_display,
            "operator": {
                "id": guide_id,
                "name": operator_name,
                "email": user.get("email"),
            },
            "price": price,
            "price_display": price_display,
            "availability": None,
            "availability_display": availability_display,
            "tour_type": row.get("activity_type", "other"),
        })

    logger.info(
        "Fetched %d published tours from Pagoda Travel Pro (%d with guide assignments, destinations=%s)",
        len(tours), len(assignment_by_tour), destinations,
    )
    return tours
