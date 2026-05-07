"""Top-level orchestration: turn a request into an `Itinerary`."""

from __future__ import annotations

import logging
import re

from .curation import geocode_city_center
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
    else:
        intent = intent.model_copy(update={"radius_m": _infer_radius_m(intent)})
    intent = _fit_stops_to_available_time(intent)

    origin = await _resolve_origin(req)
    return await _itinerary_from_intent(intent, origin, req.query)


async def refine_itinerary(
    record: SessionRecord, instruction: str
) -> tuple[Itinerary, IntentPlan]:
    new_intent = await refine_intent(record.intent, record.itinerary, instruction)
    new_intent = _fit_stops_to_available_time(new_intent)
    origin = (record.itinerary.origin_lat, record.itinerary.origin_lng)
    return await _itinerary_from_intent(new_intent, origin, instruction)


async def _itinerary_from_intent(
    intent: IntentPlan, origin: tuple[float, float], query: str
) -> tuple[Itinerary, IntentPlan]:
    candidates = await gather_candidates(
        intent.slots,
        query,
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
    schedule_window = _parse_schedule_window(query)
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
        schedule_start_minute=schedule_window[0] if schedule_window else None,
        schedule_end_minute=schedule_window[1] if schedule_window else None,
    )
    return itinerary, intent


def summarize_itinerary(itinerary: Itinerary) -> str:
    """One-paragraph human summary, displayable in a Shortcut alert."""

    if not itinerary.stops:
        return "Couldn't find anywhere to send you. Try a broader request."

    lines = [
        f"{i + 1}. {s.place.name}"
        + (f" - {s.place.description}" if s.place.description else "")
        + (f"  ({s.arrive_after})" if s.arrive_after != "any" else "")
        for i, s in enumerate(itinerary.stops)
    ]
    distance_miles = itinerary.total_distance_m * 0.000621371
    duration_min = itinerary.total_duration_s / 60
    visit_min = itinerary.estimated_visit_duration_s / 60
    total_min = itinerary.estimated_total_duration_s / 60
    suffix = (
        f"~{distance_miles:.1f} miles, ~{duration_min:.0f} min by {itinerary.travel_mode}"
        if itinerary.total_distance_m > 0
        else f"by {itinerary.travel_mode}"
    )
    details = f"Walk/ride time: ~{duration_min:.0f} min, visit time: ~{visit_min:.0f} min, total: ~{total_min:.0f} min."
    if itinerary.target_duration_s is not None:
        target_min = itinerary.target_duration_s / 60
        details += f" Target window: ~{target_min:.0f} min."
    schedule = _build_sample_schedule(itinerary)
    schedule_block = "\n".join(schedule) if schedule else "Schedule unavailable."
    return "\n".join(lines) + f"\n\n{suffix}\n{details}\n\nSample schedule:\n{schedule_block}"


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

    total_minutes = 0
    for stop in stops:
        total_minutes += _visit_minutes_for_category(stop.category)
    return float(total_minutes * 60)


def _visit_minutes_for_category(category: str) -> int:
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
    return category_minutes.get(category, 55)


async def _resolve_origin(req: PlanRequest) -> tuple[float, float]:
    if req.lat is not None and req.lng is not None:
        return req.lat, req.lng
    if req.city:
        coords = await geocode_city_center(req.city)
        if coords is not None:
            return coords
        raise ValueError(f"Could not resolve city '{req.city}' to coordinates.")
    raise ValueError("Provide either `city` or both `lat` and `lng`.")


def _infer_radius_m(intent: IntentPlan) -> int:
    if intent.available_minutes is None:
        return 8000 if intent.travel_mode == "walking" else 12000
    speed_m_per_min = {
        "walking": 80,
        "bicycling": 250,
        "driving": 500,
        "transit": 350,
    }.get(intent.travel_mode, 80)
    # Keep the search area smaller than total potential travel distance so
    # selected stops remain reasonably clusterable in the available time.
    inferred = int(intent.available_minutes * speed_m_per_min * 0.35)
    return max(2500, min(30000, inferred))


def _parse_schedule_window(query: str) -> tuple[int, int] | None:
    m = re.search(
        r"(?:from\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|till)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        query.lower(),
    )
    if not m:
        return None
    sh, sm, sap, eh, em, eap = m.groups()
    start = _to_minutes(int(sh), int(sm or 0), sap)
    end = _to_minutes(int(eh), int(em or 0), eap)
    if end <= start:
        end += 24 * 60
    return start, end


def _to_minutes(hour: int, minute: int, ampm: str | None) -> int:
    if ampm is None:
        hour = max(0, min(23, hour))
        return hour * 60 + minute
    h = hour % 12
    if ampm == "pm":
        h += 12
    return h * 60 + minute


def _fmt_clock(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    h24 = total_minutes // 60
    minute = total_minutes % 60
    suffix = "AM" if h24 < 12 else "PM"
    h12 = h24 % 12
    if h12 == 0:
        h12 = 12
    return f"{h12:02d}:{minute:02d} {suffix}"


def _build_sample_schedule(itinerary: Itinerary) -> list[str]:
    if not itinerary.stops:
        return []
    n = len(itinerary.stops)
    start = itinerary.schedule_start_minute if itinerary.schedule_start_minute is not None else 9 * 60
    target_total = (
        int(itinerary.target_duration_s // 60)
        if itinerary.target_duration_s is not None
        else int(itinerary.estimated_total_duration_s // 60)
    )
    if target_total <= 0:
        target_total = max(180, n * 70)
    travel_each = int((itinerary.total_duration_s / 60) / max(n, 1))
    current = start
    lines: list[str] = []
    for idx, stop in enumerate(itinerary.stops):
        visit = _visit_minutes_for_category(stop.place.category)
        block = max(30, travel_each + visit)
        if idx == n - 1 and itinerary.schedule_end_minute is not None:
            block = max(30, itinerary.schedule_end_minute - current)
        end = current + block
        lines.append(f"- {_fmt_clock(current)} to {_fmt_clock(end)}: {stop.place.name}")
        current = end
        if current - start >= target_total:
            break
    return lines
