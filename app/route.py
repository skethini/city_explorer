"""OSRM-based route metrics and itinerary assembly."""

from __future__ import annotations

import logging
import math

import httpx

from .config import settings
from .models import Itinerary, ItineraryStop, Place

logger = logging.getLogger(__name__)


OSRM_PROFILE = {
    "walking": "foot",
    "driving": "car",
    "bicycling": "bike",
    "transit": "car",  # OSRM has no transit; approximate with driving
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


def normalize_place_label(name: str) -> str:
    """Stable label so the same venue under different synthetic ids still dedupes."""

    return " ".join(name.strip().casefold().split())


def _chain_length(points: list[tuple[float, float]]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


async def compute_route_metrics(
    origin: tuple[float, float],
    stops: list[Place],
    travel_mode: str,
) -> tuple[float, float]:
    """Return `(total_distance_m, total_duration_s)` from OSRM, or Haversine estimates."""

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
    estimated_visit_duration_s: float = 0.0,
    estimated_total_duration_s: float = 0.0,
    target_duration_s: float | None = None,
    schedule_start_minute: int | None = None,
    schedule_end_minute: int | None = None,
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
        estimated_visit_duration_s=estimated_visit_duration_s,
        estimated_total_duration_s=estimated_total_duration_s,
        target_duration_s=target_duration_s,
        schedule_start_minute=schedule_start_minute,
        schedule_end_minute=schedule_end_minute,
    )
