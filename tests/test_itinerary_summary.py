from app.itinerary import summarize_itinerary
from app.models import Itinerary, ItineraryStop
from tests.conftest import make_place


def test_summary_uses_miles_and_schedule() -> None:
    stop1 = ItineraryStop(
        place=make_place("Plaza Mayor", 40.0, -3.7).model_copy(
            update={"description": "A top local attraction often visited by travelers."}
        ),
        order=1,
        arrive_after="any",
    )
    stop2 = ItineraryStop(
        place=make_place("Royal Palace", 40.01, -3.71).model_copy(
            update={"description": "A historic landmark tied to the city's past.", "category": "historic"}
        ),
        order=2,
        arrive_after="any",
    )
    itinerary = Itinerary(
        origin_lat=40.4167,
        origin_lng=-3.7033,
        travel_mode="walking",
        stops=[stop1, stop2],
        total_distance_m=3200,
        total_duration_s=2400,
        estimated_visit_duration_s=7200,
        estimated_total_duration_s=9600,
        target_duration_s=10 * 3600,
        schedule_start_minute=9 * 60,
        schedule_end_minute=19 * 60,
    )
    text = summarize_itinerary(itinerary)
    assert "miles" in text
    assert "Sample schedule:" in text
    assert "Plaza Mayor - A top local attraction often visited by travelers." in text
