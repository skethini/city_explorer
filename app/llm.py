"""OpenAI-backed intent parsing and refinement."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import settings
from .models import (
    IntentPlan,
    Itinerary,
    OpenAIDirectPlan,
    OpenAIDirectStop,
    Slot,
    TravelMode,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert a tourist's free-text request into a JSON plan.

Return ONLY a JSON object with these keys:
  travel_mode: one of walking, driving, bicycling, transit (default walking)
  max_stops: integer 1..12 (default 6)
  radius_m:  integer 500..30000 (default 5000)
  available_minutes: integer or null (e.g. 720 if user says 9am to 9pm)
  free_slots: integer >= 0, top-rated attractions to fill in besides explicit slots
  slots: array of objects with keys
    category:    short snake_case category (e.g. museum, park, viewpoint,
                 thai_restaurant, takeout, cafe, bar)
    must_include: optional array of keywords to require
    time_of_day: morning|lunch|afternoon|dinner|evening|any
    price_tier:  1..4 or null  (1 cheap, 4 luxury)
    notes:       free-text, optional

Rules:
- Prefer turning user intent into destination discovery rather than strict categories.
- When the user asks mainly for dining, cuisines, or restaurants, set `slots` to one
  or more food categories (e.g. restaurant with must_include for the cuisine) instead
  of relying only on generic `free_slots`.
- Otherwise keep `slots` empty unless they want strict category sequencing.
- Set `free_slots` high enough to fill `max_stops` together with `slots`.
- Be concise; never invent fields not listed above.
"""

REFINE_PROMPT = """You are editing an EXISTING itinerary based on a single
follow-up instruction. Return the same JSON schema as before, but representing
the FULL desired plan after the edit (not just the diff). Preserve the user's
prior preferences unless the instruction overrides them.
"""

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


def _coerce_plan(payload: dict[str, Any]) -> IntentPlan:
    slots_raw = payload.get("slots") or []
    slots = [Slot(**s) for s in slots_raw]
    return IntentPlan(
        travel_mode=payload.get("travel_mode", "walking"),
        max_stops=int(payload.get("max_stops", 6)),
        radius_m=int(payload.get("radius_m", 5000)),
        slots=slots,
        free_slots=int(payload.get("free_slots", 0)),
        available_minutes=(
            int(payload["available_minutes"])
            if payload.get("available_minutes") is not None
            else None
        ),
    )


async def parse_intent(query: str) -> IntentPlan:
    """Turn a free-text query into a structured `IntentPlan` via OpenAI only."""

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for planning.")
    plan = await _call_openai(SYSTEM_PROMPT, query)
    return _normalize_openai_plan(plan)


async def refine_intent(prior_intent: IntentPlan, prior: Itinerary, instruction: str) -> IntentPlan:
    """Turn a follow-up instruction + prior itinerary into a new `IntentPlan` via OpenAI."""

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for refining.")
    user = json.dumps(
        {
            "previous_intent": prior_intent.model_dump(),
            "previous_itinerary": [
                {"name": s.place.name, "category": s.place.category}
                for s in prior.stops
            ],
            "instruction": instruction,
        }
    )
    llm_plan = await _call_openai(REFINE_PROMPT, user)
    return _normalize_openai_plan(llm_plan)


def _normalize_openai_plan(plan: IntentPlan) -> IntentPlan:
    """Clamp capacity; preserve model slots so food-specific plans still resolve."""

    max_stops = max(1, min(12, int(plan.max_stops)))
    slots = list(plan.slots)[:max_stops]
    room = max(0, max_stops - len(slots))
    free_slots = max(0, min(int(plan.free_slots), room))
    if not slots and free_slots == 0 and room > 0:
        free_slots = room
    return plan.model_copy(
        update={
            "slots": slots,
            "max_stops": max_stops,
            "free_slots": free_slots,
        }
    )


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


async def _call_openai(system: str, user: str) -> IntentPlan:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "{}"
    payload = json.loads(_strip_code_fences(content))
    return _coerce_plan(payload)


