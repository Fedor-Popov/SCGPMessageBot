"""Persistent events entered through the Telegram bot."""

from __future__ import annotations

from pathlib import Path

from cache import EventCache
from events import Event


class ManualEventSource:
    name = "manual-events"

    def __init__(self, path: Path) -> None:
        self.cache = EventCache(path)

    def fetch(self) -> list[Event]:
        return self.cache.load()

    def add(self, event: Event) -> None:
        events = self.cache.load()
        key = (event.date, event.title.casefold(), event.speaker.casefold())
        unique = {
            (existing.date, existing.title.casefold(), existing.speaker.casefold()): existing
            for existing in events
        }
        unique[key] = event
        self.cache.save(sorted(unique.values(), key=lambda item: (item.date, item.title.casefold())))
