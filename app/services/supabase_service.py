"""
Supabase Integration Service
Fetches real tour/operator data from Pagoda Travel's Supabase database
and adapts it to the format the MatchingEngine expects.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a cached Supabase client instance."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment"
        )

    logger.info("Initialising Supabase client for %s", url)
    return create_client(url, key)


def _strip_html(text: str | None) -> str:
    """Remove basic HTML tags from tour descriptions."""
    if not text:
        return ""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    return " ".join(clean.split())


def _adapt_tour_to_operator(tour: dict[str, Any]) -> dict[str, Any]:
    """
    Adapt a single row from `tours_with_details` into the Operator shape
    the MatchingEngine / Pydantic schema expects.

    Real tours table is thin (no pricing, no group size, no specialties),
    so we fill sensible defaults where data is missing and flag it in
    highlights so the AI (and advisor) knows it's incomplete.
    """
    name = tour.get("name", "Unnamed Tour")
    start_location = tour.get("start_location", "Japan")
    description = _strip_html(tour.get("description"))
    tour_type = tour.get("type", "other")
    owner_name = tour.get("owner_name", "Pagoda Travel")

    # Derive a rough destination list from start/end location strings
    destinations = [d.strip() for d in start_location.split(",") if d.strip()]
    if not destinations:
        destinations = ["Japan"]

    return {
        "id": tour.get("id"),
        "name": owner_name,  # operator = the tour owner/advisor
        "destinations": destinations,
        "countries": ["Japan"],
        "tour_types": [tour_type] if tour_type else ["other"],
        "specialties": [name],  # tour name itself as the specialty for now
        "price_range": {"min": 0, "max": 0},  # not available in current schema
        "group_size_range": {"min": 1, "max": 20},  # placeholder, not in schema
        "languages": ["English"],
        "highlights": [
            description[:200] if description else "No description provided",
        ],
        "sample_tours": [
            {
                "name": name,
                "duration_days": 1,  # not available per-tour; defaults to 1
                "destinations": destinations,
                "price_per_person": 0,  # not available in current schema
                "highlights": [description[:150]] if description else [name],
            }
        ],
    }


def fetch_live_operators() -> list[dict[str, Any]]:
    """
    Fetch all published, public tours from Supabase and adapt them
    into the Operator schema used by the MatchingEngine.

    Falls back to raising if Supabase is unreachable — caller should
    catch and fall back to mock data if needed.
    """
    client = get_supabase_client()

    response = (
        client.table("tours_with_details")
        .select("*")
        .eq("status", "published")
        .eq("is_public", True)
        .execute()
    )

    tours = response.data or []
    logger.info("Fetched %d published public tours from Supabase", len(tours))

    operators = [_adapt_tour_to_operator(t) for t in tours]
    return operators
