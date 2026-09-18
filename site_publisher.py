"""Publish an allowlisted seminar snapshot, never the bot's working directory."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

PUBLIC_FIELDS = ("title", "speaker", "affiliation", "time", "location", "description")
SERIES = {
    "thermal": "Thermal Seminar",
    "thermal-seminar-sheet": "Thermal Seminar",
    "wednesday-seminar": "Wednesday Seminar",
    "journal-club": "Journal Club",
    "yitp-calendar": "YITP Calendar",
    "manual-events": "Additional Seminar",
}


def public_snapshot(cache: dict, series_names: dict[str, str] | None = None, calendar_url: str = "") -> dict:
    names = {**SERIES, **(series_names or {})}
    events = []
    for item in cache["events"]:
        event = {key: str(item.get(key) or "") for key in PUBLIC_FIELDS}
        event["date"] = date.fromisoformat(item["date"]).isoformat()
        event["series"] = names.get(item.get("source", ""), "Seminar")
        link = str(item.get("link") or "")
        try:
            url = urlsplit(link)
            safe = url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password
            # Source sheets are not website content, even when link-shared.
            safe = safe and not (url.hostname == "docs.google.com" and url.path.startswith("/spreadsheets/"))
        except ValueError:
            safe = False
        event["link"] = link if safe else ""
        events.append(event)
    events.sort(key=lambda event: (event["date"], event["time"], event["title"], event["speaker"]))
    return {
        "schema_version": 1,
        "timezone": "America/New_York",
        "updated_at": cache.get("updated_at"),
        "calendar_url": calendar_url,
        "events": events,
    }


def series_from_env() -> dict[str, str]:
    def ids(key: str) -> list[str]:
        return [value.strip() for value in os.getenv(key, "").split(",") if value.strip()]
    groups = [
        (ids("GOOGLE_WEDNESDAY_SPREADSHEET_IDS"), "Wednesday Seminar"),
        (ids("GOOGLE_JOURNAL_CLUB_SPREADSHEET_IDS"), "Journal Club"),
        (ids("GOOGLE_THERMAL_SPREADSHEET_IDS"), "Thermal Seminar"),
    ]
    if not any(values for values, _ in groups):
        legacy = ids("GOOGLE_SPREADSHEET_IDS")
        groups = [(legacy[:1], "Wednesday Seminar"), (legacy[1:], "Journal Club")]
    return {f"google:{sheet_id}": name for values, name in groups for sheet_id in values}


def write_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class SitePublisher:
    def __init__(self, repository: str) -> None:
        self.repository = repository

    def publish(self, snapshot: dict) -> bool:
        """Push only data/events.json. Reject non-fast-forward updates, never force."""
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"}

        def git(*args: str, cwd: Path | None = None) -> str:
            try:
                result = subprocess.run(
                    ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                    env=environment, text=True, capture_output=True, timeout=90,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Website Git operation timed out; retry on next refresh") from exc
            if result.returncode:
                # Avoid echoing a potentially credential-bearing remote URL.
                raise RuntimeError("Website Git operation failed; check SSH write access, main branch, and network")
            return result.stdout.strip()

        with tempfile.TemporaryDirectory(prefix="scgp-site-") as directory:
            checkout = Path(directory) / "site"
            git("clone", "--quiet", "--depth", "1", "--single-branch", "--branch", "main", "--", self.repository, str(checkout))
            target = checkout / "data" / "events.json"
            if target.is_symlink() or target.parent.is_symlink():
                raise RuntimeError("Website data path must not be a symbolic link")
            write_snapshot(target, snapshot)
            git("add", "--", "data/events.json", cwd=checkout)
            if not git("diff", "--cached", "--name-only", cwd=checkout):
                return False
            git("-c", "user.name=SCGP Seminar Bot", "-c", "user.email=scgp-seminars-bot@users.noreply.github.com",
                "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Update seminar schedule", cwd=checkout)
            git("push", "--quiet", "origin", "HEAD:main", cwd=checkout)
            return True
