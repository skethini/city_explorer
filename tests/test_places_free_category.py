"""Tests for free-pool category inference (restaurant vs attraction)."""

from __future__ import annotations

from app.models import Slot
from app.places import _free_pool_curated_category


def test_free_pool_restaurant_for_indian_restaurant_query_no_slots() -> None:
    q = "I want to visit the best Indian restaurants in the city"
    assert _free_pool_curated_category([], q) == "restaurant"


def test_free_pool_attraction_when_slots_present() -> None:
    slots = [Slot(category="museum", time_of_day="any")]
    assert _free_pool_curated_category(slots, "Indian restaurants") == "attraction"


def test_free_pool_attraction_generic_sightseeing() -> None:
    assert _free_pool_curated_category([], "top museums and parks") == "attraction"
