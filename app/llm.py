"""OpenAI-backed direct tour planning and refinement."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import settings
from .models import OpenAIDirectPlan, OpenAIDirectStop, TravelMode

logger = logging.getLogger(__name__)

DIRECT_TOUR_SYSTEM = """You plan day tours as JSON only (no prose).

The user describes what they want. You output an ordered list of real venues in
the named city that fit their interests, time budget, and transport mode.

Return ONLY a JSON object with keys:
  travel_mode: one of walking, driving, bicycling, transit (default walking if unclear)
  available_minutes: integer total minutes for the outing if inferable from the user text, else null
  radius_m: integer 1500-25000 — search radius in meters from the map center for mode and time
  stops: array of objects, each with:
    name: string (specific venue name; add neighborhood or city for disambiguation when needed)
    time_of_day: one of morning, lunch, afternoon, dinner, evening, any
    category: short snake_case (e.g. museum, park, restaurant, cafe, historic, attraction)

Rules:
- 3 to 12 stops unless the user specifies otherwise; prefer fewer if time is short.
- Order stops in a sensible visit sequence near the city center for the stated mode.
- Use only venues plausible in the named city; avoid fictional names.
"""

REFINE_DIRECT_SYSTEM = """You revise an existing tour plan based on the user's new instruction.

Return ONLY JSON with the same keys as the planner: travel_mode, available_minutes,
radius_m, stops (each stop: name, time_of_day, category). Output the FULL new plan
after applying the instruction (not a diff). Preserve prior preferences unless the
instruction overrides them."""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


def _coerce_direct_plan(
    payload: dict[str, Any],
    *,
    fallback_travel_mode: TravelMode,
) -> OpenAIDirectPlan:
    raw_stops = payload.get("stops") or []
    stops: list[OpenAIDirectStop] = []
    for row in raw_stops[:12]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        tod_raw = row.get("time_of_day") or "any"
        tod = str(tod_raw).lower() if tod_raw else "any"
        if tod not in ("morning", "lunch", "afternoon", "dinner", "evening", "any"):
            tod = "any"
        cat = str(row.get("category") or "attraction").strip() or "attraction"
        stops.append(OpenAIDirectStop(name=name, time_of_day=tod, category=cat[:80]))
    if not stops:
        raise ValueError("The planner returned no stops; try a clearer city or request.")
    tm_raw = payload.get("travel_mode") or fallback_travel_mode
    tm = str(tm_raw).lower() if tm_raw else fallback_travel_mode
    if tm not in ("walking", "driving", "bicycling", "transit"):
        tm = fallback_travel_mode
    avail = payload.get("available_minutes")
    available_minutes: int | None = None
    if avail is not None:
        try:
            available_minutes = max(15, min(24 * 60, int(avail)))
        except (TypeError, ValueError):
            available_minutes = None
    try:
        radius_m = int(payload.get("radius_m") or 8000)
    except (TypeError, ValueError):
        radius_m = 8000
    radius_m = max(500, min(30000, radius_m))
    return OpenAIDirectPlan(
        travel_mode=tm,  # type: ignore[arg-type]
        available_minutes=available_minutes,
        radius_m=radius_m,
        stops=stops,
    )


async def plan_direct_tour(
    *,
    query: str,
    city_label: str,
    origin: tuple[float, float],
    travel_mode: TravelMode,
    hint_available_minutes: int | None,
) -> OpenAIDirectPlan:
    """Ask OpenAI for an ordered stop list; caller geocodes names."""

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for planning.")
    hint = ""
    if hint_available_minutes is not None:
        hint = (
            f"\nApproximate time window from the user text: about {hint_available_minutes} minutes."
        )
    user = (
        f"City / area label: {city_label}\n"
        f"Map center (lat, lng): {origin[0]:.5f}, {origin[1]:.5f}\n"
        f"Default transport mode (use if the query does not override): {travel_mode}\n"
        f"User request:\n{query.strip()}"
        f"{hint}"
    )
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": DIRECT_TOUR_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.25,
    )
    content = resp.choices[0].message.content or "{}"
    payload = json.loads(_strip_code_fences(content))
    return _coerce_direct_plan(payload, fallback_travel_mode=travel_mode)


async def refine_direct_tour(
    *,
    query: str,
    city_label: str,
    origin: tuple[float, float],
    travel_mode: TravelMode,
    prior_stops: list[tuple[str, str]],
    instruction: str,
    hint_available_minutes: int | None,
) -> OpenAIDirectPlan:
    """Revise the tour from natural language; caller geocodes the new names."""

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for refining.")
    payload_obj = {
        "original_request": query,
        "city": city_label,
        "center": {"lat": origin[0], "lng": origin[1]},
        "prior_stops": [{"name": n, "category": c} for n, c in prior_stops],
        "instruction": instruction,
        "hint_available_minutes": hint_available_minutes,
    }
    user = json.dumps(payload_obj)
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": REFINE_DIRECT_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.25,
    )
    content = resp.choices[0].message.content or "{}"
    payload = json.loads(_strip_code_fences(content))
    return _coerce_direct_plan(payload, fallback_travel_mode=travel_mode)
