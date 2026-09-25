"""
Read-only Sensei recommendations.

Uses the day calendar + travel bands, then published inventory only.
Never writes jobs. Never invents tours. Never books Transferz.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.intake import SenseiIntake, destinations_for_sensei
from app.services.day_calendar import CalendarDay, build_day_calendar
from app.services.inventory_rules import (
    find_guide_in_inventory,
    is_airport_transfer,
    primary_guide,
    public_guide_fields,
    tour_matches_guide,
    visible_guides,
)
from app.services.travel_days import FAR_SHORT_MAX_MINUTES

DEFAULT_MAX_CARDS = 6
HARD_MAX_CARDS = 12
FULL_DAY_MINUTES = 400
MAX_FULL_DAY_PER_DAY = 2

_QUERY_STOPWORDS = frozenset(
    {
        "a", "an", "and", "by", "day", "for", "from", "guide", "in", "of",
        "on", "or", "the", "to", "tour", "tours", "with",
    }
)
_MOBILITY_LIMIT_MARKERS = (
    "wheelchair",
    "limited mobility",
    "low mobility",
    "no long walk",
    "cannot walk",
    "can't walk",
    "mobility aid",
    "stroller",
)
_WALKING_MARKERS = ("walk", "walking", "hike", "hiking", "trek", "full day")


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


def _query_tokens(query: str | None) -> list[str]:
    if not query:
        return []
    words = re.findall(r"[a-z0-9]+", query.casefold())
    return [w for w in words if w not in _QUERY_STOPWORDS and not w.isdigit() and len(w) > 2]


def _mobility_limited(notes: str | None) -> bool:
    text = (notes or "").casefold()
    return any(marker in text for marker in _MOBILITY_LIMIT_MARKERS)


def _score(
    tour: dict[str, Any],
    day: CalendarDay | None,
    intake: SenseiIntake,
    query: str | None = None,
    host_agency_id: str | None = None,
) -> tuple[float, str]:
    reasons: list[str] = []
    score = 0.35
    blob = _blob(tour)

    if day is not None:
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
        + list(intake.travelerTypes)
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
        dest_ok = True
        if day is not None and pref.destination.strip():
            dest_ok = (
                pref.destination.casefold() in day.overnight_city.casefold()
                or day.overnight_city.casefold() in pref.destination.casefold()
            )
        if not dest_ok:
            continue
        operator = tour.get("operator") or {}
        guide = primary_guide(tour, host_agency_id)
        if pref.operatorId and str(operator.get("id") or "") == str(pref.operatorId):
            score += 0.2
            reasons.append("Preferred operator")
        if pref.guideId and str(guide.get("id") or "") == str(pref.guideId):
            score += 0.2
            reasons.append("Preferred guide")

    q_hits = [tok for tok in _query_tokens(query) if tok in blob]
    if q_hits:
        score += min(0.2, 0.06 * len(q_hits))
        reasons.append("Matches advisor search")

    diet = (intake.dietaryNotes or "").strip()
    if diet:
        diet_fold = diet.casefold()
        if any(token in blob for token in diet_fold.split() if len(token) > 3):
            score += 0.08
            reasons.append("Matches dietary notes")
        elif "food" in blob or "cook" in blob:
            reasons.append("Food tour — confirm dietary notes with the operator")

    if _mobility_limited(intake.mobilityNotes):
        if any(marker in blob for marker in _WALKING_MARKERS):
            minutes = tour.get("duration_minutes") or 0
            if int(minutes) >= FULL_DAY_MINUTES:
                score -= 0.25
                reasons.append("Downranked: long walking day vs mobility notes")
            else:
                score -= 0.08
                reasons.append("Check walking level against mobility notes")

    pace = (intake.tripPace or "").casefold()
    minutes = tour.get("duration_minutes") or 0
    if pace in {"relaxed", "slow", "leisurely"} and int(minutes) >= FULL_DAY_MINUTES:
        score -= 0.12
        reasons.append("Full-day tour downranked for a relaxed pace")

    if day is not None and day.day_kind != "stay":
        reasons.append(
            f"{day.overnight_city} → {day.next_overnight_city} is a travel day; "
            "only a short activity is allowed"
        )

    if not reasons:
        reasons.append("Published tour in the day's city")

    return min(max(score, 0.0), 1.0), "; ".join(reasons)


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


def _cap_cards(value: int | None) -> int:
    if value is None:
        return DEFAULT_MAX_CARDS
    return max(1, min(int(value), HARD_MAX_CARDS))


def _tour_card(
    tour: dict[str, Any],
    score: float,
    reasoning: str,
    host_agency_id: str | None = None,
) -> dict[str, Any]:
    guide = public_guide_fields(primary_guide(tour, host_agency_id))
    guides = [public_guide_fields(g) for g in visible_guides(tour, host_agency_id)]
    if not guides and guide.get("status") == "to_be_appointed":
        guides = [guide]
    return {
        "tour_id": str(tour.get("tour_id")),
        "tour_name": tour.get("tour_name"),
        "description": tour.get("description") or "",
        "location": tour.get("location"),
        "duration_minutes": tour.get("duration_minutes"),
        "duration_display": tour.get("duration_display"),
        "operator": tour.get("operator"),
        "guide": guide,
        "guides": guides,
        "price": tour.get("price"),
        "price_display": tour.get("price_display"),
        "match_score": round(score, 3),
        "match_reasoning": reasoning,
        "needs_confirmation": True,
    }


def _pick_cards(
    ranked: list[dict[str, Any]],
    max_cards: int,
    day_kind: str,
) -> list[dict[str, Any]]:
    """
    Offer several real options. Do not fill a day with only 8-hour tours
    just to look complete.
    """
    selected: list[dict[str, Any]] = []
    full_day = 0
    for card in ranked:
        minutes = card.get("duration_minutes") or 0
        is_full = int(minutes) >= FULL_DAY_MINUTES
        if is_full and full_day >= MAX_FULL_DAY_PER_DAY:
            continue
        selected.append(card)
        if is_full:
            full_day += 1
        if len(selected) >= max_cards:
            break

    if day_kind == "stay" and selected and full_day == len(selected):
        shorter = [
            c
            for c in ranked
            if (c.get("duration_minutes") or 0) < FULL_DAY_MINUTES and c not in selected
        ]
        if shorter:
            selected[-1] = shorter[0]
    return selected


def _eligible_tours(
    tours: list[dict[str, Any]],
    *,
    exclude: set[str],
    guide_id: str | None = None,
    guide_name: str | None = None,
    host_agency_id: str | None = None,
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for tour in tours:
        if str(tour.get("tour_id")) in exclude:
            continue
        if is_airport_transfer(tour):
            continue
        if guide_id or guide_name:
            if not tour_matches_guide(tour, guide_id=guide_id, guide_name=guide_name):
                continue
            if host_agency_id and not tour_matches_guide(
                {"guides": visible_guides(tour, host_agency_id)},
                guide_id=guide_id,
                guide_name=guide_name,
            ):
                continue
        eligible.append(tour)
    return eligible


def recommend_from_inventory(
    intake: SenseiIntake,
    tours: list[dict[str, Any]],
    query: str | None = None,
    exclude_tour_ids: list[str] | None = None,
    guide_id: str | None = None,
    guide_name: str | None = None,
    host_agency_id: str | None = None,
    max_cards: int | None = None,
) -> dict[str, Any]:
    calendar = build_day_calendar(intake)
    exclude = {str(x) for x in (exclude_tour_ids or [])}
    only_day = _requested_day(query)
    card_limit = _cap_cards(max_cards)

    found_id, found_name = find_guide_in_inventory(tours, query)
    search_guide_id = guide_id or found_id
    search_guide_name = guide_name or (None if search_guide_id else found_name)
    guide_search = bool(search_guide_id or search_guide_name)

    inventory = _eligible_tours(
        tours,
        exclude=exclude,
        guide_id=search_guide_id,
        guide_name=search_guide_name,
        host_agency_id=host_agency_id if guide_search else None,
    )

    pace_warning = next((d.pace_warning for d in calendar if d.pace_warning), None)
    days_out: list[dict[str, Any]] = []

    for day in calendar:
        if only_day is not None and day.day != only_day:
            continue

        pool_cities = [day.overnight_city, *day.day_trip_cities]
        city_tours = [
            tour
            for tour in inventory
            if any(_city_match(tour, city) for city in pool_cities)
        ]

        eligible: list[dict[str, Any]] = []
        for tour in city_tours:
            if _avoided(tour, intake.experiencesToAvoid):
                continue
            if not _duration_ok(tour, day.max_tour_minutes, day.day_kind):
                continue
            eligible.append(tour)

        ranked: list[dict[str, Any]] = []
        for tour in eligible:
            score, reasoning = _score(
                tour, day, intake, query=query, host_agency_id=host_agency_id
            )
            ranked.append(_tour_card(tour, score, reasoning, host_agency_id))

        ranked.sort(key=lambda c: c["match_score"], reverse=True)
        cards = _pick_cards(ranked, card_limit, day.day_kind)

        payload = day.to_dict()
        payload["suggested_tours"] = cards
        payload["empty_reason"] = None if cards else _empty_reason(day, bool(city_tours))
        days_out.append(payload)

    guide_tours: list[dict[str, Any]] = []
    guide_empty_reason = None
    if guide_search:
        ranked_all: list[dict[str, Any]] = []
        for tour in inventory:
            score, reasoning = _score(
                tour, None, intake, query=query, host_agency_id=host_agency_id
            )
            ranked_all.append(
                _tour_card(
                    tour,
                    score,
                    reasoning or "Published tour led by the requested guide",
                    host_agency_id,
                )
            )
        ranked_all.sort(key=lambda c: c["match_score"], reverse=True)
        guide_tours = ranked_all[:HARD_MAX_CARDS]
        if not guide_tours:
            label = search_guide_name or search_guide_id
            guide_empty_reason = (
                f"No published tours for guide {label!r}. "
                "Sensei will not invent inventory."
            )

    notes = []
    if pace_warning:
        notes.append(pace_warning)
    if intake.flightDetails:
        notes.append(
            "Flight details are notes only. Sensei has not booked Airport Transfers "
            "(Transferz) and will not mix partner airport-transfer products into "
            "the Tour Library recommendations."
        )
    if guide_search:
        notes.append(
            "Guide search returns that guide's published tours — still tours, "
            "not a guide-only list."
        )
    notes.append(
        "Recommendations are unpublished until the advisor confirms a tour and a day. "
        "Sensei has not added anything to the itinerary."
    )

    return {
        "destinations_for_sensei": destinations_for_sensei(intake),
        "pace_warning": pace_warning,
        "days": days_out,
        "search_mode": "guide" if guide_search else "itinerary",
        "guide_query": {
            "guide_id": search_guide_id,
            "guide_name": search_guide_name,
        }
        if guide_search
        else None,
        "guide_tours": guide_tours,
        "guide_empty_reason": guide_empty_reason,
        "ai_notes": " ".join(notes),
        "wrote_jobs": False,
        "wrote_transfers": False,
    }


def tours_for_guide(
    tours: list[dict[str, Any]],
    *,
    guide_id: str | None = None,
    guide_name: str | None = None,
    query: str | None = None,
    host_agency_id: str | None = None,
) -> dict[str, Any]:
    """Second entry: agent views a guide → every published tour that guide can lead."""
    found_id, found_name = find_guide_in_inventory(tours, query)
    search_id = guide_id or found_id
    search_name = guide_name or (None if search_id else found_name or query)

    if not search_id and not search_name:
        return {
            "search_mode": "guide",
            "guide_query": None,
            "tours": [],
            "empty_reason": (
                "Enter a guide name or id. Sensei will show that guide's "
                "published tours — not a guide-only list."
            ),
            "wrote_jobs": False,
            "wrote_transfers": False,
        }

    inventory = _eligible_tours(
        tours,
        exclude=set(),
        guide_id=search_id,
        guide_name=search_name,
        host_agency_id=host_agency_id,
    )
    cards: list[dict[str, Any]] = []
    for tour in inventory:
        cards.append(
            _tour_card(
                tour,
                0.9,
                "Published tour assigned to this guide",
                host_agency_id,
            )
        )
    cards.sort(key=lambda c: (c.get("location") or "", c.get("tour_name") or ""))

    label = search_name or search_id
    return {
        "search_mode": "guide",
        "guide_query": {"guide_id": search_id, "guide_name": search_name},
        "tours": cards,
        "empty_reason": (
            None
            if cards
            else f"No published tours for guide {label!r}. Sensei will not invent inventory."
        ),
        "wrote_jobs": False,
        "wrote_transfers": False,
    }
