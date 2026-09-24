"""
Updated schemas for Milestone 2 — Tour-first matching.

Primary recommendation unit is now the TOUR, not the operator.
Each tour bundles: the tour itself, its operator/guide, duration,
location, and (when available) price and availability.

Day-by-day results return 2-3 ranked tour suggestions per day.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class OperatorInfo(BaseModel):
    """Minimal operator/guide info bundled with each tour."""
    id: Optional[str] = None
    name: str = Field(..., description="Operator or guide name/business")
    email: Optional[str] = None


class TourRecommendation(BaseModel):
    """
    A single tour recommendation — the primary unit Sensei returns.
    Bundles tour + operator + price + availability + duration + location
    in one object, per client requirement (no separate reveal step).
    """
    tour_id: str
    tour_name: str
    description: str = Field(default="", description="Short tour description")
    location: str = Field(..., description="Where this tour takes place")
    duration_minutes: Optional[int] = Field(
        None, description="Tour duration in minutes, from real data when available"
    )
    duration_display: str = Field(
        default="Duration not specified",
        description="Human-readable duration, e.g. '2 hours' or 'Not specified'"
    )
    operator: OperatorInfo
    price: Optional[float] = Field(
        None, description="Price per person in USD, null if not yet in database"
    )
    price_display: str = Field(
        default="Contact operator for pricing",
        description="Human-readable price, or a placeholder when price data is missing"
    )
    availability: Optional[str] = Field(
        None, description="Availability status, null if not yet tracked in database"
    )
    availability_display: str = Field(
        default="Contact operator to confirm availability",
        description="Human-readable availability, or a placeholder when missing"
    )
    match_score: float = Field(..., ge=0.0, le=1.0)
    match_reasoning: str = Field(
        ..., description="Why this tour fits this day — includes location-proximity reasoning"
    )


class DayRecommendations(BaseModel):
    """Tour suggestions for a single day of the trip."""
    day: int
    date: Optional[str] = None
    location: str = Field(..., description="Primary location for this day of the trip")
    suggested_tours: list[TourRecommendation] = Field(
        ..., max_length=3, description="2-3 ranked tour options for this day"
    )


class SenseiMatchResponse(BaseModel):
    """Top-level response — tour-first structure for Milestone 2."""
    day_by_day: list[DayRecommendations]
    ai_notes: str = Field(..., description="Overall assessment and advisor guidance")
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Flags for missing data, e.g. price/availability not yet in database"
    )
