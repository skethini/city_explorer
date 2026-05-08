"""Tests for OpenAI plan normalization (capacity clamping, slot preservation)."""

from __future__ import annotations

from app.llm import _normalize_openai_plan
from app.models import IntentPlan, Slot


def test_normalize_preserves_slots_and_clamps_free_slots() -> None:
    plan = IntentPlan(
        max_stops=6,
        free_slots=10,
        slots=[
            Slot(category="museum", time_of_day="any"),
            Slot(category="park", time_of_day="any"),
        ],
    )
    out = _normalize_openai_plan(plan)
    assert len(out.slots) == 2
    assert {s.category for s in out.slots} == {"museum", "park"}
    assert out.free_slots == 4


def test_normalize_truncates_excess_slots() -> None:
    many = [Slot(category="museum", time_of_day="any") for _ in range(10)]
    plan = IntentPlan(max_stops=4, free_slots=6, slots=many)
    out = _normalize_openai_plan(plan)
    assert len(out.slots) == 4
    assert out.free_slots == 0


def test_normalize_empty_slots_keeps_free_within_max_stops() -> None:
    plan = IntentPlan(max_stops=6, free_slots=6, slots=[])
    out = _normalize_openai_plan(plan)
    assert out.slots == []
    assert out.free_slots == 6
