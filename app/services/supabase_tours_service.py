"""
Supabase Integration Service — Milestone 2 (tour-first matching)

Fetches real tours joined with duration data (from the base `tours`
table, since `tours_with_details` doesn't expose it), and prepares
them for tour-level matching instead of operator-level matching.

Price and availability are NOT yet in the database — these fields
come through as null and the API layer fills in placeholder display
text ("Contact operator for pricing / availability").
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment"
        )

    logger.info("Initialising Supabase client for %s", url)
    return create_client(url, key)


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    return " ".join(clean.split())


def _format_duration(minutes: int | None) -> str:
    """Convert raw minutes into a human-readable duration string."""
    if not minutes or minutes <= 0:
        return "Duration not specified"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    if hours == int(hours):
        return f"{int(hours)} hour{'s' if hours != 1 else ''}"
    return f"{hours:.1f} hours"


def fetch_live_tours() -> list[dict[str, Any]]:
    """
    Fetch all published, public tours joined with their duration
    (from the base `tours` table) and owner/operator info (from
    `tours_with_details`).

    Returns a flat list of tour dicts ready for the matching engine —
    each tour is a standalone unit bundling its own operator info,
    NOT grouped by operator.
    """
    client = get_supabase_client()

    details_response = (
        client.table("tours_with_details")
        .select("*")
        .eq("status", "published")
        .eq("is_public", True)
        .execute()
    )
    details_rows = details_response.data or []

    tour_ids = [row["id"] for row in details_rows if row.get("id")]
    duration_by_id: dict[str, int] = {}
    if tour_ids:
        duration_response = (
            client.table("tours")
            .select("id, duration")
            .in_("id", tour_ids)
            .execute()
        )
        for row in duration_response.data or []:
            if row.get("duration") is not None:
                duration_by_id[row["id"]] = row["duration"]

    tours: list[dict[str, Any]] = []
    for row in details_rows:
        tour_id = row.get("id")
        duration_minutes = duration_by_id.get(tour_id)

        start_location = row.get("start_location", "Japan") or "Japan"
        locations = [loc.strip() for loc in start_location.split(",") if loc.strip()]
        primary_location = locations[0] if locations else "Japan"

        tours.append({
            "tour_id": tour_id,
            "tour_name": row.get("name", "Unnamed Tour"),
            "description": _strip_html(row.get("description"))[:300],
            "location": primary_location,
            "all_locations": locations,
            "duration_minutes": duration_minutes,
            "duration_display": _format_duration(duration_minutes),
            "operator": {
                "id": row.get("owner"),
                "name": row.get("owner_name", "Pagoda Travel"),
                "email": row.get("owner_email"),
            },
            "price": None,
            "price_display": "Contact operator for pricing",
            "availability": None,
            "availability_display": "Contact operator to confirm availability",
            "tour_type": row.get("type", "other"),
        })

    logger.info("Fetched %d published public tours (tour-first) from Supabase", len(tours))
    return tours
