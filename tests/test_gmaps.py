"""Tests for Google Maps URL formatting and chunking."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.gmaps import MAX_WAYPOINTS, build_gmaps_urls
from app.models import Itinerary, ItineraryStop
from tests.conftest import make_place


def _itinerary_with_n_stops(n: int) -> Itinerary:
    stops = [
        ItineraryStop(
            place=make_place(f"Stop {i}", 0.0, 0.001 * (i + 1)),
            order=i + 1,
            arrive_after="any",
        )
        for i in range(n)
    ]
    return Itinerary(
        origin_lat=48.8566,
        origin_lng=2.3522,
        travel_mode="walking",
        stops=stops,
    )


def test_url_format_for_three_stops() -> None:
    it = _itinerary_with_n_stops(3)
    urls = build_gmaps_urls(it)
    assert len(urls) == 1
    parsed = urlparse(urls[0])
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "www.google.com"
    assert parsed.path == "/maps/dir/"
    assert qs["api"] == ["1"]
    assert qs["origin"] == ["48.856600,2.352200"]
    assert qs["destination"][0].endswith(",0.003000")
    assert qs["travelmode"] == ["walking"]
    assert qs["waypoints"][0].count("|") == 1


def test_single_stop_has_no_waypoints() -> None:
    it = _itinerary_with_n_stops(1)
    urls = build_gmaps_urls(it)
    assert len(urls) == 1
    qs = parse_qs(urlparse(urls[0]).query)
    assert "waypoints" not in qs


def test_url_chunks_when_more_than_eleven_points() -> None:
    it = _itinerary_with_n_stops(15)
    urls = build_gmaps_urls(it)
    assert len(urls) >= 2
    for url in urls:
        qs = parse_qs(urlparse(url).query)
        wp = qs.get("waypoints", [""])[0]
        wp_count = 0 if not wp else wp.count("|") + 1
        assert wp_count <= MAX_WAYPOINTS


def test_chunks_join_seamlessly() -> None:
    it = _itinerary_with_n_stops(20)
    urls = build_gmaps_urls(it)
    for prev, nxt in zip(urls, urls[1:]):
        prev_dest = parse_qs(urlparse(prev).query)["destination"][0]
        nxt_origin = parse_qs(urlparse(nxt).query)["origin"][0]
        assert prev_dest == nxt_origin


def test_zero_stops_returns_search_url() -> None:
    it = Itinerary(
        origin_lat=48.8566, origin_lng=2.3522, travel_mode="walking", stops=[]
    )
    urls = build_gmaps_urls(it)
    assert len(urls) == 1
    assert "maps/search" in urls[0]


def test_travel_mode_is_passed_through() -> None:
    it = _itinerary_with_n_stops(2)
    it = it.model_copy(update={"travel_mode": "driving"})
    urls = build_gmaps_urls(it)
    qs = parse_qs(urlparse(urls[0]).query)
    assert qs["travelmode"] == ["driving"]
