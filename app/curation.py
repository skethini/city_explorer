"""Geocoding, city autocomplete, and stop enrichment helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from openai import AsyncOpenAI

from .config import settings
from .google_geocoding import google_geocode_forward, google_reverse_locality
from .models import CitySuggestion

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_UA = "city-explorer/0.1 (+https://github.com/skethini/city_explorer)"
# https://operations.osmfoundation.org/policies/nominatim/ — one request per second.
_NOMINATIM_MIN_INTERVAL_S = 1.1
_nom_lock = asyncio.Lock()
_nom_next_ok = 0.0

OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"

async def _nominatim_throttle() -> None:
    """Serialize Nominatim usage to respect the public usage policy."""

    global _nom_next_ok
    async with _nom_lock:
        now = time.monotonic()
        wait = _nom_next_ok - now
        if wait > 0:
            await asyncio.sleep(wait)
        _nom_next_ok = time.monotonic() + _NOMINATIM_MIN_INTERVAL_S


async def _nominatim_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET with spacing and limited retries on HTTP 429."""

    last: httpx.Response | None = None
    for attempt in range(1, 5):
        await _nominatim_throttle()
        last = await client.get(url, params=params, headers={"User-Agent": NOMINATIM_UA})
        if last.status_code == 429:
            retry_after = last.headers.get("Retry-After")
            sleep_s = 2.0 * attempt
            if retry_after:
                try:
                    sleep_s = min(float(retry_after), 60.0)
                except ValueError:
                    pass
            logger.warning(
                "Nominatim rate-limited (429); sleeping %.1fs before retry %s/4",
                sleep_s,
                attempt,
            )
            await asyncio.sleep(sleep_s)
            continue
        last.raise_for_status()
        return last
    assert last is not None
    last.raise_for_status()
    return last


def _geocode_search_query(name: str, city: str) -> str:
    """Avoid bogus disambiguators that break Nominatim search."""

    name = name.strip()
    city = city.strip()
    bogus = {"", "the current city"}
    if not city or city.lower() in bogus:
        return name
    return f"{name}, {city}"


async def infer_city_name(lat: float, lng: float) -> str:
    """Infer city name from coordinates (empty string if unknown)."""

    if settings.google_maps_api_key:
        label = await google_reverse_locality(lat, lng)
        if label:
            return label

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _nominatim_get(
                client,
                NOMINATIM_REVERSE,
                params={"lat": lat, "lon": lng, "format": "jsonv2"},
            )
            payload = resp.json()
        addr = payload.get("address") or {}
        label = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("county")
            or ""
        )
        return str(label).strip()
    except Exception as exc:
        logger.warning("Failed to reverse-geocode city: %s", exc)
        return ""


async def geocode_place(name: str, city: str) -> tuple[float, float] | None:
    """Resolve place name to coordinates (Google when configured, else Nominatim)."""

    query = _geocode_search_query(name, city)
    if settings.google_maps_api_key:
        coords = await google_geocode_forward(query)
        if coords is not None:
            return coords

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _nominatim_get(
                client,
                NOMINATIM_SEARCH,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 1,
                    "accept-language": "en",
                },
            )
            rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        return float(row["lat"]), float(row["lon"])
    except Exception as exc:
        logger.warning("Failed to geocode '%s': %s", name, exc)
        return None


async def search_city_suggestions(name: str, *, limit: int = 10) -> list[CitySuggestion]:
    """Return ranked place-name matches for a city picker (Open-Meteo Geocoding API)."""

    name = name.strip()
    if len(name) < 2:
        return []
    cap = max(1, min(int(limit), 20))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                OPEN_METEO_GEOCODE,
                params={"name": name, "count": cap, "language": "en", "format": "json"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo city search failed for %r: %s", name, exc)
        return []

    out: list[CitySuggestion] = []
    for row in payload.get("results") or []:
        nm = (row.get("name") or "").strip()
        if not nm:
            continue
        lat = row.get("latitude")
        lng = row.get("longitude")
        if lat is None or lng is None:
            continue
        country_raw = row.get("country")
        country = country_raw.strip() if isinstance(country_raw, str) else None
        if country == "":
            country = None
        admin1_raw = row.get("admin1")
        admin1 = admin1_raw.strip() if isinstance(admin1_raw, str) else None
        if admin1 == "":
            admin1 = None
        parts = [nm]
        if admin1:
            parts.append(admin1)
        if country:
            parts.append(country)
        label = ", ".join(parts)
        out.append(
            CitySuggestion(
                label=label,
                name=nm,
                country=country,
                admin1=admin1,
                latitude=float(lat),
                longitude=float(lng),
            )
        )
    return out


async def geocode_city_center(city: str) -> tuple[float, float] | None:
    """Resolve a city name to a usable center coordinate."""

    if settings.google_maps_api_key:
        coords = await google_geocode_forward(city.strip())
        if coords is not None:
            return coords

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
                resp = await _nominatim_get(client, NOMINATIM_SEARCH, params=params)
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


async def fetch_place_profile(name: str, city: str) -> tuple[str | None, str | None]:
    """Return (short_description, image_url) from English Wikipedia when possible."""

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            # Search the most relevant wiki title first.
            search = await client.get(
                WIKI_SEARCH,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": f"{name} {city}",
                    "srlimit": 1,
                    "format": "json",
                },
                headers={"User-Agent": NOMINATIM_UA},
            )
            search.raise_for_status()
            hits = ((search.json().get("query") or {}).get("search") or [])
            if not hits:
                return None, None
            title = hits[0].get("title")
            if not title:
                return None, None

            summary = await client.get(
                f"{WIKI_SUMMARY}/{quote(title, safe='')}",
                headers={"User-Agent": NOMINATIM_UA},
            )
            summary.raise_for_status()
            payload = summary.json()
            desc = (payload.get("extract") or "").strip() or None
            # Keep a generous slice for downstream summarization (batch LLM); cap for prompt size.
            if desc and len(desc) > 2000:
                desc = desc[:1997].rsplit(" ", 1)[0] + "..."
            image = ((payload.get("thumbnail") or {}).get("source")) or None
            return desc, image
    except Exception as exc:
        logger.debug("Wikipedia profile lookup failed for %s: %s", name, exc)
        return None, None


