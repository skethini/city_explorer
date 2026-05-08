from app.itinerary import itinerary_schedule_slots, summarize_itinerary
from app.models import Itinerary, ItineraryStop
from tests.conftest import make_place


def test_summary_lists_stops_without_mileage_block() -> None:
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
    assert "miles" not in text.lower()
    assert "walk/ride time" not in text.lower()
    assert "sample schedule" not in text.lower()
    assert "**Plaza Mayor** - A top local attraction often visited by travelers." in text


def test_schedule_rounds_to_half_hour() -> None:
    stop = ItineraryStop(
        place=make_place("Retiro Park", 40.0, -3.7).model_copy(
            update={"description": "Scenic green space ideal for a relaxed walk.", "category": "park"}
        ),
        order=1,
        arrive_after="any",
    )
    itinerary = Itinerary(
        origin_lat=40.4167,
        origin_lng=-3.7033,
        travel_mode="walking",
        stops=[stop],
        total_distance_m=1000,
        total_duration_s=1700,
        estimated_visit_duration_s=3600,
        estimated_total_duration_s=5300,
        target_duration_s=4 * 3600,
        schedule_start_minute=9 * 60 + 10,
        schedule_end_minute=13 * 60 + 20,
    )
    slots = itinerary_schedule_slots(itinerary)
    assert len(slots) >= 1
    joined = f"{slots[0].time_start} {slots[0].time_end}"
    assert "09:00 AM" in joined or "09:30 AM" in joined
