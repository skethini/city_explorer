"""Top-level orchestration: turn a request into an `Itinerary`."""

from __future__ import annotations

import logging

from .llm import parse_intent, refine_intent
from .models import IntentPlan, Itinerary, PlanRequest
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

    return await _itinerary_from_intent(intent, (req.lat, req.lng))


async def refine_itinerary(
    record: SessionRecord, instruction: str
) -> tuple[Itinerary, IntentPlan]:
    new_intent = await refine_intent(record.intent, record.itinerary, instruction)
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
    itinerary = assemble_itinerary(
        origin,
        ordered,
        intent.travel_mode,
        distance_m=distance_m,
        duration_s=duration_s,
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
    suffix = (
        f"~{distance_km:.1f} km, ~{duration_min:.0f} min by {itinerary.travel_mode}"
        if itinerary.total_distance_m > 0
        else f"by {itinerary.travel_mode}"
    )
    return "\n".join(lines) + f"\n\n{suffix}"
