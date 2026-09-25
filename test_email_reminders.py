from __future__ import annotations

import pytest

import email_reminders
from email_reminders import EmailReminderSender, SMTPSettings


class FakeSMTP:
    def __init__(self) -> None:
        self.tls = False
        self.login_args: tuple[str, str] | None = None
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def starttls(self, *, context) -> None:
        assert context is not None
        self.tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        self.message = message


def test_smtp_sender_uses_tls_auth_and_requested_recipients(monkeypatch):
    client = FakeSMTP()
    monkeypatch.setattr(email_reminders.smtplib, "SMTP", lambda *args, **kwargs: client)
    sender = EmailReminderSender(SMTPSettings("smtp.example.test", 587, "sender@example.test", "secret", "sender@example.test"))

    sender.send(("first@example.test", "second@example.test"), "Reminder", "Send the announcement")

    assert client.tls
    assert client.login_args == ("sender@example.test", "secret")
    assert client.message["From"] == "sender@example.test"
    assert client.message["To"] == "first@example.test, second@example.test"
    assert client.message["Subject"] == "Reminder"
    assert client.message.get_content().strip() == "Send the announcement"


def test_smtp_sender_requires_host_and_sender():
    sender = EmailReminderSender(SMTPSettings("", 587, "", "", ""))
    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        sender.send(("recipient@example.test",), "Test", "Body")
