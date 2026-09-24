"""
POST /sensei/recommend — read-only itinerary builder recommendations.

Does not write itineraries or jobs. Advisor must confirm tour + day
via the existing marketplace job API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.models.intake import SenseiIntake, SenseiIntakeError, destinations_for_sensei
from app.services.sensei_recommend import recommend_from_inventory
from app.services.supabase_tours_service_v2 import fetch_live_tours

logger = logging.getLogger(__name__)
router = APIRouter()


class SenseiRecommendRequest(SenseiIntake):
    query: str | None = None
    exclude_tour_ids: list[str] = []


@router.post("/sensei/recommend", tags=["Sensei"])
async def sensei_recommend(request: SenseiRecommendRequest):
    """
    Build a day calendar from intake, mark travel days in code, then
    return 2–4 published tours per stay day (0 on far travel days).

    Never invents tours. Never writes jobs.
    """
    try:
        tours = fetch_live_tours(destinations=destinations_for_sensei(request))
        result = recommend_from_inventory(
            intake=request,
            tours=tours,
            query=request.query,
            exclude_tour_ids=request.exclude_tour_ids,
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
        "POST /sensei/recommend | days=%d | wrote_jobs=%s | pace=%s",
        len(result.get("days") or []),
        result.get("wrote_jobs"),
        bool(result.get("pace_warning")),
    )
    return result
