"""Thermal Seminars event source."""

from website import WebsiteTalkSource


class ThermalSeminarsSource(WebsiteTalkSource):
    """Standard EventSource adapter for the Thermal Seminars website."""

    name = "thermal"
