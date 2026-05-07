"""LLM-backed intent parsing and refinement.

Falls back to a deterministic keyword parser when no API key is configured,
so the rest of the system (and the test suite) work offline.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import settings
from .models import IntentPlan, Itinerary, Slot

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert a tourist's free-text request into a JSON plan.

Return ONLY a JSON object with these keys:
  travel_mode: one of walking, driving, bicycling, transit (default walking)
  max_stops: integer 1..12 (default 6)
  radius_m:  integer 500..30000 (default 5000)
  free_slots: integer >= 0, top-rated attractions to fill in besides explicit slots
  slots: array of objects with keys
    category:    short snake_case category (e.g. museum, park, viewpoint,
                 thai_restaurant, takeout, cafe, bar)
    must_include: optional array of keywords to require
    time_of_day: morning|lunch|afternoon|dinner|evening|any
    price_tier:  1..4 or null  (1 cheap, 4 luxury)
    notes:       free-text, optional

Rules:
- If the user mentions "tourist attractions", "must-sees", or similar, set
  free_slots to fill out max_stops with top-rated places.
- Map "cheap" -> price_tier 1, "fancy/upscale" -> 4.
- Be concise; never invent fields not listed above.
"""

REFINE_PROMPT = """You are editing an EXISTING itinerary based on a single
follow-up instruction. Return the same JSON schema as before, but representing
the FULL desired plan after the edit (not just the diff). Preserve the user's
prior preferences unless the instruction overrides them.
"""


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
    )


async def parse_intent(query: str) -> IntentPlan:
    """Turn a free-text query into a structured `IntentPlan`."""

    if settings.openai_api_key:
        try:
            return await _call_openai(SYSTEM_PROMPT, query)
        except Exception as exc:  # pragma: no cover - depends on network
            logger.warning("LLM call failed, falling back to heuristic parser: %s", exc)
    return _heuristic_parse(query)


async def refine_intent(prior_intent: IntentPlan, prior: Itinerary, instruction: str) -> IntentPlan:
    """Turn a follow-up instruction + prior itinerary into a new `IntentPlan`."""

    if settings.openai_api_key:
        try:
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
            return await _call_openai(REFINE_PROMPT, user)
        except Exception as exc:  # pragma: no cover - depends on network
            logger.warning("LLM refine failed, falling back to heuristic merge: %s", exc)
    return _heuristic_refine(prior_intent, instruction)


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

    return IntentPlan(
        travel_mode=travel_mode,  # type: ignore[arg-type]
        max_stops=max_stops,
        radius_m=5000,
        slots=slots,
        free_slots=free_slots,
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
        for slot in _heuristic_parse(instruction).slots:
            if slot.category in dropped:
                continue
            if not any(s.category == slot.category for s in new_intent.slots):
                new_intent.slots.append(slot)

    if "fewer" in instr:
        new_intent.max_stops = max(1, new_intent.max_stops - 1)
    elif "more" in instr:
        new_intent.max_stops = min(12, new_intent.max_stops + 1)

    return new_intent
