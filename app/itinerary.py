"""Top-level orchestration: turn a request into an `Itinerary`."""

from __future__ import annotations

import logging
import re

from .curation import geocode_city_center, geocode_place, infer_city_name
from .llm import plan_direct_tour, refine_direct_tour
from .models import IntentPlan, Itinerary, OpenAIDirectStop, Place, PlanRequest, ScheduleSlot
from .places import _enrich_destination_profiles
from .route import assemble_itinerary, compute_route_metrics, haversine_m, normalize_place_label
from .sessions import SessionRecord

logger = logging.getLogger(__name__)


async def build_itinerary(req: PlanRequest) -> tuple[Itinerary, IntentPlan]:
    origin = await _resolve_origin(req)
    city_hint = (req.city or "").strip() or None
    city_label = (city_hint or (await infer_city_name(origin[0], origin[1]))).strip()
    if not city_label:
        city_label = "downtown"
    travel_mode = req.mode or "walking"
    schedule_window = _parse_schedule_window(req.query)
    hint_minutes: int | None = None
    if schedule_window is not None:
        hint_minutes = schedule_window[1] - schedule_window[0]

    direct = await plan_direct_tour(
        query=req.query,
        city_label=city_label,
        origin=origin,
        travel_mode=travel_mode,
        hint_available_minutes=hint_minutes,
    )
    merged_mode = req.mode or direct.travel_mode
    radius_m = req.radius_m if req.radius_m is not None else direct.radius_m
    radius_m = max(500, min(30000, int(radius_m)))
    available_minutes = (
        direct.available_minutes if direct.available_minutes is not None else hint_minutes
    )

    places = await _geocode_direct_stops(direct.stops, city_label, origin, radius_m)
    if not places:
        raise ValueError(
            "No places matched your request after geocoding. Try naming the city, "
            "widening the radius, or simplifying the venue list."
        )

    intent = IntentPlan(
        travel_mode=merged_mode,
        max_stops=len(places),
        radius_m=radius_m,
        slots=[],
        free_slots=0,
        available_minutes=available_minutes,
    )
    return await _itinerary_from_resolved_places(places, intent, origin, req.query)


async def refine_itinerary(
    record: SessionRecord, instruction: str
) -> tuple[Itinerary, IntentPlan]:
    origin = (record.itinerary.origin_lat, record.itinerary.origin_lng)
    city_hint = (record.city or "").strip() or None
    city_label = (city_hint or (await infer_city_name(origin[0], origin[1]))).strip()
    if not city_label:
        city_label = "downtown"
    combined_query = f"{record.query}\nRefinement request: {instruction}"
    prior_stops = [(s.place.name, s.place.category) for s in record.itinerary.stops]

    direct = await refine_direct_tour(
        query=record.query,
        city_label=city_label,
        origin=origin,
        travel_mode=record.intent.travel_mode,
        prior_stops=prior_stops,
        instruction=instruction,
        hint_available_minutes=record.intent.available_minutes,
    )
    radius_m = max(500, min(30000, int(direct.radius_m)))
    available_minutes = (
        direct.available_minutes
        if direct.available_minutes is not None
        else record.intent.available_minutes
    )

    places = await _geocode_direct_stops(direct.stops, city_label, origin, radius_m)
    if not places:
        raise ValueError(
            "No places matched after refining. Try a simpler change or different venues."
        )

    intent = IntentPlan(
        travel_mode=direct.travel_mode,
        max_stops=len(places),
        radius_m=radius_m,
        slots=[],
        free_slots=0,
        available_minutes=available_minutes,
    )
    return await _itinerary_from_resolved_places(places, intent, origin, combined_query)


