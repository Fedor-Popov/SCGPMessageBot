"""Telegram bot that announces talks from a cached public seminar website."""

from __future__ import annotations

import asyncio
from html import escape
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from access import PasswordAccess
from cache import EventCache
from dotenv import load_dotenv
from events import Event
from lunch import LessingsLunchSource, LunchCache, LunchMenu
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

LOG = logging.getLogger(__name__)
load_dotenv()
ADD_DATE, ADD_TITLE, ADD_SPEAKER, ADD_ABSTRACT, ADD_TIME, ADD_LOCATION = range(6)
ADD_PASSWORD, DELETE_PASSWORD = range(6, 8)


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    website_url: str
    lunch_menu_url: str
    lunch_cache_file: Path
    cache_file: Path
    manual_events_file: Path
    google_spreadsheet_ids: tuple[str, ...]
    wednesday_spreadsheet_ids: tuple[str, ...]
    journal_club_spreadsheet_ids: tuple[str, ...]
    thermal_spreadsheet_ids: tuple[str, ...]
    additional_spreadsheet_ids: tuple[str, ...]
    cache_export_spreadsheet_id: str
    google_token_file: Path
    timezone: str
    refresh_hour: int
    refresh_interval_hours: int
    announcement_hour: int
    subscribers_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise RuntimeError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
        spreadsheet_ids = tuple(value.strip() for value in os.getenv("GOOGLE_SPREADSHEET_IDS", "").split(",") if value.strip())
        wednesday_ids = tuple(value.strip() for value in os.getenv("GOOGLE_WEDNESDAY_SPREADSHEET_IDS", "").split(",") if value.strip())
        journal_ids = tuple(value.strip() for value in os.getenv("GOOGLE_JOURNAL_CLUB_SPREADSHEET_IDS", "").split(",") if value.strip())
        thermal_ids = tuple(value.strip() for value in os.getenv("GOOGLE_THERMAL_SPREADSHEET_IDS", "").split(",") if value.strip())
        additional_ids = tuple(value.strip() for value in os.getenv("GOOGLE_ADDITIONAL_SPREADSHEET_IDS", "").split(",") if value.strip())
        export_id = os.getenv("GOOGLE_CACHE_EXPORT_SPREADSHEET_ID", "").strip() or (additional_ids[0] if additional_ids else "")
        additional_ids = tuple(value for value in additional_ids if value != export_id)
        if spreadsheet_ids and not (wednesday_ids or journal_ids or thermal_ids):
            # Backward-compatible mapping for the original two-sheet setup:
            # first sheet = Wednesday Seminar, second sheet = Journal Club.
            wednesday_ids, journal_ids = spreadsheet_ids[:1], spreadsheet_ids[1:]
        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            website_url=os.getenv("SEMINAR_WEBSITE_URL", "https://sites.google.com/view/thermalseminars"),
            lunch_menu_url=os.getenv("LUNCH_MENU_URL", "https://www.lessings.com/my/lfsm/weekly-menu/simons-center"),
            lunch_cache_file=Path(os.getenv("LUNCH_CACHE_FILE", "lunch-cache.json")),
            cache_file=Path(os.getenv("TALKS_CACHE_FILE", "talks-cache.json")),
            manual_events_file=Path(os.getenv("MANUAL_EVENTS_FILE", "manual-events.json")),
            google_spreadsheet_ids=spreadsheet_ids,
            wednesday_spreadsheet_ids=wednesday_ids,
            journal_club_spreadsheet_ids=journal_ids,
            thermal_spreadsheet_ids=thermal_ids,
            additional_spreadsheet_ids=additional_ids,
            cache_export_spreadsheet_id=export_id,
            google_token_file=Path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "google-token.json")),
            timezone=os.getenv("BOT_TIMEZONE", "America/New_York"),
            refresh_hour=int(os.getenv("REFRESH_HOUR", "3")),
            refresh_interval_hours=int(os.getenv("REFRESH_INTERVAL_HOURS", "1")),
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
        return f"{escape(heading)}\n\nNo talks found."
    talks_by_day: dict[date, list[str]] = {}
    for talk in sorted(talks, key=lambda item: (item.date, item.title.lower(), item.speaker.lower())):
        speaker = f"<i>{escape(talk.speaker)}</i>" if talk.speaker else ""
        details = " · ".join(
            part for part in (escape(talk.time), speaker, escape(talk.affiliation), escape(talk.location)) if part
        )
        title = f"<b>{escape(talk.title)}</b>" if talk.title else ""
        lines = [f"• {title}" + (f" ({details})" if details else "")]
        if talk.description:
            lines.append(f"  {escape(talk.description)}")
        if talk.link:
            lines.append(f"  {escape(talk.link)}")
        talks_by_day.setdefault(talk.date, []).append("\n".join(lines))
    day_blocks = [
        f"{escape(day.strftime('%A'))}:\n" + "\n\n".join(entries)
        for day, entries in sorted(talks_by_day.items())
    ]
    return f"{escape(heading)}\n\n" + "\n\n".join(day_blocks)


