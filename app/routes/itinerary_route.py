"""
POST /create-itinerary

Runs Sensei AI tour matching (unchanged — same engine as /match-tours) then
writes the result directly into production itineraries + jobs tables.

Import and register in main.py:

    from app.routes.itinerary_route import router as itinerary_router
    app.include_router(itinerary_router)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.routes.tour_match_route import get_tour_engine
from app.services.itinerary_writer import create_itinerary_with_jobs

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Sub-models matching ItineraryIntakeData from lib/itinerary-intake.ts
# ---------------------------------------------------------------------------

class DestinationStay(BaseModel):
    """One stop in the ordered city stay plan. Mirrors TS DestinationStay."""
    city: str
    nights: int = 0
    hotelName: str | None = None


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class CreateItineraryRequest(BaseModel):
    # ------ Itinerary ownership (not in ItineraryIntakeData) ------
    user_id: str = Field(..., description="UUID of the advisor (users.id)")
    profile_id: str = Field(
        ...,
        description=(
            "UUID of the guide/advisor profile (profiles.id — NOT profiles.user_id). "
            "The itineraries table FK references profiles.id."
        ),
    )
    name: str = Field(..., description="Human-readable itinerary name")

    # ------ Trip dates (not in ItineraryIntakeData — kept as top-level) ------
    arrival_date: str = Field(..., description="YYYY-MM-DD")
    departure_date: str = Field(..., description="YYYY-MM-DD")

    # ------ Client / traveler info (ItineraryIntakeData fields) ------
    advisorName: str | None = None
    clientFullName: str | None = None
    clientEmail: str | None = None
    clientWhatsApp: str | None = None
    totalTravelers: int | None = None
    adults: int | None = None
    children: int | None = None
    infants: int | None = None

    # ------ Destination plan ------
    primaryDestination: str | None = None
    importantDestinations: str | None = None
    destinationStays: list[DestinationStay] = Field(default_factory=list)
    openToRecommendations: str | None = None
    additionalDestinations: list[str] = Field(default_factory=list)

    # ------ Preferences ------
    travelerTypes: list[str] = Field(default_factory=list)
    estimatedBudget: str | None = None
    travelStyles: list[str] = Field(default_factory=list)
    tripPace: str | None = None
    activityLevel: str | None = None

    # ------ Destination-specific experience selections ------
    japanExperiences: list[str] = Field(default_factory=list)
    thailandExperiences: list[str] = Field(default_factory=list)
    vietnamExperiences: list[str] = Field(default_factory=list)
    cambodiaExperiences: list[str] = Field(default_factory=list)
    southKoreaExperiences: list[str] = Field(default_factory=list)
    chinaExperiences: list[str] = Field(default_factory=list)
    taiwanExperiences: list[str] = Field(default_factory=list)

    # ------ Trip style / logistics ------
    tourStyles: list[str] = Field(default_factory=list)
    transportationPreferences: list[str] = Field(default_factory=list)
    experiencesToAvoid: list[str] = Field(default_factory=list)

    # ------ Priorities & free text ------
    topPriorities: list[str] = Field(default_factory=list)
    mustHaveExperiences: str = ""
    additionalNotes: str = ""


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/create-itinerary", tags=["Itinerary"])
async def create_itinerary(request: CreateItineraryRequest):
    """
    Run Sensei AI tour matching and write the result into production
    itineraries + jobs tables.

    Steps:
    1. Derive destination list from destinationStays (city names, in order) or
       fall back to primaryDestination + additionalDestinations.
    2. Fetch live published tours filtered by those destinations.
    3. Run TourMatchingEngine with the full structured intake (experience
       selections, top priorities, avoidances) — not flattened to special_notes.
    4. For each suggested tour: verify guide_tour_assignments, fetch real tour
       fields, and insert a jobs row.
    5. Return itinerary_id, job counts, AI notes, and data gaps.

    The /match-tours endpoint is NOT modified — this route calls the same
    engine internally with additional structured intake params.
    """

    # ------------------------------------------------------------------
    # 1. Derive destinations list
    # ------------------------------------------------------------------
    destinations: list[str] = []
    seen: set[str] = set()
    for stay in request.destinationStays:
        city = stay.city.strip()
        if city and city not in seen:
            destinations.append(city)
            seen.add(city)
    if not destinations:
        if request.primaryDestination:
            destinations.append(request.primaryDestination)
            seen.add(request.primaryDestination)
        for d in request.additionalDestinations:
            if d not in seen:
                destinations.append(d)
                seen.add(d)
    if not destinations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "At least one destination is required — provide destinationStays "
                "with a city, or set primaryDestination."
            ),
        )

    logger.info(
        "POST /create-itinerary | user_id=%s | name=%r | destinations=%s | %s to %s",
        request.user_id, request.name, destinations,
        request.arrival_date, request.departure_date,
    )

    # ------------------------------------------------------------------
    # 2. Build structured intake signals for the AI
    # ------------------------------------------------------------------
    # Map destination name → its experience checkbox selections
    destination_experiences: dict[str, list[str]] = {
        k: v for k, v in {
            "Japan": request.japanExperiences,
            "Thailand": request.thailandExperiences,
            "Vietnam": request.vietnamExperiences,
            "Cambodia": request.cambodiaExperiences,
            "South Korea": request.southKoreaExperiences,
            "China": request.chinaExperiences,
            "Taiwan": request.taiwanExperiences,
        }.items() if v
    }

    destination_stays_dicts = [
        {k: v for k, v in s.model_dump().items() if v is not None}
        for s in request.destinationStays
        if s.city.strip()
    ]

    # ------------------------------------------------------------------
    # 3. Run Sensei matching with structured intake
    # ------------------------------------------------------------------
    try:
        engine = get_tour_engine(destinations=destinations)
        match_result = engine.match(
            destinations=destinations,
            arrival_date=request.arrival_date,
            departure_date=request.departure_date,
            travel_styles=request.travelStyles,
            destination_stays=destination_stays_dicts or None,
            destination_experiences=destination_experiences or None,
            top_priorities=request.topPriorities or None,
            must_have_experiences=request.mustHaveExperiences,
            experiences_to_avoid=request.experiencesToAvoid or None,
            tour_styles=request.tourStyles or None,
            additional_notes=request.additionalNotes,
        )
    except Exception as exc:
        logger.exception("Sensei matching failed in /create-itinerary")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sensei tour matching error: {exc}",
        )

    day_by_day: list[dict] = match_result.get("day_by_day", [])

    # ------------------------------------------------------------------
    # 4. Build intake_data matching ItineraryIntakeData shape (camelCase)
    #    so frontend parseIntakeData() can round-trip it correctly.
    # ------------------------------------------------------------------
    intake_data: dict[str, Any] = {
        "advisorName": request.advisorName,
        "clientFullName": request.clientFullName,
        "clientEmail": request.clientEmail,
        "clientWhatsApp": request.clientWhatsApp,
        "totalTravelers": request.totalTravelers,
        "adults": request.adults,
        "children": request.children,
        "infants": request.infants,
        "primaryDestination": request.primaryDestination,
        "importantDestinations": request.importantDestinations,
        "destinationStays": destination_stays_dicts,
        "openToRecommendations": request.openToRecommendations,
        "additionalDestinations": request.additionalDestinations,
        "travelerTypes": request.travelerTypes,
        "estimatedBudget": request.estimatedBudget,
        "travelStyles": request.travelStyles,
        "tripPace": request.tripPace,
        "activityLevel": request.activityLevel,
        "japanExperiences": request.japanExperiences,
        "thailandExperiences": request.thailandExperiences,
        "vietnamExperiences": request.vietnamExperiences,
        "cambodiaExperiences": request.cambodiaExperiences,
        "southKoreaExperiences": request.southKoreaExperiences,
        "chinaExperiences": request.chinaExperiences,
        "taiwanExperiences": request.taiwanExperiences,
        "tourStyles": request.tourStyles,
        "transportationPreferences": request.transportationPreferences,
        "experiencesToAvoid": request.experiencesToAvoid,
        "topPriorities": request.topPriorities,
        "mustHaveExperiences": request.mustHaveExperiences,
        "additionalNotes": request.additionalNotes,
    }
    # Strip None / empty so jsonb stays lean
    intake_data = {k: v for k, v in intake_data.items() if v is not None and v != "" and v != []}

    # itineraries.location: human-readable city list
    location = (
        ", ".join(s.city.strip() for s in request.destinationStays if s.city.strip())
        or request.primaryDestination
        or ", ".join(destinations)
    )

    # ------------------------------------------------------------------
    # 5. Write itinerary + jobs into Supabase
    # ------------------------------------------------------------------
    try:
        write_result = create_itinerary_with_jobs(
            user_id=request.user_id,
            profile_id=request.profile_id,
            name=request.name,
            location=location,
            start_date=request.arrival_date,
            end_date=request.departure_date,
            intake_data=intake_data,
            day_by_day_matches=day_by_day,
            destination_stays=destination_stays_dicts or None,
            transportation_preferences=request.transportationPreferences or None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Itinerary write failed after successful Sensei match")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Itinerary write error: {exc}",
        )

    # ------------------------------------------------------------------
    # 6. Return combined result
    # ------------------------------------------------------------------
    return {
        "itinerary_id": write_result["itinerary_id"],
        "jobs_created": write_result["jobs_created"],
        "jobs_skipped": write_result["jobs_skipped"],
        "skip_reasons": write_result["skip_reasons"],
        "logistics_notes": write_result.get("logistics_notes", []),
        "ai_notes": match_result.get("ai_notes", ""),
        "data_gaps": match_result.get("data_gaps", []),
        "processing_metadata": match_result.get("processing_metadata", {}),
    }
