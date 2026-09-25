"""
POST /sensei/recommend — read-only itinerary builder recommendations.
GET  /sensei/guide-tours — search-by-guide-name (still tours).

Does not write itineraries, jobs, or Transferz bookings.
Advisor must confirm tour + day via the existing marketplace job API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.models.intake import SenseiIntake, SenseiIntakeError, destinations_for_sensei
from app.services.inventory_rules import find_guide_in_inventory
from app.services.sensei_recommend import recommend_from_inventory, tours_for_guide
from app.services.supabase_tours_service_v2 import fetch_live_tours

logger = logging.getLogger(__name__)
router = APIRouter()


class SenseiRecommendRequest(SenseiIntake):
    query: str | None = None
    exclude_tour_ids: list[str] = []
    guide_name: str | None = None
    guide_id: str | None = None
    host_agency_id: str | None = None
    max_cards: int | None = None


def _merge_tours(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for tour in group:
            tour_id = str(tour.get("tour_id"))
            if tour_id in seen:
                continue
            seen.add(tour_id)
            merged.append(tour)
    return merged


@router.post("/sensei/recommend", tags=["Sensei"])
async def sensei_recommend(request: SenseiRecommendRequest):
    """
    Build a day calendar from intake, mark travel days in code, then
    return published tours per stay day (0 on far travel days).

    Primary unit is the tour. Assigned guides are bundled on the card.
    Search-by-guide-name is a second entry: still tours, not a guide list.
    Never invents tours. Never writes jobs. Never books Transferz.
    """
    try:
        dests = destinations_for_sensei(request)
        guide_search = bool(request.guide_id or request.guide_name)
        tours = fetch_live_tours(
            destinations=None if guide_search else dests,
            guide_id=request.guide_id,
            guide_name=request.guide_name,
        )
        if request.query and not guide_search:
            found_id, found_name = find_guide_in_inventory(tours, request.query)
            if found_id or found_name:
                extra = fetch_live_tours(
                    destinations=None,
                    guide_id=found_id,
                    guide_name=None if found_id else found_name,
                )
                tours = _merge_tours(tours, extra)

        result = recommend_from_inventory(
            intake=request,
            tours=tours,
            query=request.query,
            exclude_tour_ids=request.exclude_tour_ids,
            guide_id=request.guide_id,
            guide_name=request.guide_name,
            host_agency_id=request.host_agency_id,
            max_cards=request.max_cards,
        )
    except SenseiIntakeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except EnvironmentError as exc:
        logger.warning("Sensei recommend missing config: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Sensei recommend failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sensei recommend error: {exc}",
        ) from exc

    logger.info(
        "POST /sensei/recommend | days=%d | wrote_jobs=%s | wrote_transfers=%s | mode=%s",
        len(result.get("days") or []),
        result.get("wrote_jobs"),
        result.get("wrote_transfers"),
        result.get("search_mode"),
    )
    return result


@router.get("/sensei/guide-tours", tags=["Sensei"])
async def sensei_guide_tours(
    guide_name: str | None = Query(default=None),
    guide_id: str | None = Query(default=None),
    query: str | None = Query(default=None),
    host_agency_id: str | None = Query(default=None),
):
    """
    Second entry: search by guide name. Returns that guide's published tours.

    Still tours — not a guide-only list pretending to be an itinerary.
    """
    try:
        tours = fetch_live_tours(
            destinations=None,
            guide_id=guide_id,
            guide_name=guide_name or query,
        )
        result = tours_for_guide(
            tours,
            guide_id=guide_id,
            guide_name=guide_name,
            query=query or guide_name,
            host_agency_id=host_agency_id,
        )
    except EnvironmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Sensei guide-tours failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sensei guide-tours error: {exc}",
        ) from exc

    logger.info(
        "GET /sensei/guide-tours | tours=%d | guide=%s",
        len(result.get("tours") or []),
        guide_id or guide_name or query,
    )
    return result
