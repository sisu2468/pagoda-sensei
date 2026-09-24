"""
JotForm Webhook Payload Parser
Maps the raw JotForm webhook payload to a TravelIntakeForm Pydantic model.

JotForm sends form submissions as multipart/form-data or JSON with fields
keyed by their internal label names. This module normalises that payload.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.models.schemas import (
    AccommodationInfo,
    AdvisorInfo,
    BudgetInfo,
    FlightDetails,
    SpecialInterest,
    TopPriority,
    TransferType,
    TravelIntakeForm,
    TravelStyle,
    TravelerGroup,
)

logger = logging.getLogger(__name__)


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float | None = None) -> float | None:
    try:
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        return float(cleaned)
    except (TypeError, ValueError):
        return default


def _safe_date(val: Any) -> date | None:
    """Try several common date string formats."""
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()

    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue

    # JotForm may send structured date dicts: {"month": "3", "day": "15", "year": "2025"}
    if isinstance(val, dict):
        try:
            m = _safe_int(val.get("month", val.get("Month", 1)))
            d = _safe_int(val.get("day", val.get("Day", 1)))
            y = _safe_int(val.get("year", val.get("Year", 2025)))
            return date(y, m, d)
        except (TypeError, ValueError):
            pass

    logger.warning("Could not parse date value: %r", val)
    return None


def _parse_checkboxes(raw: Any) -> list[str]:
    """
    JotForm checkbox/multi-select fields can be:
    - a comma-separated string: "Tokyo, Kyoto, Osaka"
    - a list: ["Tokyo", "Kyoto"]
    - a dict of checked items: {"Tokyo": "1", "Kyoto": "0"}
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if v]
    if isinstance(raw, dict):
        return [k for k, v in raw.items() if str(v) in ("1", "true", "True")]
    # String fallback
    return [s.strip() for s in str(raw).split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Field normalisation maps
# ---------------------------------------------------------------------------

# Maps JotForm field labels (lowercase) to their canonical function
# JotForm labels vary — we do fuzzy matching via `contains` logic below.

_STYLE_KEYWORD_MAP: dict[str, TravelStyle] = {
    "adventure": TravelStyle.adventure,
    "luxury": TravelStyle.luxury,
    "cultural": TravelStyle.cultural,
    "culture": TravelStyle.cultural,
    "family": TravelStyle.family,
    "honeymoon": TravelStyle.honeymoon,
    "wellness": TravelStyle.wellness,
    "safari": TravelStyle.safari,
    "ski": TravelStyle.ski,
    "eco": TravelStyle.eco,
    "budget": TravelStyle.budget,
    "food": TravelStyle.food,
    "history": TravelStyle.history,
    "nature": TravelStyle.nature,
    "photography": TravelStyle.photography,
    "spiritual": TravelStyle.spiritual,
    "diving": TravelStyle.diving,
}

_PRIORITY_MAP: dict[str, TopPriority] = {
    "culture and history": TopPriority.culture_history,
    "culture": TopPriority.culture_history,
    "history": TopPriority.culture_history,
    "food": TopPriority.food,
    "nature": TopPriority.nature,
    "luxury": TopPriority.luxury,
    "family": TopPriority.family_friendly,
    "shopping": TopPriority.shopping,
    "onsen": TopPriority.onsen_relaxation,
    "relaxation": TopPriority.onsen_relaxation,
    "active": TopPriority.active_outdoors,
    "outdoors": TopPriority.active_outdoors,
    "other": TopPriority.other,
}

_INTEREST_MAP: dict[str, SpecialInterest] = {
    "anime": SpecialInterest.anime,
    "art": SpecialInterest.art,
    "architecture": SpecialInterest.architecture,
    "cycling": SpecialInterest.cycling,
    "hiking": SpecialInterest.hiking,
    "ski": SpecialInterest.ski_snow,
    "snow": SpecialInterest.ski_snow,
    "car culture": SpecialInterest.car_culture,
    "racing": SpecialInterest.car_culture,
    "supercar": SpecialInterest.supercar_rental,
    "helicopter": SpecialInterest.helicopter,
    "yacht": SpecialInterest.private_yacht,
    "diving": SpecialInterest.diving,
    "wildlife": SpecialInterest.wildlife,
    "photography": SpecialInterest.photography,
    "cooking": SpecialInterest.cooking,
    "music": SpecialInterest.music,
    "heritage": SpecialInterest.heritage,
    "other": SpecialInterest.other,
}

_TRANSFER_MAP: dict[str, TransferType] = {
    "arrival": TransferType.arrival,
    "departure": TransferType.departure,
    "intercity": TransferType.intercity,
    "none": TransferType.none,
}


def _map_keywords(raw_values: list[str], mapping: dict[str, Any]) -> list[Any]:
    results = []
    for v in raw_values:
        v_lower = v.lower().strip()
        matched = None
        # Exact match first
        if v_lower in mapping:
            matched = mapping[v_lower]
        else:
            # Partial / keyword match
            for key, enum_val in mapping.items():
                if key in v_lower or v_lower in key:
                    matched = enum_val
                    break
        if matched and matched not in results:
            results.append(matched)
    return results


# ---------------------------------------------------------------------------
# Main Parser
# ---------------------------------------------------------------------------

def parse_jotform_payload(raw: dict[str, Any]) -> TravelIntakeForm:
    """
    Parse a raw JotForm webhook payload dict into a TravelIntakeForm.

    JotForm field names are not guaranteed to be consistent — this parser
    uses case-insensitive key lookup with fallback patterns.
    """

    # Normalise all keys to lowercase with underscores
    def _get(key_patterns: list[str], default: Any = None) -> Any:
        """Try multiple key patterns, return first match."""
        for pattern in key_patterns:
            p_lower = pattern.lower()
            for k, v in raw.items():
                if k.lower().replace(" ", "_").replace("-", "_") == p_lower.replace(" ", "_"):
                    if v not in (None, "", {}, []):
                        return v
        return default

    # -----------------------------------------------------------------------
    # Advisor
    # -----------------------------------------------------------------------
    advisor_name_raw = _get(["travel_advisors_name", "advisor_name", "advisors_name"])
    if isinstance(advisor_name_raw, dict):
        first = advisor_name_raw.get("first", "")
        last = advisor_name_raw.get("last", "")
    elif advisor_name_raw and " " in str(advisor_name_raw):
        parts = str(advisor_name_raw).split(" ", 1)
        first, last = parts[0], parts[1]
    else:
        first = str(advisor_name_raw or "Unknown")
        last = ""

    advisor = AdvisorInfo(
        first_name=first,
        last_name=last,
        email=_get(["email", "advisor_email"], "advisor@pagodatravel.com"),
    )

    # -----------------------------------------------------------------------
    # Lead traveler
    # -----------------------------------------------------------------------
    lead_traveler_name = str(
        _get(["lead_traveler", "lead_traveler_name", "traveler_name", "client_name"], "Unknown")
    )

    # -----------------------------------------------------------------------
    # Group
    # -----------------------------------------------------------------------
    total = _safe_int(_get(["number_of_travelers", "total_travelers", "travelers"], 2), 2)
    adults = _safe_int(_get(["how_many_adults", "adults", "num_adults"], total), total)
    children = _safe_int(_get(["how_many_children", "children_under_12", "children"], 0), 0)

    if adults + children < total:
        adults = total - children  # reconcile

    group = TravelerGroup(
        total_travelers=total,
        adults=adults,
        children_under_12=children,
    )

    # -----------------------------------------------------------------------
    # Dates
    # -----------------------------------------------------------------------
    arrival_date = _safe_date(_get(["arrival_date", "start_date", "from_date"]))
    departure_date = _safe_date(_get(["departure_date", "end_date", "to_date"]))

    if not arrival_date:
        from datetime import timedelta
        arrival_date = date.today()
        logger.warning("No arrival_date in payload; defaulting to today")
    if not departure_date:
        from datetime import timedelta
        departure_date = arrival_date + timedelta(days=7)
        logger.warning("No departure_date in payload; defaulting to arrival + 7 days")

    # -----------------------------------------------------------------------
    # Destinations
    # -----------------------------------------------------------------------
    dest_raw = _get(["cities_or_regions", "destinations", "cities", "itinerary_direction"])
    if dest_raw:
        destinations = _parse_checkboxes(dest_raw) if isinstance(dest_raw, (list, dict)) \
            else [d.strip() for d in str(dest_raw).split(",") if d.strip()]
    else:
        destinations = ["Japan"]  # Form default is Japan-focused

    # -----------------------------------------------------------------------
    # Budget
    # -----------------------------------------------------------------------
    budget_raw = _get(["budget_range", "budget_range_in_usd", "budget"])
    budget_float = _safe_float(budget_raw)

    budget = BudgetInfo(
        total_budget_usd=budget_float,
        per_person_budget_usd=round(budget_float / total, 2) if budget_float and total > 0 else None,
        budget_range=None,
        budget_notes=str(budget_raw) if budget_raw and not budget_float else None,
    )

    # -----------------------------------------------------------------------
    # Visited before
    # -----------------------------------------------------------------------
    visited_raw = _get(["have_they_visited", "visited_before", "visited_destination"])
    visited_before = str(visited_raw).lower() in ("yes", "true", "1") if visited_raw else False

    # -----------------------------------------------------------------------
    # Transfers
    # -----------------------------------------------------------------------
    transfer_raw = _parse_checkboxes(_get(["transfers_required", "logistics_transfers", "transfers"]))
    transfers_required = _map_keywords(transfer_raw, _TRANSFER_MAP)

    # -----------------------------------------------------------------------
    # Flights
    # -----------------------------------------------------------------------
    flights_available_raw = _get(["are_flight_details_already_available", "flights_booked", "flight_details"])
    flights_booked = str(flights_available_raw).lower() in ("yes", "true", "1")

    arrival_airport_raw = _parse_checkboxes(_get(["arrival_and_departure_airports", "arrival_airport"]))
    arrival_airport = arrival_airport_raw[0] if arrival_airport_raw else None

    arrival_time_raw = _get(["arrival_date_and_time", "arrival_datetime"])
    arrival_datetime = None
    if arrival_time_raw:
        try:
            arrival_datetime = datetime.fromisoformat(str(arrival_time_raw))
        except ValueError:
            pass

    flight_details = FlightDetails(
        arrival_airport=arrival_airport,
        departure_airport=None,
        arrival_datetime=arrival_datetime,
        flights_booked=flights_booked,
    ) if flights_booked or arrival_airport else None

    # -----------------------------------------------------------------------
    # Accommodation
    # -----------------------------------------------------------------------
    accom_booked_raw = _get(["have_you_already_booked_accommodation", "accommodation_booked"])
    accom_booked = str(accom_booked_raw).lower() in ("yes", "true", "1")
    accom_details = _get(["if_yes_list_hotels", "hotel_list", "accommodation_details"])

    accommodation = AccommodationInfo(
        already_booked=accom_booked,
        booked_details=str(accom_details) if accom_details else None,
    )

    # -----------------------------------------------------------------------
    # Priorities & interests
    # -----------------------------------------------------------------------
    priority_raw = _parse_checkboxes(_get(["top_priorities", "priorities"]))
    top_priorities = _map_keywords(priority_raw, _PRIORITY_MAP)

    interest_raw = _parse_checkboxes(_get(["special_interests", "interests"]))
    special_interests = _map_keywords(interest_raw, _INTEREST_MAP)

    # Derive travel_styles from priorities + interests
    style_candidates = priority_raw + interest_raw
    travel_styles = _map_keywords(style_candidates, _STYLE_KEYWORD_MAP)

    # -----------------------------------------------------------------------
    # Special notes
    # -----------------------------------------------------------------------
    special_notes = _get([
        "anything_to_avoid_special_notes",
        "special_notes",
        "notes",
        "practical_considerations",
        "dietary_needs",
    ])

    return TravelIntakeForm(
        advisor=advisor,
        lead_traveler_name=lead_traveler_name,
        group=group,
        arrival_date=arrival_date,
        departure_date=departure_date,
        destinations=destinations,
        visited_before=visited_before,
        transfers_required=transfers_required,
        flight_details=flight_details,
        accommodation=accommodation,
        budget=budget,
        travel_styles=travel_styles,
        top_priorities=top_priorities,
        special_interests=special_interests,
        special_notes=str(special_notes) if special_notes else None,
        consultation_scheduled=None,
    )
