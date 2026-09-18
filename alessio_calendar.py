"""One-purpose integration for rebuilding Alessio's Google Calendar sheet."""

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
    for fmt in ("%I:%M %p", "%H:%M", "%I %p"):
        try:
            return datetime.combine(event.date, datetime.strptime(event.time.strip(), fmt).time())
        except ValueError:
            pass
    return datetime.combine(event.date, time(0, 0))


def _date_time_formula(value: datetime) -> str:
    return f"=DATE({value.year};{value.month};{value.day})+TIME({value.hour};{value.minute};{value.second})"


def _description(event: Event) -> str:
    lines = []
    if event.speaker:
        lines.append(f"Speaker: {event.speaker}" + (f" ({event.affiliation})" if event.affiliation else ""))
    if event.title:
        lines.append(f"Title: {event.title}")
    if event.description:
        lines.append(f"Abstract: {event.description}")
    return "\n".join(lines)


def rows_for_events(events: list[Event]) -> list[list[object]]:
    """Build Alessio's rows, excluding events with no title and no abstract."""
    rows: list[list[object]] = [HEADERS]
    eligible = (event for event in events if event.title.strip() or event.description.strip())
    for event in sorted(eligible, key=lambda item: (item.date, item.time, item.title.lower(), item.speaker.lower())):
        start = _event_datetime(event)
        rows.append([event.title, _date_time_formula(start), _date_time_formula(start + timedelta(hours=1)), _description(event), event.location, True, ""])
    return rows


class AlessioCalendar:
    """Rebuild the configured Google Calendar-style sheet from local events."""

    def __init__(self, spreadsheet_id: str, token_file: Path | None = None, unpublish_delay_seconds: int = UNPUBLISH_DELAY_SECONDS) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._source = GoogleSheetsSource([], token_file)
        self.unpublish_delay_seconds = unpublish_delay_seconds

    def sync(self, events: list[Event]) -> int:
        service = self._source._service()
        metadata = service.spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties(sheetId,gridProperties.rowCount)").execute()
        properties = metadata["sheets"][0]["properties"]
        row_count, sheet_id = properties["gridProperties"]["rowCount"], properties["sheetId"]
        output_rows = rows_for_events(events)
        if row_count > 1:
            service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [{"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 5, "endColumnIndex": 6},
                "cell": {"userEnteredValue": {"boolValue": False}}, "fields": "userEnteredValue",
            }}]}).execute()
            LOG.info("Set Publish=false for Alessio's calendar; waiting %d seconds before rebuild", self.unpublish_delay_seconds)
            sleep(self.unpublish_delay_seconds)
        service.spreadsheets().values().clear(spreadsheetId=self.spreadsheet_id, range="A:G", body={}).execute()
        service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"A1:G{len(output_rows)}", valueInputOption="USER_ENTERED", body={"values": output_rows}).execute()
        formatted_row_count = max(row_count, len(output_rows))
        service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": formatted_row_count, "startColumnIndex": 1, "endColumnIndex": 3}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "mm/dd/yyyy hh:mm:ss"}}}, "fields": "userEnteredFormat.numberFormat"}},
            {"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": formatted_row_count, "startColumnIndex": 5, "endColumnIndex": 6}, "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True}}},
        ]}).execute()
        return len(output_rows) - 1

    def write(self, events: list[Event]) -> int:
        """Compatibility alias for deployments using the former writer name."""
        return self.sync(events)


def sync_alessio_calendar(events: list[Event], spreadsheet_id: str, token_file: Path | None = None, *, unpublish_delay_seconds: int = UNPUBLISH_DELAY_SECONDS) -> int:
    """Single-call API for rebuilding Alessio's calendar."""
    return AlessioCalendar(spreadsheet_id, token_file, unpublish_delay_seconds).sync(events)
