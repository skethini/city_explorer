"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from app.models import Place, TimeOfDay


def make_place(
    name: str,
    lat: float,
    lng: float,
    *,
    category: str = "attraction",
    rating: float | None = None,
    popularity: float = 0.0,
    time_of_day: TimeOfDay = "any",
) -> Place:
    return Place(
        id=f"test-{name.replace(' ', '-').lower()}",
        name=name,
        lat=lat,
        lng=lng,
        category=category,
        rating=rating,
        popularity=popularity,
        source="osm",
        time_of_day=time_of_day,
    )
