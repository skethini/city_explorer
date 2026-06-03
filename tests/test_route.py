"""Tests for haversine distance and itinerary assembly."""

from __future__ import annotations

from app.route import assemble_itinerary, haversine_m, normalize_place_label
from tests.conftest import make_place


def test_haversine_known_distance() -> None:
    paris = (48.8566, 2.3522)
    london = (51.5074, -0.1278)
    d = haversine_m(paris, london)
    assert 340_000 < d < 360_000


def test_normalize_place_label_dedupes_case_and_spacing() -> None:
    assert normalize_place_label("  Naan   Stop ") == normalize_place_label("naan stop")


def test_assemble_itinerary_preserves_stop_order() -> None:
    origin = (40.4167, -3.7033)
    stops = [
        make_place("A", 40.41, -3.70),
        make_place("B", 40.42, -3.71),
    ]
    itinerary = assemble_itinerary(origin, stops, "walking")
    assert [s.place.name for s in itinerary.stops] == ["A", "B"]
    assert itinerary.stops[0].order == 1
    assert itinerary.stops[1].order == 2


def test_assemble_itinerary_empty_stops() -> None:
    itinerary = assemble_itinerary((0.0, 0.0), [], "walking")
    assert itinerary.stops == []