_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("thai", "thai_restaurant"),
    ("italian", "italian_restaurant"),
    ("sushi", "sushi_restaurant"),
    ("ramen", "ramen_restaurant"),
    ("pizza", "pizzeria"),
    ("burger", "burger_joint"),
    ("vegan", "vegan_restaurant"),
    ("vegetarian", "vegetarian_restaurant"),
    ("brunch", "brunch_spot"),
    ("breakfast", "breakfast_spot"),
    ("coffee", "cafe"),
    ("cafe", "cafe"),
    ("bar", "bar"),
    ("cocktail", "cocktail_bar"),
    ("museum", "museum"),
    ("gallery", "gallery"),
    ("park", "park"),
    ("viewpoint", "viewpoint"),
    ("market", "market"),
    ("take-out", "takeout"),
    ("takeout", "takeout"),
    ("takeaway", "takeout"),
    ("street food", "street_food"),
]


_TIME_KEYWORDS: list[tuple[str, str]] = [
    ("breakfast", "morning"),
    ("brunch", "morning"),
    ("morning", "morning"),
    ("lunch", "lunch"),
    ("afternoon", "afternoon"),
    ("dinner", "dinner"),
    ("evening", "evening"),
    ("night", "evening"),
]

_STOPWORDS = {
    "add",
    "include",
    "visit",
    "more",
    "less",
    "near",
    "around",
    "please",
    "want",
    "with",
    "for",
    "from",
    "that",
    "this",
    "then",
    "also",
    "stop",
    "route",
    "city",
    "tour",
}


def _heuristic_parse(query: str) -> IntentPlan:
    """Best-effort parser used when no LLM key is configured."""

    q = query.lower()
    slots: list[Slot] = []
    used_categories: set[str] = set()

    for keyword, category in _CATEGORY_KEYWORDS:
        if keyword in q and category not in used_categories:
            time_of_day = "any"
            for tk, tval in _TIME_KEYWORDS:
                if tk in q and (tk in keyword or _close(q, keyword, tk)):
                    time_of_day = tval
                    break
            price_tier: int | None = None
            if "cheap" in q or "budget" in q:
                price_tier = 1
            elif "fancy" in q or "upscale" in q or "luxury" in q:
                price_tier = 4
            slots.append(
                Slot(
                    category=category,
                    time_of_day=time_of_day,  # type: ignore[arg-type]
                    price_tier=price_tier,  # type: ignore[arg-type]
                )
            )
            used_categories.add(category)

    free_slots = 0
    if any(kw in q for kw in ("attraction", "tourist", "see", "explore", "highlight")):
        free_slots = max(3, 6 - len(slots))

    travel_mode = "walking"
    if "drive" in q or "driving" in q or "car" in q:
        travel_mode = "driving"
    elif "bike" in q or "cycling" in q:
        travel_mode = "bicycling"
    elif "transit" in q or "metro" in q or "bus" in q or "subway" in q:
        travel_mode = "transit"

    max_stops = 6
    m = re.search(r"(\d{1,2})\s+(?:stops|places|spots|attractions)", q)
    if m:
        max_stops = max(1, min(12, int(m.group(1))))
    available_minutes = _parse_available_minutes(q)
    if available_minutes is not None and not m:
        # Rough fit: ~75 min per stop + ~15 min transfer when mostly walking.
        suggested = max(2, min(12, round(available_minutes / 90)))
        max_stops = suggested

    return IntentPlan(
        travel_mode=travel_mode,  # type: ignore[arg-type]
        max_stops=max_stops,
        radius_m=5000,
        slots=slots,
        free_slots=free_slots,
        available_minutes=available_minutes,
    )


def _close(haystack: str, a: str, b: str, window: int = 25) -> bool:
    """True if substrings `a` and `b` appear within `window` chars of each other."""
    ai = haystack.find(a)
    bi = haystack.find(b)
    if ai < 0 or bi < 0:
        return False
    return abs(ai - bi) <= window


