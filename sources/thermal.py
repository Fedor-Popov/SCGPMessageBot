"""Thermal Seminars event source."""

from website import WebsiteTalkSource


class ThermalSeminarsSource(WebsiteTalkSource):
    """EventSource adapter for the online Thermal Seminars schedule."""

    name = "thermal"
    # The website supplies a Berlin time for each talk; there is no safe fixed
    # New York default across the US/European daylight-saving transitions.
    default_time = ""
    default_location = "Online (Zoom)"

    def fetch(self):
        events = super().fetch()
        return [event.__class__(
            date=event.date, title=event.title, speaker=event.speaker,
            affiliation=event.affiliation, time=event.time or self.default_time,
            location=event.location or self.default_location,
            description=event.description, link=event.link,
            source=event.source or self.name,
        ) for event in events]
