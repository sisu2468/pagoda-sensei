"""
Read-only Sensei recommendations.

Uses the day calendar + travel bands, then published inventory only.
Never writes jobs. Never invents tours.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.intake import SenseiIntake, destinations_for_sensei
from app.services.day_calendar import CalendarDay, build_day_calendar
from app.services.travel_days import FAR_SHORT_MAX_MINUTES

MAX_CARDS = 4


def _blob(tour: dict[str, Any]) -> str:
    parts = [
        str(tour.get("tour_name") or ""),
        str(tour.get("description") or ""),
        str(tour.get("tour_type") or ""),
        str(tour.get("location") or ""),
    ]
    return " ".join(parts).casefold()


def _city_match(tour: dict[str, Any], city: str) -> bool:
    loc = (tour.get("location") or "").casefold()
    token = city.strip().casefold()
    if not token:
        return False
    return token in loc or loc in token


def _duration_ok(tour: dict[str, Any], max_minutes: int, day_kind: str) -> bool:
    minutes = tour.get("duration_minutes")
    if day_kind == "travel_far":
        cap = FAR_SHORT_MAX_MINUTES if max_minutes == 0 else max_minutes
        if minutes is None:
            return False
        return 0 < int(minutes) <= cap
    if day_kind == "travel_near":
        if minutes is None:
            return False
        return int(minutes) <= max_minutes
    if minutes is None:
        return True
    return int(minutes) <= max_minutes


def _avoided(tour: dict[str, Any], avoids: list[str]) -> bool:
    text = _blob(tour)
    for item in avoids:
        token = item.strip().casefold()
        if token and token in text:
            return True
    return False


def _score(
    tour: dict[str, Any],
    day: CalendarDay,
    intake: SenseiIntake,
) -> tuple[float, str]:
    reasons: list[str] = []
    score = 0.35
    blob = _blob(tour)

    if _city_match(tour, day.overnight_city):
        score += 0.35
        reasons.append(f"Located in {day.overnight_city}")
    else:
        for trip_city in day.day_trip_cities:
            if _city_match(tour, trip_city):
                score += 0.3
                reasons.append(f"Day trip to {trip_city} from {day.overnight_city}")
                break

    interests = (
        list(intake.japanExperiences)
        + list(intake.chinaExperiences)
        + list(intake.travelStyles)
        + list(intake.tourStyles)
        + list(intake.topPriorities)
    )
    if intake.mustHaveExperiences:
        interests.append(intake.mustHaveExperiences)

    matched_interest = False
    for item in interests:
        token = item.strip().casefold()
        if token and token in blob:
            score += 0.08
            matched_interest = True
    if matched_interest:
        reasons.append("Matches intake experience / style tags")

    for pref in intake.preferredSuppliers:
        dest_ok = (
            not pref.destination.strip()
            or pref.destination.casefold() in day.overnight_city.casefold()
            or day.overnight_city.casefold() in pref.destination.casefold()
        )
        if not dest_ok:
            continue
        operator = tour.get("operator") or {}
        guide = tour.get("guide") or {}
        if pref.operatorId and str(operator.get("id") or "") == str(pref.operatorId):
            score += 0.2
            reasons.append("Preferred operator")
        if pref.guideId and str(guide.get("id") or "") == str(pref.guideId):
            score += 0.2
            reasons.append("Preferred guide")

    if day.day_kind != "stay":
        reasons.append(
            f"{day.overnight_city} → {day.next_overnight_city} is a travel day; "
            "only a short activity is allowed"
        )

    if not reasons:
        reasons.append("Published tour in the day's city")

    return min(score, 1.0), "; ".join(reasons)


def _empty_reason(day: CalendarDay, had_city_tours: bool) -> str:
    if day.day_kind == "travel_far":
        dest = day.next_overnight_city or "the next city"
        msg = (
            f"Travel day ({day.overnight_city} → {dest}). "
            "A full-day tour is too much to ask."
        )
        if day.pace_warning:
            return f"{msg} {day.pace_warning}"
        return msg
    if day.day_kind == "travel_near":
        dest = day.next_overnight_city or "the next city"
        return (
            f"Travel day ({day.overnight_city} → {dest}). "
            "Only a half-day tour is allowed — none matched in published inventory."
        )
    if not had_city_tours:
        cities = [day.overnight_city, *day.day_trip_cities]
        listed = ", ".join(cities)
        return (
            f"No published tours in inventory for {listed}. "
            "Sensei will not substitute another region's catalogue."
        )
    return (
        f"Published tours in {day.overnight_city} did not fit this day's "
        "duration or intake filters."
    )


def _requested_day(query: str | None) -> int | None:
    if not query:
        return None
    match = re.search(r"\bday\s+(\d+)\b", query, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def recommend_from_inventory(
    intake: SenseiIntake,
    tours: list[dict[str, Any]],
    query: str | None = None,
    exclude_tour_ids: list[str] | None = None,
) -> dict[str, Any]:
    calendar = build_day_calendar(intake)
    exclude = {str(x) for x in (exclude_tour_ids or [])}
    only_day = _requested_day(query)

    pace_warning = next((d.pace_warning for d in calendar if d.pace_warning), None)
    days_out: list[dict[str, Any]] = []

    for day in calendar:
        if only_day is not None and day.day != only_day:
            continue

        pool_cities = [day.overnight_city, *day.day_trip_cities]
        city_tours: list[dict[str, Any]] = []
        for tour in tours:
            if str(tour.get("tour_id")) in exclude:
                continue
            if any(_city_match(tour, city) for city in pool_cities):
                city_tours.append(tour)

        eligible: list[dict[str, Any]] = []
        for tour in city_tours:
            if _avoided(tour, intake.experiencesToAvoid):
                continue
            if not _duration_ok(tour, day.max_tour_minutes, day.day_kind):
                continue
            eligible.append(tour)

        ranked: list[dict[str, Any]] = []
        for tour in eligible:
            score, reasoning = _score(tour, day, intake)
            card = {
                "tour_id": str(tour.get("tour_id")),
                "tour_name": tour.get("tour_name"),
                "description": tour.get("description") or "",
                "location": tour.get("location"),
                "duration_minutes": tour.get("duration_minutes"),
                "duration_display": tour.get("duration_display"),
                "operator": tour.get("operator"),
                "guide": tour.get("guide") or {
                    "id": None,
                    "name": None,
                    "status": "to_be_appointed",
                },
                "price": tour.get("price"),
                "price_display": tour.get("price_display"),
                "match_score": round(score, 3),
                "match_reasoning": reasoning,
                "needs_confirmation": True,
            }
            ranked.append(card)

        ranked.sort(key=lambda c: c["match_score"], reverse=True)
        cards = ranked[:MAX_CARDS]

        payload = day.to_dict()
        payload["suggested_tours"] = cards
        payload["empty_reason"] = None if cards else _empty_reason(day, bool(city_tours))
        days_out.append(payload)

    notes = []
    if pace_warning:
        notes.append(pace_warning)
    notes.append(
        "Recommendations are unpublished until the advisor confirms a tour and a day. "
        "Sensei has not added anything to the itinerary."
    )

    return {
        "destinations_for_sensei": destinations_for_sensei(intake),
        "pace_warning": pace_warning,
        "days": days_out,
        "ai_notes": " ".join(notes),
        "wrote_jobs": False,
    }
