import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from bot import menu_markup
from trains import (STATIONS, TIMEZONE, ScheduleUnavailable, Timetable, TrainSchedules,
                    SearchResult, format_trains, seconds, service_timestamp)
from trains_ui import register_trains, station_buttons


NOW = datetime(2026, 9, 11, 10, tzinfo=TIMEZONE)


@pytest.fixture
def tables():
    return {
        "stops.txt": [{"stop_id": key, "stop_name": name} for key, name in
                      [("R", "Ronkonkoma"), ("S", "Stony Brook"), ("P", "Penn Station"),
                       ("J", "Jamaica"), ("H", "Hicksville"), ("U", "Huntington")]],
        "trips.txt": [], "stop_times.txt": [], "transfers.txt": [],
        "calendar_dates.txt": [{"service_id": "daily", "date": day, "exception_type": "1"}
                               for day in ("20260910", "20260911", "20260912")],
    }


def trip(tables, identifier, stops, service="daily"):
    tables["trips.txt"].append({"trip_id": identifier, "trip_short_name": identifier, "service_id": service})
    for i, (station, when) in enumerate(stops):
        tables["stop_times.txt"].append({"trip_id": identifier, "stop_id": station, "stop_sequence": str(i + 1),
                                        "arrival_time": when, "departure_time": when, "pickup_type": "0", "drop_off_type": "0"})


def rule(incoming, outgoing, kind, minimum="", station="J"):
    return {"from_stop_id": station, "to_stop_id": station, "from_trip_id": incoming,
            "to_trip_id": outgoing, "transfer_type": kind, "min_transfer_time": minimum}


def test_departure_window_includes_arrival_after_three_hours(tables):
    trip(tables, "past", [("S", "09:59:00"), ("P", "11:00:00")])
    trip(tables, "now", [("S", "10:00:00"), ("P", "11:30:00")])
    trip(tables, "last", [("S", "12:59:00"), ("P", "14:30:00")])
    trip(tables, "outside", [("S", "13:00:00"), ("P", "14:40:00")])
    paths = Timetable(tables).journeys("stonybrook", "nyc", NOW)
    assert [path[0].train for path in paths] == ["now", "last"]
    assert paths[-1][-1].arrival > NOW.timestamp() + 10800


def test_transfer_minimum_timed_connection_and_prohibited_transfer(tables):
    trip(tables, "first", [("S", "10:10:00"), ("J", "11:00:00")])
    trip(tables, "tight", [("J", "11:02:00"), ("P", "11:20:00")])
    trip(tables, "safe", [("J", "11:08:00"), ("P", "11:30:00")])
    assert Timetable(tables).journeys("stonybrook", "nyc", NOW)[0][-1].train == "safe"
    tables["transfers.txt"] = [rule("first", "tight", "1")]
    assert Timetable(tables).journeys("stonybrook", "nyc", NOW)[0][-1].train == "tight"
    tables["transfers.txt"] = [rule("first", "tight", "3"), rule("first", "safe", "2", "600")]
    assert Timetable(tables).journeys("stonybrook", "nyc", NOW) == ()


def test_two_changes_and_branch_connection(tables):
    trip(tables, "first", [("R", "10:10:00"), ("H", "10:40:00")])
    trip(tables, "middle", [("H", "10:46:00"), ("U", "11:00:00")])
    trip(tables, "last", [("U", "11:05:00"), ("S", "11:45:00")])
    path, = Timetable(tables).journeys("ronkonkoma", "stonybrook", NOW)
    assert [leg.train for leg in path] == ["first", "middle", "last"]
    assert path[0].destination == "Hicksville"
    assert path[1].destination == "Huntington"


def test_no_boarding_or_alighting_and_reversed_direction(tables):
    trip(tables, "no-board", [("S", "10:10:00"), ("P", "11:00:00")])
    tables["stop_times.txt"][0]["pickup_type"] = "1"
    trip(tables, "no-exit", [("S", "10:20:00"), ("P", "11:10:00")])
    tables["stop_times.txt"][-1]["drop_off_type"] = "1"
    trip(tables, "reverse", [("P", "10:30:00"), ("S", "12:00:00")])
    assert Timetable(tables).journeys("stonybrook", "nyc", NOW) == ()


def test_calendar_dates_override_weekday_and_weekend(tables):
    tables["calendar.txt"] = [{"service_id": "weekday", "start_date": "20260901", "end_date": "20260930",
                                **{day: "1" for day in ("monday", "tuesday", "wednesday", "thursday", "friday")},
                                "saturday": "0", "sunday": "0"}]
    tables["calendar_dates.txt"] += [{"service_id": "weekday", "date": "20260911", "exception_type": "2"},
                                    {"service_id": "special", "date": "20260911", "exception_type": "1"}]
    trip(tables, "normal", [("S", "10:10:00"), ("P", "11:00:00")], "weekday")
    trip(tables, "holiday", [("S", "10:15:00"), ("P", "11:05:00")], "special")
    timetable = Timetable(tables)
    assert timetable.journeys("stonybrook", "nyc", NOW)[0][0].train == "holiday"
    assert timetable.journeys("stonybrook", "nyc", NOW + timedelta(days=1)) == ()


