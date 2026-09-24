"""
Pagoda Travel AI Matching Microservice — Pydantic Schemas
Mirrors the JotForm intake form structure (Japan Intake Form + generalized travel intake).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TravelStyle(str, Enum):
    adventure = "adventure"
    luxury = "luxury"
    cultural = "cultural"
    family = "family"
    honeymoon = "honeymoon"
    wellness = "wellness"
    safari = "safari"
    ski = "ski"
    eco = "eco"
    budget = "budget"
    food = "food"
    history = "history"
    nature = "nature"
    photography = "photography"
    spiritual = "spiritual"
    diving = "diving"


class TopPriority(str, Enum):
    culture_history = "culture_history"
    food = "food"
    nature = "nature"
    luxury = "luxury"
    family_friendly = "family_friendly"
    shopping = "shopping"
    onsen_relaxation = "onsen_relaxation"
    active_outdoors = "active_outdoors"
    other = "other"


class SpecialInterest(str, Enum):
    anime = "anime"
    art = "art"
    architecture = "architecture"
    cycling = "cycling"
    hiking = "hiking"
    ski_snow = "ski_snow"
    car_culture = "car_culture"
    supercar_rental = "supercar_rental"
    helicopter = "helicopter"
    private_yacht = "private_yacht"
    diving = "diving"
    wildlife = "wildlife"
    photography = "photography"
    cooking = "cooking"
    music = "music"
    heritage = "heritage"
    other = "other"


class TransferType(str, Enum):
    arrival = "arrival"
    departure = "departure"
    intercity = "intercity"
    none = "none"


class BudgetRange(str, Enum):
    budget = "budget"           # < $1,500 pp
    moderate = "moderate"       # $1,500–$3,000 pp
    premium = "premium"         # $3,000–$6,000 pp
    luxury = "luxury"           # $6,000–$12,000 pp
    ultra_luxury = "ultra_luxury"  # $12,000+ pp


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class TravelerGroup(BaseModel):
    total_travelers: int = Field(..., ge=1, le=500, description="Total number of travelers")
    adults: int = Field(..., ge=1, description="Number of adults (12+)")
    children_under_12: int = Field(0, ge=0, description="Number of children 11 years or younger")

    @model_validator(mode="after")
    def validate_totals(self) -> "TravelerGroup":
        if self.adults + self.children_under_12 != self.total_travelers:
            # Allow slight mismatch; just ensure adults >= 1 and total makes sense
            if self.total_travelers < self.adults:
                raise ValueError("total_travelers cannot be less than adults count")
        return self


class FlightDetails(BaseModel):
    arrival_airport: Optional[str] = Field(None, description="Arrival airport code or name")
    departure_airport: Optional[str] = Field(None, description="Departure airport code or name")
    arrival_datetime: Optional[datetime] = Field(None, description="Arrival date and time in destination")
    flights_booked: bool = Field(False, description="Whether flights are already confirmed")


class AccommodationInfo(BaseModel):
    already_booked: bool = Field(False, description="Whether accommodation is pre-booked")
    booked_details: Optional[str] = Field(
        None,
        description="List of hotels booked, where and on which dates (free text)"
    )


class BudgetInfo(BaseModel):
    total_budget_usd: Optional[float] = Field(
        None, ge=0, description="Total trip budget in USD"
    )
    per_person_budget_usd: Optional[float] = Field(
        None, ge=0, description="Per-person budget in USD"
    )
    budget_range: Optional[BudgetRange] = Field(
        None, description="Categorical budget band"
    )
    budget_notes: Optional[str] = Field(
        None, description="Additional budget context or constraints"
    )

    @model_validator(mode="after")
    def at_least_one_budget(self) -> "BudgetInfo":
        if all(v is None for v in [
            self.total_budget_usd,
            self.per_person_budget_usd,
            self.budget_range,
        ]):
            raise ValueError(
                "At least one of total_budget_usd, per_person_budget_usd, or budget_range must be provided"
            )
        return self


class AdvisorInfo(BaseModel):
    first_name: str = Field(..., description="Travel advisor first name")
    last_name: str = Field(..., description="Travel advisor last name")
    email: str = Field(..., description="Travel advisor email address")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


# ---------------------------------------------------------------------------
# Primary Intake Form Schema
# ---------------------------------------------------------------------------

class TravelIntakeForm(BaseModel):
    """
    Mirrors the Pagoda Travel JotForm intake form structure.
    Used as the body of POST /match.
    """

    # Advisor
    advisor: AdvisorInfo = Field(..., description="Submitting travel advisor details")

    # Lead traveler
    lead_traveler_name: str = Field(..., description="Name of the primary/lead traveler")

    # Group composition
    group: TravelerGroup = Field(..., description="Traveler group breakdown")

    # Dates
    arrival_date: date = Field(..., description="Client arrival date")
    departure_date: date = Field(..., description="Client departure date")

    # Destinations
    destinations: list[str] = Field(
        ...,
        min_length=1,
        description="Cities or regions to include (e.g. Tokyo, Kyoto, Osaka)"
    )
    countries: Optional[list[str]] = Field(
        None,
        description="Countries to visit (inferred from destinations if not provided)"
    )

    # Prior experience
    visited_before: bool = Field(False, description="Whether client has visited this destination before")

    # Logistics
    transfers_required: list[TransferType] = Field(
        default_factory=list,
        description="Types of transfers required"
    )
    flight_details: Optional[FlightDetails] = Field(None, description="Flight information if available")

    # Accommodation
    accommodation: AccommodationInfo = Field(
        default_factory=AccommodationInfo,
        description="Accommodation booking status"
    )

    # Budget
    budget: BudgetInfo = Field(..., description="Budget information")

    # Preferences
    travel_styles: list[TravelStyle] = Field(
        default_factory=list,
        description="Preferred travel styles"
    )
    top_priorities: list[TopPriority] = Field(
        default_factory=list,
        description="Top priority experiences during trip"
    )
    special_interests: list[SpecialInterest] = Field(
        default_factory=list,
        description="Optional special interests"
    )

    # Age range of group
    age_range_youngest: Optional[int] = Field(None, ge=0, description="Youngest traveler age")
    age_range_oldest: Optional[int] = Field(None, ge=0, description="Oldest traveler age")

    # Special requests
    special_notes: Optional[str] = Field(
        None,
        description=(
            "Dietary needs, mobility concerns, preferred pace, "
            "special celebrations / occasions, things to avoid"
        )
    )

    # Scheduling
    consultation_scheduled: Optional[bool] = Field(
        None,
        description="Whether a Google Meet consultation has been scheduled"
    )

    @field_validator("departure_date")
    @classmethod
    def departure_after_arrival(cls, v: date, info: Any) -> date:
        arrival = info.data.get("arrival_date")
        if arrival and v <= arrival:
            raise ValueError("departure_date must be after arrival_date")
        return v

    @property
    def trip_duration_days(self) -> int:
        return (self.departure_date - self.arrival_date).days


# ---------------------------------------------------------------------------
# JotForm Webhook Payload Schema
# ---------------------------------------------------------------------------

class JotFormRawPayload(BaseModel):
    """
    Raw JotForm webhook payload. JotForm sends form data as a flat
    key-value dict under `rawRequest` or as named fields.
    We accept the raw payload and parse it in the webhook handler.
    """
    formID: Optional[str] = None
    submissionID: Optional[str] = None
    formTitle: Optional[str] = None
    ip: Optional[str] = None
    submissionDate: Optional[str] = None
    rawRequest: Optional[dict[str, Any]] = None

    # Allow any extra fields JotForm may send
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Operator Database Models
# ---------------------------------------------------------------------------

class PriceRange(BaseModel):
    min: float = Field(..., ge=0)
    max: float = Field(..., ge=0)


class GroupSizeRange(BaseModel):
    min: int = Field(..., ge=1)
    max: int = Field(..., ge=1)


class SampleTour(BaseModel):
    name: str
    duration_days: int
    destinations: list[str]
    price_per_person: float
    highlights: list[str]


class Operator(BaseModel):
    id: str
    name: str
    destinations: list[str]
    countries: list[str]
    tour_types: list[str]
    specialties: list[str]
    price_range: PriceRange
    group_size_range: GroupSizeRange
    languages: list[str]
    highlights: list[str]
    sample_tours: list[SampleTour]


# ---------------------------------------------------------------------------
# AI Matching Output Schemas
# ---------------------------------------------------------------------------

class MatchedOperator(BaseModel):
    operator: Operator
    match_score: float = Field(..., ge=0.0, le=1.0, description="Match confidence score 0–1")
    match_reasoning: str = Field(..., description="Human-readable explanation of why this operator was matched")
    suggested_tours: list[SampleTour] = Field(
        default_factory=list,
        description="Tours from this operator most relevant to client preferences"
    )


class DayItinerary(BaseModel):
    day: int
    location: str
    title: str
    description: str
    activities: list[str]
    operator_id: Optional[str] = Field(None, description="Which operator handles this day")
    accommodation_notes: Optional[str] = None
    meal_notes: Optional[str] = None


class PreliminaryItinerary(BaseModel):
    day_by_day: list[DayItinerary]
    estimated_budget: str = Field(..., description="Budget estimate narrative")
    notes: str = Field(..., description="Additional itinerary notes, caveats, and advisor tips")


class MatchGap(BaseModel):
    category: str = Field(..., description="Gap category (e.g. 'Budget', 'Destination', 'Group size')")
    description: str = Field(..., description="What the client wants vs what operators offer")
    recommendation: str = Field(..., description="Suggested resolution for the advisor")


class MatchResponse(BaseModel):
    """Top-level response returned by POST /match"""
    matched_operators: list[MatchedOperator] = Field(
        ..., max_length=3, description="Top 3 matched operators ranked by score"
    )
    preliminary_itinerary: PreliminaryItinerary
    ai_notes: str = Field(..., description="Overall AI assessment, flags, and advisor guidance")
    gaps: list[MatchGap] = Field(
        default_factory=list,
        description="Gaps between client preferences and available operators"
    )
    processing_metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Internal metadata (model used, latency, token counts)"
    )


# ---------------------------------------------------------------------------
# Health Check Schema
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "pagoda-ai-matching"
    version: str = "1.0.0"
    operators_loaded: int = 0
