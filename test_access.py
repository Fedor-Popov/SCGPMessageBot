import asyncio
import hashlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import access
from bot import ADD_DATE, ADD_LOCATION, ADD_PASSWORD, DELETE_PASSWORD, Settings, build_application
from events import Event
from sources.manual import ManualEventSource
from sources.thermal import ThermalSeminarsSource
from telegram.ext import CallbackQueryHandler, ConversationHandler
from website import WebsiteTalkSource


@pytest.fixture
def password(monkeypatch):
    value = "test-only-password"
    salt = b"test-only-salt"
    monkeypatch.setattr(access, "PASSWORD_SALT", salt)
    monkeypatch.setattr(access, "PASSWORD_HASH", hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1))
    return value


def test_password_hash_verification(password):
    assert access.verify_password(password)
    assert not access.verify_password(password.upper())
    assert not access.verify_password("")
    assert not access.verify_password("x" * 257)


def test_authorization_is_bound_to_user_chat_action_nonce_and_expiry(password, monkeypatch):
    monkeypatch.setattr(access, "monotonic", lambda: 10)
    guard = access.PasswordAccess()
    nonce = guard.authenticate(1, 2, "delete", password)
    assert guard.allowed(1, 2, "delete", nonce)
    assert not guard.allowed(9, 2, "delete", nonce)
    assert not guard.allowed(1, 9, "delete", nonce)
    assert not guard.allowed(1, 2, "add", nonce)
    assert not guard.allowed(1, 2, "delete", "old-nonce")
    monkeypatch.setattr(access, "monotonic", lambda: 911)
    assert not guard.allowed(1, 2, "delete", nonce)


def test_wrong_password_cooldown_and_revocation(password, monkeypatch):
    monkeypatch.setattr(access, "monotonic", lambda: 10)
    guard = access.PasswordAccess()
    for _ in range(5):
        with pytest.raises(ValueError, match="Incorrect"):
            guard.authenticate(1, 2, "add", "wrong")
    with pytest.raises(ValueError, match="Too many"):
        guard.authenticate(1, 2, "add", password)
    monkeypatch.setattr(access, "monotonic", lambda: 311)
    guard.authenticate(1, 2, "add", password)
    guard.revoke(1, 2)
    assert not guard.allowed(1, 2, "add")


def test_protected_commands_and_delete_callback(tmp_path, password, monkeypatch):
    settings = Settings(
        telegram_token="123456:unit-test-token", website_url="https://example.test",
        lunch_menu_url="https://example.test/menu", lunch_cache_file=tmp_path / "lunch.json",
        cache_file=tmp_path / "cache.json", manual_events_file=tmp_path / "manual.json",
        google_spreadsheet_ids=(), wednesday_spreadsheet_ids=(), journal_club_spreadsheet_ids=(),
        thermal_spreadsheet_ids=(), additional_spreadsheet_ids=(), cache_export_spreadsheet_id="",
        google_token_file=tmp_path / "token.json", timezone="America/New_York", refresh_hour=3,
        refresh_interval_hours=1, announcement_hour=10, subscribers_file=tmp_path / "subscribers.json",
    )
    monkeypatch.setattr(ThermalSeminarsSource, "fetch", lambda self: [])
    source = ManualEventSource(settings.manual_events_file)
    event = Event(date(2099, 9, 11), "Manual talk")
    source.add(event)
    app = build_application(settings)
    conversation = next(h for h in app.handlers[0] if isinstance(h, ConversationHandler))
    commands = {next(iter(h.commands)): h.callback for h in conversation.entry_points}
    assert set(commands) == {"add", "delete"}
    delete_callback = next(h.callback for h in app.handlers[0]
                           if isinstance(h, CallbackQueryHandler) and h.pattern)
    message = SimpleNamespace(text="", reply_text=AsyncMock(), delete=AsyncMock())
    query = SimpleNamespace(data=f"delete:forged:{source.event_id(event)}", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=2, type="private"),
                             effective_message=message, callback_query=query)
    context = SimpleNamespace(user_data={}, application=app)

    async def exercise():
        await delete_callback(update, context)
        assert source.fetch() == [event]
        assert await commands["add"](update, context) == ADD_PASSWORD
        message.text = "incorrect"
        assert await conversation.states[ADD_PASSWORD][0].callback(update, context) == ConversationHandler.END
        assert await conversation.states[ADD_LOCATION][0].callback(update, context) == ConversationHandler.END
        assert source.fetch() == [event]
        assert await commands["delete"](update, context) == DELETE_PASSWORD
        message.text = password
        await conversation.states[DELETE_PASSWORD][0].callback(update, context)
        message.delete.assert_awaited()
        query.data = message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        assert len(query.data.encode()) <= 64
        update.effective_user.id = 99
        await delete_callback(update, context)
        assert source.fetch() == [event]
        update.effective_user.id = 1
        await delete_callback(update, context)
        assert source.fetch() == []
        # Replayed buttons cannot delete a newly recreated event.
        source.add(event)
        await delete_callback(update, context)
        assert source.fetch() == [event]
        await commands["add"](update, context)
        message.text = password
        assert await conversation.states[ADD_PASSWORD][0].callback(update, context) == ADD_DATE
        context.user_data["new_event"] = {"date": date(2099, 9, 12), "title": "New event", "time": "11:00 AM"}
        message.text = "102"
        assert await conversation.states[ADD_LOCATION][0].callback(update, context) == ConversationHandler.END
        assert {event.title for event in source.fetch()} == {"Manual talk", "New event"}
        await commands["delete"](update, context)
        message.text = password
        await conversation.states[DELETE_PASSWORD][0].callback(update, context)
        query.data = message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        await conversation.fallbacks[0].callback(update, context)
        await delete_callback(update, context)
        assert len(source.fetch()) == 2
        update.effective_chat.type = "group"
        assert await commands["add"](update, context) == ConversationHandler.END

    asyncio.run(exercise())


def test_thermal_default_time_and_location(monkeypatch):
    monkeypatch.setattr(WebsiteTalkSource, "fetch", lambda self: [Event(date(2026, 9, 14), "Thermal")])
    event, = ThermalSeminarsSource("https://example.test").fetch()
    assert event.time == "11:00 AM"
    assert event.location == "102"
