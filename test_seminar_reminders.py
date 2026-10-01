import asyncio
import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import ExtBot

import access
import bot
from bot import Settings, build_application
from cache import EventCache
from email_reminders import EmailReminderSender, PartialEmailDeliveryError
from events import Event
from seminar_reminders import (
    SEMINAR_RECIPIENTS, SEMINAR_TEST_RECIPIENTS, compose_seminar_email, current_week_events,
)


@pytest.mark.parametrize("today", [date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 4)])
def test_current_week_filters_dates_weekday_and_series(today):
    wanted = Event(date(2026, 9, 30), "This week", source="google:wed")
    events = [
        wanted, wanted,
        Event(date(2026, 9, 23), "Previous week", source="google:wed"),
        Event(date(2026, 10, 7), "Next week", source="google:wed"),
        Event(date(2026, 10, 1), "Wrong day", source="google:wed"),
        Event(date(2026, 9, 30), "Other series", source="google:thermal"),
    ]
    assert current_week_events(events, today, 2, ("google:wed",)) == [wanted]


def test_current_week_across_year_boundary_and_empty_series():
    event = Event(date(2026, 12, 30), "Year-end talk", source="wednesday-seminar")
    assert current_week_events([event], date(2027, 1, 3), 2, (event.source,)) == [event]
    assert current_week_events([event], date(2027, 1, 4), 2, (event.source,)) == []
    assert current_week_events([event], date(2027, 1, 3), 2, ()) == []


def test_email_contains_event_details_and_test_preview():
    event = Event(
        date(2026, 9, 30), "Quantum fields", speaker="A. Speaker", affiliation="SCGP",
        time="2:00 PM", location="313", description="First line.\nSecond line.",
        link="https://example.test/talk", source="google:wed",
    )
    email = compose_seminar_email([event], "Wednesday Seminar")
    assert email.subject == "Reminder: Wednesday Seminar — September 30, 2026"
    for value in ("Wednesday, September 30, 2026", "2:00 PM (New York time)", "313",
                  "A. Speaker (SCGP)", "Quantum fields", "Abstract:\nFirst line.\nSecond line.", event.link):
        assert value in email.body
    preview = compose_seminar_email([event], "Wednesday Seminar", test=True)
    assert preview.subject == "[TEST] " + email.subject
    assert preview.body.endswith(email.body)
    with pytest.raises(ValueError):
        compose_seminar_email([], "Wednesday Seminar")


def test_multiple_talks_and_missing_optional_fields():
    events = [Event(date(2026, 10, 1), "First"), Event(date(2026, 10, 1), "", speaker="Second speaker")]
    email = compose_seminar_email(events, "Journal Club")
    assert "Title: First" in email.body
    assert "Speaker: Second speaker" in email.body
    assert "Abstract:" not in email.body
    assert "Time:" not in email.body


