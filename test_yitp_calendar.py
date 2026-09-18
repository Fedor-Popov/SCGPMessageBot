from datetime import date

from events import Event
from sources.yitp_calendar import filter_events, parse_icalendar


def test_parse_public_yitp_icalendar_event():
    events = parse_icalendar("""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:one@example.org
DTSTART;TZID=America/New_York:20260922T140000
DTEND;TZID=America/New_York:20260922T150000
SUMMARY:YITP Colloquium\\, Special Edition
LOCATION:Room 103\\, SCGP
DESCRIPTION:Speaker: Alex Smith\\nAbstract: Details\\, with commas
URL:https://example.org/talk
END:VEVENT
END:VCALENDAR
""")
    assert len(events) == 1
    assert events[0].date == date(2026, 9, 22)
    assert events[0].title == "YITP Colloquium, Special Edition"
    assert events[0].time == "2:00 PM"
    assert events[0].location == "Room 103, SCGP"
    assert events[0].description == "Speaker: Alex Smith\nAbstract: Details, with commas"
    assert events[0].link == "https://example.org/talk"
    assert events[0].source == "yitp-calendar"


def test_parse_all_day_and_utc_event():
    events = parse_icalendar("""BEGIN:VEVENT
DTSTART;VALUE=DATE:20261001
SUMMARY:All day event
END:VEVENT
BEGIN:VEVENT
DTSTART:20261002T000000Z
SUMMARY:UTC event
END:VEVENT
""")
    assert events[0].date == date(2026, 10, 1)
    assert events[0].time == ""
    assert events[1].date == date(2026, 10, 1)
    assert events[1].time == "8:00 PM"


def test_yitp_source_keeps_recent_history_and_future_only():
    events = [
        Event(date(2026, 8, 18), "Too old"),
        Event(date(2026, 8, 19), "Recent"),
        Event(date(2026, 12, 2), "Future"),
    ]
    assert [event.title for event in filter_events(events, date(2026, 9, 18), lookback_days=30)] == ["Recent", "Future"]


def test_weekly_yitp_recurrence_expands_future_occurrences():
    events = parse_icalendar("""BEGIN:VEVENT
DTSTART;TZID=America/New_York:20260918T133000
RRULE:FREQ=WEEKLY;UNTIL=20261003T035959Z;BYDAY=FR
SUMMARY:Advanced Graduate Theory Seminar-Martin Rocek
END:VEVENT
""")
    assert [event.date for event in events] == [date(2026, 9, 18), date(2026, 9, 25), date(2026, 10, 2)]
    assert all(event.time == "1:30 PM" for event in events)
