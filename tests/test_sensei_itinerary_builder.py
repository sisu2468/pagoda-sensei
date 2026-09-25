"""Sensei itinerary-builder fixtures. No Claude. No Q&A."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.intake import SenseiIntake, SenseiIntakeError, destinations_for_sensei
from app.services.day_calendar import build_day_calendar
from app.services.inventory_rules import is_airport_transfer
from app.services.sensei_recommend import recommend_from_inventory, tours_for_guide
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


def _tour(
    tour_id,
    name,
    location,
    minutes,
    operator_id=None,
    guide_id=None,
    guide_name=None,
    tour_type="culture",
    introduced_by_agency_id=None,
    description=None,
):
    guide = {
        "id": guide_id,
        "name": guide_name or ("Guide" if guide_id else None),
        "status": "appointed" if guide_id else "to_be_appointed",
        "introduced_by_agency_id": introduced_by_agency_id,
    }
    return {
        "tour_id": str(tour_id),
        "tour_name": name,
        "description": description or name,
        "location": location,
        "duration_minutes": minutes,
        "duration_display": f"{minutes} min" if minutes else "Duration not specified",
        "operator": {"id": operator_id, "name": "Op", "email": None},
        "guide": guide,
        "guides": [guide] if guide_id else [],
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
        _tour(4, "Tokyo 1h transfer", "Tokyo", 60, tour_type="transfer"),
        _tour(5, "Tokyo Station Snack Stop", "Tokyo", 60, tour_type="food"),
    ]
    result = recommend_from_inventory(_intake(), tours)
    assert result["wrote_jobs"] is False
    assert result["wrote_transfers"] is False
    assert result["pace_warning"]
    day1, day2, day3 = result["days"]
    assert day1["suggested_tours"] == [] or all(
        t["duration_minutes"] <= 90 for t in day1["suggested_tours"]
    )
    assert "4" not in {t["tour_id"] for t in day1["suggested_tours"]}
    assert "too much" in (day1["empty_reason"] or "").lower() or {
        t["tour_id"] for t in day1["suggested_tours"]
    } <= {"5"}
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


def test_hotel_name_is_copied_from_intake():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-03",
        destinationStays=[
            {"city": "Kyoto", "nights": 2, "hotelName": "Hotel Okura Kyoto"},
            {"city": "Osaka", "nights": 1, "hotelName": "Conrad Osaka"},
        ],
    )
    days = build_day_calendar(intake)
    assert [d.hotel_name for d in days] == [
        "Hotel Okura Kyoto",
        "Hotel Okura Kyoto",
        "Conrad Osaka",
    ]


def test_airport_transfer_is_never_recommended():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-02",
        destinationStays=[{"city": "Tokyo", "nights": 2}],
        flightDetails="NH 101 NRT 09:00",
    )
    tours = [
        _tour(1, "Haneda Hotel Pickup (short)", "Tokyo", 60, tour_type="transfer"),
        _tour(2, "Tsukiji Food Walk", "Tokyo", 180, tour_type="food"),
    ]
    result = recommend_from_inventory(intake, tours)
    ids = {t["tour_id"] for d in result["days"] for t in d["suggested_tours"]}
    assert "1" not in ids
    assert "2" in ids
    assert result["wrote_transfers"] is False
    assert "Transferz" in result["ai_notes"]


def test_guide_name_search_returns_that_guides_tours():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-03",
        destinationStays=[{"city": "Tokyo", "nights": 3}],
    )
    tours = [
        _tour(10, "Tokyo Full Day Highlights", "Tokyo", 480, guide_id="g-tokyo-1", guide_name="Yuki Tanaka"),
        _tour(11, "Yanaka Morning", "Tokyo", 150, guide_id="g-tokyo-1", guide_name="Yuki Tanaka"),
        _tour(20, "Kyoto Gion Morning", "Kyoto", 210, guide_id="g-kyoto-1", guide_name="Aiko Mori"),
        _tour(30, "Tokyo Food Walk", "Tokyo", 180),
    ]
    result = recommend_from_inventory(intake, tours, query="Yuki Tanaka")
    assert result["search_mode"] == "guide"
    guide_ids = {t["tour_id"] for t in result["guide_tours"]}
    assert guide_ids == {"10", "11"}
    stay_ids = {t["tour_id"] for d in result["days"] for t in d["suggested_tours"]}
    assert "11" in stay_ids
    assert "20" not in stay_ids
    assert "30" not in stay_ids


def test_food_query_is_itinerary_mode_not_guide_mode():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-03",
        destinationStays=[{"city": "Osaka", "nights": 3}],
    )
    tours = [
        _tour(401, "Osaka Street Food Evening", "Osaka", 180, tour_type="food"),
        _tour(402, "Osaka Castle Walk", "Osaka", 180, tour_type="culture"),
        _tour(10, "Tokyo Full Day", "Tokyo", 480, guide_id="g-1", guide_name="Yuki Tanaka"),
    ]
    result = recommend_from_inventory(
        intake, tours, query="Family-friendly food tour in Osaka for Day 3"
    )
    assert result["search_mode"] == "itinerary"
    assert len(result["days"]) == 1
    assert result["days"][0]["day"] == 3
    ids = {t["tour_id"] for t in result["days"][0]["suggested_tours"]}
    assert "401" in ids
    assert "10" not in ids


def test_host_agency_hides_exclusive_guide_but_keeps_published_tour():
    intake = SenseiIntake(
        arrival_date="2026-11-01",
        departure_date="2026-11-02",
        destinationStays=[{"city": "Tokyo", "nights": 2}],
    )
    tours = [
        _tour(
            102,
            "Tokyo Full Day Highlights",
            "Tokyo",
            300,
            guide_id="g-tokyo-1",
            guide_name="Yuki Tanaka",
            introduced_by_agency_id="fora",
        )
    ]
    other = recommend_from_inventory(intake, tours, host_agency_id="other-agency")
    card = other["days"][0]["suggested_tours"][0]
    assert card["tour_id"] == "102"
    assert card["guide"]["status"] == "to_be_appointed"
    assert card["guide"]["name"] is None

    fora = recommend_from_inventory(intake, tours, host_agency_id="fora")
    fora_card = fora["days"][0]["suggested_tours"][0]
    assert fora_card["guide"]["name"] == "Yuki Tanaka"
    assert fora_card["guide"]["status"] == "appointed"


def test_mock_inventory_drops_airport_transfers():
    path = Path(__file__).resolve().parents[1] / "app" / "data" / "mock_tours.json"
    tours = json.loads(path.read_text(encoding="utf-8"))
    remaining = [t for t in tours if not is_airport_transfer(t)]
    ids = {t["tour_id"] for t in remaining}
    assert "103" not in ids
    assert "101" in ids
    assert "102" in ids


def test_tours_for_guide_second_entry():
    tours = [
        _tour(10, "Tokyo Full Day Highlights", "Tokyo", 480, guide_id="g-tokyo-1", guide_name="Yuki Tanaka"),
        _tour(20, "Kyoto Gion Morning", "Kyoto", 210, guide_id="g-kyoto-1", guide_name="Aiko Mori"),
    ]
    result = tours_for_guide(tours, guide_name="Yuki Tanaka")
    assert result["wrote_jobs"] is False
    assert result["wrote_transfers"] is False
    assert [t["tour_id"] for t in result["tours"]] == ["10"]
    assert result["empty_reason"] is None