def display_date(value: date, include_weekday: bool = False) -> str:
    formatted = value.strftime("%B %d").replace(" 0", " ")
    return f"{value:%A}, {formatted}" if include_weekday else formatted


def parse_event_date(value: str, today: date | None = None) -> date:
    text = value.strip()
    today = today or date.today()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(f"{today.year}/{text}", "%Y/%m/%d").date()
    except ValueError as exc:
        raise ValueError("Use YYYY-MM-DD or MM/DD/YYYY") from exc


def normalize_event_time(value: str) -> str:
    text = value.strip()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(text.upper(), fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    raise ValueError("Use a time such as 2:00 PM or 14:00")


def optional_field(value: str) -> str:
    value = value.strip()
    return "" if value == "-" else value


def menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Today", callback_data="today"),
            InlineKeyboardButton("This week", callback_data="week"),
        ],
        [
            InlineKeyboardButton("Next week", callback_data="nextweek"),
            InlineKeyboardButton("Lunch", callback_data="lunch"),
        ],
        [
            InlineKeyboardButton("Help", callback_data="help"),
        ],
        [InlineKeyboardButton("Stop announcements", callback_data="stop")],
    ])


def format_lunch(menu: LunchMenu) -> str:
    lines = [f"🍽 <b>Lunch — {escape(menu.date.strftime('%A, %B %-d'))}</b>"]
    for section, items in menu.sections:
        if not items:
            continue
        lines.append(f"\n<b>{escape(section)}</b>")
        for item in items:
            line = f"• {escape(item.name)}"
            if item.description:
                line += f" — {escape(item.description)}"
            lines.append(line)
    if len(lines) == 1:
        lines.append("\nNo lunch items found.")
    return "\n".join(lines)


