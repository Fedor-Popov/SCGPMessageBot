"""Write the local event cache to a Google Calendar-style spreadsheet."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from events import Event
from sources.google_sheets import GoogleSheetsSource


HEADERS = ["Title", "Start", "End", "Description", "Location", "Publish", "Event ID"]


def _event_datetime(event: Event) -> datetime:
    parsed_time = None
    for fmt in ("%I:%M %p", "%H:%M", "%I %p"):
        try:
            parsed_time = datetime.strptime(event.time.strip(), fmt).time()
            break
        except ValueError:
            continue
    parsed_time = parsed_time or time(0, 0)
    return datetime.combine(event.date, parsed_time)


def _date_time_formula(value: datetime) -> str:
    """Build the date-time formula expected by the calendar spreadsheet."""
    return (
        f"=DATE({value.year};{value.month};{value.day})"
        f"+TIME({value.hour};{value.minute};{value.second})"
    )


def _description(event: Event) -> str:
    lines = []
    if event.speaker:
        speaker = event.speaker
        if event.affiliation:
            speaker += f" ({event.affiliation})"
        lines.append(f"Speaker: {speaker}")
    if event.title:
        lines.append(f"Title: {event.title}")
    if event.description:
        lines.append(f"Abstract: {event.description}")
    return "\n".join(lines)


def rows_for_events(events: list[Event]) -> list[list[object]]:
    """Build output rows, excluding events with no title or description."""
    rows: list[list[object]] = [HEADERS]
    eligible_events = (event for event in events if event.title.strip() or event.description.strip())
    for event in sorted(eligible_events, key=lambda item: (item.date, item.time, item.title.lower(), item.speaker.lower())):
        start = _event_datetime(event)
        end = start + timedelta(hours=1)
        rows.append([
            event.title,
            _date_time_formula(start),
            _date_time_formula(end),
            _description(event),
            event.location,
            True,
            "",
        ])
    return rows


def _row_identity(row: list[object]) -> tuple[str, str] | None:
    title = " ".join(str(row[0] if len(row) > 0 else "").casefold().split())
    start = "".join(str(row[1] if len(row) > 1 else "").casefold().split())
    description = " ".join(str(row[3] if len(row) > 3 else "").casefold().split())
    subject = title or description
    return (subject, start) if subject and start else None


def missing_rows(events: list[Event], existing_rows: list[list[object]]) -> list[list[object]]:
    """Return only cache rows not already present in the spreadsheet."""
    existing = {identity for row in existing_rows for identity in [_row_identity(row)] if identity}
    missing = []
    for row in rows_for_events(events)[1:]:
        identity = _row_identity(row)
        if identity is None or identity in existing:
            continue
        existing.add(identity)
        missing.append(row)
    return missing


class GoogleSheetsCacheWriter:
    """Append cache events that are not already in the output sheet."""

    name = "google-sheets-cache-export"

    def __init__(self, spreadsheet_id: str, token_file: Path | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._source = GoogleSheetsSource([], token_file)

    def write(self, events: list[Event]) -> int:
        service = self._source._service()
        existing_rows = service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range="A1:G",
            valueRenderOption="FORMULA",
        ).execute().get("values", [])
        rows = missing_rows(events, existing_rows[1:] if existing_rows else [])
        metadata = service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets.properties(sheetId,gridProperties.rowCount)",
        ).execute()
        sheet_properties = metadata["sheets"][0]["properties"]
        if not existing_rows:
            service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range="A1:G1",
                valueInputOption="RAW",
                body={"values": [HEADERS]},
            ).execute()
        if rows:
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range="A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
        row_count = sheet_properties["gridProperties"]["rowCount"]
        service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_properties["sheetId"],
                                "startRowIndex": 1,
                                "endRowIndex": row_count,
                                "startColumnIndex": 1,
                                "endColumnIndex": 3,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "numberFormat": {
                                        "type": "DATE_TIME",
                                        "pattern": "mm/dd/yyyy hh:mm:ss",
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.numberFormat",
                        }
                    },
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": sheet_properties["sheetId"],
                                "startRowIndex": 1,
                                "endRowIndex": row_count,
                                "startColumnIndex": 5,
                                "endColumnIndex": 6,
                            },
                            "rule": {
                                "condition": {"type": "BOOLEAN"},
                                "strict": True,
                                "showCustomUi": True,
                            },
                        }
                    },
                ]
            },
        ).execute()
        return len(rows)
