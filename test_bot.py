from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from bot import format_lunch, format_talks, normalize_event_time, parse_event_date, week_start
from cache import EventCache
from events import Event
from website import parse_schedule
from sources.google_sheets import parse_rows
from lunch import LunchCache, LunchItem, LunchMenu
from sources.bouncing import BouncingSeminarSource
from sources.manual import ManualEventSource
from sheets_writer import GoogleSheetsCacheWriter, rows_for_events


def test_parse_schedule():
    html = """
    <h2>Future Seminars schedule</h2>
    <div>Sep 15: Francesco Russo (Ecole Polytechnique, CPHT) on</div>
    <div>2606.17167</div>
    <div>Thermal One-point Functions</div>
    <div>Abstract: A talk about thermal one-point functions.</div>
    <h2>Past Seminars</h2>
    <div>01/01/26: Old talk</div>
    """
    talks = parse_schedule(html, year=2026)
    assert len(talks) == 1
    assert talks[0].date == date(2026, 9, 15)
    assert talks[0].speaker == "Francesco Russo"
    assert talks[0].affiliation == "Ecole Polytechnique, CPHT"
    assert talks[0].title == "Thermal One-point Functions"
    assert talks[0].link == "https://arxiv.org/abs/2606.17167"


def test_cache_round_trip(tmp_path: Path):
    cache = EventCache(tmp_path / "talks.json")
    talks = [Event(date(2026, 9, 15), "A Talk", speaker="A Speaker")]
    cache.save(talks)
    assert cache.load() == talks


def test_week_start():
    assert week_start(date(2026, 9, 16)) == date(2026, 9, 14)


def test_format_empty():
    assert format_talks([], "Talks") == "Talks\n\nNo talks found."


def test_format_talks_uses_html_emphasis():
    message = format_talks([
        Event(date(2026, 9, 15), "A <Talk>", speaker="A Speaker"),
        Event(date(2026, 9, 16), "Another Talk", speaker="Another Speaker"),
    ], "Talks")
    assert "<b>A &lt;Talk&gt;</b>" in message
    assert "<i>A Speaker</i>" in message
    assert "Tuesday:\n• <b>A &lt;Talk&gt;</b>" in message
    assert "Wednesday:\n• <b>Another Talk</b>" in message


def test_sheet_rows_keep_missing_title_and_abstract():
    talks = parse_rows([["Dates", "Name", "Talk Title", "Talk Abstract"], ["09/17/2026", "A Speaker", "", ""]], "test")
    assert len(talks) == 1
    assert talks[0].title == ""
    assert talks[0].description == ""
    assert "A Speaker" in format_talks(talks, "Talks")


def test_calendar_style_sheet_rows_are_supported():
    talks = parse_rows([
        ["Title", "Start", "Description", "Location", "Publish"],
        ["Thermal Seminar", "11/09/2026 11.00.00", "Speaker: Alex Smith (YITP)\nTitle: A New Talk\nAbstract: Details", "Room 102", "TRUE"],
        ["Hidden", "11/10/2026 11.00.00", "Speaker: Nobody", "Room 1", "FALSE"],
    ], "calendar")
    assert len(talks) == 1
    assert talks[0].date == date(2026, 11, 9)
    assert talks[0].time == "11:00 AM"
    assert talks[0].title == "A New Talk"
    assert talks[0].speaker == "Alex Smith"
    assert talks[0].affiliation == "YITP"
    assert talks[0].description == "Details"


def test_sheet_export_requires_title_or_description_and_checks_publish():
    rows = rows_for_events([
        Event(date(2026, 9, 14), "", speaker="Speaker only"),
        Event(date(2026, 9, 15), "A Talk", time="2:00 PM"),
        Event(date(2026, 9, 16), "", description="Abstract only"),
    ])
    assert len(rows) == 3
    assert rows[1][0] == "A Talk"
    assert rows[1][1] == "=DATE(2026;9;15)+TIME(14;0;0)"
    assert rows[1][2] == "=DATE(2026;9;15)+TIME(15;0;0)"
    assert rows[1][5] is True
    assert rows[2][3] == "Abstract: Abstract only"
    assert all(row[0] != "Speaker only" for row in rows[1:])


def test_sheet_writer_unpublishes_clears_and_rebuilds_from_all_events():
    spreadsheet_api = MagicMock()
    service = MagicMock()
    service.spreadsheets.return_value = spreadsheet_api
    spreadsheet_api.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 7, "gridProperties": {"rowCount": 1001}}}]
    }
    writer = GoogleSheetsCacheWriter("sheet-id", unpublish_delay_seconds=0)
    writer._source._service = lambda: service
    events = [
        Event(date(2026, 9, 15), "First", time="2:00 PM"),
        Event(date(2026, 9, 16), "Second", time="11:00 AM"),
    ]

    assert writer.write(events) == 2

    first_batch = spreadsheet_api.batchUpdate.call_args_list[0].kwargs["body"]
    publish_cell = first_batch["requests"][0]["repeatCell"]["cell"]
    assert publish_cell == {"userEnteredValue": {"boolValue": False}}
    spreadsheet_api.values.return_value.clear.assert_called_once_with(
        spreadsheetId="sheet-id", range="A:G", body={}
    )
    update = spreadsheet_api.values.return_value.update.call_args.kwargs
    assert update["range"] == "A1:G3"
    assert update["body"]["values"] == rows_for_events(events)


def test_format_lunch():
    menu = LunchMenu(date(2026, 9, 10), (("Soup", (LunchItem("Tomato Soup", "With herbs"),)),))
    message = format_lunch(menu)
    assert "Thursday, September 10" in message
    assert "<b>Soup</b>" in message
    assert "Tomato Soup — With herbs" in message


def test_lunch_cache_round_trip(tmp_path: Path):
    menu = LunchMenu(date(2026, 9, 10), (("Soup", (LunchItem("Tomato Soup", "With herbs"),)),))
    cache = LunchCache(tmp_path / "lunch.json")
    cache.save(menu)
    assert cache.load() == menu


def test_bouncing_seminar_is_friday_in_common_room():
    events = BouncingSeminarSource(12, start_date=date(2026, 9, 11)).fetch()
    assert len(events) == 3
    assert all(event.date.weekday() == 4 for event in events)
    assert all(event.time == "11:00 AM" for event in events)
    assert all(event.location == "Common Room" for event in events)
    assert all(event.date < date(2026, 10, 1) for event in events)


def test_manual_event_source_persists_and_replaces_duplicates(tmp_path: Path):
    source = ManualEventSource(tmp_path / "manual.json")
    source.add(Event(date(2026, 9, 20), "A Talk", speaker="Alice", description="First"))
    source.add(Event(date(2026, 9, 20), "A Talk", speaker="Alice", description="Updated"))
    assert source.fetch() == [Event(
        date(2026, 9, 20),
        "A Talk",
        speaker="Alice",
        description="Updated",
    )]


def test_manual_event_source_deletes_only_selected_event(tmp_path: Path):
    source = ManualEventSource(tmp_path / "manual.json")
    first = Event(date(2026, 9, 20), "First", speaker="Alice")
    second = Event(date(2026, 9, 21), "Second", speaker="Bob")
    source.add(first)
    source.add(second)

    assert source.delete(source.event_id(first)) == first
    assert source.fetch() == [second]
    assert source.delete(source.event_id(first)) is None


def test_manual_event_date_and_time_parsing():
    assert parse_event_date("09/20/2026") == date(2026, 9, 20)
    assert parse_event_date("09/20", date(2026, 1, 1)) == date(2026, 9, 20)
    assert normalize_event_time("14:00") == "2:00 PM"
