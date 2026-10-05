"""Validated, persistent email subscriptions for seminar announcements."""

from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Iterable


def normalize_email(value: str) -> str:
    """Accept one plain ASCII mailbox, never display names or recipient lists."""
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Enter one email address, such as name@example.com.")
    address = value.strip().lower()
    if not address.isascii() or len(address) > 254 or address.count("@") != 1:
        raise ValueError("Enter one email address, such as name@example.com.")
    local, domain = address.split("@")
    if (not local or len(local) > 64 or local.startswith(".") or local.endswith(".")
            or ".." in local or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
        raise ValueError("Enter one email address, such as name@example.com.")
    labels = domain.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Enter one email address, such as name@example.com.")
    return address


def merge_recipients(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_email(address) for group in groups for address in group))


class EmailSubscriberStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def _read(self) -> set[str]:
        if not self.path.exists():
            return set()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Email subscriber file must contain a list of email addresses")
        return {normalize_email(value) for value in payload}

    def all(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._read()))

    def add(self, address: str) -> bool:
        address = normalize_email(address)
        with self._lock:
            subscribers = self._read()
            if address in subscribers:
                return False
            subscribers.add(address)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                        prefix=f".{self.path.name}.", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(sorted(subscribers), stream, indent=2)
                    stream.write("\n")
                temporary.chmod(0o600)
                temporary.replace(self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return True
