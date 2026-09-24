"""
Pagoda Travel AI Matching Engine
Uses Anthropic Claude to match client intake forms to operators
and generate preliminary itineraries.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from app.models.schemas import (
    DayItinerary,
    MatchGap,
    MatchedOperator,
    MatchResponse,
    Operator,
    PreliminaryItinerary,
    SampleTour,
    TravelIntakeForm,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

MATCHING_SYSTEM_PROMPT = """You are the AI matching engine for Pagoda Travel, a premium travel marketplace.
Your sole job is to analyze a client's intake form and match them to the best operators
from the provided operator database, then generate a preliminary itinerary.

## RULES — READ CAREFULLY

1. **Database-only**: You may ONLY recommend operators that exist in the provided JSON database.
   Never invent operator names, tour names, prices, or destinations that are not in the data.

2. **Structured JSON output**: You MUST respond with ONLY a valid JSON object.
   No preamble, no explanation, no markdown fences. Just the raw JSON.

3. **Top 3 matches**: Always return exactly 3 matched operators, ranked by match_score descending.
   If fewer than 3 operators are suitable, include the best available with honest scores.

4. **Match scoring**: Score each operator 0.0–1.0 based on:
   - Destination overlap (0.30 weight): Does the operator cover the requested cities/regions?
   - Budget fit (0.25 weight): Does the operator's price range fit client's budget per person?
   - Group compatibility (0.20 weight): Does the operator accept the group size?
   - Style/interest alignment (0.15 weight): Do tour types and specialties match preferences?
   - Special needs coverage (0.10 weight): Languages, accessibility, dietary considerations?

5. **Suggested tours**: For each matched operator, suggest 1–2 tours from their `sample_tours`
   array that best fit the client's dates, group, and interests. Do not invent new tours.

6. **Preliminary itinerary**: Generate a day-by-day itinerary using ONLY destinations and
   activities that the matched operators serve. Each day must include:
   - day (number), location (city/region), title (short), description, activities (list),
     operator_id (which operator handles this), optional accommodation_notes, optional meal_notes

7. **Gap reporting**: If there is a mismatch between what the client wants and what the database
   can provide (e.g. destination not covered, budget too low, group too large), report it in
   the `gaps` array with a clear category, description, and recommendation.

8. **ai_notes**: Write 2–4 sentences of overall assessment for the travel advisor.
   Flag anything unusual, time-sensitive, or worth discussing in the consultation.
   Reference the lead traveler by name. Mention if this is a repeat visitor.

## OUTPUT JSON SCHEMA

{
  "matched_operators": [
    {
      "operator": { /* full operator object from database */ },
      "match_score": 0.95,
      "match_reasoning": "Detailed string explaining why this operator is a good fit",
      "suggested_tours": [ /* 1-2 tour objects from operator's sample_tours */ ]
    }
  ],
  "preliminary_itinerary": {
    "day_by_day": [
      {
        "day": 1,
        "location": "Tokyo",
        "title": "Arrival & Shinjuku",
        "description": "Settle in and explore Shinjuku district",
        "activities": ["Airport transfer", "Shinjuku Gyoen stroll", "Omoide Yokocho dinner"],
        "operator_id": "OP001",
        "accommodation_notes": "Luxury hotel Shinjuku recommended",
        "meal_notes": "Ramen dinner or yakitori alley"
      }
    ],
    "estimated_budget": "$X,XXX–$X,XXX per person based on client's budget range and matched tour pricing",
    "notes": "Additional advisor notes about the itinerary, best booking windows, seasonality"
  },
  "ai_notes": "Overall 2-4 sentence assessment for the travel advisor",
  "gaps": [
    {
      "category": "Budget",
      "description": "What the gap is",
      "recommendation": "What the advisor should do"
    }
  ]
}

## IMPORTANT REMINDERS
- Budget per person = total_budget_usd / total_travelers (if per_person not explicit)
- Check group_size_range: operator.group_size_range.min <= group.total_travelers <= operator.group_size_range.max
- For Japan destinations specifically: OP001 (Sakura Routes Japan) and OP015 (Japan Alpine & Ski) are the Japan specialists
- Return ONLY the JSON. Any text outside the JSON will break the parser.

