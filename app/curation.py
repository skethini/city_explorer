"""Curated walkable attraction recommendations (LLM-first)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_UA = "city-explorer/0.1 (+https://github.com/skethini/city_explorer)"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

async def infer_city_name(lat: float, lng: float) -> str:
    """Infer city name from coordinates."""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                NOMINATIM_REVERSE,
                params={"lat": lat, "lon": lng, "format": "jsonv2"},
                headers={"User-Agent": NOMINATIM_UA},
            )
            resp.raise_for_status()
            payload = resp.json()
        addr = payload.get("address") or {}
        return (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("county")
            or "the current city"
        )
    except Exception as exc:
        logger.warning("Failed to reverse-geocode city: %s", exc)
        return "the current city"


async def recommend_walkable_place_names(
    query: str,
    city: str,
    *,
    limit: int,
    category_hint: str | None = None,
) -> list[str]:
    """Use OpenAI to produce best-walkable-place names."""

    if settings.openai_api_key:
        prompt = (
            f"User request: {query}\n"
            f"City: {city}\n"
            f"Category hint: {category_hint or 'general attractions'}\n\n"
            "Return ONLY JSON: {\"places\": [\"name1\", \"name2\", ...]}.\n"
            f"Pick the best walkable, high-interest places in {city} for tourists. "
            "Prefer central places and avoid duplicates. Use English place names."
        )
        try:
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a city walking-tour curator."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = resp.choices[0].message.content or "{}"
            payload: dict[str, Any] = json.loads(content)
            places = [str(x).strip() for x in payload.get("places", []) if str(x).strip()]
            if places:
                return places[:limit]
        except Exception as exc:
            logger.warning("LLM curation failed; returning no curated names: %s", exc)

    return []


async def geocode_place(name: str, city: str) -> tuple[float, float] | None:
    """Resolve place name to coordinates using Nominatim."""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                NOMINATIM_SEARCH,
                params={
                    "q": f"{name}, {city}",
                    "format": "jsonv2",
                    "limit": 1,
                    "accept-language": "en",
                },
                headers={"User-Agent": NOMINATIM_UA},
            )
            resp.raise_for_status()
            rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        return float(row["lat"]), float(row["lon"])
    except Exception as exc:
        logger.warning("Failed to geocode '%s': %s", name, exc)
        return None


async def geocode_city_center(city: str) -> tuple[float, float] | None:
    """Resolve a city name to a usable center coordinate."""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Nominatim can be strict with `featuretype=city`; use a couple of
            # progressively looser queries so common inputs like "Madrid"
            # resolve reliably.
            query_variants = [
                {"q": city, "format": "jsonv2", "limit": 1, "featuretype": "city"},
                {"q": city, "format": "jsonv2", "limit": 1},
                {"city": city, "format": "jsonv2", "limit": 1},
            ]
            rows: list[dict[str, Any]] = []
            for params in query_variants:
                resp = await client.get(
                    NOMINATIM_SEARCH,
                    params=params,
                    headers={"User-Agent": NOMINATIM_UA},
                )
                resp.raise_for_status()
                rows = resp.json()
                if rows:
                    break
        if not rows:
            raise ValueError("No Nominatim rows")
        row = rows[0]
        return float(row["lat"]), float(row["lon"])
    except Exception as exc:
        logger.warning("Nominatim city geocoding failed for '%s': %s", city, exc)

    # Secondary non-hardcoded fallback provider.
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                OPEN_METEO_GEOCODE,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results") or []
            if not results:
                return None
            row = results[0]
            return float(row["latitude"]), float(row["longitude"])
    except Exception as exc:
        logger.warning("Open-Meteo city geocoding failed for '%s': %s", city, exc)
        return None
