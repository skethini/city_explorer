"""Place discovery: OpenStreetMap (Overpass) for attractions and Foursquare
for restaurants / category-specific search.

Both clients degrade gracefully — if the Foursquare key is missing or
Overpass is unreachable we return whatever we managed to gather. The result
is a list of `Place` objects with a synthetic `popularity` score in [0, 1].
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import settings
from .curation import geocode_place, infer_city_name, recommend_walkable_place_names
from .models import Place, Slot
from .route import haversine_m

logger = logging.getLogger(__name__)


OSM_ATTRACTION_FILTERS = [
    'tourism~"attraction|museum|gallery|viewpoint|artwork|zoo|aquarium|theme_park"',
    'historic~"monument|memorial|castle|ruins|archaeological_site"',
    'leisure~"park|garden"',
]


FSQ_CATEGORY_IDS: dict[str, list[str]] = {
    "restaurant": ["13065"],
    "thai_restaurant": ["13302"],
    "italian_restaurant": ["13236"],
    "japanese_restaurant": ["13263"],
    "sushi_restaurant": ["13276"],
    "ramen_restaurant": ["13265"],
    "pizzeria": ["13064"],
    "burger_joint": ["13031"],
    "vegan_restaurant": ["13377"],
    "vegetarian_restaurant": ["13377"],
    "brunch_spot": ["13278"],
    "breakfast_spot": ["13002"],
    "cafe": ["13032", "13035"],
    "bar": ["13003"],
    "cocktail_bar": ["13029"],
    "takeout": ["13145"],
    "street_food": ["13059"],
    "market": ["17069"],
}


_LEISURE_CATEGORIES = {"park", "viewpoint", "gallery", "museum", "garden"}


async def find_candidates_for_slot(
    slot: Slot,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    query: str,
    limit: int = 10,
) -> list[Place]:
    """Resolve a single intent slot into a ranked list of candidate places."""

    if slot.category in _LEISURE_CATEGORIES or slot.category in {"attraction", "historic"}:
        return await _curated_walkable_search(
            query=query,
            category=slot.category,
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            limit=limit,
        )
    return await _foursquare_search(slot, lat, lng, radius_m, limit=limit)


async def find_top_attractions(
    query: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int = 12,
) -> list[Place]:
    """LLM-curated top walkable attractions for `free_slots`."""
    return await _curated_walkable_search(
        query=query,
        category="attraction",
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        limit=limit,
    )


async def _curated_walkable_search(
    *,
    query: str,
    category: str,
    lat: float,
    lng: float,
    radius_m: int,
    limit: int,
) -> list[Place]:
    city = await infer_city_name(lat, lng)
    names = await recommend_walkable_place_names(
        query=query, city=city, limit=max(limit * 2, 8), category_hint=category
    )

    places: list[Place] = []
    for idx, name in enumerate(names):
        coords = await geocode_place(name, city)
        if coords is None:
            continue
        plat, plng = coords
        distance = haversine_m((lat, lng), (plat, plng))
        if distance > radius_m:
            continue
        popularity = max(0.1, 1.0 - (idx * 0.06))
        places.append(
            Place(
                id=f"curated-{city.lower().replace(' ', '-')}-{idx}-{name.lower().replace(' ', '-')}",
                name=name,
                lat=plat,
                lng=plng,
                category=category,
                rating=None,
                popularity=popularity,
                is_anchor=idx < 3,
                address=None,
                source="osm",
            )
        )
        if len(places) >= limit:
            break
    if places:
        return places

    # Final safety net: if LLM/geocoding pipeline is unavailable, fall back to
    # Overpass so we still return a workable route.
    logger.warning(
        "Curated walkable search returned no results for city=%s category=%s; "
        "falling back to Overpass.",
        city,
        category,
    )
    return await _overpass_search(category, lat, lng, radius_m, limit=limit)


async def _overpass_search(
    category: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int,
) -> list[Place]:
    if category in {"park", "garden"}:
        filters = ['leisure~"park|garden"']
    elif category == "viewpoint":
        filters = ['tourism="viewpoint"']
    elif category == "museum":
        filters = ['tourism="museum"']
    elif category == "gallery":
        filters = ['tourism="gallery"']
    elif category == "historic":
        filters = ['historic~"monument|memorial|castle|ruins|archaeological_site"']
    else:
        filters = OSM_ATTRACTION_FILTERS

    parts: list[str] = []
    for f in filters:
        parts.append(f"node[{f}](around:{radius_m},{lat},{lng});")
        parts.append(f"way[{f}](around:{radius_m},{lat},{lng});")
    # Overpass QL expects the element limit directly after `out` and before
    # output modifiers. `out center tags 48;` is invalid and returns no data.
    body = f"[out:json][timeout:25];({''.join(parts)});out tags center {limit * 4};"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.overpass_url,
                data={"data": body},
                headers={
                    # Overpass rejects some generic client signatures with 406.
                    "User-Agent": "city-explorer/0.1 (+https://github.com/skethini/city_explorer)",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("Overpass query failed for category=%s: %s", category, exc)
        return []

    places: list[Place] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        if el.get("type") == "node":
            plat, plng = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            plat, plng = center.get("lat"), center.get("lon")
        if plat is None or plng is None:
            continue
        popularity = _osm_popularity(tags)
        place = Place(
            id=f"osm-{el.get('type')}-{el.get('id')}",
            name=name,
            lat=float(plat),
            lng=float(plng),
            category=tags.get("tourism") or tags.get("historic") or tags.get("leisure") or category,
            rating=None,
            popularity=popularity,
            address=tags.get("addr:full") or _join_address(tags),
            source="osm",
        )
        places.append(place)

    places.sort(key=lambda p: p.popularity, reverse=True)
    return places[:limit]


def _osm_popularity(tags: dict[str, str]) -> float:
    """Synthetic popularity score in [0, 1] derived from OSM tags."""

    score = 0.0
    if tags.get("wikipedia"):
        score += 0.5
    if tags.get("wikidata"):
        score += 0.25
    if tags.get("heritage"):
        score += 0.15
    if tags.get("tourism") == "attraction":
        score += 0.1
    if tags.get("historic") in {"castle", "monument", "memorial"}:
        score += 0.1
    if tags.get("name:en"):
        score += 0.05
    return min(score, 1.0)


def _join_address(tags: dict[str, str]) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
    ]
    cleaned = [p for p in parts if p]
    return " ".join(cleaned) if cleaned else None


async def _foursquare_search(
    slot: Slot,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int,
) -> list[Place]:
    if not settings.foursquare_api_key:
        logger.info("FOURSQUARE_API_KEY not set, returning empty restaurant list")
        return []

    params: dict[str, Any] = {
        "ll": f"{lat},{lng}",
        "radius": radius_m,
        "limit": min(limit, 50),
        "sort": "RATING",
    }
    cats = FSQ_CATEGORY_IDS.get(slot.category)
    if cats:
        params["categories"] = ",".join(cats)
    else:
        params["query"] = slot.category.replace("_", " ")

    keywords = list(slot.must_include)
    if keywords:
        params["query"] = " ".join(keywords + ([params["query"]] if "query" in params else []))

    if slot.price_tier:
        params["min_price"] = slot.price_tier
        params["max_price"] = slot.price_tier

    headers = {
        "accept": "application/json",
        "Authorization": settings.foursquare_api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.foursquare.com/v3/places/search",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("Foursquare query failed for %s: %s", slot.category, exc)
        return []

    places: list[Place] = []
    for r in payload.get("results", []):
        geo = (r.get("geocodes") or {}).get("main") or {}
        plat, plng = geo.get("latitude"), geo.get("longitude")
        if plat is None or plng is None:
            continue
        rating = r.get("rating")
        popularity = float(r.get("popularity") or 0)
        if rating is not None:
            popularity = max(popularity, rating / 10.0)
        location = r.get("location") or {}
        places.append(
            Place(
                id=f"fsq-{r.get('fsq_id')}",
                name=r.get("name") or "Unnamed",
                lat=float(plat),
                lng=float(plng),
                category=slot.category,
                rating=float(rating) if rating is not None else None,
                popularity=popularity,
                address=location.get("formatted_address"),
                source="foursquare",
                time_of_day=slot.time_of_day,
            )
        )

    places.sort(key=lambda p: (p.rating or 0, p.popularity), reverse=True)
    return places[:limit]


async def gather_candidates(
    slots: list[Slot],
    query: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    free_slots: int,
    per_slot_limit: int = 10,
) -> dict[str, list[Place]]:
    """Run all slot lookups in parallel plus the generic attractions list."""

    tasks: list[tuple[str, asyncio.Task[list[Place]]]] = []
    for i, slot in enumerate(slots):
        key = f"slot:{i}:{slot.category}"
        tasks.append((key, asyncio.create_task(
            find_candidates_for_slot(slot, lat, lng, radius_m, query=query, limit=per_slot_limit)
        )))
    if free_slots > 0:
        tasks.append((
            "free",
            asyncio.create_task(
                find_top_attractions(query, lat, lng, radius_m, limit=free_slots * 4)
            ),
        ))

    results: dict[str, list[Place]] = {}
    for key, task in tasks:
        try:
            results[key] = await task
        except Exception as exc:
            logger.warning("Candidate fetch %s failed: %s", key, exc)
            results[key] = []
    return results
