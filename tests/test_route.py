"""Tests for selection, ordering, and TSP behaviour."""

from __future__ import annotations

import math

import pytest

from app.models import IntentPlan, Slot
from app.route import (
    TIME_ORDER,
    haversine_m,
    order_stops,
    select_places,
)
from tests.conftest import make_place


def test_haversine_known_distance() -> None:
    paris = (48.8566, 2.3522)
    london = (51.5074, -0.1278)
    d = haversine_m(paris, london)
    assert 340_000 < d < 360_000


def test_two_opt_unwinds_a_crossed_route() -> None:
    origin = (0.0, 0.0)
    a = make_place("A", 0.0, 0.001)
    b = make_place("B", 0.0, 0.002)
    c = make_place("C", 0.0, 0.003)
    d = make_place("D", 0.0, 0.004)

    ordered = order_stops(origin, [c, a, d, b])
    assert [p.name for p in ordered] == ["A", "B", "C", "D"]


def test_order_respects_time_anchors() -> None:
    origin = (0.0, 0.0)
    morning_park = make_place("Park", 0.0, 0.001, time_of_day="morning")
    lunch_thai = make_place("Thai", 0.0, 0.010, category="thai_restaurant", time_of_day="lunch")
    afternoon_museum = make_place("Museum", 0.0, 0.005, time_of_day="any")
    dinner_takeout = make_place("Takeout", 0.0, 0.020, category="takeout", time_of_day="dinner")
    evening_view = make_place("Viewpoint", 0.0, 0.015, time_of_day="any")

    ordered = order_stops(
        origin,
        [dinner_takeout, evening_view, lunch_thai, afternoon_museum, morning_park],
    )

    indices = {p.name: i for i, p in enumerate(ordered)}
    assert indices["Park"] < indices["Thai"] < indices["Takeout"]
    assert TIME_ORDER[ordered[0].time_of_day] <= TIME_ORDER[ordered[-1].time_of_day]


def test_select_places_respects_max_stops_and_dedupes() -> None:
    intent = IntentPlan(
        max_stops=3,
        free_slots=5,
        slots=[Slot(category="thai_restaurant", time_of_day="lunch")],
    )
    thai = make_place("Bangkok Bistro", 0.0, 0.001, category="thai_restaurant", rating=8.5)
    duplicate = thai
    parks = [
        make_place("Park A", 0.0, 0.002, popularity=0.9),
        make_place("Park B", 0.0, 0.003, popularity=0.8),
        make_place("Park C", 0.0, 0.004, popularity=0.7),
        make_place("Park D", 0.0, 0.005, popularity=0.6),
    ]
    candidates = {
        "slot:0:thai_restaurant": [thai, duplicate],
        "free": parks,
    }
    chosen = select_places(intent, candidates, origin=(0.0, 0.0))
    assert len(chosen) == 3
    names = [p.name for p in chosen]
    assert "Bangkok Bistro" in names
    assert names.count("Bangkok Bistro") == 1


def test_select_places_prioritizes_anchor_attractions() -> None:
    intent = IntentPlan(max_stops=2, free_slots=2, slots=[])
    anchor = make_place("Plaza Mayor", 0.0, 0.01, popularity=0.3)
    anchor = anchor.model_copy(update={"id": "anchor-1", "is_anchor": True})
    non_anchor = make_place("Other Spot", 0.0, 0.001, popularity=0.95)
    chosen = select_places(intent, {"free": [non_anchor, anchor]}, origin=(0.0, 0.0))
    assert chosen[0].name == "Plaza Mayor"


def test_order_handles_empty_input() -> None:
    assert order_stops((1.0, 2.0), []) == []


def test_order_single_stop() -> None:
    p = make_place("Solo", 0.1, 0.1)
    out = order_stops((0.0, 0.0), [p])
    assert [x.name for x in out] == ["Solo"]


@pytest.mark.parametrize("travel_mode", ["walking", "driving", "bicycling"])
def test_select_uses_origin_distance_as_tiebreaker(travel_mode: str) -> None:
    intent = IntentPlan(
        max_stops=1,
        free_slots=0,
        slots=[Slot(category="cafe", time_of_day="any")],
        travel_mode=travel_mode,  # type: ignore[arg-type]
    )
    near = make_place("Near", 0.0, 0.001, category="cafe", rating=8.0)
    far = make_place("Far", 0.0, 0.050, category="cafe", rating=8.0)
    chosen = select_places(intent, {"slot:0:cafe": [near, far]}, origin=(0.0, 0.0))
    assert chosen[0].name == "Near"


def test_total_distance_decreases_or_equals_after_2opt() -> None:
    origin = (0.0, 0.0)
    places = [
        make_place("P3", 0.003, 0.0),
        make_place("P1", 0.001, 0.0),
        make_place("P4", 0.004, 0.0),
        make_place("P2", 0.002, 0.0),
    ]
    naive = [origin] + [(p.lat, p.lng) for p in places]
    naive_len = sum(
        haversine_m(naive[i], naive[i + 1]) for i in range(len(naive) - 1)
    )
    ordered = order_stops(origin, places)
    optimized = [origin] + [(p.lat, p.lng) for p in ordered]
    opt_len = sum(
        haversine_m(optimized[i], optimized[i + 1])
        for i in range(len(optimized) - 1)
    )
    assert opt_len <= naive_len + 1e-6
    assert math.isfinite(opt_len)
