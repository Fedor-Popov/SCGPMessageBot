"""Telegram bot that announces talks from a Google Sheet."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

LOG = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    spreadsheet_id: str
    spreadsheet_range: str
    service_account_file: Path
    timezone: str
    announcement_hour: int
    subscribers_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        required = {"TELEGRAM_BOT_TOKEN", "GOOGLE_SPREADSHEET_ID", "GOOGLE_SERVICE_ACCOUNT_FILE"}
        missing = sorted(name for name in required if not os.getenv(name))
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        hour = int(os.getenv("ANNOUNCEMENT_HOUR", "10"))
        if not 0 <= hour <= 23:
            raise ValueError("ANNOUNCEMENT_HOUR must be between 0 and 23")
        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            spreadsheet_id=os.environ["GOOGLE_SPREADSHEET_ID"],
            spreadsheet_range=os.getenv("GOOGLE_SPREADSHEET_RANGE", "Talks!A:G"),
            service_account_file=Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]),
            timezone=os.getenv("BOT_TIMEZONE", "UTC"),
            announcement_hour=hour,
            subscribers_file=Path(os.getenv("SUBSCRIBERS_FILE", "subscribers.json")),
        )


@dataclass(frozen=True)
class Talk:
    date: date
    title: str
    time: str = ""
    speaker: str = ""
    location: str = ""
    description: str = ""
    link: str = ""


HEADER_ALIASES = {
    "date": {"date", "dates", "day", "when"},
    "title": {"title", "talk", "talk title"},
    "time": {"time", "start", "start time"},
    "speaker": {"speaker", "presenter", "lecturer", "name"},
    "location": {"location", "room", "where"},
    "description": {"description", "abstract", "talk abstract", "details"},
    "link": {"link", "url", "slides", "recording"},
}


def _normalise_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _parse_date(value: Any) -> date:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    for fmt in ("%m/%d", "%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(year=date.today().year).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported talk date: {text!r}; use YYYY-MM-DD or MM/DD")


def parse_rows(rows: list[list[Any]]) -> list[Talk]:
    if not rows:
        return []
    headers = {_normalise_header(value): index for index, value in enumerate(rows[0])}
    indexes: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        match = next((headers[alias] for alias in aliases if alias in headers), None)
        if match is not None:
            indexes[field] = match
    if "date" not in indexes or "title" not in indexes:
        raise ValueError("The sheet must contain date and title columns")

    def cell(row: list[Any], field: str) -> str:
        index = indexes.get(field)
        return str(row[index]).strip() if index is not None and index < len(row) else ""

    talks: list[Talk] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(str(cell_value).strip() for cell_value in row):
            continue
        try:
            talks.append(Talk(_parse_date(cell(row, "date")), cell(row, "title"), cell(row, "time"),
                              cell(row, "speaker"), cell(row, "location"), cell(row, "description"), cell(row, "link")))
        except ValueError as exc:
            LOG.warning("Skipping row %s: %s", row_number, exc)
    return sorted(talks, key=lambda talk: (talk.date, talk.time, talk.title.lower()))


class TalkRepository:
    def __init__(self, settings: Settings) -> None:
        credentials = Credentials.from_service_account_file(settings.service_account_file, scopes=SCOPES)
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._spreadsheet_id = settings.spreadsheet_id
        self._range = settings.spreadsheet_range

    def all_talks(self) -> list[Talk]:
        result = self._service.spreadsheets().values().get(
            spreadsheetId=self._spreadsheet_id, range=self._range
        ).execute()
        return parse_rows(result.get("values", []))


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


def format_talks(talks: Iterable[Talk], heading: str) -> str:
    talks = list(talks)
    if not talks:
        return f"{heading}\n\nNo talks found."
    lines = [heading, ""]
    for talk in talks:
        details = " · ".join(part for part in (talk.time, talk.speaker, talk.location) if part)
        lines.append(f"• {talk.title}" + (f" ({details})" if details else ""))
        if talk.description:
            lines.append(f"  {talk.description}")
        if talk.link:
            lines.append(f"  {talk.link}")
    return "\n".join(lines)


def display_date(value: date, include_weekday: bool = False) -> str:
    """Format dates without relying on platform-specific %-d support."""
    formatted = value.strftime("%B %d").replace(" 0", " ")
    return f"{value:%A}, {formatted}" if include_weekday else formatted


def build_application(settings: Settings) -> Application:
    repository = TalkRepository(settings)
    subscribers = SubscriberStore(settings.subscribers_file)
    timezone = ZoneInfo(settings.timezone)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.add(update.effective_chat.id)
            await update.effective_message.reply_text(
                "Subscribed to the Monday weekly talks announcement. Use /today for today’s talks."
            )

    async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.remove(update.effective_chat.id)
            await update.effective_message.reply_text("Unsubscribed from weekly announcements.")

    async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(timezone).date()
        talks = await asyncio.to_thread(repository.all_talks)
        matching_talks = (talk for talk in talks if talk.date == now)
        await update.effective_message.reply_text(
            format_talks(matching_talks, f"Talks for {display_date(now, include_weekday=True)}")
        )

    async def weekly_announcement(context: ContextTypes.DEFAULT_TYPE) -> None:
        today_date = datetime.now(timezone).date()
        start_date = week_start(today_date)
        end_date = start_date + timedelta(days=7)
        talks = await asyncio.to_thread(repository.all_talks)
        last_date = end_date - timedelta(days=1)
        matching_talks = (talk for talk in talks if start_date <= talk.date < end_date)
        message = format_talks(
            matching_talks,
            f"Talks this week ({start_date:%b} {start_date.day}–{last_date:%b} {last_date.day})",
        )
        for chat_id in await subscribers.all():
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                LOG.exception("Could not send weekly announcement to chat %s", chat_id)

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Use /today to see today’s talks. /start subscribes to Monday announcements; /stop unsubscribes."
        )

    application = Application.builder().token(settings.telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("help", help_command))
    if application.job_queue is None:
        raise RuntimeError('Install the job queue extra: pip install "python-telegram-bot[job-queue]"')
    # python-telegram-bot v20+ maps 0-6 to Sunday-Saturday.
    application.job_queue.run_daily(
        weekly_announcement,
        time=time(settings.announcement_hour, 0, tzinfo=timezone),
        days=(1,),
        name="weekly-talks",
    )
    return application


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    build_application(settings).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
