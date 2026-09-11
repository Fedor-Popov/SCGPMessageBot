"""Recurring Bouncing Seminar event source."""

from __future__ import annotations

from datetime import date, timedelta

from events import Event


class BouncingSeminarSource:
    name = "bouncing-seminar"

    def __init__(
        self,
        weeks_ahead: int = 12,
        end_date: date = date(2026, 10, 1),
        start_date: date | None = None,
    ) -> None:
        self.weeks_ahead = weeks_ahead
        self.end_date = end_date
        self.start_date = start_date

    def fetch(self) -> list[Event]:
        today = self.start_date or date.today()
        days_until_friday = (4 - today.weekday()) % 7
        first_friday = today + timedelta(days=days_until_friday)
        return [
            Event(
                date=first_friday + timedelta(weeks=week),
                title="Bouncing Seminar",
                time="11:00 AM",
                location="Common Room",
                source=self.name,
            )
            for week in range(self.weeks_ahead)
            if first_friday + timedelta(weeks=week) < self.end_date
        ]