def test_recipient_lists_are_exact_and_distinct():
    expected = """
    fpopov@scgp.stonybrook.edu
    zeqi.zhang@stonybrook.edu nnekrasov@scgp.stonybrook.edu siwei.zhong@stonybrook.edu
    zkomargodski@scgp.stonybrook.edu aabanov@scgp.stonybrook.edu leonardo.rastelli@gmail.com
    martin.rocek@stonybrook.edu anirudh.deb@stonybrook.edu yaman.sanghavi@stonybrook.edu
    lalvarezgaume@scgp.stonybrook.edu araviv-moshe@scgp.stonybrook.edu fyan.hepth@gmail.com
    siqi.chen.1@stonybrook.edu dilara.kosva@stonybrook.edu mathieu.boisvert@stonybrook.edu
    spinor87@gmail.com vasilii.iugov@stonybrook.edu lin.ziyi@stonybrook.edu jesse.caojiaxi@gmail.com
    shihab.fadda@stonybrook.edu ramtin.mohasselyazdi@stonybrook.edu han.ma@stonybrook.edu
    yashvinder.singh@stonybrook.edu gurulakshmi.subramanian@stonybrook.edu marcelo.barbosa@stonybrook.edu
    nevillejoshua.rajappa@stonybrook.edu yanmong.chan@stonybrook.edu dmitrii.pavshinkin@stonybrook.edu
    luke.martin@stonybrook.edu nathanan.tantivasadakarn@stonybrook.edu jacob.mcnamara@scgp.stonybrook.edu
    alessio.miscioscia@stonybrook.edu mnocchi@scgp.stonybrook.edu arkya.chat@gmail.com
    joseph.helfer@gmail.com msacchi@scgp.stonybrook.edu
    """.split()
    assert len(SEMINAR_RECIPIENTS) == len(set(SEMINAR_RECIPIENTS)) == 37
    assert set(SEMINAR_RECIPIENTS) == set(expected)
    assert SEMINAR_TEST_RECIPIENTS == (
        "fpopov@scgp.stonybrook.edu", "frenkelalexander1@gmail.com", "alessio.miscioscia@stonybrook.edu",
    )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    test_password = "test-only-password"
    salt = b"seminar-test-salt"
    monkeypatch.setattr(access, "PASSWORD_SALT", salt)
    monkeypatch.setattr(access, "PASSWORD_HASH", hashlib.scrypt(test_password.encode(), salt=salt, n=16384, r=8, p=1))

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC is already Monday, but New York is still Sunday October 4.
            return datetime(2026, 10, 5, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(bot, "datetime", FrozenDateTime)
    settings = Settings(
        telegram_token="123456:unit-test-token", website_url="https://example.test",
        lunch_menu_url="https://example.test/menu", lunch_cache_file=tmp_path / "lunch.json",
        cache_file=tmp_path / "cache.json", manual_events_file=tmp_path / "manual.json",
        google_spreadsheet_ids=(), wednesday_spreadsheet_ids=("wed",), journal_club_spreadsheet_ids=("thur",),
        thermal_spreadsheet_ids=(), additional_spreadsheet_ids=(), cache_export_spreadsheet_id="",
        google_token_file=tmp_path / "token.json", timezone="UTC", refresh_hour=3,
        refresh_interval_hours=1, announcement_hour=10, subscribers_file=tmp_path / "subscribers.json",
        smtp_host="smtp.example.test", smtp_from="organizers@example.test",
        # These operational settings must never redirect the seminar test command.
        email_test_recipient="unrelated@example.test", reminder_email_recipients=("unrelated@example.test",),
    )
    cache = EventCache(settings.cache_file)
    cache.save([
        Event(date(2026, 9, 30), "Wednesday title", speaker="Wednesday speaker", source="google:wed"),
        Event(date(2026, 10, 1), "Thursday title", speaker="Thursday speaker", source="google:thur"),
        Event(date(2026, 10, 7), "NEXT WEEK", source="google:wed"),
        Event(date(2026, 10, 8), "NEXT WEEK CLUB", source="google:thur"),
        Event(date(2026, 9, 30), "UNRELATED", source="google:thermal"),
    ])
    send = Mock()
    replies = AsyncMock()
    deletes = AsyncMock()
    monkeypatch.setattr(EmailReminderSender, "send", send)
    monkeypatch.setattr(ExtBot, "send_message", replies)
    monkeypatch.setattr(ExtBot, "delete_message", deletes)

    class Harness:
        def __init__(self):
            self.settings = settings
            self.password = test_password
            self.cache = cache
            self.send = send
            self.replies = replies
            self.deletes = deletes
            self.counter = 0
            self.build()

        def build(self):
            self.app = build_application(self.settings)
            # Exercise real handler routing while keeping Telegram and jobs offline.
            self.app._initialized = True
            self.app.bot._bot_user = User(123456, "Test Bot", True, username="seminar_test_bot")

        async def dispatch(self, text, *, user=1, chat=1, chat_type="private"):
            self.counter += 1
            entities = [MessageEntity("bot_command", 0, len(text.split()[0]))] if text.startswith("/") else []
            message = Message(
                self.counter, datetime.now(timezone.utc), Chat(chat, chat_type),
                from_user=User(user, "User", False), text=text, entities=entities,
            )
            message.set_bot(self.app.bot)
            update = Update(self.counter, message=message)
            update.set_bot(self.app.bot)
            await self.app.process_update(update)

        @property
        def last_reply(self):
            return self.replies.call_args.kwargs["text"]

    return Harness()


@pytest.mark.parametrize("command,expected_titles", [
    ("seminarreminderwed", ["Wednesday title"]),
    ("seminarreminderthur", ["Thursday title"]),
    ("seminarremindertest", ["Wednesday title", "Thursday title"]),
])
def test_commands_authenticate_and_send_only_correct_events_and_recipients(harness, command, expected_titles):
    async def exercise():
        await harness.dispatch("/" + command)
        harness.send.assert_not_called()
        assert "Enter the password" in harness.last_reply
        await harness.dispatch(harness.password)
        assert harness.send.call_count == len(expected_titles)
        recipients = SEMINAR_TEST_RECIPIENTS if command.endswith("test") else SEMINAR_RECIPIENTS
        for call, title in zip(harness.send.call_args_list, expected_titles):
            actual_recipients, subject, body = call.args
            assert actual_recipients == recipients
            assert title in body
            assert "NEXT WEEK" not in body and "UNRELATED" not in body
            assert subject.startswith("[TEST]") == command.endswith("test")
        harness.deletes.assert_awaited_once()
        assert "sent to" in harness.last_reply
        # A stray/replayed password does nothing, and the next command prompts again.
        harness.send.reset_mock()
        await harness.dispatch(harness.password)
        await harness.dispatch("/" + command)
        harness.send.assert_not_called()
        assert "Enter the password" in harness.last_reply

    asyncio.run(exercise())


@pytest.mark.parametrize("command", ["seminarreminderwed", "seminarreminderthur", "seminarremindertest"])
def test_wrong_password_cancel_and_group_chat_cannot_send(harness, command):
    async def exercise():
        await harness.dispatch("/" + command)
        await harness.dispatch("wrong")
        assert "Incorrect password" in harness.last_reply
        await harness.dispatch(harness.password)
        await harness.dispatch("/" + command)
        await harness.dispatch("/cancel")
        assert "cancelled" in harness.last_reply
        await harness.dispatch(harness.password)
        await harness.dispatch("/" + command, chat=-3, chat_type="group")
        assert "private chat" in harness.last_reply
        await harness.dispatch(harness.password, chat=-3, chat_type="group")
        harness.send.assert_not_called()

    asyncio.run(exercise())


def test_user_isolation_and_switching_from_test_to_real_requires_password(harness):
    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch(harness.password, user=2, chat=2)
        harness.send.assert_not_called()
        await harness.dispatch("/seminarreminderwed")
        harness.send.assert_not_called()
        await harness.dispatch(harness.password)
        harness.send.assert_called_once()
        assert harness.send.call_args.args[0] == SEMINAR_RECIPIENTS
        assert "Wednesday title" in harness.send.call_args.args[2]

    asyncio.run(exercise())


def test_test_command_sends_available_series_and_reports_missing_one(harness):
    harness.cache.save([event for event in harness.cache.load() if event.source != "google:wed"])

    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch(harness.password)
        harness.send.assert_called_once()
        assert harness.send.call_args.args[0] == SEMINAR_TEST_RECIPIENTS
        assert "Thursday title" in harness.send.call_args.args[2]
        assert "Wednesday Seminar: no events" in harness.last_reply
        assert "sent to 3 recipients" in harness.last_reply

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["missing_smtp", "empty_week", "corrupt_cache"])
def test_unavailable_schedule_or_smtp_never_sends(harness, failure):
    if failure == "missing_smtp":
        harness.settings = replace(harness.settings, smtp_host="")
        harness.build()
    elif failure == "empty_week":
        harness.cache.save([Event(date(2026, 10, 7), "Next week", source="google:wed")])
    else:
        harness.settings.cache_file.write_text("invalid json")

    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch(harness.password)
        harness.send.assert_not_called()
        expected = {"missing_smtp": "not configured", "empty_week": "no events", "corrupt_cache": "could not be read"}
        assert expected[failure] in harness.last_reply

    asyncio.run(exercise())


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("mail server unavailable"), "could not be confirmed"),
    (PartialEmailDeliveryError(2, 1), "accepted for 2 recipients; 1 refused"),
])
def test_send_failure_is_reported_per_series_without_automatic_retry(harness, error, expected):
    harness.send.side_effect = [error, None]

    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch(harness.password)
        assert harness.send.call_count == 2
        assert expected in harness.last_reply
        assert "Journal Club" in harness.last_reply and "sent to 3 recipients" in harness.last_reply

    asyncio.run(exercise())
