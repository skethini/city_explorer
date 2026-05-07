"""Top-level orchestration: turn a request into an `Itinerary`."""

from __future__ import annotations

import logging

from .llm import parse_intent, refine_intent
from .models import IntentPlan, Itinerary, Place, PlanRequest
from .places import gather_candidates
from .route import (
    assemble_itinerary,
    compute_route_metrics,
    order_stops,
    select_places,
)
from .sessions import SessionRecord

logger = logging.getLogger(__name__)


async def build_itinerary(req: PlanRequest) -> tuple[Itinerary, IntentPlan]:
    intent = await parse_intent(req.query)
    if req.mode is not None:
        intent = intent.model_copy(update={"travel_mode": req.mode})
    if req.radius_m is not None:
        intent = intent.model_copy(update={"radius_m": req.radius_m})
    intent = _fit_stops_to_available_time(intent)

    return await _itinerary_from_intent(intent, (req.lat, req.lng))


async def refine_itinerary(
    record: SessionRecord, instruction: str
) -> tuple[Itinerary, IntentPlan]:
    new_intent = await refine_intent(record.intent, record.itinerary, instruction)
    new_intent = _fit_stops_to_available_time(new_intent)
    origin = (record.itinerary.origin_lat, record.itinerary.origin_lng)
    return await _itinerary_from_intent(new_intent, origin)


async def _itinerary_from_intent(
    intent: IntentPlan, origin: tuple[float, float]
) -> tuple[Itinerary, IntentPlan]:
    candidates = await gather_candidates(
        intent.slots,
        origin[0],
        origin[1],
        intent.radius_m,
        free_slots=intent.free_slots,
    )
    chosen = select_places(intent, candidates, origin)
    if not chosen:
        raise ValueError(
            "No places matched your request. Try widening the radius or "
            "loosening category constraints."
        )
    ordered = order_stops(origin, chosen)
    distance_m, duration_s = await compute_route_metrics(origin, ordered, intent.travel_mode)
    visit_s = _estimate_visit_duration_s(ordered)
    total_estimated_s = duration_s + visit_s
    itinerary = assemble_itinerary(
        origin,
        ordered,
        intent.travel_mode,
        distance_m=distance_m,
        duration_s=duration_s,
        estimated_visit_duration_s=visit_s,
        estimated_total_duration_s=total_estimated_s,
        target_duration_s=(
            intent.available_minutes * 60 if intent.available_minutes is not None else None
        ),
    )
    return itinerary, intent


def summarize_itinerary(itinerary: Itinerary) -> str:
    """One-paragraph human summary, displayable in a Shortcut alert."""

    if not itinerary.stops:
        return "Couldn't find anywhere to send you. Try a broader request."

    lines = [
        f"{i + 1}. {s.place.name}"
        + (f"  ({s.arrive_after})" if s.arrive_after != "any" else "")
        for i, s in enumerate(itinerary.stops)
    ]
    distance_km = itinerary.total_distance_m / 1000
    duration_min = itinerary.total_duration_s / 60
    visit_min = itinerary.estimated_visit_duration_s / 60
    total_min = itinerary.estimated_total_duration_s / 60
    suffix = (
        f"~{distance_km:.1f} km, ~{duration_min:.0f} min by {itinerary.travel_mode}"
        if itinerary.total_distance_m > 0
        else f"by {itinerary.travel_mode}"
    )
    details = f"Walk/ride time: ~{duration_min:.0f} min, visit time: ~{visit_min:.0f} min, total: ~{total_min:.0f} min."
    if itinerary.target_duration_s is not None:
        target_min = itinerary.target_duration_s / 60
        details += f" Target window: ~{target_min:.0f} min."
    return "\n".join(lines) + f"\n\n{suffix}\n{details}"


def _fit_stops_to_available_time(intent: IntentPlan) -> IntentPlan:
    if intent.available_minutes is None:
        return intent
    per_stop = 75 if intent.travel_mode == "walking" else 60
    transfer = 15 if intent.travel_mode == "walking" else 10
    estimated = max(2, min(12, round(intent.available_minutes / (per_stop + transfer))))
    slots_count = len(intent.slots)
    max_stops = max(estimated, slots_count)
    free_slots = max(0, max_stops - slots_count)
    return intent.model_copy(
        update={
            "max_stops": max_stops,
            "free_slots": free_slots,
        }
    )


def _estimate_visit_duration_s(stops: list[Place]) -> float:
    """Approximate dwell time so we can target user time windows."""

    category_minutes = {
        "museum": 90,
        "gallery": 75,
        "park": 60,
        "viewpoint": 35,
        "historic": 60,
        "attraction": 60,
        "thai_restaurant": 70,
        "restaurant": 65,
        "cafe": 40,
        "takeout": 25,
    }
    total_minutes = 0
    for stop in stops:
        category = stop.category
        total_minutes += category_minutes.get(category, 55)
    return float(total_minutes * 60)
