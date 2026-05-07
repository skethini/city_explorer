"""Tests for the deterministic fallback parser in `app.llm`."""

from __future__ import annotations

from app.llm import _heuristic_parse, _heuristic_refine


def test_parse_extracts_thai_lunch_and_cheap_takeout_dinner() -> None:
    plan = _heuristic_parse(
        "I want to see all the major tourist attractions, stop at a thai "
        "restaurant for lunch and a cheap take-out place for dinner."
    )
    cats = {s.category for s in plan.slots}
    assert "thai_restaurant" in cats
    assert "takeout" in cats
    assert plan.free_slots > 0

    takeout = next(s for s in plan.slots if s.category == "takeout")
    assert takeout.price_tier == 1


def test_parse_detects_travel_mode() -> None:
    walk = _heuristic_parse("walking tour of paris")
    assert walk.travel_mode == "walking"
    drive = _heuristic_parse("driving tour please")
    assert drive.travel_mode == "driving"
    bike = _heuristic_parse("bike around the old town")
    assert bike.travel_mode == "bicycling"


def test_parse_max_stops_from_count() -> None:
    plan = _heuristic_parse("show me 4 attractions")
    assert plan.max_stops == 4


def test_refine_appends_new_slots() -> None:
    base = _heuristic_parse("see major tourist attractions")
    refined = _heuristic_refine(base, "also a cafe in the morning")
    cats = {s.category for s in refined.slots}
    assert "cafe" in cats


def test_refine_drops_categories() -> None:
    base = _heuristic_parse("museum and cafe")
    assert {s.category for s in base.slots} == {"museum", "cafe"}
    refined = _heuristic_refine(base, "skip the museum")
    cats = {s.category for s in refined.slots}
    assert "museum" not in cats
    assert "cafe" in cats


def test_parse_time_window_sets_available_minutes() -> None:
    plan = _heuristic_parse("I'm free from 9am to 9pm for a walking tour")
    assert plan.available_minutes == 12 * 60
    assert plan.max_stops >= 6


def test_refine_updates_time_window() -> None:
    base = _heuristic_parse("tourist attractions in madrid")
    refined = _heuristic_refine(base, "actually I only have 10am-3pm")
    assert refined.available_minutes == 5 * 60
