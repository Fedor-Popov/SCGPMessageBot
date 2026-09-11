"""Thermal Seminars event source."""

from website import WebsiteTalkSource


class ThermalSeminarsSource(WebsiteTalkSource):
    """Standard EventSource adapter for the Thermal Seminars website."""

    name = "thermal"
    default_time = "11:00 AM"
    default_location = "102"

    def fetch(self):
        events = super().fetch()
        return [event.__class__(
            date=event.date, title=event.title, speaker=event.speaker,
            affiliation=event.affiliation, time=event.time or self.default_time,
            location=event.location or self.default_location,
            description=event.description, link=event.link,
            source=event.source or self.name,
        ) for event in events]
