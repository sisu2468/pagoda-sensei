"""
Tour-First Matching Engine — Milestone 2

Replaces operator-first matching with tour-first matching:
for each day of the trip, ranks and returns 2-3 tours that best
fit that day's location and the traveler's overall preferences.

Location is a first-class ranking factor: tours are scored higher
when their location matches or is near the day's planned location
(destination, or hotel city if provided).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


TOUR_MATCHING_SYSTEM_PROMPT = """You are Sensei, the AI trip-planning engine for Pagoda Travel.

Your job: for EACH DAY of a client's trip, recommend 2-3 SPECIFIC TOURS from the
provided tour inventory that best fit that day's location and the client's stated
preferences.

## CRITICAL RULES

1. **Tour-first, not operator-first.** The tour/experience is the primary
   recommendation. Each tour recommendation must include its own operator/guide
   info bundled in — never recommend an operator without a specific tour.

2. **Database-only.** Only recommend tours that exist in the provided inventory.
   Never invent tour names, operators, or locations.

3. **Client selections are hard pre-filters.** The user message includes a
   "CLIENT SELECTIONS" section. Treat every item there as a binding constraint
   applied BEFORE location ranking:
   - **Experience interests** (e.g. "Experience interests — Japan: Food tours,
     Cooking class"): ONLY recommend tours whose name, description, or
     activity_type matches at least one selected tag. If no inventory tour
     matches for a given day, you MUST say so in match_reasoning (e.g.
     "No 'Cooking class' tours in inventory for this day — nearest alternative
     selected."). NEVER silently substitute an unrelated tour.
   - **Experiences to avoid**: Exclude any tour whose name, description, or
     activity_type suggests the flagged type (e.g. "Long Walking Days" →
     skip high-exertion walking tours).
   - **Top priorities (ranked)**: Use to break ties between equally
     location-matched tours. Client ranked "Food & Culinary" #1 → food-themed
     tours beat equivalent non-food alternatives.
   - **Must-have experiences** and **additional notes** (free text): Apply the
     same keyword-match rule — check against tour name/description first, then
     explain in match_reasoning if no inventory match found.
   - **Special notes** (legacy field, present when no structured experience
     selections exist): apply the same keyword-match rule.

4. **Location is a primary ranking factor.** For each day, prioritize tours whose
   location matches or is geographically closest to that day's planned city/region.
   A tour in the wrong city ranks lower even if its theme fits well.

5. **One JSON object per day.** For each day of the trip:
   - The user message contains a "DAY-BY-DAY DESTINATION ASSIGNMENTS" table
     that maps every single day number to its city. Use it exactly as given —
     do not recalculate, do not round-robin, do not guess. If no such table
     is present, cycle through the destinations list proportionally by trip length.
   - Apply client selection filters (rule 3) first, then select 2-3 tours
     from inventory located in or near that day's city.
   - Rank by location fit first, then alignment with top_priorities and
     experience interests.
   - match_reasoning must cover: (a) location fit for the day, (b) which
     experience interests were matched, (c) explicit note if any stated
     interest had no inventory match.

6. **No duplicate tours across the itinerary.** Each tour_id must appear at
   most once in the entire day_by_day output. If you have already used a
   tour on an earlier day, do not suggest it again — pick the next best
   matching tour from inventory instead.

7. **Price and availability.** Most tours now include real pricing
   (price_display, e.g. "¥23,500 per adult"). Some tours have no price set —
   for those, price_display will say "Contact operator for pricing"; pass that
   through unchanged, never invent a number. Availability comes from the
   guide's calendar, not a per-tour field — pass availability_display through
   as given, never invent specific dates or "available" claims.

8. **Output ONLY valid JSON.** No markdown, no prose outside the JSON.

## OUTPUT SCHEMA

{
  "day_by_day": [
    {
      "day": 1,
      "date": "2026-11-01",
      "location": "Tokyo",
      "suggested_tours": [
        {
          "tour_id": "<exact id from inventory>",
          "tour_name": "<exact name from inventory>",
          "description": "<from inventory>",
          "location": "<from inventory>",
          "duration_minutes": <from inventory or null>,
          "duration_display": "<from inventory>",
          "operator": {"id": "<from inventory>", "name": "<from inventory>", "email": "<from inventory or null>"},
          "price": <from inventory, a number or null — copy exactly, never invent>,
          "price_display": "<from inventory — copy exactly>",
          "availability": null,
          "availability_display": "<from inventory — copy exactly>",
          "match_reasoning": "Located in Tokyo, matches client's interest in food tours (keyword match on activity_type='food'). Strong location fit for day 1."
        }
      ]
    }
  ],
  "ai_notes": "2-3 sentence overall assessment for the advisor.",
  "data_gaps": []
}

## DATA_GAPS FIELD — IMPORTANT

Only include a data_gap entry if it is TRUE for the tours you actually selected.
Check the real price/availability values in the inventory you were given —
do not assume they are missing. For example:
- If most/all selected tours have a real numeric price, do NOT add a
  "price not available" gap.
- If some selected tours show "Contact operator for pricing" (null price),
  you MAY note that a few tours need manual pricing confirmation — be specific,
  don't generalize to the whole inventory.
- Availability is genuinely never per-tour in this system (it lives on the
  guide's calendar) — it's fine to note "confirm availability directly with
  the guide" as a standing gap, since that's true for every tour, not a
  data quality problem.
- If nothing is actually missing for the tours you picked, return an empty
  array — do not pad this field to seem thorough.

Return ONLY the JSON object above, populated with real tours from the inventory.

IMPORTANT: Your entire response must be a single valid JSON object. No markdown fences,
no prose before or after the JSON. json.loads() must succeed on the raw response string."""


def _build_user_message(
    destinations: list[str],
    arrival_date: str,
    departure_date: str,
    travel_styles: list[str],
    special_notes: str,
    tours: list[dict[str, Any]],
    destination_stays: list[dict[str, Any]] | None = None,
    destination_experiences: dict[str, list[str]] | None = None,
    top_priorities: list[str] | None = None,
    must_have_experiences: str = "",
    experiences_to_avoid: list[str] | None = None,
    tour_styles: list[str] | None = None,
    additional_notes: str = "",
    transportation_preferences: list[str] | None = None,  # handled in itinerary_writer, NOT sent to AI
) -> str:
    start = date.fromisoformat(arrival_date)
    end = date.fromisoformat(departure_date)
    num_days = (end - start).days + 1
    day_dates = [(start + timedelta(days=i)).isoformat() for i in range(num_days)]

    # --- Pre-compute deterministic day→city mapping from destination_stays ---
    # This is done in Python so the AI never has to do night-to-day arithmetic.
    day_city: list[str] = []  # index 0 = day 1
    if destination_stays:
        valid_stays = [s for s in destination_stays if s.get("city", "").strip()]
        for s in valid_stays:
            nights = int(s.get("nights") or 0)
            city = s["city"].strip()
            day_city.extend([city] * nights)

        total_stay_nights = len(day_city)
        if total_stay_nights != num_days:
            logger.warning(
                "destination_stays total nights (%d) != trip length (%d days) — "
                "adjusting last city to cover remaining days",
                total_stay_nights, num_days,
            )
        if total_stay_nights < num_days:
            # Extend the last city to fill any remaining days
            last_city = day_city[-1] if day_city else (destinations[0] if destinations else "Unknown")
            day_city.extend([last_city] * (num_days - total_stay_nights))
        elif total_stay_nights > num_days:
            # Trim to actual trip length
            day_city = day_city[:num_days]

    # --- Trip details section ---
    trip_lines = [f"Destinations: {', '.join(destinations)}"]
    trip_lines += [
        f"Trip length: {num_days} days ({arrival_date} to {departure_date})",
    ]

    if day_city:
        # Replace free-text "City stay plan" with explicit per-day table
        mapping_lines = ["", "DAY-BY-DAY DESTINATION ASSIGNMENTS (use exactly as given, do not recalculate):"]
        for i, (d, city) in enumerate(zip(day_dates, day_city), start=1):
            mapping_lines.append(f"  Day {i:2d} ({d}): {city}")
        trip_lines.append("\n".join(mapping_lines))
    else:
        trip_lines.append(f"Day dates: {json.dumps(day_dates)}")

    trip_section = "\n".join(trip_lines)

    # --- Client selections block ---
    selections: list[str] = []
    if travel_styles:
        selections.append(f"Travel styles: {', '.join(travel_styles)}")
    if destination_experiences:
        for dest, exps in destination_experiences.items():
            if exps:
                selections.append(
                    f"Experience interests — {dest} "
                    f"(HARD FILTER: match tour name/description/activity_type): "
                    f"{', '.join(exps)}"
                )
    if top_priorities:
        ranked = ", ".join(f"{i + 1}. {p}" for i, p in enumerate(top_priorities))
        selections.append(f"Top priorities (ranked, use to break ties): {ranked}")
    if must_have_experiences:
        selections.append(f"Must-have experiences (HARD FILTER): {must_have_experiences}")
    if experiences_to_avoid:
        selections.append(
            f"Experiences to AVOID — exclude any tour suggesting these: "
            f"{', '.join(experiences_to_avoid)}"
        )
    if tour_styles:
        selections.append(f"Tour styles preferred: {', '.join(tour_styles)}")
    if additional_notes:
        selections.append(f"Additional notes: {additional_notes}")
    # Legacy special_notes — backward compat for /match-tours
    if special_notes:
        label = (
            "Special notes (HARD FILTER)"
            if not destination_experiences
            else "Special notes (supplemental)"
        )
        selections.append(f"{label}: {special_notes}")

    selections_block = (
        "\n".join(f"  - {s}" for s in selections)
        if selections
        else "  (none provided)"
    )

    return f"""## TRIP DETAILS

{trip_section}

## CLIENT SELECTIONS (binding — apply before location ranking)

{selections_block}

## TOUR INVENTORY ({len(tours)} tours)

```json
{json.dumps(tours, indent=2, default=str)}
```

Generate day-by-day tour recommendations for all {num_days} days, one entry per day.
Follow the DAY-BY-DAY DESTINATION ASSIGNMENTS table above exactly for each day's city.
Return ONLY the JSON."""


def _parse_response(raw: str) -> dict[str, Any]:
    clean = raw.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    return json.loads(clean)


class TourMatchingEngine:
    """Tour-first matching engine — Milestone 2."""

    def __init__(self, api_key: str, tours: list[dict[str, Any]]) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.tours = tours
        logger.info("TourMatchingEngine initialised with %d tours", len(tours))

    def match(
        self,
        destinations: list[str],
        arrival_date: str,
        departure_date: str,
        travel_styles: list[str] | None = None,
        special_notes: str = "",
        destination_stays: list[dict[str, Any]] | None = None,
        destination_experiences: dict[str, list[str]] | None = None,
        top_priorities: list[str] | None = None,
        must_have_experiences: str = "",
        experiences_to_avoid: list[str] | None = None,
        tour_styles: list[str] | None = None,
        additional_notes: str = "",
        transportation_preferences: list[str] | None = None,  # not forwarded to AI
    ) -> dict[str, Any]:
        start_t = time.perf_counter()

        user_message = _build_user_message(
            destinations, arrival_date, departure_date,
            travel_styles or [], special_notes, self.tours,
            destination_stays=destination_stays,
            destination_experiences=destination_experiences,
            top_priorities=top_priorities,
            must_have_experiences=must_have_experiences,
            experiences_to_avoid=experiences_to_avoid,
            tour_styles=tour_styles,
            additional_notes=additional_notes,
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16384,
            system=TOUR_MATCHING_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )

        raw_text = response.content[0].text
        elapsed = time.perf_counter() - start_t

        logger.info(
            "Tour match response in %.2fs | tokens in=%d out=%d",
            elapsed, response.usage.input_tokens, response.usage.output_tokens,
        )

        result = _parse_response(raw_text)
        result["processing_metadata"] = {
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "latency_seconds": round(elapsed, 3),
        }
        return result
