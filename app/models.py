"""Shared pydantic models used across the API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TravelMode = Literal["walking", "driving", "bicycling", "transit"]
TimeOfDay = Literal["morning", "lunch", "afternoon", "dinner", "evening", "any"]


class IntentPlan(BaseModel):
    """Session metadata captured after a plan or refine (travel mode, radius, time budget)."""

    travel_mode: TravelMode = "walking"
    max_stops: int = Field(default=6, ge=1, le=12)
    radius_m: int = Field(default=5000, ge=500, le=30000)
    available_minutes: int | None = Field(
        default=None,
        ge=30,
        le=24 * 60,
        description="User available time window in minutes, if specified.",
    )


class Place(BaseModel):
    """A resolved place that may end up on the itinerary."""

    id: str
    name: str
    lat: float
    lng: float
    category: str
    description: str | None = None
    rating: float | None = None
    popularity: float = 0.0
    is_anchor: bool = False
    address: str | None = None
    image_url: str | None = None
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
    estimated_visit_duration_s: float = 0.0
    estimated_total_duration_s: float = 0.0
    target_duration_s: float | None = None
    schedule_start_minute: int | None = None
    schedule_end_minute: int | None = None


class PlanRequest(BaseModel):
    query: str
    city: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_m: int | None = None
    mode: TravelMode | None = None


class RefineRequest(BaseModel):
    session_id: str
    instruction: str


class CitySuggestion(BaseModel):
    """One geocoded place the user can pick as their tour city."""

    label: str
    name: str
    country: str | None = None
    admin1: str | None = None
    latitude: float
    longitude: float


class ScheduleSlot(BaseModel):
    """One suggested time block for the day (half-hour rounded)."""

    time_start: str
    time_end: str
    place_name: str


class OpenAIDirectStop(BaseModel):
    """One stop from the direct OpenAI tour planner before geocoding."""

    name: str = Field(min_length=1, max_length=400)
    time_of_day: TimeOfDay = "any"
    category: str = Field(default="attraction", max_length=80)


class OpenAIDirectPlan(BaseModel):
    """Full JSON plan from the direct OpenAI tour planner."""

    travel_mode: TravelMode = "walking"
    available_minutes: int | None = Field(default=None, ge=15, le=24 * 60)
    radius_m: int = Field(default=8000, ge=500, le=30000)
    stops: list[OpenAIDirectStop] = Field(min_length=1)


class PlanResponse(BaseModel):
    session_id: str
    summary: str
    itinerary_text: str
    itinerary: Itinerary
    schedule: list[ScheduleSlot] = Field(default_factory=list)
    gmaps_url: str
    gmaps_urls: list[str]
