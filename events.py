"""Shared event model and source interface.

Every event source should implement ``fetch()`` and return ``Event`` objects.
The Telegram bot does not need to know where an event came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Event:
    date: date
    title: str
    speaker: str = ""
    affiliation: str = ""
    time: str = ""
    location: str = ""
    description: str = ""
    link: str = ""
    source: str = ""


class EventSource(Protocol):
    name: str

    def fetch(self) -> list[Event]:
        """Fetch current/upcoming events from this source."""
