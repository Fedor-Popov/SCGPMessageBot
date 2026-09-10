"""Wednesday Seminar source with its standard schedule details."""

from pathlib import Path

from sources.google_sheets import GoogleSheetsSource


class WednesdaySeminarSource(GoogleSheetsSource):
    name = "wednesday-seminar"

    def __init__(self, spreadsheet_ids: list[str], token_file: Path | None = None) -> None:
        super().__init__(spreadsheet_ids, token_file, default_time="2:00 PM", default_location="313", source_name=self.name)
