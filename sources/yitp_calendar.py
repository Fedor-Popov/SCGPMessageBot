"""Public YITP Google Calendar event source."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

import requests

from events import Event


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_ICAL_URL = "https://calendar.google.com/calendar/ical/cal%40max2.physics.sunysb.edu/public/basic.ics"
_ESCAPED = re.compile(r"\\([\\,;nN])")


def _unescape(value: str) -> str:
    return _ESCAPED.sub(lambda match: "\n" if match.group(1).lower() == "n" else match.group(1), value).strip()


def _unfold(payload: str) -> list[str]:
    lines: list[str] = []
    for line in payload.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _property(line: str, name: str) -> tuple[str, dict[str, str]] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    parts = key.split(";")
    if parts[0].upper() != name:
        return None
    parameters = {}
    for part in parts[1:]:
        if "=" in part:
            parameter, parameter_value = part.split("=", 1)
            parameters[parameter.upper()] = parameter_value.strip('"')
    return _unescape(value), parameters


def _calendar_datetime(value: str, parameters: dict[str, str]) -> datetime:
    if len(value) == 8:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=NEW_YORK)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(NEW_YORK)
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    timezone = parameters.get("TZID")
    return parsed.replace(tzinfo=ZoneInfo(timezone) if timezone else NEW_YORK).astimezone(NEW_YORK)


def parse_icalendar(payload: str) -> list[Event]:
    events: list[Event] = []
    current: dict[str, tuple[str, dict[str, str]]] | None = None
    for line in _unfold(payload):
        if line.upper() == "BEGIN:VEVENT":
            current = {}
        elif line.upper() == "END:VEVENT" and current is not None:
            start = current.get("DTSTART")
            summary = current.get("SUMMARY", ("", {}))[0]
            if start and summary:
                start_dt = _calendar_datetime(*start)
                end_dt = _calendar_datetime(*current["DTEND"]) if "DTEND" in current else None
                location = current.get("LOCATION", ("", {}))[0]
                description = current.get("DESCRIPTION", ("", {}))[0]
                link = current.get("URL", ("", {}))[0]
                events.append(Event(
                    date=start_dt.date(),
                    title=summary,
                    time="" if start[1].get("VALUE") == "DATE" else start_dt.strftime("%I:%M %p").lstrip("0"),
                    location=location,
                    description=description,
                    link=link,
                    source="yitp-calendar",
                ))
            current = None
        elif current is not None:
            for name in ("DTSTART", "DTEND", "SUMMARY", "DESCRIPTION", "LOCATION", "URL"):
                parsed = _property(line, name)
                if parsed is not None:
                    current[name] = parsed
                    break
    return sorted(events, key=lambda event: (event.date, event.time, event.title.casefold()))


def filter_events(events: list[Event], today: date, lookback_days: int = 30) -> list[Event]:
    """Keep upcoming events and a short recent window for Previous events."""
    first_date = today - timedelta(days=lookback_days)
    return [event for event in events if event.date >= first_date]


class YITPCalendarSource:
    """Fetch events from the public YITP Google Calendar iCalendar feed."""

    name = "yitp-calendar"

    def __init__(self, url: str = DEFAULT_ICAL_URL, lookback_days: int = 30) -> None:
        self.url = url
        self.lookback_days = lookback_days

    def fetch(self) -> list[Event]:
        response = requests.get(self.url, timeout=30, headers={"User-Agent": "SCGPMessageBot/1.0"})
        response.raise_for_status()
        events = filter_events(parse_icalendar(response.text), date.today(), self.lookback_days)
        if not events:
            raise ValueError("The YITP calendar was fetched, but no events were parsed")
        return events