async def _geocode_direct_stops(
    stops: list[OpenAIDirectStop],
    city_label: str,
    origin: tuple[float, float],
    radius_m: int,
) -> list[Place]:
    """Resolve planned names to coordinates; keep LLM order; dedupe labels."""

    city = (city_label or "").strip()
    out: list[Place] = []
    seen: set[str] = set()
    for i, s in enumerate(stops):
        if len(out) >= 12:
            break
        coords = await geocode_place(s.name, city)
        if coords is None:
            logger.info("Skipping ungeocodable stop: %s", s.name[:80])
            continue
        if haversine_m(origin, coords) > radius_m:
            logger.info("Skipping stop outside radius: %s", s.name[:80])
            continue
        label = normalize_place_label(s.name)
        if label in seen:
            continue
        seen.add(label)
        slug = re.sub(r"[^a-z0-9]+", "-", s.name.lower())[:48].strip("-") or "stop"
        out.append(
            Place(
                id=f"plan-{i}-{slug}",
                name=s.name.strip(),
                lat=coords[0],
                lng=coords[1],
                category=(s.category or "attraction")[:80],
                description=None,
                rating=None,
                popularity=max(0.1, 1.0 - i * 0.05),
                is_anchor=i < 3,
                address=None,
                image_url=None,
                source="osm",
                time_of_day=s.time_of_day,
            )
        )
    if out:
        await _enrich_destination_profiles(out, city=city or None)
    return out


async def _itinerary_from_resolved_places(
    places: list[Place],
    intent: IntentPlan,
    origin: tuple[float, float],
    query: str,
) -> tuple[Itinerary, IntentPlan]:
    intent = intent.model_copy(update={"max_stops": len(places)})
    distance_m, duration_s = await compute_route_metrics(origin, places, intent.travel_mode)
    visit_s = _estimate_visit_duration_s(places)
    total_estimated_s = duration_s + visit_s
    schedule_window = _parse_schedule_window(query)
    itinerary = assemble_itinerary(
        origin,
        places,
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
    """Plain-text stop list for Shortcuts / legacy clients (no mileage or schedule)."""

    if not itinerary.stops:
        return "Couldn't find anywhere to send you. Try a broader request."

    lines = [
        f"{i + 1}. **{s.place.name}**"
        + (f" - {s.place.description}" if s.place.description else "")
        + (f"  ({s.arrive_after})" if s.arrive_after != "any" else "")
        for i, s in enumerate(itinerary.stops)
    ]
    return "\n".join(lines)


def itinerary_schedule_slots(itinerary: Itinerary) -> list[ScheduleSlot]:
    """Suggested time blocks for each stop (same logic as the former sample schedule)."""

    if not itinerary.stops:
        return []
    n = len(itinerary.stops)
    start = (
        itinerary.schedule_start_minute if itinerary.schedule_start_minute is not None else 9 * 60
    )
    target_total = (
        int(itinerary.target_duration_s // 60)
        if itinerary.target_duration_s is not None
        else int(itinerary.estimated_total_duration_s // 60)
    )
    if target_total <= 0:
        target_total = max(180, n * 70)
    travel_each = int((itinerary.total_duration_s / 60) / max(n, 1))
    current = start
    slots: list[ScheduleSlot] = []
    for idx, stop in enumerate(itinerary.stops):
        visit = _visit_minutes_for_category(stop.place.category)
        block = max(30, travel_each + visit)
        if idx == n - 1 and itinerary.schedule_end_minute is not None:
            block = max(30, itinerary.schedule_end_minute - current)
        end = current + block
        display_start = _round_to_half_hour(current)
        display_end = _round_to_half_hour(end)
        slots.append(
            ScheduleSlot(
                time_start=_fmt_clock(display_start),
                time_end=_fmt_clock(display_end),
                place_name=stop.place.name,
            )
        )
        current = end
        if current - start >= target_total:
            break
    return slots


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
    """Markdown-friendly lines (used by tests and any plain-text consumers)."""

    return [
        f"- {s.time_start} to {s.time_end}: {s.place_name}"
        for s in itinerary_schedule_slots(itinerary)
    ]


def _round_to_half_hour(total_minutes: int) -> int:
    return int(round(total_minutes / 30.0) * 30)
