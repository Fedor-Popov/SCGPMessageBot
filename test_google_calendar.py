from datetime import date

from events import Event
from google_calendar import _calendar_event, calendar_url


def test_calendar_event_contains_talk_metadata():
    body = _calendar_event(
        Event(
            date(2026, 9, 25),
            "Advanced Graduate Theory Seminar",
            speaker="Martin Rocek",
            affiliation="YITP",
            time="1:30 PM",
            location="Room 103",
            description="A seminar abstract.",
        ),
        "America/New_York",
    )

    assert body["summary"] == "Advanced Graduate Theory Seminar"
    assert body["start"] == {"dateTime": "2026-09-25T13:30:00", "timeZone": "America/New_York"}
    assert body["end"]["dateTime"] == "2026-09-25T14:30:00"
    assert body["location"] == "Room 103"
    assert "Martin Rocek (YITP)" in body["description"]
    assert body["extendedProperties"]["private"]["scgpManaged"] == "true"


def test_calendar_event_without_time_is_all_day():
    body = _calendar_event(Event(date(2026, 9, 25), "All day"), "America/New_York")
    assert body["start"] == {"date": "2026-09-25"}
    assert body["end"] == {"date": "2026-09-26"}


def test_calendar_url_encodes_calendar_id():
    assert calendar_url("scgp@example.com") == "https://calendar.google.com/calendar/u/0/r?cid=scgp%40example.com"