_VISITOR_BLURB_SYSTEM = (
    "You write one-line blurbs for a walking-tour app.\n\n"
    "Rules for EACH blurb:\n"
    "- Exactly ONE clear English sentence.\n"
    "- At most 220 characters.\n"
    "- Visitor-focused: what it is and why someone stops there.\n"
    "- Context notes may be a Wikipedia excerpt OR factual listing metadata "
    "(address, ratings, venue type). Use only supported facts; phrase in your own "
    "words; do not copy distinctive Wikipedia lead wording.\n"
    "- When context notes are (none), infer a short description from the place name "
    "and category only (no invented historical claims).\n"
    "- End with proper punctuation (. ? or !)."
)


def finalize_visitor_sentence(text: str, *, max_len: int = 220) -> str:
    """Keep a single concise sentence within max_len."""

    t = " ".join(text.strip().split())
    if not t:
        return t
    # Prefer first complete sentence if the model returned several.
    for sep in (". ", "? ", "! "):
        idx = t.find(sep)
        if idx != -1 and idx + 1 < len(t):
            t = t[: idx + 1].strip()
            break
    if len(t) > max_len:
        t = t[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return t


def condense_wiki_extract_fallback(extract: str, *, max_len: int = 220) -> str:
    """Offline: compress Wikipedia extract to roughly one short sentence."""

    t = re.sub(r"\s+", " ", extract).strip()
    if not t:
        return t
    m = re.match(r"^(.+?[.!?])(\s|$)", t)
    first = m.group(1) if m else t
    if len(first) > max_len:
        first = first[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return first


async def visitor_one_sentence_blurbs(
    rows: list[tuple[str, str, str, str | None]],
) -> list[str | None]:
    """Paraphrase each stop into one visitor sentence via OpenAI.

    `rows` are (place_name, city, category, context_notes) where context_notes is
    a Wikipedia excerpt, structured listing hints, or None.
    Returns the same length; entries are None when the API key is missing or the call fails.
    """

    if not rows:
        return []
    if not settings.openai_api_key:
        return [None] * len(rows)

    lines: list[str] = []
    for i, (name, city, category, excerpt) in enumerate(rows, start=1):
        note = (excerpt or "").strip().replace("\n", " ")
        if len(note) > 900:
            note = note[:897].rsplit(" ", 1)[0] + "..."
        if note:
            lines.append(
                f"{i}. place_name={name!r} | city={city!r} | category={category!r} | "
                f"context_notes={note!r}"
            )
        else:
            lines.append(
                f"{i}. place_name={name!r} | city={city!r} | category={category!r} | "
                "context_notes=(none)"
            )

    user = (
        f"Return ONLY JSON: {{\"blurbs\": [<string>, ...]}} with exactly {len(rows)} strings "
        "in the same order as the numbered items below.\n\n"
        + "\n".join(lines)
    )

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _VISITOR_BLURB_SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.35,
        )
        content = resp.choices[0].message.content or "{}"
        payload: dict[str, Any] = json.loads(content)
        raw_blurbs = payload.get("blurbs") or []
        out: list[str | None] = []
        for i in range(len(rows)):
            if i < len(raw_blurbs) and raw_blurbs[i]:
                out.append(finalize_visitor_sentence(str(raw_blurbs[i])))
            else:
                out.append(None)
        return out
    except Exception as exc:
        logger.warning("Visitor blurb batch failed: %s", exc)
        return [None] * len(rows)
