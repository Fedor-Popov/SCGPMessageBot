"""Write the local event cache to a Google Calendar-style spreadsheet."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging
from pathlib import Path
from time import sleep

from events import Event
from sources.google_sheets import GoogleSheetsSource


HEADERS = ["Title", "Start", "End", "Description", "Location", "Publish", "Event ID"]
UNPUBLISH_DELAY_SECONDS = 120
LOG = logging.getLogger(__name__)


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
    """Rebuild the output sheet from the complete local event cache."""

    name = "google-sheets-cache-export"

    def __init__(
        self,
        spreadsheet_id: str,
        token_file: Path | None = None,
        unpublish_delay_seconds: int = UNPUBLISH_DELAY_SECONDS,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._source = GoogleSheetsSource([], token_file)
        self.unpublish_delay_seconds = unpublish_delay_seconds

    def write(self, events: list[Event]) -> int:
        service = self._source._service()
        metadata = service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets.properties(sheetId,gridProperties.rowCount)",
        ).execute()
        sheet_properties = metadata["sheets"][0]["properties"]
        row_count = sheet_properties["gridProperties"]["rowCount"]
        sheet_id = sheet_properties["sheetId"]
        output_rows = rows_for_events(events)

        if row_count > 1:
            service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [{
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": row_count,
                                "startColumnIndex": 5,
                                "endColumnIndex": 6,
                            },
                            "cell": {"userEnteredValue": {"boolValue": False}},
                            "fields": "userEnteredValue",
                        }
                    }]
                },
            ).execute()
            LOG.info(
                "Set Publish=false for the existing calendar sheet; waiting %d seconds before rebuild",
                self.unpublish_delay_seconds,
            )
            sleep(self.unpublish_delay_seconds)

        service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range="A:G",
            body={},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"A1:G{len(output_rows)}",
            valueInputOption="USER_ENTERED",
            body={"values": output_rows},
        ).execute()

        formatted_row_count = max(row_count, len(output_rows))
        service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": formatted_row_count,
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
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": formatted_row_count,
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
        return len(output_rows) - 1
