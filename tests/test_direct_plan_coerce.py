"""Tests for OpenAI direct tour JSON coercion (no network)."""

from __future__ import annotations

import pytest

from app.llm import _coerce_direct_plan


def test_coerce_direct_plan_parses_stops_and_clamps_radius() -> None:
    plan = _coerce_direct_plan(
        {
            "travel_mode": "walking",
            "available_minutes": 360,
            "radius_m": 999999,
            "stops": [
                {"name": "  Museo del Prado  ", "time_of_day": "morning", "category": "museum"},
                {"name": "Retiro Park", "category": "park"},
            ],
        },
        fallback_travel_mode="walking",
    )
    assert plan.travel_mode == "walking"
    assert plan.available_minutes == 360
    assert plan.radius_m == 30000
    assert len(plan.stops) == 2
    assert plan.stops[0].name == "Museo del Prado"
    assert plan.stops[0].time_of_day == "morning"


def test_coerce_direct_plan_rejects_empty_stops() -> None:
    with pytest.raises(ValueError, match="no stops"):
        _coerce_direct_plan({"stops": []}, fallback_travel_mode="walking")
