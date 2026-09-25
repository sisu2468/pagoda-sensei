"""
Async itinerary creation endpoints.

ARCHITECTURE — WHY A TWO-STEP CHAIN:
  FastAPI BackgroundTasks do NOT work on Vercel's Python serverless runtime.
  The function process is terminated as soon as the HTTP response is sent;
  any background task registered with FastAPI is silently dropped.

  Solution: two-step chain.
    1. POST /create-itinerary-async
         Inserts a sensei_jobs row (status='pending'), returns {job_id} as 202.
         Fast — no AI calls, no Supabase tour queries.
    2. POST /create-itinerary-process/{job_id}   <-- CALLED FIRE-AND-FORGET BY CLIENT
         Runs the full Sensei matching + itinerary_writer pipeline.
         The client DOES NOT await the response — it fires it and immediately
         starts polling step 3.  From Vercel's perspective this is a normal
         function invocation running within the plan's max duration.
    3. GET /create-itinerary-status/{job_id}
         Returns status / result / error. Client polls until status='done'|'failed'.

TIMEOUT WARNING (Vercel Hobby plan):
  Hobby functions are killed after 60 seconds.  A 15+ day itinerary can take
  90+ seconds.  On Hobby the worker will be terminated mid-processing, leaving
  the job stuck in 'processing'.  The client should treat a job that has not
  moved past 'processing' for > 90s as timed-out.

  On Vercel Pro (max duration 300s) jobs up to ~4 min complete fine.

  For guaranteed completion on Hobby, use an external worker: Inngest,
  Supabase Edge Function, or QStash pointing at this same process endpoint
  with a longer execution budget.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.models.intake import destinations_for_sensei
from app.routes.itinerary_route import CreateItineraryRequest
from app.routes.tour_match_route import get_tour_engine
from app.services.itinerary_writer import create_itinerary_with_jobs
from app.services.supabase_tours_service_v2 import get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_job(job_id: str, **fields: Any) -> None:
    """Patch a sensei_jobs row. updated_at is handled by the DB trigger."""
    get_supabase_client().table("sensei_jobs").update(fields).eq("id", job_id).execute()


def _fetch_job(job_id: str, *, include_payload: bool = False) -> dict | None:
    cols = "id, status, result, error, created_at, updated_at"
    if include_payload:
        cols += ", request_payload"
    resp = (
        get_supabase_client()
        .table("sensei_jobs")
        .select(cols)
        .eq("id", job_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _derive_destinations(request: CreateItineraryRequest) -> list[str]:
    return destinations_for_sensei(request)


def _build_intake_dict(request: CreateItineraryRequest) -> dict[str, Any]:
    destination_stays_dicts = [
        {k: v for k, v in s.model_dump().items() if v is not None}
        for s in request.destinationStays
        if s.city.strip()
    ]
    raw: dict[str, Any] = {
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
        "interestDestinations": request.interestDestinations,
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
        "flightDetails": request.flightDetails,
        "dietaryNotes": request.dietaryNotes,
        "mobilityNotes": request.mobilityNotes,
        "buildMode": request.buildMode,
        "preferredSuppliers": [
            p.model_dump() for p in request.preferredSuppliers
        ],
    }
    return {k: v for k, v in raw.items() if v is not None and v != "" and v != []}


def _run_itinerary(job_id: str, request: CreateItineraryRequest) -> dict[str, Any]:
    """
    Core processing logic shared between the process endpoint and any future
    worker. Mutates the sensei_jobs row (status → processing → done/failed).
    Returns the final result dict on success; raises on failure.
    """
    destinations = _derive_destinations(request)
    if not destinations:
        msg = (
            "At least one destination is required — provide destinationStays "
            "with a city, or set primaryDestination."
        )
        _update_job(job_id, status="failed", error=msg)
        raise ValueError(msg)

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
        msg = f"Sensei tour matching error: {exc}"
        logger.exception("Sensei matching failed for job %s", job_id)
        _update_job(job_id, status="failed", error=msg)
        raise RuntimeError(msg) from exc

    day_by_day: list[dict] = match_result.get("day_by_day", [])
    intake_data = _build_intake_dict(request)
    location = (
        ", ".join(s.city.strip() for s in request.destinationStays if s.city.strip())
        or request.primaryDestination
        or ", ".join(destinations)
    )

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
    except Exception as exc:
        msg = f"Itinerary write error: {exc}"
        logger.exception("Itinerary write failed for job %s", job_id)
        _update_job(job_id, status="failed", error=msg)
        raise RuntimeError(msg) from exc

    result = {
        "itinerary_id": write_result["itinerary_id"],
        "jobs_created": write_result["jobs_created"],
        "jobs_skipped": write_result["jobs_skipped"],
        "skip_reasons": write_result["skip_reasons"],
        "logistics_notes": write_result.get("logistics_notes", []),
        "ai_notes": match_result.get("ai_notes", ""),
        "data_gaps": match_result.get("data_gaps", []),
        "processing_metadata": match_result.get("processing_metadata", {}),
    }
    _update_job(job_id, status="done", result=result)
    return result


# ---------------------------------------------------------------------------
# POST /create-itinerary-async
# ---------------------------------------------------------------------------

@router.post(
    "/create-itinerary-async",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Itinerary"],
    summary="Enqueue async itinerary creation — returns job_id immediately",
)
async def create_itinerary_async(request: CreateItineraryRequest):
    """
    Accepts the same body as POST /create-itinerary but returns immediately
    with a job_id (202 Accepted) instead of blocking until AI processing
    completes.

    After receiving the job_id the client should:
      1. Call POST /create-itinerary-process/{job_id} **without awaiting the
         response** (fire-and-forget).  It will run the full AI pipeline and
         block for 30–120s before responding.
      2. Poll GET /create-itinerary-status/{job_id} every 3–5s until
         status is 'done' or 'failed'.
    """
    client = get_supabase_client()
    resp = (
        client.table("sensei_jobs")
        .insert({
            "status": "pending",
            "request_payload": request.model_dump(),
        })
        .execute()
    )
    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to insert sensei_jobs row",
        )
    job_id: str = resp.data[0]["id"]
    logger.info("POST /create-itinerary-async | enqueued job_id=%s", job_id)
    return JSONResponse(status_code=202, content={"job_id": job_id})


# ---------------------------------------------------------------------------
# POST /create-itinerary-process/{job_id}
# ---------------------------------------------------------------------------

@router.post(
    "/create-itinerary-process/{job_id}",
    tags=["Itinerary"],
    summary="Execute a pending itinerary job (fire-and-forget from client)",
)
async def create_itinerary_process(job_id: str):
    """
    The actual worker.  Runs the full Sensei matching + write pipeline for
    the job enqueued by POST /create-itinerary-async.

    The client should call this **fire-and-forget** (without awaiting the
    response) immediately after receiving the job_id.  This endpoint blocks
    until processing completes, then returns — but the client doesn't need
    that response; it polls /create-itinerary-status instead.

    Idempotency: if the job is already 'processing', 'done', or 'failed',
    this returns 409 Conflict and does nothing.
    """
    job = _fetch_job(job_id, include_payload=True)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    if job["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is already status={job['status']!r}; ignoring duplicate process call",
        )

    _update_job(job_id, status="processing")
    logger.info("POST /create-itinerary-process/%s | status → processing", job_id)

    raw_payload: dict = job["request_payload"]
    request = CreateItineraryRequest(**raw_payload)

    try:
        result = _run_itinerary(job_id, request)
    except (ValueError, RuntimeError) as exc:
        # _run_itinerary already updated the job row; just surface the error
        raise HTTPException(status_code=502, detail=str(exc))

    logger.info(
        "POST /create-itinerary-process/%s | done | itinerary=%s | jobs=%d",
        job_id, result["itinerary_id"], result["jobs_created"],
    )
    return {"job_id": job_id, "status": "done", **result}


# ---------------------------------------------------------------------------
# GET /create-itinerary-status/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/create-itinerary-status/{job_id}",
    tags=["Itinerary"],
    summary="Poll the status of an async itinerary job",
)
async def create_itinerary_status(job_id: str):
    """
    Returns the current state of a sensei_jobs row.

    Response shapes:
      pending / processing:
        { job_id, status, created_at, updated_at }
      done:
        { job_id, status, result: { itinerary_id, jobs_created, jobs_skipped,
          skip_reasons, ai_notes, data_gaps, processing_metadata },
          created_at, updated_at }
      failed:
        { job_id, status, error: "...", created_at, updated_at }
    """
    job = _fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    out: dict[str, Any] = {
        "job_id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job["status"] == "done" and job.get("result"):
        out["result"] = job["result"]
    elif job["status"] == "failed" and job.get("error"):
        out["error"] = job["error"]
    return out
