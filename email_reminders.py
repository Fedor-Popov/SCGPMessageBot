"""SMTP delivery for operational reminder emails."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import smtplib
import ssl


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


class EmailReminderSender:
    """Send plaintext reminder emails without logging credentials or contents."""

    def __init__(self, settings: SMTPSettings) -> None:
        self.settings = settings

    def send(self, recipients: tuple[str, ...], subject: str, body: str) -> None:
        if not self.settings.configured:
            raise RuntimeError("SMTP_HOST and SMTP_FROM must be configured before email reminders can be sent")
        addresses = tuple(address.strip() for address in recipients if address.strip())
        if not addresses:
            raise ValueError("At least one email recipient is required")

        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = ", ".join(addresses)
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self.settings.host, self.settings.port, timeout=30) as client:
            if self.settings.use_tls:
                client.starttls(context=ssl.create_default_context())
            if self.settings.username:
                client.login(self.settings.username, self.settings.password)
            client.send_message(message)
