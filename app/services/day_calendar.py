"""
Expand intake overnight stays into a dated day calendar. No AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models.intake import SenseiIntake, SenseiIntakeError
from app.services.travel_days import (
    LOCAL_MAX_MINUTES,
    PACE_WARNING,
    TravelBand,
    band_for_hop,
    is_local_pair,
)


@dataclass
class OvernightSlot:
    city: str
    hotel_name: str | None = None


@dataclass
class CalendarDay:
    day: int
    date: str
    overnight_city: str
    next_overnight_city: str | None
    band: TravelBand
    day_kind: str
    max_tour_minutes: int
    day_trip_cities: list[str] = field(default_factory=list)
    pace_warning: str | None = None
    hotel_name: str | None = None

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "date": self.date,
            "overnight_city": self.overnight_city,
            "next_overnight_city": self.next_overnight_city,
            "hotel_name": self.hotel_name,
            "band": self.band.name,
            "day_kind": self.day_kind,
            "max_tour_minutes": self.max_tour_minutes,
            "day_trip_cities": self.day_trip_cities,
            "pace_warning": self.pace_warning,
        }


def _overnight_sequence(intake: SenseiIntake) -> list[OvernightSlot]:
    """One overnight slot per calendar day from destinationStays nights."""
    sequence: list[OvernightSlot] = []
    for stay in intake.destinationStays:
        city = stay.city.strip()
        if not city:
            continue
        nights = int(stay.nights or 0)
        if nights < 1:
            raise SenseiIntakeError(
                f"Overnight stay for {city!r} needs nights >= 1. "
                "Fix the intake form; Sensei will not invent a night count."
            )
        hotel = (stay.hotelName or "").strip() or None
        sequence.extend([OvernightSlot(city, hotel)] * nights)
    return sequence


def build_day_calendar(intake: SenseiIntake) -> list[CalendarDay]:
    """
    Map each inclusive trip day to an overnight city.

    sum(nights) must equal inclusive trip days. Do not pad with the last city
    or round-robin leftover days across regions.
    """
    trip_days = intake.trip_days
    sequence = _overnight_sequence(intake)

    if not sequence:
        primary = (intake.primaryDestination or "").strip()
        if not primary:
            raise SenseiIntakeError(
                "At least one overnight city is required on destinationStays."
            )
        raise SenseiIntakeError(
            f"destinationStays for {primary!r} has no nights. "
            "Set nights for each overnight city so they cover the trip dates."
        )

    if len(sequence) != trip_days:
        raise SenseiIntakeError(
            f"Overnight nights ({len(sequence)}) do not match trip length "
            f"({trip_days} days, {intake.arrival_date} to {intake.departure_date}). "
            "Fix destinationStays nights on the intake form. "
            "Sensei will not invent or drop cities to make the calendar fit."
        )

    start = date.fromisoformat(intake.arrival_date)
    interest = [c.strip() for c in intake.interestDestinations if c.strip()]
    overnight_keys = {
        slot.city.strip().casefold() for slot in sequence if slot.city.strip()
    }

    days: list[CalendarDay] = []
    for i, slot in enumerate(sequence):
        city = slot.city
        next_city = sequence[i + 1].city if i + 1 < len(sequence) else None
        if next_city:
            band = band_for_hop(city, next_city)
        else:
            band = TravelBand("local", "stay", LOCAL_MAX_MINUTES)

        day_trips: list[str] = []
        if band.day_kind == "stay":
            for extra in interest:
                key = extra.casefold()
                if key == city.casefold():
                    continue
                # Osaka as a later overnight is not a Kyoto day trip. Nara is.
                if key in overnight_keys:
                    continue
                if is_local_pair(city, extra):
                    day_trips.append(extra)

        days.append(
            CalendarDay(
                day=i + 1,
                date=(start + timedelta(days=i)).isoformat(),
                overnight_city=city,
                next_overnight_city=next_city,
                band=band,
                day_kind=band.day_kind,
                max_tour_minutes=band.max_tour_minutes,
                day_trip_cities=day_trips,
                hotel_name=slot.hotel_name,
            )
        )

    consecutive_far = 0
    max_consecutive_far = 0
    for d in days:
        if d.day_kind == "travel_far":
            consecutive_far += 1
            max_consecutive_far = max(max_consecutive_far, consecutive_far)
        else:
            consecutive_far = 0

    if max_consecutive_far >= 2:
        for d in days:
            if d.day_kind == "travel_far":
                d.pace_warning = PACE_WARNING

    return days
