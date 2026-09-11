"""Persistent events entered through the Telegram bot."""

from __future__ import annotations

import hashlib
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

    @staticmethod
    def event_id(event: Event) -> str:
        identity = "\x1f".join((event.date.isoformat(), event.title.casefold(), event.speaker.casefold()))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    def delete(self, event_id: str) -> Event | None:
        events = self.cache.load()
        deleted = next((event for event in events if self.event_id(event) == event_id), None)
        if deleted is None:
            return None
        self.cache.save([event for event in events if self.event_id(event) != event_id])
        return deleted
