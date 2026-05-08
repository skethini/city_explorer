"""Optional Google Geocoding API (forward + reverse).

When `GOOGLE_MAPS_API_KEY` is set, `curation` uses these instead of Nominatim
for place/city geocoding and reverse city lookup — avoiding public OSM
Nominatim rate limits on busy deployments.

Billing and quotas: https://developers.google.com/maps/documentation/geocoding/usage-and-billing
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

GOOGLE_GEOCODE_JSON = "https://maps.googleapis.com/maps/api/geocode/json"


def _locality_from_components(components: list[dict[str, Any]]) -> str:
    """Pick a human city-like label from Google address_components."""

    for want in ("locality", "postal_town"):
        for comp in components:
            if want in (comp.get("types") or []):
                name = (comp.get("long_name") or "").strip()
                if name:
                    return name
    for comp in components:
        types = comp.get("types") or []
        if "administrative_area_level_3" in types:
            name = (comp.get("long_name") or "").strip()
            if name:
                return name
    return ""


async def google_geocode_forward(query: str) -> tuple[float, float] | None:
    """Resolve a free-text address or place name to coordinates."""

    key = settings.google_maps_api_key
    if not key or not query.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                GOOGLE_GEOCODE_JSON,
                params={"address": query.strip(), "key": key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Google forward geocode HTTP error for %r: %s", query[:80], exc)
        return None

    status = data.get("status")
    if status != "OK" or not data.get("results"):
        logger.debug("Google forward geocode status=%s query=%r", status, query[:80])
        return None
    loc = data["results"][0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


async def google_reverse_locality(lat: float, lng: float) -> str:
    """Return a city / town label for coordinates, or empty string."""

    key = settings.google_maps_api_key
    if not key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                GOOGLE_GEOCODE_JSON,
                params={"latlng": f"{lat},{lng}", "key": key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Google reverse geocode failed: %s", exc)
        return ""

    if data.get("status") != "OK" or not data.get("results"):
        return ""
    components = data["results"][0].get("address_components") or []
    return _locality_from_components(components)
