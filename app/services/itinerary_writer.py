"""
Itinerary Writer — writes Sensei AI match results directly into production
itineraries + jobs tables.

Does NOT write to guide_tour_assignments or job_applications.
Reuses get_supabase_client() from supabase_tours_service_v2 — no new client.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from app.services.supabase_tours_service_v2 import get_supabase_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transfer product lookup tables (hardcoded tour_ids, Alphard variant default)
# ---------------------------------------------------------------------------

# Arrival airport transfer: first destination city → tour_id
_ARRIVAL_TRANSFER: dict[str, int] = {
    "tokyo":       651,   # Haneda (HND) → Tokyo by Alphard
    "osaka":       655,   # KIX → Osaka by Alphard
    "sapporo":     601,   # CTS → Sapporo by Alphard
    "niseko":      596,   # CTS → Niseko by Alphard
    "otaru":       602,   # CTS → Otaru by Alphard
    "hakodate":    597,   # CTS → Hakodate by Alphard
    "noboribetsu": 598,   # CTS → Noboribetsu by Alphard
    "furano":      600,   # CTS → Furano by Alphard
}

# Departure airport transfer: last destination city → tour_id
_DEPARTURE_TRANSFER: dict[str, int] = {
    "tokyo":       651,   # Haneda ↔ Tokyo Alphard (bidirectional)
    "osaka":       655,   # KIX ↔ Osaka Alphard
    "kyoto":       658,   # KIX ↔ Kyoto/Nara/Kobe Alphard
    "nara":        658,
    "kobe":        658,
    "sapporo":     601,
    "niseko":      596,
    "otaru":       602,
    "hakodate":    597,
    "noboribetsu": 598,
    "furano":      600,
}

# Inter-city transfer: frozenset of two city names (lowercase) → tour_id
_INTERCITY_TRANSFER: dict[frozenset, int] = {
    frozenset({"osaka", "kyoto"}): 645,   # Osaka ↔ Kyoto by Alphard
    frozenset({"tokyo", "nikko"}): 680,   # Tokyo ↔ Nikko by Alphard
    frozenset({"tokyo", "hakone"}): 685,  # Hakone ↔ Tokyo
}


def _combine_date_time(day_date: str, time_str: str | None) -> str | None:
    """
    Combine a date string (YYYY-MM-DD) with a time string (HH:MM:SS or HH:MM)
    into a full ISO timestamp string.  Returns None if either value is missing
    or unparseable.
    """
    if not day_date or not time_str:
        return None
    try:
        d = date.fromisoformat(day_date)
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(float(parts[2])) if len(parts) > 2 else 0
        return datetime(d.year, d.month, d.day, hour, minute, second).isoformat()
    except (ValueError, IndexError, TypeError):
        return None


def _insert_transfer_job(
    client,
    itinerary_id: str,
    user_id: str,
    tour_id: int,
    job_date: str,        # YYYY-MM-DD
    start_hhmm: str,      # e.g. "10:00"
    end_hhmm: str,        # e.g. "11:00"
    name_override: str | None = None,
) -> bool:
    """
    Insert one transfer/logistics job row with hardcoded timestamps (transfer
    tours have NULL start_time/end_time in the DB, so we use fixed times).
    Bypasses guide_tour_assignments check — transfer products are booked
    directly by Pagoda, not via a guide assignment.
    Returns True on successful INSERT.
    """
    tour_resp = (
        client.table("tour")
        .select("id, name, location, activity_type, image")
        .eq("id", tour_id)
        .limit(1)
        .execute()
    )
    if not tour_resp.data:
        logger.warning("Transfer tour_id=%d not found in tour table — skipping", tour_id)
        return False

    row = tour_resp.data[0]
    image_raw = row.get("image")
    images: list[str] = []
    if image_raw:
        try:
            parsed = json.loads(image_raw)
            images = parsed if isinstance(parsed, list) else [image_raw]
        except (json.JSONDecodeError, TypeError):
            images = [image_raw]

    job_resp = client.table("jobs").insert({
        "itinerary_id": itinerary_id,
        "created_by": user_id,
        "name": name_override or row.get("name") or "Transfer",
        "activity_type": row.get("activity_type") or "Transfers",
        "start_time": f"{job_date}T{start_hhmm}:00",
        "end_time": f"{job_date}T{end_hhmm}:00",
        "location": row.get("location") or "Japan",
        "tour_id": tour_id,
        "job_available": True,
        "images": images,
    }).execute()

    if job_resp.data:
        logger.info(
            "Created transfer job %s | tour_id=%d | %s | itinerary=%s",
            job_resp.data[0]["id"], tour_id, job_date, itinerary_id,
        )
        return True
    logger.error("Transfer jobs INSERT returned no data | tour_id=%d", tour_id)
    return False


def create_itinerary_with_jobs(
    user_id: str,
    profile_id: str,
    name: str,
    location: str,
    start_date: str,
    end_date: str,
    intake_data: dict[str, Any],
    day_by_day_matches: list[dict[str, Any]],
    destination_stays: list[dict[str, Any]] | None = None,
    transportation_preferences: list[str] | None = None,
) -> dict[str, Any]:
    """
    Insert one row into itineraries and one jobs row per validated tour match.

    For each suggested tour in day_by_day_matches:
      1. Verify guide_tour_assignments has at least one row for tour_id.
      2. Fetch real tour fields from the tour table.
      3. Combine day date + tour start_time/end_time into full timestamps.
      4. Insert job with created_by = user_id, job_available = True.

    If transportation_preferences includes "Airport transfers" or "Bullet Trains",
    a post-loop step deterministically inserts arrival/departure transfer jobs
    and inter-city transfer jobs where a matching product exists, and records
    logistics_notes for transitions that have no product.

    Returns:
      {
        "itinerary_id": str,
        "jobs_created": int,
        "jobs_skipped": int,
        "skip_reasons": [{"tour_id": ..., "day": ..., "reason": str}, ...],
        "logistics_notes": [str, ...]
      }
    """
    client = get_supabase_client()

    # ------------------------------------------------------------------
    # 1. Validate profile_id is a real profiles.id
    # ------------------------------------------------------------------
    profile_check = (
        client.table("profiles")
        .select("id")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    if not profile_check.data:
        raise ValueError(
            f"profile_id '{profile_id}' not found in profiles.id — "
            "itineraries.profile_id references profiles.id (the UUID PK), "
            "not profiles.user_id"
        )

    # ------------------------------------------------------------------
    # 2. Insert the itinerary row
    # ------------------------------------------------------------------
    itin_payload = {
        "user_id": user_id,
        "profile_id": profile_id,
        "name": name,
        "location": location,
        "start_date": start_date,
        "end_date": end_date,
        "build_mode": "pagoda_build",
        "intake_data": intake_data,
    }
    resp = client.table("itineraries").insert(itin_payload).execute()
    if not resp.data:
        raise RuntimeError(
            "itineraries INSERT returned no data — check FK constraints "
            "(profile_id must be profiles.id, not profiles.user_id)"
        )
    itinerary_id: str = resp.data[0]["id"]
    logger.info("Created itinerary %s — '%s'", itinerary_id, name)

    # ------------------------------------------------------------------
    # 2. Process each day's suggested tours
    # ------------------------------------------------------------------
    jobs_created = 0
    jobs_skipped = 0
    skip_reasons: list[dict[str, Any]] = []
    seen_tour_ids: set[int] = set()  # dedup: each tour_id written at most once per itinerary

    for day_entry in day_by_day_matches:
        day_num: int = day_entry.get("day", 0)
        day_date: str = day_entry.get("date", "")
        suggested_tours: list[dict] = day_entry.get("suggested_tours", [])

        for tour_suggestion in suggested_tours:
            tour_id_raw = tour_suggestion.get("tour_id")

            # Validate tour_id is present and coercible to bigint
            if not tour_id_raw:
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": None,
                    "day": day_num,
                    "reason": "missing tour_id in AI response",
                })
                continue

            try:
                tour_id = int(tour_id_raw)
            except (ValueError, TypeError):
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": tour_id_raw,
                    "day": day_num,
                    "reason": f"tour_id '{tour_id_raw}' is not a valid integer",
                })
                continue

            # ----------------------------------------------------------
            # Dedup: skip if this tour_id was already written for an
            # earlier day in this same itinerary
            # ----------------------------------------------------------
            if tour_id in seen_tour_ids:
                logger.warning(
                    "Day %d: tour_id=%d already used on an earlier day — skipping duplicate",
                    day_num, tour_id,
                )
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": tour_id,
                    "day": day_num,
                    "reason": "duplicate tour_id — already used on an earlier day",
                })
                continue

            # Published tours may have no guide allocated yet — still writable.
            # Assignment is ops, not a gate.

            # ----------------------------------------------------------
            # 2b. Fetch real tour fields from the tour table
            # ----------------------------------------------------------
            tour_resp = (
                client.table("tour")
                .select("id, name, location, activity_type, start_time, end_time, image")
                .eq("id", tour_id)
                .limit(1)
                .execute()
            )
            if not tour_resp.data:
                logger.warning(
                    "Day %d: tour_id=%d not found in tour table — skipping",
                    day_num, tour_id,
                )
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": tour_id,
                    "day": day_num,
                    "reason": "tour not found in tour table",
                })
                continue

            tour_row = tour_resp.data[0]
            tour_name: str = tour_row.get("name") or tour_suggestion.get("tour_name") or "Unnamed Tour"
            tour_location: str = tour_row.get("location") or location
            activity_type: str = tour_row.get("activity_type") or "tour"

            # Parse tour.image (stored as a JSON-encoded array string, e.g.
            # '["images/abc.jpg","images/def.png"]') into a real list for
            # the jobs.images array column.
            image_raw = tour_row.get("image")
            images: list[str] = []
            if image_raw:
                try:
                    parsed = json.loads(image_raw)
                    images = parsed if isinstance(parsed, list) else [image_raw]
                except (json.JSONDecodeError, TypeError):
                    images = [image_raw]

            # ----------------------------------------------------------
            # 2c. Build full timestamps from day date + tour times
            # ----------------------------------------------------------
            start_timestamp = _combine_date_time(day_date, tour_row.get("start_time"))
            end_timestamp = _combine_date_time(day_date, tour_row.get("end_time"))

            if not start_timestamp or not end_timestamp:
                logger.warning(
                    "Day %d: tour_id=%d missing start_time or end_time "
                    "(start=%r, end=%r) — skipping",
                    day_num, tour_id,
                    tour_row.get("start_time"), tour_row.get("end_time"),
                )
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": tour_id,
                    "day": day_num,
                    "reason": (
                        f"tour missing start_time or end_time "
                        f"(start={tour_row.get('start_time')!r}, "
                        f"end={tour_row.get('end_time')!r})"
                    ),
                })
                continue

            # ----------------------------------------------------------
            # 2d. Insert the job row
            # ----------------------------------------------------------
            job_payload: dict[str, Any] = {
                "itinerary_id": itinerary_id,
                "created_by": user_id,
                "name": tour_name,
                "activity_type": activity_type,
                "start_time": start_timestamp,
                "end_time": end_timestamp,
                "location": tour_location,
                "tour_id": tour_id,
                "job_available": True,
                "images": images,
                # min_price / max_price intentionally omitted —
                # confirmed always NULL in production; price is sourced
                # from tour.price_per_adult or a confirmed guide bid.
            }

            job_resp = client.table("jobs").insert(job_payload).execute()
            if job_resp.data:
                jobs_created += 1
                seen_tour_ids.add(tour_id)
                logger.info(
                    "Created job %s | day=%d | tour_id=%d | itinerary=%s",
                    job_resp.data[0]["id"], day_num, tour_id, itinerary_id,
                )
            else:
                logger.error(
                    "Day %d: jobs INSERT returned no data for tour_id=%d",
                    day_num, tour_id,
                )
                jobs_skipped += 1
                skip_reasons.append({
                    "tour_id": tour_id,
                    "day": day_num,
                    "reason": "jobs INSERT returned no data",
                })

    # ------------------------------------------------------------------
    # 3. Deterministic transfer jobs (airport + inter-city)
    # ------------------------------------------------------------------
    logistics_notes: list[str] = []

    prefs_lower = [p.lower() for p in (transportation_preferences or [])]
    # Airport transfers are Transferz-only. Never insert library airport-transfer tours.
    want_airport = False
    if any("airport" in p for p in prefs_lower):
        logistics_notes.append(
            "Airport transfers are the Transferz menu item — not a Tour Library product. "
            "Sensei did not add an airport transfer."
        )
    want_bullet  = any("bullet" in p or "shinkansen" in p or "train" in p for p in prefs_lower)

    if (want_airport or want_bullet) and destination_stays:
        valid_stays = [s for s in destination_stays if s.get("city", "").strip()]
        arrival    = date.fromisoformat(start_date)
        departure  = date.fromisoformat(end_date)

        if valid_stays:
            first_city = valid_stays[0]["city"].strip().lower()
            last_city  = valid_stays[-1]["city"].strip().lower()

            # --- 3a. Arrival transfer (Day 1) ---
            if want_airport:
                arr_tid = _ARRIVAL_TRANSFER.get(first_city)
                if arr_tid and arr_tid not in seen_tour_ids:
                    if _insert_transfer_job(
                        client, itinerary_id, user_id, arr_tid,
                        start_date, "10:00", "11:00",
                        name_override=f"Arrival Transfer — {valid_stays[0]['city'].strip()}",
                    ):
                        jobs_created += 1
                        seen_tour_ids.add(arr_tid)
                elif not arr_tid:
                    logistics_notes.append(
                        f"No arrival airport transfer product for {valid_stays[0]['city'].strip()} "
                        f"— advisor should arrange transfer manually."
                    )

            # --- 3b. Inter-city legs ---
            cumulative_nights = 0
            for i, stay in enumerate(valid_stays[:-1]):
                cumulative_nights += int(stay.get("nights") or 0)
                next_stay = valid_stays[i + 1]
                city_a = stay["city"].strip()
                city_b = next_stay["city"].strip()
                transition_date = (arrival + timedelta(days=cumulative_nights)).isoformat()
                pair = frozenset({city_a.lower(), city_b.lower()})
                ic_tid = _INTERCITY_TRANSFER.get(pair)
                if ic_tid and ic_tid not in seen_tour_ids:
                    if _insert_transfer_job(
                        client, itinerary_id, user_id, ic_tid,
                        transition_date, "09:00", "17:00",
                        name_override=f"Transfer: {city_a} → {city_b}",
                    ):
                        jobs_created += 1
                        seen_tour_ids.add(ic_tid)
                else:
                    note = (
                        f"No transfer product available for {city_a} → {city_b}"
                    )
                    if want_bullet:
                        note += (
                            " — advisor should arrange Shinkansen booking"
                            " (tour_id 477) or transfer manually"
                        )
                    logistics_notes.append(note)

            # --- 3c. Departure transfer (last day) ---
            if want_airport:
                dep_tid = _DEPARTURE_TRANSFER.get(last_city)
                if dep_tid and dep_tid not in seen_tour_ids:
                    if _insert_transfer_job(
                        client, itinerary_id, user_id, dep_tid,
                        end_date, "08:00", "09:00",
                        name_override=f"Departure Transfer — {valid_stays[-1]['city'].strip()}",
                    ):
                        jobs_created += 1
                        seen_tour_ids.add(dep_tid)
                elif not dep_tid:
                    logistics_notes.append(
                        f"No departure airport transfer product for {valid_stays[-1]['city'].strip()} "
                        f"— advisor should arrange transfer manually."
                    )

    logger.info(
        "Itinerary %s complete — %d jobs created, %d skipped, %d logistics notes",
        itinerary_id, jobs_created, jobs_skipped, len(logistics_notes),
    )

    return {
        "itinerary_id": itinerary_id,
        "jobs_created": jobs_created,
        "jobs_skipped": jobs_skipped,
        "skip_reasons": skip_reasons,
        "logistics_notes": logistics_notes,
    }
