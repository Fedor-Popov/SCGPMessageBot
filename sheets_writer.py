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


class GoogleSheetsCacheWriter:
    """Replace the output sheet's rows with the current local cache."""

    name = "google-sheets-cache-export"

    def __init__(self, spreadsheet_id: str, token_file: Path | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._source = GoogleSheetsSource([], token_file)

    def write(self, events: list[Event]) -> None:
        service = self._source._service()
        rows = rows_for_events(events)
        metadata = service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets.properties(sheetId,gridProperties.rowCount)",
        ).execute()
        sheet_properties = metadata["sheets"][0]["properties"]
        service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range="A2:G"
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"A1:G{len(rows)}",
            valueInputOption="USER_ENTERED",
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