IMPORTANT: Your entire response must be a single valid JSON object. No markdown fences,
no prose before or after the JSON. json.loads() must succeed on the raw response string.
"""


def _build_user_message(intake: TravelIntakeForm, operators: list[dict[str, Any]]) -> str:
    """Build the user message containing client intake data and the operator database."""

    intake_dict = intake.model_dump(mode="json")

    return f"""## CLIENT INTAKE FORM

```json
{json.dumps(intake_dict, indent=2, default=str)}
```

---

## OPERATOR DATABASE (15 operators)

```json
{json.dumps(operators, indent=2)}
```

---

Please analyze this intake form, match the best operators, and generate the preliminary itinerary.
Remember: output ONLY valid JSON — no markdown, no explanation, just the JSON object."""


def _parse_ai_response(raw: str, operators_by_id: dict[str, dict]) -> MatchResponse:
    """
    Parse Claude's raw JSON string into a MatchResponse object.
    Hydrates operator objects from the database to ensure data integrity.
    """
    # Strip any accidental markdown fences if present
    clean = raw.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

    data: dict[str, Any] = json.loads(clean)

    # Build matched_operators
    matched_operators: list[MatchedOperator] = []
    for mo in data.get("matched_operators", []):
        op_data = mo.get("operator", {})
        op_id = op_data.get("id")

        # Use database operator data as source of truth (prevents hallucination drift)
        if op_id and op_id in operators_by_id:
            op_data = operators_by_id[op_id]

        operator = Operator(**op_data)

        suggested_tours = [
            SampleTour(**t) for t in mo.get("suggested_tours", [])
        ]

        matched_operators.append(
            MatchedOperator(
                operator=operator,
                match_score=float(mo.get("match_score", 0.0)),
                match_reasoning=mo.get("match_reasoning", ""),
                suggested_tours=suggested_tours,
            )
        )

    # Build itinerary
    raw_itin = data.get("preliminary_itinerary", {})

    def _sanitize_day(d: Any) -> dict:
        """Claude occasionally returns activities as a list-of-lists. Flatten it."""
        if not isinstance(d, dict):
            return d
        activities = d.get("activities", [])
        if activities and isinstance(activities[0], list):
            activities = [item for sublist in activities for item in sublist]
        return {**d, "activities": [str(a) for a in activities]}

    day_by_day = [
        DayItinerary(**_sanitize_day(d)) for d in raw_itin.get("day_by_day", [])
    ]
    itinerary = PreliminaryItinerary(
        day_by_day=day_by_day,
        estimated_budget=raw_itin.get("estimated_budget", ""),
        notes=raw_itin.get("notes", ""),
    )

    # Build gaps
    gaps = [MatchGap(**g) for g in data.get("gaps", [])]

    return MatchResponse(
        matched_operators=matched_operators,
        preliminary_itinerary=itinerary,
        ai_notes=data.get("ai_notes", ""),
        gaps=gaps,
    )


class MatchingEngine:
    """
    Core AI matching engine. Stateless — instantiate once per application lifecycle.
    """

    def __init__(self, api_key: str, operators: list[dict[str, Any]]) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.operators = operators
        self.operators_by_id: dict[str, dict] = {op["id"]: op for op in operators}
        logger.info("MatchingEngine initialised with %d operators", len(operators))

    def match(self, intake: TravelIntakeForm) -> MatchResponse:
        """
        Run the full matching pipeline for a given intake form.
        Returns a MatchResponse with top 3 operators and preliminary itinerary.
        """
        start = time.perf_counter()
        logger.info(
            "Starting match for lead traveler=%s destinations=%s",
            intake.lead_traveler_name,
            intake.destinations,
        )

        user_message = _build_user_message(intake, self.operators)

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=MATCHING_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )

        raw_text = response.content[0].text
        elapsed = time.perf_counter() - start

        logger.info(
            "Claude response received in %.2fs | input_tokens=%d output_tokens=%d",
            elapsed,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        result = _parse_ai_response(raw_text, self.operators_by_id)

        # Attach processing metadata
        result.processing_metadata = {
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "latency_seconds": round(elapsed, 3),
            "finish_reason": response.stop_reason,
        }

        logger.info(
            "Match complete: top operator=%s score=%.2f",
            result.matched_operators[0].operator.name if result.matched_operators else "none",
            result.matched_operators[0].match_score if result.matched_operators else 0,
        )

        return result
