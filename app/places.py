"""Wikipedia / LLM enrichment for resolved itinerary stops."""

from __future__ import annotations

import asyncio

from .curation import (
    condense_wiki_extract_fallback,
    fetch_place_profile,
    finalize_visitor_sentence,
    visitor_one_sentence_blurbs,
)
from .models import Place


def _listing_context_notes(place: Place) -> str | None:
    """Non-Wikipedia facts for the blurb model when no article excerpt is available."""

    parts: list[str] = []
    if place.address:
        parts.append(f"Address: {place.address}")
    if place.rating is not None:
        parts.append(f"Venue score about {place.rating:.1f}/10")
    parts.append(f"Venue type: {place.category.replace('_', ' ')}")
    return " | ".join(parts) if parts else None


async def enrich_destination_profiles(places: list[Place], city: str | None) -> None:
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
