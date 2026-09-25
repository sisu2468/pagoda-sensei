"""
Pagoda Travel AI Matching Microservice
FastAPI application entry point.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from app.models.schemas import (
    HealthResponse,
    JotFormRawPayload,
    MatchResponse,
    TravelIntakeForm,
)
from app.routes.itinerary_async_route import router as itinerary_async_router
from app.routes.itinerary_route import router as itinerary_router
from app.routes.sensei_recommend_route import router as sensei_recommend_router
from app.routes.tour_match_route import router as tour_match_router
from app.services.matching_engine import MatchingEngine
from app.services.webhook_parser import parse_jotform_payload
from app.utils.dependencies import get_matching_engine, get_operator_count

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("pagoda.api")


# ---------------------------------------------------------------------------
# Lifespan: warm up the engine on startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up dependencies on startup so first request is fast."""
    logger.info("Pagoda AI Matching Service starting up…")
    try:
        engine = get_matching_engine()
        logger.info(
            "Engine ready — %d operators loaded",
            len(engine.operators),
        )
    except EnvironmentError as exc:
        logger.warning("Engine not initialised at startup: %s", exc)
    yield
    logger.info("Pagoda AI Matching Service shutting down")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pagoda Travel AI Matching API",
    description=(
        "AI-powered operator matching and itinerary generation microservice "
        "for the Pagoda Travel marketplace. Accepts intake form data, matches "
        "against the operator database using Claude claude-sonnet-4-6, and returns "
        "ranked operator recommendations with a preliminary itinerary."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(tour_match_router)
app.include_router(itinerary_router)
app.include_router(itinerary_async_router)
app.include_router(sensei_recommend_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Process-Time"] = f"{elapsed:.3f}s"
    logger.info("%s %s → %d (%.3fs)", request.method, request.url.path, response.status_code, elapsed)
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    tags=["System"],
)
async def health_check() -> HealthResponse:
    """
    Returns service health status and number of operators loaded.
    Used by load balancers and monitoring systems.
    """
    return HealthResponse(
        status="ok",
        service="pagoda-ai-matching",
        version="1.0.0",
        operators_loaded=get_operator_count(),
    )


@app.post(
    "/match",
    response_model=MatchResponse,
    summary="Match intake form to operators and generate itinerary",
    tags=["Matching"],
    status_code=status.HTTP_200_OK,
)
async def match_intake(
    intake: TravelIntakeForm,
    engine: MatchingEngine = Depends(get_matching_engine),
) -> MatchResponse:
    """
    **Primary matching endpoint.**

    Accepts a structured intake form (mirroring the Pagoda JotForm fields),
    runs the AI matching engine against the operator database, and returns:

    - Top 3 matched operators with match scores and reasoning
    - Suggested tours from each matched operator
    - Day-by-day preliminary itinerary
    - Estimated budget narrative
    - Gap analysis (where client preferences outpace available operators)
    - AI notes for the travel advisor

    **Processing time:** ~8–15 seconds (Claude API call).
    """
    logger.info(
        "POST /match | advisor=%s | lead_traveler=%s | destinations=%s | group=%d",
        intake.advisor.email,
        intake.lead_traveler_name,
        intake.destinations,
        intake.group.total_travelers,
    )

    try:
        result = engine.match(intake)
    except Exception as exc:
        logger.exception("Matching engine failed for lead=%s", intake.lead_traveler_name)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI matching engine error: {exc}",
        )

    return result


@app.post(
    "/webhook",
    response_model=MatchResponse,
    summary="Receive JotForm webhook and run matching",
    tags=["Matching"],
    status_code=status.HTTP_200_OK,
)
async def jotform_webhook(
    request: Request,
    engine: MatchingEngine = Depends(get_matching_engine),
) -> MatchResponse:
    """
    **JotForm webhook endpoint.**

    Configure this URL in your JotForm Integrations → Webhooks settings.
    JotForm will POST form submission data here automatically when a new
    intake form is submitted.

    The handler:
    1. Parses the raw JotForm payload (multipart/form-data or JSON)
    2. Maps fields to the internal TravelIntakeForm schema
    3. Runs the same AI matching pipeline as POST /match
    4. Returns structured MatchResponse JSON

    **Setup in JotForm:**
    - Form → Settings → Integrations → Webhooks
    - Set URL to: `https://your-domain.com/webhook`
    - Enable: Send on submit
    """
    content_type = request.headers.get("content-type", "")

    # Parse body depending on content type
    raw_data: dict[str, Any] = {}

    if "application/json" in content_type:
        try:
            raw_data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON in webhook payload",
            )
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        for key, value in form_data.items():
            # JotForm sometimes sends nested JSON in `rawRequest` field
            if key == "rawRequest":
                try:
                    raw_data.update(json.loads(str(value)))
                except json.JSONDecodeError:
                    raw_data[key] = str(value)
            else:
                raw_data[key] = str(value)
    else:
        # Attempt JSON parse as fallback
        try:
            body = await request.body()
            raw_data = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported content type: {content_type}",
            )

    logger.info(
        "POST /webhook | submission_id=%s | form_id=%s | keys=%s",
        raw_data.get("submissionID", "unknown"),
        raw_data.get("formID", "unknown"),
        list(raw_data.keys())[:10],
    )

    # Parse into intake form
    try:
        intake = parse_jotform_payload(raw_data)
    except Exception as exc:
        logger.exception("Webhook payload parsing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse JotForm payload: {exc}",
        )

    # Run matching
    try:
        result = engine.match(intake)
    except Exception as exc:
        logger.exception("Matching engine failed on webhook submission")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI matching engine error: {exc}",
        )

    return result


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Pagoda Travel AI Matching API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "sensei_recommend": "/sensei/recommend",
        "sensei_guide_tours": "/sensei/guide-tours",
    }
