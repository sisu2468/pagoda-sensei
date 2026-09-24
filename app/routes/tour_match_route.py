"""
New /match-tours endpoint — Milestone 2 tour-first matching.

Import and include this router in main.py:

    from app.routes.tour_match_route import router as tour_match_router
    app.include_router(tour_match_router)

Kept as a separate router so the existing /match endpoint (operator-first,
Milestone 1) remains untouched and working.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.supabase_tours_service_v2 import fetch_live_tours
from app.services.tour_matching_engine import TourMatchingEngine

logger = logging.getLogger(__name__)
router = APIRouter()


class TourMatchRequest(BaseModel):
    destinations: list[str] = Field(..., min_length=1)
    arrival_date: str
    departure_date: str
    travel_styles: list[str] = Field(default_factory=list)
    special_notes: str = ""


def get_tour_engine(destinations: list[str]) -> TourMatchingEngine:
    """
    Build a fresh engine per request, with tours filtered to the
    requested destinations. Not cached — different requests have
    different destinations, so a single cached engine would serve
    stale/wrong-city tours after the first call.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set")
    tours = fetch_live_tours(destinations=destinations)
    return TourMatchingEngine(api_key=api_key, tours=tours)


@router.post("/match-tours", tags=["Matching"])
async def match_tours(request: TourMatchRequest):
    """
    Milestone 2 endpoint — tour-first, day-by-day matching.

    For each day of the trip, returns 2-3 ranked tour recommendations,
    each bundling tour + operator + duration + location + price/availability.
    Tours are pre-filtered by destination at the database level to stay
    well under the Claude API context/rate limits.
    """
    logger.info(
        "POST /match-tours | destinations=%s | %s to %s",
        request.destinations, request.arrival_date, request.departure_date,
    )

    try:
        engine = get_tour_engine(destinations=request.destinations)
        result = engine.match(
            destinations=request.destinations,
            arrival_date=request.arrival_date,
            departure_date=request.departure_date,
            travel_styles=request.travel_styles,
            special_notes=request.special_notes,
        )
    except Exception as exc:
        logger.exception("Tour matching failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sensei tour matching error: {exc}",
        )

    return result
