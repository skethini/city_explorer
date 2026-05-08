"""Place discovery: OpenStreetMap (Overpass) for attractions and Foursquare
for restaurants / category-specific search.

Both clients degrade gracefully — if the Foursquare key is missing or
Overpass is unreachable we return whatever we managed to gather. The result
is a list of `Place` objects with a synthetic `popularity` score in [0, 1].
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .config import settings
from .curation import (
    condense_wiki_extract_fallback,
    fetch_place_profile,
    finalize_visitor_sentence,
    geocode_place,
    infer_city_name,
    recommend_walkable_place_names,
    visitor_one_sentence_blurbs,
)
from .models import Place, Slot
from .route import haversine_m, normalize_place_label

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


async def _city_label_for_places(lat: float, lng: float, city_hint: str | None) -> str:
    """Prefer the client-provided city name; otherwise reverse-geocode."""

    hinted = (city_hint or "").strip()
    if hinted:
        return hinted
    return (await infer_city_name(lat, lng)).strip()


async def find_candidates_for_slot(
    slot: Slot,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    query: str,
    limit: int = 10,
    city_hint: str | None = None,
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
            city_hint=city_hint,
        )
    return await _foursquare_search(
        slot, lat, lng, radius_m, limit=limit, city_hint=city_hint
    )


async def find_top_curated_walkable(
    query: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    category: str,
    limit: int = 12,
    city_hint: str | None = None,
) -> list[Place]:
    """LLM-curated walkable stops for `free_slots` (attractions, restaurants, etc.)."""

    return await _curated_walkable_search(
        query=query,
        category=category,
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        limit=limit,
        city_hint=city_hint,
    )


async def find_top_attractions(
    query: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int = 12,
    city_hint: str | None = None,
) -> list[Place]:
    """LLM-curated top walkable attractions for `free_slots`."""

    return await find_top_curated_walkable(
        query,
        lat,
        lng,
        radius_m,
        category="attraction",
        limit=limit,
        city_hint=city_hint,
    )


def _free_pool_curated_category(slots: list[Slot], query: str) -> str:
    """When there are no explicit slots, infer whether the free pool should be food."""

    if slots:
        return "attraction"
    ql = (query or "").lower()
    if re.search(
        r"\b(indian|thai|italian|japanese|chinese|mexican|korean|vietnamese|greek|"
        r"spanish|french|turkish|lebanese|ethiopian|peruvian)\b",
        ql,
    ) and re.search(
        r"\b(food|cuisine|dining|kitchen|meals?|restaurants?|lunch|brunch|dinner|supper)\b",
        ql,
    ):
        return "restaurant"
    triggers = (
        "restaurant",
        "restaurants",
        "dining",
        "eatery",
        "eateries",
        "food tour",
        "places to eat",
        "where to eat",
        "lunch spot",
        "dinner spot",
        "brunch",
        "lunch",
        "dinner",
        "supper",
        "cuisine",
        "takeout",
        "take-out",
        "foodie",
        "to eat",
    )
    if any(t in ql for t in triggers):
        return "restaurant"
    return "attraction"


async def _curated_walkable_search(
    *,
    query: str,
    category: str,
    lat: float,
    lng: float,
    radius_m: int,
    limit: int,
    city_hint: str | None = None,
) -> list[Place]:
    city = await _city_label_for_places(lat, lng, city_hint)
    city_for_llm = city or "this destination"
    names = await recommend_walkable_place_names(
        query=query, city=city_for_llm, limit=max(limit * 2, 8), category_hint=category
    )

    places: list[Place] = []
    seen_labels: set[str] = set()
    for idx, name in enumerate(names):
        label = normalize_place_label(name)
        if label in seen_labels:
            continue
        coords = await geocode_place(name, city)
        if coords is None:
            continue
        plat, plng = coords
        distance = haversine_m((lat, lng), (plat, plng))
        if distance > radius_m:
            continue
        popularity = max(0.1, 1.0 - (idx * 0.06))
        slug_city = (city or "area").lower().replace(" ", "-")
        slug_name = name.lower().replace(" ", "-")
        places.append(
            Place(
                id=f"curated-{slug_city}-{idx}-{slug_name}",
                name=name,
                lat=plat,
                lng=plng,
                category=category,
                description=None,
                rating=None,
                popularity=popularity,
                is_anchor=idx < 3,
                address=None,
                image_url=None,
                source="osm",
            )
        )
        seen_labels.add(label)
        if len(places) >= limit:
            break
    if places:
        await _enrich_destination_profiles(places, city=city)
        return places

    if category == "restaurant":
        ql = (query or "").lower()
        must: list[str] = []
        for needle, label in (
            ("indian", "Indian"),
            ("thai", "Thai"),
            ("italian", "Italian"),
            ("japanese", "Japanese"),
            ("sushi", "Sushi"),
            ("vegan", "vegan"),
            ("vegetarian", "vegetarian"),
        ):
            if needle in ql and label not in must:
                must.append(label)
        slot = Slot(category="restaurant", time_of_day="any", must_include=must)
        osm_food = await _osm_food_search(
            slot, lat, lng, radius_m, limit=limit, city_hint=city_hint
        )
        if osm_food:
            logger.warning(
                "Curated restaurant search empty for city=%s; using OSM food results.",
                city,
            )
            return osm_food

    # Final safety net: if LLM/geocoding pipeline is unavailable, fall back to
    # Overpass so we still return a workable route.
    logger.warning(
        "Curated walkable search returned no results for city=%s category=%s; "
        "falling back to Overpass.",
        city,
        category,
    )
    return await _overpass_search(
        category, lat, lng, radius_m, limit=limit, city_hint=city_hint
    )


async def _overpass_search(
    category: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int,
    city_hint: str | None = None,
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
        name = tags.get("name:en") or tags.get("name")
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
            description=None,
            rating=None,
            popularity=popularity,
            address=tags.get("addr:full") or _join_address(tags),
            image_url=None,
            source="osm",
        )
        places.append(place)

    places.sort(key=lambda p: p.popularity, reverse=True)
    top = places[:limit]
    city = await _city_label_for_places(lat, lng, city_hint)
    await _enrich_destination_profiles(top, city=city or None)
    return top


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
    city_hint: str | None = None,
) -> list[Place]:
    if not settings.foursquare_api_key:
        logger.info("FOURSQUARE_API_KEY not set, falling back to OSM food search")
        return await _osm_food_search(
            slot, lat, lng, radius_m, limit=limit, city_hint=city_hint
        )

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
                description=None,
                rating=float(rating) if rating is not None else None,
                popularity=popularity,
                address=location.get("formatted_address"),
                image_url=None,
                source="foursquare",
                time_of_day=slot.time_of_day,
            )
        )

    places.sort(key=lambda p: (p.rating or 0, p.popularity), reverse=True)
    if places:
        out = places[:limit]
        city = await _city_label_for_places(lat, lng, city_hint)
        await _enrich_destination_profiles(out, city=city or None)
        return out
    return await _osm_food_search(
        slot, lat, lng, radius_m, limit=limit, city_hint=city_hint
    )


async def gather_candidates(
    slots: list[Slot],
    query: str,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    free_slots: int,
    per_slot_limit: int = 10,
    city_hint: str | None = None,
) -> dict[str, list[Place]]:
    """Run all slot lookups in parallel plus the generic attractions list."""

    free_curated_category = _free_pool_curated_category(slots, query)
    tasks: list[tuple[str, asyncio.Task[list[Place]]]] = []
    for i, slot in enumerate(slots):
        key = f"slot:{i}:{slot.category}"
        tasks.append((
            key,
            asyncio.create_task(
                find_candidates_for_slot(
                    slot,
                    lat,
                    lng,
                    radius_m,
                    query=query,
                    limit=per_slot_limit,
                    city_hint=city_hint,
                )
            ),
        ))
    if free_slots > 0:
        tasks.append((
            "free",
            asyncio.create_task(
                find_top_curated_walkable(
                    query,
                    lat,
                    lng,
                    radius_m,
                    category=free_curated_category,
                    limit=free_slots * 4,
                    city_hint=city_hint,
                )
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


async def _osm_food_search(
    slot: Slot,
    lat: float,
    lng: float,
    radius_m: int,
    *,
    limit: int,
    city_hint: str | None = None,
) -> list[Place]:
    cuisine_hint = None
    if "thai" in slot.category:
        cuisine_hint = "thai"
    elif "italian" in slot.category:
        cuisine_hint = "italian"
    elif "indian" in slot.category:
        cuisine_hint = "indian"
    elif "sushi" in slot.category or "japanese" in slot.category:
        cuisine_hint = "japanese|sushi"
    elif "vegan" in slot.category:
        cuisine_hint = "vegan"
    elif "vegetarian" in slot.category:
        cuisine_hint = "vegetarian"
    if cuisine_hint is None:
        joined = " ".join(slot.must_include).lower()
        if "indian" in joined:
            cuisine_hint = "indian"

    if cuisine_hint:
        filters = [f'amenity="restaurant"][cuisine~"{cuisine_hint}"']
    elif slot.category in {"cafe", "coffee"}:
        filters = ['amenity="cafe"']
    elif slot.category == "bar":
        filters = ['amenity="bar"']
    elif slot.category == "takeout":
        filters = ['amenity~"fast_food|restaurant"']
    else:
        filters = ['amenity~"restaurant|cafe|fast_food"']

    parts: list[str] = []
    for f in filters:
        parts.append(f"node[{f}](around:{radius_m},{lat},{lng});")
        parts.append(f"way[{f}](around:{radius_m},{lat},{lng});")
    body = f"[out:json][timeout:25];({''.join(parts)});out tags center {limit * 5};"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.overpass_url,
                data={"data": body},
                headers={
                    "User-Agent": "city-explorer/0.1 (+https://github.com/skethini/city_explorer)",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("OSM food fallback failed for %s: %s", slot.category, exc)
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
        places.append(
            Place(
                id=f"osm-food-{el.get('type')}-{el.get('id')}",
                name=name,
                lat=float(plat),
                lng=float(plng),
                category=slot.category,
                description=None,
                rating=None,
                popularity=0.5 + (0.2 if tags.get("cuisine") else 0.0),
                address=tags.get("addr:full") or _join_address(tags),
                image_url=None,
                source="osm",
                time_of_day=slot.time_of_day,
            )
        )
    places.sort(key=lambda p: p.popularity, reverse=True)
    out = places[:limit]
    city = await _city_label_for_places(lat, lng, city_hint)
    await _enrich_destination_profiles(out, city=city or None)
    return out


def _listing_context_notes(place: Place) -> str | None:
    """Non-Wikipedia facts for the blurb model when no article excerpt is available."""

    parts: list[str] = []
    if place.address:
        parts.append(f"Address: {place.address}")
    if place.rating is not None:
        parts.append(f"Venue score about {place.rating:.1f}/10")
    parts.append(f"Venue type: {place.category.replace('_', ' ')}")
    if place.source == "foursquare":
        parts.append("Source: venue directory listing")
    elif place.source == "osm":
        parts.append("Source: OpenStreetMap community data")
    return " | ".join(parts) if parts else None


def _is_food_category(category: str) -> bool:
    return any(
        token in category
        for token in (
            "restaurant",
            "cafe",
            "bar",
            "takeout",
            "food",
            "pizza",
            "sushi",
            "ramen",
            "burger",
            "vegan",
            "vegetarian",
            "street_food",
        )
    )


async def _enrich_destination_profiles(places: list[Place], city: str | None) -> None:
    """Attach one-sentence visitor summaries (OpenAI) and optional Wikipedia thumbnails."""

    if not places:
        return

    profiles = await asyncio.gather(
        *[fetch_place_profile(p.name, city or "") for p in places]
    )
    rows: list[tuple[str, str, str, str | None]] = []
    for i, p in enumerate(places):
        wiki_excerpt, _img = profiles[i]
        note: str | None = None
        if wiki_excerpt and wiki_excerpt.strip():
            note = wiki_excerpt.strip()
        else:
            listing = _listing_context_notes(p)
            if listing:
                note = listing
        rows.append((p.name, city or "", p.category, note))

    blurbs = await visitor_one_sentence_blurbs(rows)

    for i, place in enumerate(places):
        wiki_excerpt, image = profiles[i]
        desc = blurbs[i]
        if not desc and wiki_excerpt:
            desc = condense_wiki_extract_fallback(wiki_excerpt)
        if desc:
            desc = finalize_visitor_sentence(desc)
        else:
            desc = None
        updated = place.model_copy(update={"description": desc})
        if image:
            updated = updated.model_copy(update={"image_url": image})
        places[i] = updated
