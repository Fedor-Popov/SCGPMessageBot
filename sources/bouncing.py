"""Recurring Bouncing Seminar event source."""

from __future__ import annotations

from datetime import date, timedelta

from events import Event


class BouncingSeminarSource:
    name = "bouncing-seminar"

    def __init__(self, weeks_ahead: int = 12) -> None:
        self.weeks_ahead = weeks_ahead

    def fetch(self) -> list[Event]:
        today = date.today()
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
        ]