def build_application(settings: Settings) -> Application:
    from sources.journal_club import JournalClubSource
    from sources.bouncing import BouncingSeminarSource
    from sources.thermal import ThermalSeminarsSource
    from sources.wednesday import WednesdaySeminarSource
    from sources.manual import ManualEventSource

    manual_events = ManualEventSource(settings.manual_events_file)
    password_access = PasswordAccess()
    sources = [ThermalSeminarsSource(settings.website_url), BouncingSeminarSource(), manual_events]
    lunch_source = LessingsLunchSource(settings.lunch_menu_url)
    lunch_cache = LunchCache(settings.lunch_cache_file)
    if settings.wednesday_spreadsheet_ids:
        sources.append(WednesdaySeminarSource(list(settings.wednesday_spreadsheet_ids), settings.google_token_file))
    if settings.journal_club_spreadsheet_ids:
        sources.append(JournalClubSource(list(settings.journal_club_spreadsheet_ids), settings.google_token_file))
    if settings.thermal_spreadsheet_ids:
        from sources.google_sheets import GoogleSheetsSource
        sources.append(GoogleSheetsSource(list(settings.thermal_spreadsheet_ids), settings.google_token_file, default_time=ThermalSeminarsSource.default_time, default_location=ThermalSeminarsSource.default_location, source_name="thermal-seminar-sheet"))
    if settings.additional_spreadsheet_ids:
        from sources.google_sheets import GoogleSheetsSource
        sources.append(GoogleSheetsSource(list(settings.additional_spreadsheet_ids), settings.google_token_file, source_name="additional-google-sheet"))
    cache = EventCache(settings.cache_file)
    cache_writer = None
    if settings.cache_export_spreadsheet_id:
        from sheets_writer import GoogleSheetsCacheWriter
        cache_writer = GoogleSheetsCacheWriter(settings.cache_export_spreadsheet_id, settings.google_token_file)
    spreadsheet_sync_lock = asyncio.Lock()
    subscribers = SubscriberStore(settings.subscribers_file)
    timezone = ZoneInfo(settings.timezone)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.add(update.effective_chat.id)
            await update.effective_message.reply_text(
                "Subscribed to Monday talks announcements. Choose an option below, or use /today, /week, or /nextweek.",
                reply_markup=menu_markup(),
            )

    async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            await subscribers.remove(update.effective_chat.id)
            await update.effective_message.reply_text("Unsubscribed from weekly announcements.", reply_markup=menu_markup())

    async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(timezone).date()
        talks = cache.load()
        await update.effective_message.reply_text(
            format_talks((talk for talk in talks if talk.date == now), f"Talks for {display_date(now, True)}"),
            reply_markup=menu_markup(),
            parse_mode=ParseMode.HTML,
        )

    async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await send_period(update, week_start(datetime.now(timezone).date()), "this week")

    async def nextweek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        start_date = week_start(datetime.now(timezone).date()) + timedelta(days=7)
        await send_period(update, start_date, "next week")

    async def lunch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        menu = lunch_cache.load()
        message = format_lunch(menu) if menu else "🍽 The lunch menu is not available yet. Please try again shortly."
        await update.effective_message.reply_text(
            message,
            reply_markup=menu_markup(),
            parse_mode=ParseMode.HTML,
        )

    async def password_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
        password_access.revoke(update.effective_user.id, update.effective_chat.id)
        context.user_data.pop("new_event", None)
        if update.effective_chat.type != "private":
            await update.effective_message.reply_text("Please use this command in a private chat with the bot.")
            return ConversationHandler.END
        await update.effective_message.reply_text("Enter the password, or /cancel to stop.")
        return state

    async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        return await password_prompt(update, context, ADD_PASSWORD)

    async def delete_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        return await password_prompt(update, context, DELETE_PASSWORD)

    async def accept_password(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> str | None:
        password = update.effective_message.text or ""
        try:
            await update.effective_message.delete()
        except TelegramError:
            pass
        try:
            return password_access.authenticate(update.effective_user.id, update.effective_chat.id, action, password)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return None

    async def add_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if await accept_password(update, context, "add") is None:
            return ConversationHandler.END
        context.user_data["new_event"] = {}
        await update.effective_message.reply_text(
            "Enter the event date (YYYY-MM-DD or MM/DD/YYYY). Send /cancel to stop."
        )
        return ADD_DATE

    async def delete_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        nonce = await accept_password(update, context, "delete")
        if nonce is not None:
            await delete_added_event(update, context, nonce)
        return ConversationHandler.END

    async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        try:
            event_date = parse_event_date(update.effective_message.text or "", datetime.now(timezone).date())
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return ADD_DATE
        context.user_data["new_event"]["date"] = event_date
        await update.effective_message.reply_text("Enter the talk title. Send - to leave it blank.")
        return ADD_TITLE

    async def add_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["new_event"]["title"] = optional_field(update.effective_message.text or "")
        await update.effective_message.reply_text("Enter the speaker name. Send - to leave it blank.")
        return ADD_SPEAKER

    async def add_event_speaker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["new_event"]["speaker"] = optional_field(update.effective_message.text or "")
        await update.effective_message.reply_text("Enter the abstract. Send - to leave it blank.")
        return ADD_ABSTRACT

    async def add_event_abstract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["new_event"]["description"] = optional_field(update.effective_message.text or "")
        await update.effective_message.reply_text("Enter the start time, for example 2:00 PM or 14:00.")
        return ADD_TIME

    async def add_event_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        try:
            event_time = normalize_event_time(update.effective_message.text or "")
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return ADD_TIME
        context.user_data["new_event"]["time"] = event_time
        await update.effective_message.reply_text("Enter the location. Send - to leave it blank.")
        return ADD_LOCATION

    async def add_event_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        data = context.user_data.pop("new_event", {})
        if not password_access.allowed(update.effective_user.id, update.effective_chat.id, "add"):
            await update.effective_message.reply_text("Authorization expired. Use /add again.")
            return ConversationHandler.END
        password_access.revoke(update.effective_user.id, update.effective_chat.id)
        data["location"] = optional_field(update.effective_message.text or "")
        if not data.get("title") and not data.get("description"):
            await update.effective_message.reply_text(
                "The event was not saved because both title and abstract were blank.",
                reply_markup=menu_markup(),
            )
            return ConversationHandler.END
        event = Event(
            date=data["date"],
            title=data.get("title", ""),
            speaker=data.get("speaker", ""),
            description=data.get("description", ""),
            time=data["time"],
            location=data["location"],
            source=manual_events.name,
        )
        await asyncio.to_thread(manual_events.add, event)
        await refresh(context, sync_spreadsheet=False)
        if cache_writer is None:
            result = "Event saved locally, but the calendar spreadsheet is not configured."
        else:
            context.application.create_task(export_cache(context), name="manual-event-calendar-rebuild")
            result = "Event saved. The calendar spreadsheet rebuild has started and will finish in about 2 minutes."
        await update.effective_message.reply_text(result, reply_markup=menu_markup())
        return ConversationHandler.END

    async def cancel_add_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        password_access.revoke(update.effective_user.id, update.effective_chat.id)
        context.user_data.pop("new_event", None)
        await update.effective_message.reply_text("Event entry cancelled.", reply_markup=menu_markup())
        return ConversationHandler.END

    async def delete_added_event(update: Update, context: ContextTypes.DEFAULT_TYPE, nonce: str) -> None:
        today_date = datetime.now(timezone).date()
        future_events = [event for event in manual_events.fetch() if event.date >= today_date]
        if not future_events:
            await update.effective_message.reply_text(
                "There are no future events created with /add.",
                reply_markup=menu_markup(),
            )
            return
        buttons = []
        for event in sorted(future_events, key=lambda item: (item.date, item.time, item.title.casefold())):
            subject = event.title or event.description or event.speaker or "Untitled event"
            label = f"{event.date:%b %d} · {subject}"
            if len(label) > 60:
                label = label[:57].rstrip() + "…"
            buttons.append([
                InlineKeyboardButton(
                    label,
                    callback_data=f"delete:{nonce}:{manual_events.event_id(event)}",
                )
            ])
        await update.effective_message.reply_text(
            "Choose a future event added with /add to delete:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def delete_added_event_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        parts = (query.data or "").split(":")
        if len(parts) != 3 or not password_access.allowed(
            update.effective_user.id, update.effective_chat.id, "delete", parts[1]
        ):
            await query.answer("Authorization expired. Use /delete and enter the password again.", show_alert=True)
            return
        event_id = parts[2]
        eligible = any(manual_events.event_id(event) == event_id and event.date >= datetime.now(timezone).date()
                       for event in manual_events.fetch())
        if not eligible:
            await query.answer("That event is no longer available for deletion.", show_alert=True)
            return
        password_access.revoke(update.effective_user.id, update.effective_chat.id)
        await query.answer()
        deleted = await asyncio.to_thread(manual_events.delete, event_id)
        if deleted is None:
            await query.edit_message_text(
                "That event was already deleted or is no longer available.",
                reply_markup=menu_markup(),
            )
            return

        await refresh(context, sync_spreadsheet=False)
        subject = deleted.title or deleted.description or deleted.speaker or "Untitled event"
        if cache_writer is None:
            result = f"Deleted {subject}. The calendar spreadsheet is not configured."
        else:
            context.application.create_task(export_cache(context), name="manual-event-deletion-calendar-rebuild")
            result = f"Deleted {subject}. The calendar spreadsheet rebuild will finish in about 2 minutes."
        await query.edit_message_text(result, reply_markup=menu_markup())

    async def refresh_lunch(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            menu = await asyncio.to_thread(lunch_source.fetch)
            lunch_cache.save(menu)
            LOG.info("Refreshed lunch menu for %s into %s", menu.date, settings.lunch_cache_file)
        except Exception:
            LOG.exception("Lunch menu refresh failed; retaining existing cache")

    async def send_period(update: Update, start_date: date, label: str) -> None:
        end_date = start_date + timedelta(days=7)
        talks = cache.load()
        matching_talks = (talk for talk in talks if start_date <= talk.date < end_date)
        last_date = end_date - timedelta(days=1)
        heading = f"Talks {label} ({start_date:%b} {start_date.day}–{last_date:%b} {last_date.day})"
        await update.effective_message.reply_text(
            format_talks(matching_talks, heading),
            reply_markup=menu_markup(),
            parse_mode=ParseMode.HTML,
        )

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        handlers = {"today": today, "week": week, "nextweek": nextweek, "lunch": lunch, "help": help_command, "stop": stop}
        handler = handlers.get(query.data or "")
        if handler:
            await handler(update, context)

    async def refresh(context: ContextTypes.DEFAULT_TYPE, *, sync_spreadsheet: bool = True) -> None:
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
            cached_events = sorted(unique.values(), key=lambda event: (event.date, event.title.lower()))
            cache.save(cached_events)
            LOG.info("Refreshed %d events from %d sources into %s", len(unique), len(sources), settings.cache_file)
            if sync_spreadsheet:
                await sync_events(cached_events)
        except Exception:
            LOG.exception("Weekly seminar refresh failed; retaining existing cache")

    async def sync_events(events: list[Event]) -> bool:
        if cache_writer is None:
            return False
        try:
            if not events:
                LOG.warning("No events supplied; skipping Google Sheets export")
                return False
            async with spreadsheet_sync_lock:
                added_count = await asyncio.to_thread(cache_writer.write, events)
            LOG.info("Rebuilt the spreadsheet with %d event rows", added_count)
            return True
        except Exception:
            LOG.exception("Could not sync events to Google Sheets")
            return False

    async def export_cache(context: ContextTypes.DEFAULT_TYPE) -> bool:
        return await sync_events(cache.load())

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
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
            except Exception:
                LOG.exception("Could not send weekly announcement to chat %s", chat_id)

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Choose a button below, or use /today, /week, /nextweek, /lunch, /add, or /delete. /add and /delete require the password in a private chat. /start subscribes to Monday announcements; /stop unsubscribes; /cancel stops event entry.",
            reply_markup=menu_markup(),
        )

    application = Application.builder().token(settings.telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("nextweek", nextweek))
    application.add_handler(CommandHandler("lunch", lunch))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_event_start), CommandHandler("delete", delete_event_start)],
        allow_reentry=True,
        states={
            ADD_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_password)],
            DELETE_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_password)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title)],
            ADD_SPEAKER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_speaker)],
            ADD_ABSTRACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_abstract)],
            ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_time)],
            ADD_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_location)],
        },
        fallbacks=[CommandHandler("cancel", cancel_add_event)],
    ))
    application.add_handler(CommandHandler("cancel", cancel_add_event))
    application.add_handler(CallbackQueryHandler(delete_added_event_callback, pattern=r"^delete:"))
    application.add_handler(CallbackQueryHandler(button_callback))
    if application.job_queue is None:
        raise RuntimeError('Install the job queue extra: pip install "python-telegram-bot[job-queue]"')
    application.job_queue.run_repeating(
        refresh,
        interval=timedelta(hours=settings.refresh_interval_hours),
        first=timedelta(hours=settings.refresh_interval_hours),
        name="hourly-refresh",
    )
    application.job_queue.run_repeating(
        refresh_lunch,
        interval=timedelta(minutes=10),
        first=timedelta(seconds=1),
        name="lunch-refresh",
    )
    application.job_queue.run_daily(weekly_announcement, time=time(settings.announcement_hour, 0, tzinfo=timezone), days=(1,), name="weekly-talks")
    application.job_queue.run_daily(export_cache, time=time(1, 0, tzinfo=timezone), name="daily-cache-export")
    # Refresh once at every startup so newly configured sources are included
    # immediately; the regular Saturday job keeps the cache current afterward.
    application.job_queue.run_once(refresh, when=timedelta(seconds=1), name="initial-refresh-and-export")
    return application


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    build_application(settings).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
