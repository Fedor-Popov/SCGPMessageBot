"""Backward-compatible imports for the Alessio calendar integration."""

from alessio_calendar import AlessioCalendar, HEADERS, UNPUBLISH_DELAY_SECONDS, rows_for_events, sync_alessio_calendar

GoogleSheetsCacheWriter = AlessioCalendar

__all__ = ["AlessioCalendar", "GoogleSheetsCacheWriter", "HEADERS", "UNPUBLISH_DELAY_SECONDS", "rows_for_events", "sync_alessio_calendar"]
