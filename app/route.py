"""Stop selection, route ordering, and OSRM-based duration estimation."""

from __future__ import annotations

import logging
import math
from typing import Iterable

import httpx

from .config import settings
from .models import IntentPlan, Itinerary, ItineraryStop, Place, Slot, TimeOfDay

logger = logging.getLogger(__name__)


TIME_ORDER: dict[TimeOfDay, int] = {
    "morning": 0,
    "lunch": 1,
    "afternoon": 2,
    "dinner": 3,
    "evening": 4,
    "any": 99,
}


OSRM_PROFILE = {
    "walking": "foot",
    "driving": "car",
    "bicycling": "bike",
    "transit": "car",  # OSRM has no transit; the user's app will route this
}


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres."""
    lat1, lng1 = a
    lat2, lng2 = b
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def select_places(
    intent: IntentPlan,
    candidates_by_key: dict[str, list[Place]],
    origin: tuple[float, float],
) -> list[Place]:
    """Pick concrete places for each slot, then top up with `free_slots` attractions.

    Avoids picking the same place twice and prefers candidates close to the
    origin to keep the final route compact.
    """

    chosen: list[Place] = []
    seen_ids: set[str] = set()

    def pick(candidates: Iterable[Place], slot: Slot | None) -> Place | None:
        ranked = sorted(
            candidates,
            key=lambda p: (
                -(p.rating or 0),
                -p.popularity,
                haversine_m(origin, (p.lat, p.lng)),
            ),
        )
        for p in ranked:
            if p.id in seen_ids:
                continue
            chosen_place = p.model_copy()
            if slot is not None:
                chosen_place.time_of_day = slot.time_of_day
            seen_ids.add(p.id)
            return chosen_place
        return None

    for i, slot in enumerate(intent.slots):
        key = f"slot:{i}:{slot.category}"
        cands = candidates_by_key.get(key, [])
        place = pick(cands, slot)
        if place is not None:
            chosen.append(place)

    if intent.free_slots > 0:
        free_pool = candidates_by_key.get("free", [])
        remaining = max(0, intent.max_stops - len(chosen))
        wanted = min(intent.free_slots, remaining)
        for _ in range(wanted):
            place = pick(free_pool, None)
            if place is None:
                break
            chosen.append(place)

    if len(chosen) > intent.max_stops:
        chosen = chosen[: intent.max_stops]
    return chosen


def order_stops(origin: tuple[float, float], places: list[Place]) -> list[Place]:
    """Order stops respecting `time_of_day` anchors, then apply 2-opt within
    flexible segments."""

    if not places:
        return []

    fixed = sorted(
        [p for p in places if p.time_of_day != "any"],
        key=lambda p: TIME_ORDER[p.time_of_day],
    )
    flexible = [p for p in places if p.time_of_day == "any"]

    if not fixed:
        return _two_opt(origin, _nearest_neighbor(origin, flexible))

    anchors: list[Place | None] = [None] + list(fixed) + [None]
    buckets: list[list[Place]] = [[] for _ in range(len(fixed) + 1)]

    for place in flexible:
        best_idx = 0
        best_delta = math.inf
        for i in range(len(buckets)):
            left = (origin if anchors[i] is None else (anchors[i].lat, anchors[i].lng))
            right_anchor = anchors[i + 1]
            current_chain = [left] + [(p.lat, p.lng) for p in buckets[i]]
            if right_anchor is not None:
                base = _chain_length(current_chain + [(right_anchor.lat, right_anchor.lng)])
                with_new = _chain_length(
                    current_chain
                    + [(place.lat, place.lng), (right_anchor.lat, right_anchor.lng)]
                )
            else:
                base = _chain_length(current_chain)
                with_new = _chain_length(current_chain + [(place.lat, place.lng)])
            delta = with_new - base
            if delta < best_delta:
                best_delta = delta
                best_idx = i
        buckets[best_idx].append(place)

    ordered: list[Place] = []
    for i, bucket in enumerate(buckets):
        if bucket:
            left = origin if anchors[i] is None else (anchors[i].lat, anchors[i].lng)
            right = (
                None
                if anchors[i + 1] is None
                else (anchors[i + 1].lat, anchors[i + 1].lng)
            )
            ordered.extend(_two_opt_segment(left, bucket, right))
        if i < len(fixed):
            ordered.append(fixed[i])
    return ordered


def _chain_length(points: list[tuple[float, float]]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def _nearest_neighbor(origin: tuple[float, float], places: list[Place]) -> list[Place]:
    remaining = list(places)
    ordered: list[Place] = []
    cur = origin
    while remaining:
        idx = min(range(len(remaining)), key=lambda i: haversine_m(cur, (remaining[i].lat, remaining[i].lng)))
        nxt = remaining.pop(idx)
        ordered.append(nxt)
        cur = (nxt.lat, nxt.lng)
    return ordered


def _two_opt(origin: tuple[float, float], places: list[Place]) -> list[Place]:
    return _two_opt_segment(origin, places, None)


def _two_opt_segment(
    left: tuple[float, float],
    places: list[Place],
    right: tuple[float, float] | None,
    *,
    max_iter: int = 20,
) -> list[Place]:
    """Classic 2-opt over a segment with fixed left/right anchors."""

    if len(places) < 2:
        return list(places)

    coords = [left] + [(p.lat, p.lng) for p in places]
    if right is not None:
        coords.append(right)

    def total() -> float:
        return _chain_length(coords)

    improved = True
    iters = 0
    while improved and iters < max_iter:
        improved = False
        iters += 1
        for i in range(1, len(coords) - 2):
            for j in range(i + 1, len(coords) - 1):
                new_coords = coords[:i] + coords[i:j + 1][::-1] + coords[j + 1:]
                old = (
                    haversine_m(coords[i - 1], coords[i])
                    + haversine_m(coords[j], coords[j + 1])
                )
                new = (
                    haversine_m(new_coords[i - 1], new_coords[i])
                    + haversine_m(new_coords[j], new_coords[j + 1])
                )
                if new + 1e-6 < old:
                    coords = new_coords
                    improved = True

    inner = coords[1:-1] if right is not None else coords[1:]
    by_coord = {(p.lat, p.lng): p for p in places}
    return [by_coord[c] for c in inner]


async def compute_route_metrics(
    origin: tuple[float, float],
    stops: list[Place],
    travel_mode: str,
) -> tuple[float, float]:
    """Return `(total_distance_m, total_duration_s)` from OSRM, or Haversine
    estimates if OSRM is unreachable."""

    if not stops:
        return 0.0, 0.0

    profile = OSRM_PROFILE.get(travel_mode, "foot")
    coords = ";".join(
        f"{lng},{lat}" for lat, lng in [origin] + [(s.lat, s.lng) for s in stops]
    )
    url = f"{settings.osrm_url.rstrip('/')}/route/v1/{profile}/{coords}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params={"overview": "false"})
            resp.raise_for_status()
            data = resp.json()
        route = (data.get("routes") or [{}])[0]
        return float(route.get("distance", 0.0)), float(route.get("duration", 0.0))
    except Exception as exc:
        logger.warning("OSRM call failed (%s); using Haversine estimate", exc)
        chain = [origin] + [(s.lat, s.lng) for s in stops]
        dist = _chain_length(chain)
        speed_mps = {"foot": 1.4, "bike": 4.5, "car": 11.0}.get(profile, 1.4)
        return dist, dist / speed_mps


def assemble_itinerary(
    origin: tuple[float, float],
    stops: list[Place],
    travel_mode: str,
    *,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
) -> Itinerary:
    return Itinerary(
        origin_lat=origin[0],
        origin_lng=origin[1],
        travel_mode=travel_mode,  # type: ignore[arg-type]
        stops=[
            ItineraryStop(place=p, order=i + 1, arrive_after=p.time_of_day)
            for i, p in enumerate(stops)
        ],
        total_distance_m=distance_m,
        total_duration_s=duration_s,
    )
