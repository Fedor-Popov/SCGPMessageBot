"""Journal Club source with its standard schedule details."""

from pathlib import Path

from sources.google_sheets import GoogleSheetsSource


class JournalClubSource(GoogleSheetsSource):
    name = "journal-club"

    def __init__(self, spreadsheet_ids: list[str], token_file: Path | None = None) -> None:
        super().__init__(spreadsheet_ids, token_file, default_time="2:00 PM", default_location="Common Room", source_name=self.name)
