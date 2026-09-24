"""Sensei itinerary-builder fixtures. No Claude. No Q&A."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.intake import SenseiIntake, SenseiIntakeError, destinations_for_sensei
from app.services.day_calendar import build_day_calendar
from app.services.sensei_recommend import recommend_from_inventory
from app.services.travel_days import band_for_hop, cluster_for_city


def _intake(**kwargs) -> SenseiIntake:
    defaults = {
        "arrival_date": "2026-11-01",
        "departure_date": "2026-11-03",
        "destinationStays": [
            {"city": "Tokyo", "nights": 1},
            {"city": "Sapporo", "nights": 1},
            {"city": "Naha", "nights": 1},
        ],
    }
    defaults.update(kwargs)
    return SenseiIntake(**defaults)


def _tour(tour_id, name, location, minutes, operator_id=None, guide_id=None, tour_type="culture"):
    return {
        "tour_id": str(tour_id),
        "tour_name": name,
        "description": name,
        "location": location,
        "duration_minutes": minutes,
        "duration_display": f"{minutes} min" if minutes else "Duration not specified",
        "operator": {"id": operator_id, "name": "Op", "email": None},
        "guide": {
            "id": guide_id,
            "name": "Guide" if guide_id else None,
            "status": "appointed" if guide_id else "to_be_appointed",
        },
        "price": 10000,
        "price_display": "¥10,000 per adult",
        "tour_type": tour_type,
    }


def test_destinations_for_sensei_keeps_nara_day_trip():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-04",
        destinationStays=[{"city": "Kyoto", "nights": 4}],
        interestDestinations=["Nara"],
    )
    cities = destinations_for_sensei(intake)
    assert cities == ["Kyoto", "Nara"]


def test_missing_stays_fails_loudly():
    with pytest.raises((ValidationError, SenseiIntakeError)):
        SenseiIntake(
            arrival_date="2026-11-01",
            departure_date="2026-11-03",
            destinationStays=[],
        )


def test_nights_mismatch_does_not_invent_cities():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-10",
        destinationStays=[{"city": "Kyoto", "nights": 2}],
    )
    with pytest.raises(SenseiIntakeError, match="do not match trip length"):
        build_day_calendar(intake)


def test_kyoto_osaka_calendar_stay_days():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-05",
        destinationStays=[
            {"city": "Kyoto", "nights": 3},
            {"city": "Osaka", "nights": 2},
        ],
        interestDestinations=["Nara"],
    )
    days = build_day_calendar(intake)
    assert [d.overnight_city for d in days] == ["Kyoto", "Kyoto", "Kyoto", "Osaka", "Osaka"]
    assert days[0].day_kind == "stay"
    assert "Nara" in days[0].day_trip_cities
    assert days[2].day_kind == "stay"  # Kyoto → Osaka is local Kansai


def test_tokyo_hokkaido_okinawa_is_travel_far_with_pace_warning():
    days = build_day_calendar(_intake())
    assert [d.day_kind for d in days] == ["travel_far", "travel_far", "stay"]
    assert days[0].pace_warning
    assert days[1].pace_warning
    assert cluster_for_city("Tokyo") == "kanto"
    assert band_for_hop("Tokyo", "Sapporo").name == "far"


def test_kyoto_nara_is_local():
    assert band_for_hop("Kyoto", "Nara").name == "local"
    assert band_for_hop("Tokyo", "Kyoto").name == "near"


def test_far_days_get_no_full_day_tours():
    tours = [
        _tour(1, "Tokyo Full Day", "Tokyo", 480),
        _tour(2, "Sapporo Full Day", "Sapporo", 420),
        _tour(3, "Naha Full Day", "Naha", 400),
        _tour(4, "Tokyo 1h transfer", "Tokyo", 60),
    ]
    result = recommend_from_inventory(_intake(), tours)
    assert result["wrote_jobs"] is False
    assert result["pace_warning"]
    day1, day2, day3 = result["days"]
    assert day1["suggested_tours"] == [] or all(
        t["duration_minutes"] <= 90 for t in day1["suggested_tours"]
    )
    assert "too much" in (day1["empty_reason"] or "").lower() or (
        day1["suggested_tours"] and day1["suggested_tours"][0]["tour_id"] == "4"
    )
    full_ids = {
        t["tour_id"]
        for d in result["days"]
        for t in d["suggested_tours"]
        if t["duration_minutes"] and t["duration_minutes"] >= 400
    }
    assert full_ids == set() or full_ids <= {"3"}  # last day is stay in Naha
    assert "1" not in {t["tour_id"] for t in day1["suggested_tours"]}
    assert "2" not in {t["tour_id"] for t in day2["suggested_tours"]}


def test_unassigned_published_tour_is_recommended():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-03",
        destinationStays=[{"city": "Kyoto", "nights": 3}],
        interestDestinations=["Nara"],
        japanExperiences=["food"],
    )
    tours = [
        _tour(10, "Kyoto Food Walk", "Kyoto", 180, operator_id="op-1"),
        _tour(11, "Nara Temples", "Nara", 240, operator_id="op-2"),
        _tour(99, "Tokyo Skytree", "Tokyo", 180, operator_id="op-3"),
    ]
    result = recommend_from_inventory(intake, tours)
    stay_ids = {t["tour_id"] for d in result["days"] for t in d["suggested_tours"]}
    assert "10" in stay_ids
    assert "11" in stay_ids
    assert "99" not in stay_ids
    kyoto_card = next(t for d in result["days"] for t in d["suggested_tours"] if t["tour_id"] == "10")
    assert kyoto_card["guide"]["status"] == "to_be_appointed"
    assert kyoto_card["needs_confirmation"] is True


def test_empty_city_does_not_use_other_region():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-02",
        destinationStays=[{"city": "Naha", "nights": 2}],
    )
    tours = [_tour(1, "Tokyo Full Day", "Tokyo", 300)]
    result = recommend_from_inventory(intake, tours)
    assert result["days"][0]["suggested_tours"] == []
    assert "Naha" in result["days"][0]["empty_reason"]
    assert "Tokyo" not in result["days"][0]["empty_reason"]