def test_after_midnight_uses_previous_service_day(tables):
    tables["calendar_dates.txt"] = [{"service_id": "night", "date": "20260911", "exception_type": "1"},
                                    {"service_id": "other", "date": "20260912", "exception_type": "1"}]
    trip(tables, "overnight", [("S", "25:00:00"), ("P", "26:30:00")], "night")
    now = datetime(2026, 9, 11, 23, 45, tzinfo=TIMEZONE)
    paths = Timetable(tables).journeys("stonybrook", "nyc", now)
    assert len(paths) == 1
    assert datetime.fromtimestamp(paths[0][0].departure, TIMEZONE) == datetime(2026, 9, 12, 1, tzinfo=TIMEZONE)
    result = SearchResult("stonybrook", "nyc", now, now + timedelta(hours=3), paths, now)
    assert "1:00 AM (Sep 12)" in format_trains(result)[0]
    assert len(Timetable(tables).journeys("stonybrook", "nyc", now + timedelta(hours=1))) == 1


def test_timezone_validation_and_expired_feed(tables):
    timetable = Timetable(tables)
    with pytest.raises(ValueError):
        timetable.journeys("nyc", "nyc", NOW)
    with pytest.raises(ValueError):
        timetable.journeys("nyc", "jamaica", NOW.replace(tzinfo=None))
    with pytest.raises(ScheduleUnavailable):
        timetable.journeys("nyc", "jamaica", NOW + timedelta(days=30))
    # GTFS noon-minus-12-hours interpretation on the fall-back date.
    day = datetime(2026, 11, 1).date()
    assert service_timestamp(day, seconds("12:00:00")) == datetime(2026, 11, 1, 12, tzinfo=TIMEZONE).timestamp()


def test_fallback_cache_rejects_overly_old_data(tables, tmp_path, monkeypatch):
    schedules = TrainSchedules(tmp_path / "cached.zip")
    schedules._timetable = Timetable(tables)
    schedules._updated = NOW.timestamp() - 7 * 3600
    monkeypatch.setattr("trains.clock.time", lambda: NOW.timestamp())
    fetch = MagicMock(side_effect=requests.Timeout("unavailable"))
    monkeypatch.setattr("trains.requests.get", fetch)
    result = schedules.search("stonybrook", "nyc", NOW)
    assert result.stale
    assert "Using cached MTA" in format_trains(result)[0]
    schedules._updated = NOW.timestamp() - 49 * 3600
    with pytest.raises(ScheduleUnavailable):
        schedules.search("stonybrook", "nyc", NOW)


def test_selector_flow_and_result_pagination(tables):
    for i in range(8):
        trip(tables, str(i), [("P", f"10:{i * 5:02}:00"), ("J", f"11:{i * 5:02}:00")])
    paths = Timetable(tables).journeys("nyc", "jamaica", NOW)
    result = SearchResult("nyc", "jamaica", NOW, NOW + timedelta(hours=3), paths, NOW)
    text, pages = format_trains(result)
    assert pages == 2 and "Page 1/2" in text and "live delays" in text
    assert "Page 2/2" in format_trains(result, 1)[0]
    assert len(text) < 4096
    assert {b.text for row in station_buttons().inline_keyboard for b in row} == set(STATIONS.values())
    assert {b.text for row in station_buttons("nyc").inline_keyboard for b in row} == set(STATIONS.values())
    assert any(b.text == "Trains" for row in menu_markup().inline_keyboard for b in row)
    app = MagicMock()
    schedules = SimpleNamespace(search=MagicMock(return_value=result))
    register_trains(app, schedules, menu_markup)
    callback = app.add_handler.call_args_list[1].args[0].callback
    query = SimpleNamespace(data="trains:from:nyc", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={}, application=app)
    async def exercise():
        await callback(update, context)
        assert "To:" in query.edit_message_text.call_args.args[0]
        query.data = "trains:to:nyc:nyc"
        await callback(update, context)
        schedules.search.assert_not_called()
        query.data = "trains:to:nyc:jamaica"
        await callback(update, context)
        await app.create_task.call_args.args[0]
        assert "Page 1/2" in query.edit_message_text.call_args.args[0]
        query.data = next(b.callback_data for row in query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
                          for b in row if b.text == "Next")
        await callback(update, context)
        assert "Page 2/2" in query.edit_message_text.call_args.args[0]
    asyncio.run(exercise())
