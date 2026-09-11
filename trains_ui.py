"""Telegram controls for the LIRR schedule module."""

import asyncio
import logging
import secrets
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler

from trains import STATIONS, ScheduleUnavailable, format_trains

LOG = logging.getLogger(__name__)


def main_menu_button():
    return InlineKeyboardButton("Main menu", callback_data="trains:main")


def station_buttons(origin=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"trains:from:{key}" if origin is None else f"trains:to:{origin}:{key}")]
        for key, label in STATIONS.items()
    ] + [[main_menu_button()]])


def register_trains(application, schedules, menu_markup):
    async def show_menu(update, context):
        context.user_data.pop("train_lookup", None)
        if update.callback_query:
            await update.callback_query.answer()
        await update.effective_message.reply_text("From:", reply_markup=station_buttons())

    async def show_page(query, context, key, page):
        stored = context.user_data.get("train_results", {}).get(key)
        if stored is None or monotonic() - stored[0] > 600:
            await query.edit_message_text("This train search has expired. Choose your starting station:", reply_markup=station_buttons())
            return
        result = stored[1]
        text, pages = format_trains(result, page)
        page = max(0, min(page, pages - 1))
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton("Previous", callback_data=f"trains:page:{key}:{page - 1}"))
        if page + 1 < pages:
            navigation.append(InlineKeyboardButton("Next", callback_data=f"trains:page:{key}:{page + 1}"))
        rows = [navigation] if navigation else []
        rows.append([InlineKeyboardButton("Refresh times", callback_data=f"trains:to:{result.origin}:{result.destination}")])
        rows.append([InlineKeyboardButton("New train search", callback_data="trains")])
        rows.append([main_menu_button()])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows), disable_web_page_preview=True)

    async def search(update, context, origin, destination, lookup):
        try:
            result = await asyncio.to_thread(schedules.search, origin, destination)
            if context.user_data.get("train_lookup") != lookup:
                return
            results = context.user_data.setdefault("train_results", {})
            while len(results) >= 8:
                results.pop(next(iter(results)))
            key = secrets.token_hex(5)
            results[key] = (monotonic(), result)
            await show_page(update.callback_query, context, key, 0)
        except ScheduleUnavailable as exc:
            if context.user_data.get("train_lookup") == lookup:
                await update.callback_query.edit_message_text(str(exc), reply_markup=menu_markup())
        except TelegramError:
            LOG.warning("Train search message is no longer available")
        except Exception:
            LOG.exception("LIRR schedule lookup failed")
            if context.user_data.get("train_lookup") == lookup:
                await update.callback_query.edit_message_text("Could not retrieve LIRR schedules. Please try again shortly.", reply_markup=menu_markup())

    async def callback(update, context):
        query = update.callback_query
        data = (query.data or "").split(":")
        if data == ["trains"]:
            await show_menu(update, context)
            return
        await query.answer()
        context.user_data.pop("train_lookup", None)
        if data == ["trains", "main"]:
            await query.edit_message_text("Choose an option:", reply_markup=menu_markup())
        elif len(data) == 3 and data[1] == "from" and data[2] in STATIONS:
            await query.edit_message_text(f"From: {STATIONS[data[2]]}\nTo:", reply_markup=station_buttons(data[2]))
        elif len(data) == 4 and data[1] == "to" and data[2] in STATIONS and data[3] in STATIONS:
            origin, destination = data[2:]
            if origin == destination:
                await query.edit_message_text("Choose a destination different from your starting station.\nTo:", reply_markup=station_buttons(origin))
                return
            lookup = secrets.token_hex(8)
            context.user_data["train_lookup"] = lookup
            await query.edit_message_text(f"Looking up trains: {STATIONS[origin]} → {STATIONS[destination]}…",
                                          reply_markup=InlineKeyboardMarkup([[main_menu_button()]]))
            context.application.create_task(search(update, context, origin, destination, lookup), update=update, name="lirr-lookup")
        elif len(data) == 4 and data[1] == "page" and data[3].isdigit():
            await show_page(query, context, data[2], int(data[3]))

    async def refresh(context):
        try:
            await asyncio.to_thread(schedules.timetable)
        except ScheduleUnavailable:
            LOG.warning("LIRR timetable refresh unavailable; will retry on demand")

    application.add_handler(CommandHandler("trains", show_menu))
    application.add_handler(CallbackQueryHandler(callback, pattern=r"^trains(?::|$)"))
    application.job_queue.run_repeating(refresh, interval=6 * 3600, first=5, name="lirr-timetable-refresh")