def _heuristic_refine(prior: IntentPlan, instruction: str) -> IntentPlan:
    """Apply a follow-up instruction without an LLM."""

    new_intent = prior.model_copy(deep=True)
    instr = instruction.lower()
    is_drop = any(kw in instr for kw in ("skip", "remove", "drop", "no more"))

    dropped: set[str] = set()
    if is_drop:
        keep = []
        for slot in new_intent.slots:
            cat_words = slot.category.replace("_", " ")
            if slot.category in instr or cat_words in instr:
                dropped.add(slot.category)
                continue
            keep.append(slot)
        new_intent.slots = keep

    if not is_drop:
        parsed = _heuristic_parse(instruction)
        for slot in parsed.slots:
            if slot.category in dropped:
                continue
            if not any(s.category == slot.category for s in new_intent.slots):
                new_intent.slots.append(slot)
        if not parsed.slots:
            must_words = [
                w
                for w in re.findall(r"[a-zA-Z]{4,}", instr)
                if w not in _STOPWORDS
            ][:3]
            if must_words:
                new_intent.slots.append(
                    Slot(category="attraction", must_include=must_words, notes=instruction)
                )
            else:
                new_intent.free_slots = min(12, new_intent.free_slots + 2)

    if "fewer" in instr:
        new_intent.max_stops = max(1, new_intent.max_stops - 1)
    elif "more" in instr:
        new_intent.max_stops = min(12, new_intent.max_stops + 1)
    minutes = _parse_available_minutes(instr)
    if minutes is not None:
        new_intent.available_minutes = minutes

    return new_intent


def _enforce_instruction(
    llm_plan: IntentPlan,
    prior: IntentPlan,
    instruction: str,
) -> IntentPlan:
    """Ensure refine instructions are reflected even when LLM output is vague."""

    # Reuse the deterministic instruction parser as a guard-rail.
    merged = llm_plan.model_copy(deep=True)
    heuristic_delta = _heuristic_refine(prior, instruction)

    # Merge in any explicit categories requested in instruction.
    existing = {s.category for s in merged.slots}
    for slot in heuristic_delta.slots:
        if slot.category not in existing:
            merged.slots.append(slot)
            existing.add(slot.category)

    # Preserve updated available time from heuristic extraction.
    if heuristic_delta.available_minutes is not None:
        merged.available_minutes = heuristic_delta.available_minutes

    instr = instruction.lower()
    requested_fewer = "fewer" in instr
    requested_more = "more" in instr

    # Prevent accidental shrink of itinerary capacity on generic refine prompts
    # like "add a Thai lunch".
    if not requested_fewer and not requested_more:
        merged.max_stops = max(merged.max_stops, prior.max_stops)
    elif requested_more:
        merged.max_stops = max(merged.max_stops, heuristic_delta.max_stops)
    elif requested_fewer:
        merged.max_stops = min(merged.max_stops, heuristic_delta.max_stops)

    # Keep enough capacity to include newly-added slots.
    merged.max_stops = max(merged.max_stops, len(merged.slots))
    merged.free_slots = max(
        0,
        max(prior.free_slots if not requested_fewer else 0, merged.max_stops - len(merged.slots)),
    )
    return merged


def _parse_available_minutes(text: str) -> int | None:
    """Parse simple ranges like `9am-9pm`, `from 10:30 am to 6 pm`."""

    match = re.search(
        r"(?:from\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:-|to|until|till)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        text,
    )
    if not match:
        return None
    sh, sm, sap, eh, em, eap = match.groups()
    start = _to_minutes_24h(int(sh), int(sm or 0), sap)
    end = _to_minutes_24h(int(eh), int(em or 0), eap)
    if end <= start:
        end += 24 * 60
    minutes = end - start
    if minutes < 30:
        return None
    return minutes


def _to_minutes_24h(hour_12: int, minute: int, ampm: str) -> int:
    hour = hour_12 % 12
    if ampm == "pm":
        hour += 12
    return hour * 60 + minute
