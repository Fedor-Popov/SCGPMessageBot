"""Publish the bot's events to a dedicated public Google Calendar."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import logging
from pathlib import Path
from urllib.parse import quote

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from events import Event

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_CALENDAR_NAME = "SCGP Seminars"
LOG = logging.getLogger(__name__)


def _event_time(value: str) -> time | None:
    if not value.strip():
        return None
    for fmt in ("%I:%M %p", "%H:%M", "%I %p"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Unsupported event time {value!r}")


def _description(event: Event) -> str:
    lines = []
    if event.speaker:
        lines.append(f"Speaker: {event.speaker}" + (f" ({event.affiliation})" if event.affiliation else ""))
    if event.description:
        lines.append(event.description)
    if event.link:
        lines.append(event.link)
    return "\n\n".join(lines)


def _calendar_event(event: Event, timezone: str) -> dict:
    summary = event.title.strip() or event.speaker.strip() or "SCGP Seminar"
    description = _description(event)
    start_time = _event_time(event.time)
    if start_time is None:
        end_date = event.date + timedelta(days=1)
        start = {"date": event.date.isoformat()}
        end = {"date": end_date.isoformat()}
    else:
        start_dt = datetime.combine(event.date, start_time)
        end_dt = start_dt + timedelta(hours=1)
        start = {"dateTime": start_dt.isoformat(), "timeZone": timezone}
        end = {"dateTime": end_dt.isoformat(), "timeZone": timezone}
    body = {
        "summary": summary,
        "start": start,
        "end": end,
        "extendedProperties": {"private": {"scgpManaged": "true"}},
    }
    if description:
        body["description"] = description
    if event.location:
        body["location"] = event.location
    return body


def calendar_url(calendar_id: str) -> str:
    return f"https://calendar.google.com/calendar/u/0/r?cid={quote(calendar_id, safe='')}"


class GoogleCalendarPublisher:
    """Create and rebuild one public calendar owned by the configured account."""

    def __init__(
        self,
        calendar_id: str = "",
        token_file: Path | None = None,
        state_file: Path = Path("google-calendar.json"),
        name: str = DEFAULT_CALENDAR_NAME,
        timezone: str = "America/New_York",
    ) -> None:
        self.calendar_id = calendar_id.strip()
        self.token_file = token_file
        self.state_file = state_file
        self.name = name
        self.timezone = timezone

    def _credentials(self):
        if self.token_file and self.token_file.exists():
            credentials = Credentials.from_authorized_user_file(self.token_file, CALENDAR_SCOPES)
            if not credentials.has_scopes(CALENDAR_SCOPES):
                raise RuntimeError("Google OAuth token lacks Calendar access; rerun authorize_google.py")
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self.token_file.write_text(credentials.to_json())
                self.token_file.chmod(0o600)
            if not credentials.valid:
                raise RuntimeError("Google Calendar token is invalid or expired; rerun authorize_google.py")
            return credentials
        try:
            credentials, _ = google.auth.default(scopes=CALENDAR_SCOPES)
            return credentials
        except DefaultCredentialsError as exc:
            raise RuntimeError(
                "Google credentials not found. Run authorize_google.py with Calendar access."
            ) from exc

    def _service(self):
        return build("calendar", "v3", credentials=self._credentials(), cache_discovery=False)

    def _stored_id(self) -> str:
        if self.calendar_id:
            return self.calendar_id
        if self.state_file.exists():
            try:
                value = json.loads(self.state_file.read_text()).get("calendar_id", "")
            except (OSError, ValueError):
                value = ""
            if value:
                self.calendar_id = str(value)
        return self.calendar_id

    def _save_id(self, calendar_id: str) -> None:
        self.calendar_id = calendar_id
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"calendar_id": calendar_id}, indent=2) + "\n")
        self.state_file.chmod(0o600)

    def ensure_calendar(self, service) -> str:
        calendar_id = self._stored_id()
        if calendar_id:
            return calendar_id
        page_token = None
        while True:
            result = service.calendarList().list(pageToken=page_token, maxResults=250).execute()
            for item in result.get("items", []):
                if item.get("summary") == self.name:
                    self._save_id(item["id"])
                    return item["id"]
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        created = service.calendars().insert(body={
            "summary": self.name,
            "description": "Seminars and talks at the Simons Center for Geometry and Physics.",
            "timeZone": self.timezone,
        }).execute()
        calendar_id = created["id"]
        service.acl().insert(
            calendarId=calendar_id,
            sendNotifications=False,
            body={"scope": {"type": "default"}, "role": "reader"},
        ).execute()
        self._save_id(calendar_id)
        LOG.info("Created public Google Calendar %s", self.name)
        return calendar_id

    def sync(self, events: list[Event]) -> int:
        service = self._service()
        calendar_id = self.ensure_calendar(service)
        page_token = None
        managed_ids: list[str] = []
        while True:
            result = service.events().list(
                calendarId=calendar_id,
                privateExtendedProperty="scgpManaged=true",
                showDeleted=False,
                maxResults=2500,
                pageToken=page_token,
            ).execute()
            managed_ids.extend(item["id"] for item in result.get("items", []) if item.get("id"))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        for event_id in managed_ids:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        eligible = [event for event in events if event.title.strip() or event.description.strip()]
        for event in eligible:
            service.events().insert(calendarId=calendar_id, body=_calendar_event(event, self.timezone)).execute()
        return len(eligible)

    @property
    def public_url(self) -> str:
        calendar_id = self._stored_id()
        return calendar_url(calendar_id) if calendar_id else ""
