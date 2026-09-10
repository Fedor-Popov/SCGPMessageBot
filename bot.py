"""Telegram bot that announces talks from a cached public seminar website."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from cache import EventCache
from dotenv import load_dotenv
from events import Event
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

LOG = logging.getLogger(__name__)
load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    website_url: str
    cache_file: Path
    google_spreadsheet_ids: tuple[str, ...]
    google_token_file: Path
    timezone: str
    refresh_hour: int
    announcement_hour: int
    subscribers_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise RuntimeError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            website_url=os.getenv("SEMINAR_WEBSITE_URL", "https://sites.google.com/view/thermalseminars"),
            cache_file=Path(os.getenv("TALKS_CACHE_FILE", "talks-cache.json")),
            google_spreadsheet_ids=tuple(value.strip() for value in os.getenv("GOOGLE_SPREADSHEET_IDS", "").split(",") if value.strip()),
            google_token_file=Path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "google-token.json")),
            timezone=os.getenv("BOT_TIMEZONE", "America/New_York"),
            refresh_hour=int(os.getenv("REFRESH_HOUR", "3")),
            announcement_hour=int(os.getenv("ANNOUNCEMENT_HOUR", "10")),
            subscribers_file=Path(os.getenv("SUBSCRIBERS_FILE", "subscribers.json")),
        )


class SubscriberStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def _read(self) -> set[int]:
        if not self.path.exists():
            return set()
        return {int(value) for value in json.loads(self.path.read_text())}

    async def _write(self, subscribers: Iterable[int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(set(subscribers)), indent=2) + "\n")
        self.path.chmod(0o600)

    async def add(self, chat_id: int) -> None:
        async with self._lock:
            subscribers = await self._read()
            subscribers.add(chat_id)
            await self._write(subscribers)

    async def remove(self, chat_id: int) -> None:
        async with self._lock:
            subscribers = await self._read()
            subscribers.discard(chat_id)
            await self._write(subscribers)

    async def all(self) -> set[int]:
        async with self._lock:
            return await self._read()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def format_talks(talks: Iterable[Event], heading: str) -> str:
    talks = list(talks)
    if not talks:
        return f"{heading}\n\nNo talks found."
    lines = [heading, ""]
    for talk in talks:
        details = " · ".join(part for part in (talk.time, talk.speaker, talk.affiliation, talk.location) if part)
        lines.append(f"• {talk.title}" + (f" ({details})" if details else ""))
        if talk.description:
            lines.append(f"  {talk.description}")
        if talk.link:
            lines.append(f"  {talk.link}")
    return "\n".join(lines)


def display_date(value: date, include_weekday: bool = False) -> str:
    formatted = value.strftime("%B %d").replace(" 0", " ")
    return f"{value:%A}, {formatted}" if include_weekday else formatted


def build_application(settings: Settings) -> Application:
    from sources.google_sheets import GoogleSheetsSource
    from sources.thermal import ThermalSeminarsSource

    sources = [ThermalSeminarsSource(settings.website_url)]
    if settings.google_spreadsheet_ids:
        sources.append(GoogleSheetsSource(list(settings.google_spreadsheet_ids), settings.google_token_file))
    cache = EventCache(settings.cache_file)
    subscribers = SubscriberStore(settings.subscribers_file)
    timezone = ZoneInfo(settings.timezone)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.add(update.effective_chat.id)
            await update.effective_message.reply_text("Subscribed to Monday talks announcements. Use /today to see today’s talks.")

    async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.remove(update.effective_chat.id)
            await update.effective_message.reply_text("Unsubscribed from weekly announcements.")

    async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(timezone).date()
        talks = cache.load()
        await update.effective_message.reply_text(format_talks((talk for talk in talks if talk.date == now), f"Talks for {display_date(now, True)}"))

    async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await send_period(update, week_start(datetime.now(timezone).date()), "this week")

    async def nextweek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        start_date = week_start(datetime.now(timezone).date()) + timedelta(days=7)
        await send_period(update, start_date, "next week")

    async def send_period(update: Update, start_date: date, label: str) -> None:
        end_date = start_date + timedelta(days=7)
        talks = cache.load()
        matching_talks = (talk for talk in talks if start_date <= talk.date < end_date)
        last_date = end_date - timedelta(days=1)
        heading = f"Talks {label} ({start_date:%b} {start_date.day}–{last_date:%b} {last_date.day})"
        await update.effective_message.reply_text(format_talks(matching_talks, heading))

    async def refresh(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            events: list[Event] = []
            for source in sources:
                try:
                    events.extend(await asyncio.to_thread(source.fetch))
                except Exception:
                    LOG.exception("Source %s failed; continuing with other sources", source.name)
            if not events:
                raise RuntimeError("All event sources failed or returned no events")
            unique = {(event.date, event.title.lower(), event.speaker.lower()): event for event in events}
            cache.save(sorted(unique.values(), key=lambda event: (event.date, event.title.lower())))
            LOG.info("Refreshed %d events from %d sources into %s", len(unique), len(sources), settings.cache_file)
        except Exception:
            LOG.exception("Weekly seminar refresh failed; retaining existing cache")

    async def weekly_announcement(context: ContextTypes.DEFAULT_TYPE) -> None:
        today_date = datetime.now(timezone).date()
        start_date = week_start(today_date)
        end_date = start_date + timedelta(days=7)
        talks = cache.load()
        matching_talks = (talk for talk in talks if start_date <= talk.date < end_date)
        last_date = end_date - timedelta(days=1)
        message = format_talks(matching_talks, f"Talks this week ({start_date:%b} {start_date.day}–{last_date:%b} {last_date.day})")
        for chat_id in await subscribers.all():
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                LOG.exception("Could not send weekly announcement to chat %s", chat_id)

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text("Use /today, /week, or /nextweek. /start subscribes to Monday announcements; /stop unsubscribes.")

    application = Application.builder().token(settings.telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("nextweek", nextweek))
    application.add_handler(CommandHandler("help", help_command))
    if application.job_queue is None:
        raise RuntimeError('Install the job queue extra: pip install "python-telegram-bot[job-queue]"')
    # PTB v20+ maps 0-6 to Sunday-Saturday: Saturday=6, Monday=1.
    application.job_queue.run_daily(refresh, time=time(settings.refresh_hour, 0, tzinfo=timezone), days=(6,), name="weekly-refresh")
    application.job_queue.run_daily(weekly_announcement, time=time(settings.announcement_hour, 0, tzinfo=timezone), days=(1,), name="weekly-talks")
    if not cache.load():
        application.job_queue.run_once(refresh, when=timedelta(seconds=1), name="initial-refresh")
    return application


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    build_application(settings).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
