"""Thermal Seminars event source."""

from dataclasses import replace

from website import WebsiteTalkSource


class ThermalSeminarsSource(WebsiteTalkSource):
    """EventSource adapter for the online Thermal Seminars schedule."""

    name = "thermal"
    # The website supplies a Berlin time for each talk; there is no safe fixed
    # New York default across the US/European daylight-saving transitions.
    default_time = ""
    default_location = "Online (Zoom)"

    def fetch(self):
        return [
            replace(
                event,
                time=event.time or self.default_time,
                location=event.location or self.default_location,
                source=event.source or self.name,
            )
            for event in super().fetch()
        ]
