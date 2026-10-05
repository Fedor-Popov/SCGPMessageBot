import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from bot import menu_markup
from email_subscribers import EmailSubscriberStore, merge_recipients, normalize_email
from seminar_reminders import SEMINAR_RECIPIENTS, SEMINAR_TEST_RECIPIENTS
from test_seminar_reminders import harness


@pytest.mark.parametrize("address", [
    "", "not-an-email", "a@b", "a@@example.com", "Name <a@example.com>",
    "a@example.com,b@example.com", "a@example.com; b@example.com", "a@example.com\nBcc:b@example.com",
    "a@example.com\r\n", "a\x00b@example.com", "a b@example.com", ".a@example.com", "a..b@example.com",
    "a.@example.com", "a@-example.com", "a@example-.com", "a@example..com", "a@exam_ple.com",
    "a@" + "x" * 64 + ".com", "x" * 65 + "@example.com", "тест@example.com",
])
def test_invalid_or_multi_recipient_input_is_rejected(address):
    with pytest.raises(ValueError):
        normalize_email(address)


def test_persistent_deduplicated_subscriptions_and_concurrent_adds(tmp_path):
    path = tmp_path / "data" / "emails.json"
    store = EmailSubscriberStore(path)
    assert store.all() == ()
    assert store.add(" First.Last+Seminars@Example.COM ")
    assert not store.add("first.last+seminars@example.com")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(store.add, [f"person{i}@example.com" for i in range(12)]))
    restored = EmailSubscriberStore(path).all()
    assert len(restored) == 13
    assert "first.last+seminars@example.com" in restored
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]
    assert merge_recipients(("FIRST.LAST+SEMINARS@example.com",), restored).count("first.last+seminars@example.com") == 1


@pytest.mark.parametrize("contents", ["broken json", "{}", '[null]', '["invalid-email"]'])
def test_corrupt_storage_is_not_overwritten(tmp_path, contents):
    path = tmp_path / "emails.json"
    path.write_text(contents)
    store = EmailSubscriberStore(path)
    with pytest.raises(ValueError):
        store.add("valid@example.com")
    assert path.read_text() == contents


def test_subscribe_button_validates_input_and_persists_without_sending(harness):
    buttons = [button for row in menu_markup().inline_keyboard for button in row]
    assert any(button.text == "Subscribe" and button.callback_data == "subscribe" for button in buttons)
    assert all(button.text != "Trains" for button in buttons)

    async def exercise():
        await harness.click("subscribe")
        assert "Enter your email" in harness.last_reply
        await harness.dispatch("invalid")
        assert "Enter one email address" in harness.last_reply
        assert not harness.settings.email_subscribers_file.exists()
        await harness.dispatch(" First.Last+Events@Example.com ")
        assert "Subscribed first.last+events@example.com" in harness.last_reply
        harness.send.assert_not_called()
        assert EmailSubscriberStore(harness.settings.email_subscribers_file).all() == ("first.last+events@example.com",)
        await harness.dispatch("another@example.com")  # Flow ended; unsolicited text is not enrollment.
        assert len(EmailSubscriberStore(harness.settings.email_subscribers_file).all()) == 1
        harness.build()  # Recreate the application as on a restart.
        await harness.dispatch("/subscribe")
        await harness.dispatch("FIRST.LAST+EVENTS@example.com")
        assert "already on" in harness.last_reply
        assert len(EmailSubscriberStore(harness.settings.email_subscribers_file).all()) == 1
        assert not harness.settings.subscribers_file.exists()  # Separate from Telegram subscriptions.

    asyncio.run(exercise())


@pytest.mark.parametrize("command", ["seminarreminderwed", "seminarreminderthur"])
def test_new_subscriber_receives_each_real_reminder_once_after_restart(harness, command):
    async def exercise():
        await harness.dispatch("/subscribe")
        await harness.dispatch("new@example.com")
        await harness.dispatch("/subscribe")
        await harness.dispatch("FPOPOV@SCGP.STONYBROOK.EDU")
        assert "already on" in harness.last_reply
        harness.build()
        await harness.dispatch("/" + command)
        harness.send.assert_not_called()
        await harness.dispatch(harness.password)
        harness.send.assert_called_once()
        recipients, subject, body = harness.send.call_args.args
        assert recipients == (*SEMINAR_RECIPIENTS, "new@example.com")
        assert len(recipients) == 38
        assert "38 recipients" in harness.last_reply
        assert "subscriber list" not in body and "new@example.com" not in body
        assert not subject.startswith("[TEST]")

    asyncio.run(exercise())


