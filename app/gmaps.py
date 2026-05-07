"""Build Google Maps universal directions URLs.

`https://www.google.com/maps/dir/?api=1` is a documented public deep link
that opens directly in the Google Maps iOS app when installed and falls
back to the web view otherwise.

Limits:
- 9 waypoints between `origin` and `destination`, so a single URL covers at
  most 11 places (origin + 9 waypoints + destination). We split longer
  itineraries into back-to-back URLs that pick up where the previous one
  ended.
"""

from __future__ import annotations

from urllib.parse import urlencode

from .models import Itinerary

MAX_WAYPOINTS = 9


def build_gmaps_urls(itinerary: Itinerary) -> list[str]:
    """Return one or more Google Maps URLs covering the full itinerary."""

    if not itinerary.stops:
        return [_origin_only_url(itinerary)]

    coords: list[tuple[float, float]] = [
        (itinerary.origin_lat, itinerary.origin_lng)
    ] + [(s.place.lat, s.place.lng) for s in itinerary.stops]

    return _split_into_urls(coords, itinerary.travel_mode)


def _split_into_urls(coords: list[tuple[float, float]], travel_mode: str) -> list[str]:
    """Chunk a coordinate chain into Google Maps URLs of <=11 points each."""

    urls: list[str] = []
    chunk_size = MAX_WAYPOINTS + 2  # origin + 9 waypoints + destination

    i = 0
    while i < len(coords) - 1:
        chunk = coords[i : i + chunk_size]
        if len(chunk) < 2:
            break
        urls.append(_format_url(chunk, travel_mode))
        i += chunk_size - 1

    return urls or [_origin_only_url_from_coords(coords[0], travel_mode)]


def _format_url(chunk: list[tuple[float, float]], travel_mode: str) -> str:
    origin = chunk[0]
    destination = chunk[-1]
    waypoints = chunk[1:-1]

    params: dict[str, str] = {
        "api": "1",
        "origin": _fmt(origin),
        "destination": _fmt(destination),
        "travelmode": travel_mode,
    }
    if waypoints:
        params["waypoints"] = "|".join(_fmt(c) for c in waypoints)
    return "https://www.google.com/maps/dir/?" + urlencode(params, safe="|,")


def _origin_only_url(itinerary: Itinerary) -> str:
    return _origin_only_url_from_coords(
        (itinerary.origin_lat, itinerary.origin_lng), itinerary.travel_mode
    )


def _origin_only_url_from_coords(origin: tuple[float, float], travel_mode: str) -> str:
    params = {
        "api": "1",
        "query": _fmt(origin),
        "travelmode": travel_mode,
    }
    return "https://www.google.com/maps/search/?" + urlencode(params, safe=",")


def _fmt(coord: tuple[float, float]) -> str:
    return f"{coord[0]:.6f},{coord[1]:.6f}"
