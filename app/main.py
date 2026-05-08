"""FastAPI entry point for the City Explorer backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .curation import search_city_suggestions
from .gmaps import build_gmaps_urls
from .itinerary import (
    build_itinerary,
    itinerary_schedule_slots,
    refine_itinerary,
    summarize_itinerary,
)
from .models import CitySuggestion, PlanRequest, PlanResponse, RefineRequest
from .sessions import SessionStore

logger = logging.getLogger("city_explorer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="City Explorer", version="0.1.0")
sessions = SessionStore()


@app.get("/")
async def root() -> dict[str, str]:
    """So the public host URL in a browser does not look broken (Render, Fly, etc.)."""

    return {
        "service": "City Explorer API",
        "status": "running",
        "health": "/health",
        "docs": "/docs",
        "plan": "POST /plan",
    }

# Allow browser clients (Next.js/Vercel/local dev) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.openai_model}


@app.get("/city-suggestions", response_model=list[CitySuggestion])
async def city_suggestions(q: str = "", limit: int = 10) -> list[CitySuggestion]:
    """Autocomplete cities for the web UI (disambiguates Paris TX vs Paris FR, etc.)."""

    q = q.strip()
    if len(q) < 2:
        return []
    return await search_city_suggestions(q, limit=min(max(limit, 1), 20))


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    try:
        itinerary, intent = await build_itinerary(req)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = summarize_itinerary(itinerary)
    schedule = itinerary_schedule_slots(itinerary)
    gmaps_urls = build_gmaps_urls(itinerary)
    session_id = sessions.create(
        query=req.query, intent=intent, itinerary=itinerary, city=req.city
    )
    return PlanResponse(
        session_id=session_id,
        summary=summary,
        itinerary_text=summary,
        itinerary=itinerary,
        schedule=schedule,
        gmaps_url=gmaps_urls[0],
        gmaps_urls=gmaps_urls,
    )


@app.post("/refine", response_model=PlanResponse)
async def refine(req: RefineRequest) -> PlanResponse:
    record = sessions.get(req.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    try:
        itinerary, intent = await refine_itinerary(record, req.instruction)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = summarize_itinerary(itinerary)
    schedule = itinerary_schedule_slots(itinerary)
    gmaps_urls = build_gmaps_urls(itinerary)
    sessions.update(req.session_id, instruction=req.instruction, intent=intent, itinerary=itinerary)
    return PlanResponse(
        session_id=req.session_id,
        summary=summary,
        itinerary_text=summary,
        itinerary=itinerary,
        schedule=schedule,
        gmaps_url=gmaps_urls[0],
        gmaps_urls=gmaps_urls,
    )
