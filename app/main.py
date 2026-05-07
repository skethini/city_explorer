"""FastAPI entry point for the City Explorer backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from .config import settings
from .gmaps import build_gmaps_urls
from .itinerary import build_itinerary, refine_itinerary, summarize_itinerary
from .models import PlanRequest, PlanResponse, RefineRequest
from .sessions import SessionStore

logger = logging.getLogger("city_explorer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="City Explorer", version="0.1.0")
sessions = SessionStore()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.openai_model}


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    try:
        itinerary, intent = await build_itinerary(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = summarize_itinerary(itinerary)
    gmaps_urls = build_gmaps_urls(itinerary)
    session_id = sessions.create(query=req.query, intent=intent, itinerary=itinerary)
    return PlanResponse(
        session_id=session_id,
        summary=summary,
        itinerary=itinerary,
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = summarize_itinerary(itinerary)
    gmaps_urls = build_gmaps_urls(itinerary)
    sessions.update(req.session_id, instruction=req.instruction, intent=intent, itinerary=itinerary)
    return PlanResponse(
        session_id=req.session_id,
        summary=summary,
        itinerary=itinerary,
        gmaps_url=gmaps_urls[0],
        gmaps_urls=gmaps_urls,
    )