def test_subscriber_report_and_previews_go_only_to_organizers(harness):
    async def exercise():
        await harness.dispatch("/subscribe")
        await harness.dispatch("new@example.com")
        await harness.dispatch("/seminarremindertest")
        harness.send.assert_not_called()
        await harness.dispatch(harness.password)
        assert harness.send.call_count == 3
        for call in harness.send.call_args_list:
            assert call.args[0] == SEMINAR_TEST_RECIPIENTS
        report = harness.send.call_args_list[0].args[2]
        for address in (*SEMINAR_RECIPIENTS, "new@example.com"):
            assert address in report
        assert "38 unique addresses" in report
        assert "Addresses added through Subscribe (1)" in report
        assert "new@example.com" not in harness.last_reply
        assert "new@example.com" not in harness.send.call_args_list[1].args[2]
        assert "new@example.com" not in harness.send.call_args_list[2].args[2]

    asyncio.run(exercise())


def test_subscriber_report_requires_password_even_when_no_talks(harness):
    harness.cache.save([])

    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch("wrong")
        harness.send.assert_not_called()
        await harness.dispatch("/seminarremindertest")
        await harness.dispatch(harness.password)
        harness.send.assert_called_once()
        assert harness.send.call_args.args[0] == SEMINAR_TEST_RECIPIENTS
        assert "subscriber list" in harness.send.call_args.args[1]
        assert "37 unique addresses" in harness.send.call_args.args[2]
        assert "No additional subscribers yet" in harness.send.call_args.args[2]

    asyncio.run(exercise())


@pytest.mark.parametrize("navigation", ["/cancel", "/help", "/today", "help", "week"])
def test_cancel_or_navigation_stops_email_capture(harness, navigation):
    async def exercise():
        await harness.click("subscribe")
        if navigation.startswith("/"):
            await harness.dispatch(navigation)
        else:
            await harness.click(navigation)
        await harness.dispatch("not-subscribed@example.com")
        assert not harness.settings.email_subscribers_file.exists()
        harness.send.assert_not_called()

    asyncio.run(exercise())


def test_private_chat_and_user_isolation(harness):
    async def exercise():
        await harness.click("subscribe", chat=-3, chat_type="group")
        assert "private chat" in harness.last_reply
        await harness.dispatch("group@example.com", chat=-3, chat_type="group")
        await harness.dispatch("/subscribe", user=1, chat=1)
        await harness.dispatch("other@example.com", user=2, chat=2)
        assert not harness.settings.email_subscribers_file.exists()
        await harness.dispatch("owner@example.com", user=1, chat=1)
        assert EmailSubscriberStore(harness.settings.email_subscribers_file).all() == ("owner@example.com",)

    asyncio.run(exercise())


def test_switching_from_password_prompt_to_subscribe_and_back(harness):
    async def exercise():
        await harness.dispatch("/seminarremindertest")
        await harness.click("subscribe")
        await harness.dispatch(harness.password)
        harness.send.assert_not_called()
        assert "Enter one email address" in harness.last_reply
        await harness.dispatch("/seminarreminderwed")
        await harness.dispatch("not-subscribed@example.com")
        assert "Incorrect password" in harness.last_reply
        assert not harness.settings.email_subscribers_file.exists()
        harness.send.assert_not_called()

    asyncio.run(exercise())


def test_storage_failure_does_not_claim_success_or_send_partial_list(harness):
    path = harness.settings.email_subscribers_file
    path.write_text("broken json")

    async def exercise():
        await harness.dispatch("/subscribe")
        await harness.dispatch("new@example.com")
        assert "could not be saved" in harness.last_reply
        assert path.read_text() == "broken json"
        await harness.dispatch("/seminarreminderwed")
        await harness.dispatch(harness.password)
        assert "subscriber list could not be read" in harness.last_reply
        harness.send.assert_not_called()

    asyncio.run(exercise())


def test_duplicate_in_saved_list_does_not_receive_twice(harness):
    harness.settings.email_subscribers_file.write_text(json.dumps([SEMINAR_RECIPIENTS[0].upper(), "new@example.com"]))

    async def exercise():
        await harness.dispatch("/seminarreminderthur")
        await harness.dispatch(harness.password)
        recipients = harness.send.call_args.args[0]
        assert len(recipients) == len(set(recipients)) == 38

    asyncio.run(exercise())
