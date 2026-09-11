"""LIRR scheduled journeys from MTA's public GTFS feed (no API key)."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import heapq
from html import escape
import io
from itertools import count
import logging
import os
from pathlib import Path
import tempfile
from threading import Lock
import time as clock
import zipfile
from zoneinfo import ZoneInfo

import requests


GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip"
TIMEZONE = ZoneInfo("America/New_York")
STATIONS = {"ronkonkoma": "Ronkonkoma", "stonybrook": "Stony Brook", "nyc": "NYC (Penn Station)", "jamaica": "Jamaica"}
STOP_NAMES = {**STATIONS, "nyc": "Penn Station"}
LOG = logging.getLogger(__name__)


class ScheduleUnavailable(RuntimeError):
    pass


def seconds(value: str) -> int:
    hours, minutes, secs = map(int, value.split(":"))
    return hours * 3600 + minutes * 60 + secs


def service_timestamp(day: date, offset: int) -> float:
    # GTFS times are elapsed seconds from local noon minus 12 hours, including
    # times beyond 24:00 and daylight-saving transitions.
    return datetime.combine(day, time(12), TIMEZONE).timestamp() - 43200 + offset


@dataclass(frozen=True)
class Call:
    station: str
    arrival: int
    departure: int
    pickup: bool
    dropoff: bool


@dataclass(frozen=True)
class Run:
    trip_id: str
    train: str
    day: date
    calls: tuple[Call, ...]


@dataclass(frozen=True)
class Leg:
    train: str
    origin: str
    destination: str
    departure: float
    arrival: float


@dataclass(frozen=True)
class SearchResult:
    origin: str
    destination: str
    start: datetime
    end: datetime
    journeys: tuple[tuple[Leg, ...], ...]
    cached_at: datetime
    stale: bool = False


class Timetable:
    def __init__(self, tables: dict[str, list[dict[str, str]]]):
        stops = tables["stops.txt"]
        self.station_names = {row["stop_id"]: row["stop_name"] for row in stops}
        self.station_ids = {}
        for key, name in STOP_NAMES.items():
            matches = [row["stop_id"] for row in stops if row["stop_name"].casefold() == name.casefold()]
            if len(matches) != 1:
                raise ScheduleUnavailable(f"Cannot identify {name} in MTA's timetable.")
            self.station_ids[key] = matches[0]
        self.calendar = tables.get("calendar.txt", [])
        self.exceptions = tables.get("calendar_dates.txt", [])
        dates = [r["date"] for r in self.exceptions]
        dates += [r[k] for r in self.calendar for k in ("start_date", "end_date")]
        if not dates:
            raise ScheduleUnavailable("MTA's timetable contains no service dates.")
        self.first_day = datetime.strptime(min(dates), "%Y%m%d").date()
        self.last_day = datetime.strptime(max(dates), "%Y%m%d").date()
        calls = defaultdict(list)
        self.max_seconds = 0
        for row in tables["stop_times.txt"]:
            arrival = seconds(row["arrival_time"])
            departure = seconds(row["departure_time"])
            self.max_seconds = max(self.max_seconds, departure)
            calls[row["trip_id"]].append((int(row["stop_sequence"]), Call(
                row["stop_id"], arrival, departure,
                row.get("pickup_type", "0") in ("", "0"), row.get("drop_off_type", "0") in ("", "0"),
            )))
        self.trips = [(r["trip_id"], r["service_id"], r.get("trip_short_name") or r["trip_id"],
                       tuple(c for _, c in sorted(calls[r["trip_id"]]))) for r in tables["trips.txt"]]
        self.transfers = {}
        for row in tables.get("transfers.txt", []):
            if row["from_stop_id"] != row["to_stop_id"]:
                continue  # This planner transfers within stations, without walking legs.
            key = (row["from_stop_id"], row.get("from_trip_id", ""), row.get("to_trip_id", ""))
            kind = row["transfer_type"]
            self.transfers[key] = None if kind == "3" else (0 if kind == "1" else int(row.get("min_transfer_time") or 300))

    @classmethod
    def from_zip(cls, payload: bytes) -> Timetable:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            tables = {}
            for name in ("stops.txt", "trips.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt", "transfers.txt"):
                if name in archive.namelist():
                    with archive.open(name) as file:
                        tables[name] = list(csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig")))
        return cls(tables)

    def services(self, day: date) -> set[str]:
        value = day.strftime("%Y%m%d")
        weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[day.weekday()]
        active = {r["service_id"] for r in self.calendar if r["start_date"] <= value <= r["end_date"] and r[weekday] == "1"}
        for row in self.exceptions:
            if row["date"] == value:
                if row["exception_type"] == "1":
                    active.add(row["service_id"])
                elif row["exception_type"] == "2":
                    active.discard(row["service_id"])
        return active

    def transfer_seconds(self, station: str, incoming: str, outgoing: str) -> int | None:
        for key in ((station, incoming, outgoing), (station, incoming, ""), (station, "", outgoing), (station, "", "")):
            if key in self.transfers:
                return self.transfers[key]
        return 300

    def journeys(self, origin: str, destination: str, now: datetime) -> tuple[tuple[Leg, ...], ...]:
        if origin not in STATIONS or destination not in STATIONS or origin == destination:
            raise ValueError("Choose two different stations.")
        if now.tzinfo is None:
            raise ValueError("Train queries require a timezone-aware time.")
        now = now.astimezone(TIMEZONE)
        window_end = now.timestamp() + 3 * 3600
        if now.date() < self.first_day or datetime.fromtimestamp(window_end, TIMEZONE).date() > self.last_day:
            raise ScheduleUnavailable("MTA's downloaded timetable does not cover the requested dates. Please try again later.")
        origin_id, destination_id = self.station_ids[origin], self.station_ids[destination]
        horizon = window_end + 6 * 3600  # Permit arrivals after the departure window.
        departures = defaultdict(list)
        runs = []
        first_day = now.date() - timedelta(days=max(1, self.max_seconds // 86400))
        last_day = datetime.fromtimestamp(horizon, TIMEZONE).date()
        for delta in range((last_day - first_day).days + 1):
            day = first_day + timedelta(days=delta)
            active = self.services(day)
            for trip_id, service, train, calls in self.trips:
                if service not in active or not calls:
                    continue
                run = Run(trip_id, train, day, calls)
                index = len(runs)
                runs.append(run)
                for stop_index, call in enumerate(calls[:-1]):
                    departure = service_timestamp(day, call.departure)
                    if call.pickup and now.timestamp() <= departure <= horizon:
                        departures[call.station].append((departure, index, stop_index))
        for values in departures.values():
            values.sort()

        def best_journey(seed):
            serial = count()
            heap = []
            seen = set()

            def ride(run_index, board_index, path, visited):
                run = runs[run_index]
                board = run.calls[board_index]
                passed = set(visited)
                passed.add(board.station)
                for call_index in range(board_index + 1, len(run.calls)):
                    call = run.calls[call_index]
                    if call.station in passed:
                        break  # Do not ride away and double back over the same stations.
                    passed.add(call.station)
                    arrival = service_timestamp(run.day, call.arrival)
                    if arrival > seed[0] + 6 * 3600:
                        break
                    if not call.dropoff:
                        continue
                    leg = Leg(run.train, self.station_names[board.station], self.station_names[call.station],
                              service_timestamp(run.day, board.departure), arrival)
                    heapq.heappush(heap, (arrival, len(path), next(serial), run_index, call_index, path + (leg,), frozenset(passed)))

            ride(seed[1], seed[2], (), frozenset())
            while heap:
                arrival, _, _, run_index, stop_index, path, visited = heapq.heappop(heap)
                run = runs[run_index]
                station = run.calls[stop_index].station
                if station == destination_id:
                    return path
                if len(path) >= 3:
                    continue
                state = (run_index, stop_index, len(path), visited)
                if state in seen:
                    continue
                seen.add(state)
                options = departures[station]
                start = bisect_left(options, (arrival, -1, -1))
                for departure, next_run, board in options[start:]:
                    if departure > seed[0] + 6 * 3600:
                        break
                    if next_run == run_index:
                        continue
                    wait = self.transfer_seconds(station, run.trip_id, runs[next_run].trip_id)
                    if wait is not None and departure >= arrival + wait:
                        ride(next_run, board, path, visited - {station})
            return None

        found = []
        for seed in departures[origin_id]:
            if seed[0] >= window_end:
                break
            path = best_journey(seed)
            if path:
                found.append(path)
        # Hide slow detours when a later departure arrives sooner with no more changes.
        efficient = [path for path in found if not any(
            other[0].departure >= path[0].departure and other[-1].arrival <= path[-1].arrival and len(other) <= len(path)
            and (other[0].departure > path[0].departure or other[-1].arrival < path[-1].arrival or len(other) < len(path))
            for other in found
        )]
        return tuple(efficient)


class TrainSchedules:
    def __init__(self, cache_file: Path = Path("lirr-schedule.zip")):
        self.cache_file = cache_file
        self._lock = Lock()
        self._timetable = None
        self._updated = 0.0
        self._retry_after = 0.0

    def timetable(self) -> tuple[Timetable, float, bool]:
        with self._lock:
            now = clock.time()
            if self._timetable is None and self.cache_file.exists():
                try:
                    self._timetable = Timetable.from_zip(self.cache_file.read_bytes())
                    self._updated = self.cache_file.stat().st_mtime
                except (OSError, ValueError, KeyError, zipfile.BadZipFile, ScheduleUnavailable):
                    LOG.warning("Discarding invalid LIRR schedule cache")
            if self._timetable is None or (now - self._updated >= 6 * 3600 and now >= self._retry_after):
                try:
                    response = requests.get(GTFS_URL, timeout=(5, 30))
                    response.raise_for_status()
                    timetable = Timetable.from_zip(response.content)
                    self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=self.cache_file.parent, delete=False) as file:
                        temporary = Path(file.name)
                        file.write(response.content)
                    try:
                        os.replace(temporary, self.cache_file)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self._timetable, self._updated = timetable, now
                    self._retry_after = 0.0
                except (requests.RequestException, OSError, ValueError, KeyError, zipfile.BadZipFile, ScheduleUnavailable) as exc:
                    self._retry_after = now + 300
                    if self._timetable is None or now - self._updated > 48 * 3600:
                        raise ScheduleUnavailable("LIRR schedules are temporarily unavailable. Please try again shortly.") from exc
                    LOG.warning("MTA schedule refresh failed; using the cached timetable")
            if now - self._updated > 48 * 3600:
                raise ScheduleUnavailable("The cached LIRR timetable is too old. Please try again shortly.")
            return self._timetable, self._updated, now - self._updated >= 6 * 3600

    def search(self, origin: str, destination: str, now: datetime | None = None) -> SearchResult:
        timetable, updated, stale = self.timetable()
        now = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
        paths = timetable.journeys(origin, destination, now)
        return SearchResult(origin, destination, now, datetime.fromtimestamp(now.timestamp() + 10800, TIMEZONE), paths,
                            datetime.fromtimestamp(updated, TIMEZONE), stale)


def format_trains(result: SearchResult, page: int = 0) -> tuple[str, int]:
    page_size = 5
    pages = max(1, (len(result.journeys) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    def display(timestamp):
        moment = datetime.fromtimestamp(timestamp, TIMEZONE)
        return moment.strftime("%I:%M %p").lstrip("0") + (moment.strftime(" (%b %d)") if moment.date() != result.start.date() else "")
    lines = [f"<b>LIRR: {escape(STATIONS[result.origin])} → {escape(STATIONS[result.destination])}</b>",
             f"Departures {result.start:%b %d}: {display(result.start.timestamp())}–{display(result.end.timestamp())} (New York time)",
             "Scheduled times; live delays and cancellations are not included."]
    if not result.journeys:
        lines.append("\nNo scheduled journey found in the next 3 hours (up to 2 changes, 6-hour journey limit).")
    for path in result.journeys[page * page_size:(page + 1) * page_size]:
        duration = int((path[-1].arrival - path[0].departure) // 60)
        kind = "Direct" if len(path) == 1 else f"{len(path) - 1} change(s)"
        lines.append(f"\n<b>{display(path[0].departure)} → {display(path[-1].arrival)}</b> · {duration} min · {kind}")
        for leg in path:
            lines.append(f"Train {escape(leg.train)}: {escape(leg.origin)} {display(leg.departure)} → {escape(leg.destination)} {display(leg.arrival)}")
    if pages > 1:
        lines.append(f"\nPage {page + 1}/{pages} · {len(result.journeys)} journeys")
    if result.stale:
        lines.append(f"\nUsing cached MTA timetable from {result.cached_at:%b %d %H:%M}.")
    lines.append('\nSource: MTA. Check live service in <a href="https://www.mta.info/traintime">TrainTime</a>.')
    return "\n".join(lines), pages
