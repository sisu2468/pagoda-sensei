"""
Sensei intake contract — mirrors lib/itinerary-intake.ts.

Planning reads this JSON. Do not flatten to a destinations string.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator


class DestinationStay(BaseModel):
    """One overnight stop. Mirrors TS DestinationStay."""

    city: str
    nights: int = Field(0, ge=0)
    hotelName: str | None = None


class PreferredSupplier(BaseModel):
    """Preferred operator/guide for a destination. Store ids, not free text only."""

    destination: str
    operatorId: str | None = None
    guideId: str | None = None


class SenseiIntakeError(ValueError):
    """Advisor-actionable intake error. Do not invent cities to paper over this."""


class SenseiIntake(BaseModel):
    """
    Intake fields Sensei uses to plan. Dates are YYYY-MM-DD.
    At least one destinationStays[].city is required.
    """

    arrival_date: str = Field(..., description="YYYY-MM-DD")
    departure_date: str = Field(..., description="YYYY-MM-DD")

    advisorName: str | None = None
    clientFullName: str | None = None
    clientEmail: str | None = None
    clientWhatsApp: str | None = None
    totalTravelers: int | None = None
    adults: int | None = None
    children: int | None = None
    infants: int | None = None

    primaryDestination: str | None = None
    importantDestinations: str | None = None
    destinationStays: list[DestinationStay] = Field(default_factory=list)
    interestDestinations: list[str] = Field(
        default_factory=list,
        description="Day-trip cities (e.g. Nara while sleeping in Kyoto)",
    )
    openToRecommendations: str | None = None
    additionalDestinations: list[str] = Field(default_factory=list)

    travelerTypes: list[str] = Field(default_factory=list)
    estimatedBudget: str | None = None
    travelStyles: list[str] = Field(default_factory=list)
    tripPace: str | None = None
    activityLevel: str | None = None

    japanExperiences: list[str] = Field(default_factory=list)
    chinaExperiences: list[str] = Field(default_factory=list)
    thailandExperiences: list[str] = Field(default_factory=list)
    vietnamExperiences: list[str] = Field(default_factory=list)
    cambodiaExperiences: list[str] = Field(default_factory=list)
    southKoreaExperiences: list[str] = Field(default_factory=list)
    taiwanExperiences: list[str] = Field(default_factory=list)

    tourStyles: list[str] = Field(default_factory=list)
    transportationPreferences: list[str] = Field(default_factory=list)
    experiencesToAvoid: list[str] = Field(default_factory=list)

    topPriorities: list[str] = Field(default_factory=list)
    mustHaveExperiences: str = ""
    additionalNotes: str = ""
    flightDetails: str | None = Field(
        None,
        description="Free-text flight notes only. Never auto-create Transferz.",
    )
    preferredSuppliers: list[PreferredSupplier] = Field(default_factory=list)

    @field_validator("arrival_date", "departure_date")
    @classmethod
    def _valid_iso_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Dates must be YYYY-MM-DD, got {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _dates_and_stays(self) -> "SenseiIntake":
        arrival = date.fromisoformat(self.arrival_date)
        departure = date.fromisoformat(self.departure_date)
        if departure <= arrival:
            raise ValueError("departure_date must be after arrival_date")

        stays = [s for s in self.destinationStays if s.city.strip()]
        if not stays and not (self.primaryDestination or "").strip():
            raise SenseiIntakeError(
                "At least one overnight city is required. Add destinationStays "
                "with a city (and nights), or set primaryDestination."
            )
        return self

    @property
    def trip_days(self) -> int:
        """Inclusive calendar days (arrival through departure)."""
        start = date.fromisoformat(self.arrival_date)
        end = date.fromisoformat(self.departure_date)
        return (end - start).days + 1

    @property
    def stay_nights_sum(self) -> int:
        return sum(int(s.nights or 0) for s in self.destinationStays if s.city.strip())


def destinations_for_sensei(intake: SenseiIntake) -> list[str]:
    """
    Overnight cities plus day-trip interest cities, order preserved, unique.
    Nara is not dropped when the client sleeps in Kyoto.
    """
    cities: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        city = raw.strip()
        key = city.casefold()
        if city and key not in seen:
            seen.add(key)
            cities.append(city)

    for stay in intake.destinationStays:
        _add(stay.city)
    for city in intake.interestDestinations:
        _add(city)
    _add(intake.primaryDestination)
    for city in intake.additionalDestinations:
        _add(city)
    return cities
