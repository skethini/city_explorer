"""Shared pydantic models used across the API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TravelMode = Literal["walking", "driving", "bicycling", "transit"]
TimeOfDay = Literal["morning", "lunch", "afternoon", "dinner", "evening", "any"]
PriceTier = Literal[1, 2, 3, 4]


class Slot(BaseModel):
    """A single desired stop, before we resolve it to a real place."""

    category: str = Field(..., description="High-level category, e.g. 'museum', 'thai_restaurant'.")
    must_include: list[str] = Field(default_factory=list, description="Required keywords.")
    time_of_day: TimeOfDay = "any"
    price_tier: PriceTier | None = None
    notes: str | None = None


class IntentPlan(BaseModel):
    """Structured representation of a user's request."""

    travel_mode: TravelMode = "walking"
    max_stops: int = Field(default=6, ge=1, le=12)
    radius_m: int = Field(default=5000, ge=500, le=30000)
    slots: list[Slot] = Field(default_factory=list)
    free_slots: int = Field(
        default=0,
        ge=0,
        description="Number of additional top-rated attractions to fill in.",
    )


class Place(BaseModel):
    """A resolved place that may end up on the itinerary."""

    id: str
    name: str
    lat: float
    lng: float
    category: str
    rating: float | None = None
    popularity: float = 0.0
    address: str | None = None
    source: Literal["osm", "foursquare"] = "osm"
    time_of_day: TimeOfDay = "any"


class ItineraryStop(BaseModel):
    place: Place
    order: int
    arrive_after: TimeOfDay = "any"


class Itinerary(BaseModel):
    origin_lat: float
    origin_lng: float
    travel_mode: TravelMode
    stops: list[ItineraryStop]
    total_distance_m: float = 0.0
    total_duration_s: float = 0.0


class PlanRequest(BaseModel):
    query: str
    lat: float
    lng: float
    radius_m: int | None = None
    mode: TravelMode | None = None


class RefineRequest(BaseModel):
    session_id: str
    instruction: str


class PlanResponse(BaseModel):
    session_id: str
    summary: str
    itinerary: Itinerary
    gmaps_url: str
    gmaps_urls: list[str]
