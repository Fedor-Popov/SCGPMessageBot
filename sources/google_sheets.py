"""Google Sheets event source using a previously authorized user token."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import Request
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from events import Event

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
ALIASES = {
    "date": {"date", "dates", "day", "when", "date of wednesday seminar", "date of seminar"},
    "title": {"title", "talk", "talk title", "name of talk"},
    "speaker": {"speaker", "presenter", "lecturer", "name"},
    "affiliation": {"affiliation", "institution", "university"},
    "time": {"time", "start time"},
    "location": {"location", "room", "where"},
    "description": {"description", "abstract", "talk abstract", "details"},
    "link": {"link", "url", "slides", "recording"},
    "start": {"start", "start date", "starts"},
    "publish": {"publish", "published", "visible"},
}


def _header(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _parse_date(value: Any) -> date:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d", "%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(year=date.today().year).date() if "%Y" not in fmt else parsed.date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported spreadsheet date {text!r}")


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    for fmt in (
        "%m/%d/%Y %H.%M.%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported spreadsheet start time {text!r}")


def _description_fields(description: str) -> tuple[str, str, str, str]:
    """Extract optional calendar-style metadata from a description."""
    fields: dict[str, str] = {}
    pattern = re.compile(r"(?im)^\s*(speaker|title|abstract)\s*:\s*(.*?)(?=^\s*(?:speaker|title|abstract)\s*:|\Z)", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(description):
        fields[match.group(1).lower()] = match.group(2).strip()
    speaker = fields.get("speaker", "")
    affiliation = ""
    affiliation_match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", speaker)
    if affiliation_match:
        speaker, affiliation = affiliation_match.groups()
    remaining = fields.get("abstract", "")
    if not fields:
        remaining = description
    return speaker, affiliation, fields.get("title", ""), remaining


def parse_rows(rows: list[list[Any]], source: str, default_time: str = "", default_location: str = "") -> list[Event]:
    if not rows:
        return []
    headers = {_header(value): index for index, value in enumerate(rows[0])}
    indexes = {field: next((headers[name] for name in aliases if name in headers), None) for field, aliases in ALIASES.items()}
    if indexes["title"] is None or (indexes["date"] is None and indexes["start"] is None):
        raise ValueError("Spreadsheet must contain a title and either date or start columns")

    def cell(row: list[Any], field: str) -> str:
        index = indexes[field]
        return str(row[index]).strip() if index is not None and index < len(row) else ""

    events: list[Event] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(str(value).strip() for value in row):
            continue
        try:
            if indexes["publish"] is not None and cell(row, "publish").lower() in {"false", "no", "0"}:
                continue
            description = cell(row, "description")
            embedded_speaker, embedded_affiliation, embedded_title, embedded_abstract = _description_fields(description)
            start = _parse_datetime(cell(row, "start")) if indexes["start"] is not None and cell(row, "start") else None
            events.append(Event(
                date=start.date() if start else _parse_date(cell(row, "date")),
                title=embedded_title or cell(row, "title"),
                speaker=cell(row, "speaker") or embedded_speaker,
                affiliation=cell(row, "affiliation") or embedded_affiliation,
                time=cell(row, "time") or (start.strftime("%I:%M %p").lstrip("0") if start else default_time),
                location=cell(row, "location") or default_location,
                description=embedded_abstract,
                link=cell(row, "link"),
                source=source,
            ))
        except ValueError as exc:
            print(f"Skipping spreadsheet row {row_number}: {exc}")
    return events


class GoogleSheetsSource:
    name = "google-sheets"

    def __init__(self, spreadsheet_ids: list[str], token_file: Path | None = None, cell_range: str = "A:ZZ", *, default_time: str = "", default_location: str = "", source_name: str | None = None) -> None:
        self.spreadsheet_ids = spreadsheet_ids
        self.token_file = token_file
        self.cell_range = cell_range
        self.default_time = default_time
        self.default_location = default_location
        if source_name:
            self.name = source_name

    def _service(self):
        if self.token_file and self.token_file.exists():
            credentials = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self.token_file.write_text(credentials.to_json())
                self.token_file.chmod(0o600)
            if not credentials.valid:
                raise RuntimeError("Google token is invalid or expired; rerun authorize_google.py locally")
        else:
            try:
                credentials, _ = google.auth.default(scopes=SCOPES)
            except DefaultCredentialsError as exc:
                raise RuntimeError(
                    "Google credentials not found. Run `gcloud auth application-default login` "
                    "or provide GOOGLE_OAUTH_TOKEN_FILE."
                ) from exc
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def fetch(self) -> list[Event]:
        service = self._service()
        events: list[Event] = []
        for spreadsheet_id in self.spreadsheet_ids:
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=self.cell_range
            ).execute()
            events.extend(parse_rows(result.get("values", []), f"google:{spreadsheet_id}", self.default_time, self.default_location))
        return events
