"""Local JSON cache shared by all event sources."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from events import Event


# Sources removed from the bot must not survive in the retained history.
REMOVED_EVENT_SOURCES = frozenset({"bouncing-seminar"})


class EventCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Event]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text())
        return [Event(date.fromisoformat(item.pop("date")), **item) for item in payload.get("events", [])]

    def save(self, events: list[Event]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now().astimezone().isoformat(),
            "events": [{**asdict(event), "date": event.date.isoformat()} for event in events],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        self.path.chmod(0o600)

    def save_refresh(self, fresh_events: list[Event], today: date) -> list[Event]:
        """Replace current/upcoming data while retaining previously cached history."""
        historical = [event for event in self.load() if event.date < today and event.source not in REMOVED_EVENT_SOURCES]
        fresh_events = [event for event in fresh_events if event.source not in REMOVED_EVENT_SOURCES]
        unique = {(event.date, event.title.casefold(), event.speaker.casefold()): event for event in historical + fresh_events}
        merged = sorted(unique.values(), key=lambda event: (event.date, event.title.casefold(), event.speaker.casefold()))
        self.save(merged)
        return merged
