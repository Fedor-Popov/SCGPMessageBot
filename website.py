"""Fetch and parse the public Thermal Seminars schedule."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from events import Event

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_LINE = re.compile(rf"^(?P<month>{MONTHS})\s+(?P<day>\d{{1,2}})(?:\s*:\s*|\s+|$)(?P<details>.*)$")
ARXIV_LINE = re.compile(r"(?:arxiv|\d{4}\.\d{4,5})", re.IGNORECASE)


def _clean_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    lines: list[str] = []
    for raw_line in soup.get_text("\n").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t•*")
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return lines


def _section(lines: list[str], start_marker: str, end_marker: str) -> list[str]:
    start = next((i for i, line in enumerate(lines) if start_marker.lower() in line.lower()), None)
    if start is None:
        raise ValueError(f"Could not find {start_marker!r} on the seminar page")
    end = next((i for i in range(start + 1, len(lines)) if end_marker.lower() in lines[i].lower()), len(lines))
    return lines[start + 1:end]


def _join_site_fragments(lines: list[str]) -> list[str]:
    """Join text fragments that Google Sites renders as separate spans."""
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(rf"(?:{MONTHS})", line) and index + 1 < len(lines) and re.fullmatch(r"\d{1,2}", lines[index + 1]):
            line = f"{line} {lines[index + 1]}"
            index += 1
        if re.fullmatch(rf"(?:{MONTHS})\s+\d{{1,2}}", line) and index + 1 < len(lines) and lines[index + 1] == ":":
            line += ":"
            index += 1
        if (
            re.fullmatch(rf"(?:{MONTHS})\s+\d", line)
            and index + 2 < len(lines)
            and re.fullmatch(r"\d", lines[index + 1])
            and lines[index + 2] == ":"
        ):
            line = f"{line}{lines[index + 1]}"
            index += 2
        if line.lower() == "abstract" and index + 1 < len(lines) and lines[index + 1] == ":":
            line = "Abstract:"
            index += 1
        joined.append(line)
        index += 1
    return joined


def _parse_entry(lines: list[str], year: int) -> Event | None:
    match = DATE_LINE.match(lines[0])
    if not match:
        return None
    talk_date = datetime.strptime(f"{match['month']} {match['day']} {year}", "%b %d %Y").date()
    body = ([match["details"].strip()] if match["details"].strip() else []) + lines[1:]

    abstract = ""
    title = ""
    link = ""
    abstract_index = next((i for i, line in enumerate(body) if line.lower().startswith("abstract:")), None)
    before_abstract = body[:abstract_index if abstract_index is not None else len(body)]
    if abstract_index is not None:
        abstract = " ".join([body[abstract_index][len("Abstract:"):].strip(), *body[abstract_index + 1:]]).strip()
    if not before_abstract:
        return None

    speaker = before_abstract[0]
    affiliation = ""
    inline_match = re.match(r"^(?P<speaker>.*?)\s*\((?P<affiliation>[^()]*)\)(?P<rest>.*)$", speaker)
    if inline_match:
        speaker = inline_match.group("speaker").strip()
        affiliation = inline_match.group("affiliation").strip()
        after_metadata = [inline_match.group("rest").strip(), *before_abstract[1:]]
    else:
        close_index = next((i for i, line in enumerate(before_abstract[1:], 1) if ")" in line), None)
        if close_index is not None:
            affiliation = " ".join(before_abstract[1:close_index]).strip(" ()")
            after_metadata = before_abstract[close_index:]
        else:
            after_metadata = before_abstract[1:]
    speaker = re.sub(r"\s+(?:on|:|review talk)\s*$", "", speaker, flags=re.IGNORECASE).strip()
    for line in after_metadata:
        clean = line.strip(" ()")
        if not clean or clean.lower() in {"on", ":", "review talk"}:
            continue
        if ARXIV_LINE.search(clean):
            arxiv_match = re.search(r"\d{4}\.\d{4,5}", clean)
            link = f"https://arxiv.org/abs/{arxiv_match.group(0)}" if arxiv_match else clean
            continue
        title = clean
        break
    if not title:
        return None
    return Event(talk_date, title, speaker=speaker, affiliation=affiliation, description=abstract, link=link, source="thermal")


def parse_schedule(html: str, year: int | None = None) -> list[Event]:
    year = year or date.today().year
    lines = _join_site_fragments(_section(_clean_lines(html), "Future Seminars schedule", "Past Seminars"))
    entries: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if DATE_LINE.match(line):
            if current:
                entries.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append(current)
    talks = [_parse_entry(entry, year) for entry in entries]
    return sorted((talk for talk in talks if talk is not None), key=lambda talk: (talk.date, talk.title.lower()))


class WebsiteTalkSource:
    name = "thermal"
    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self) -> list[Event]:
        response = requests.get(self.url, timeout=30, headers={"User-Agent": "SCGPMessageBot/1.0"})
        response.raise_for_status()
        talks = parse_schedule(response.text)
        if not talks:
            raise ValueError("The seminar page was fetched, but no future talks were parsed")
        return talks


class JsonTalkCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Event]:
        if not self.path.exists():
            return []
        payload: dict[str, Any] = json.loads(self.path.read_text())
        return [
            Event(
                date.fromisoformat(item["date"]), item["title"], item.get("time", ""),
                item.get("speaker", ""), item.get("location", ""),
                item.get("description", ""), item.get("link", ""),
            )
            for item in payload.get("talks", [])
        ]

    def save(self, talks: list[Event]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now().astimezone().isoformat(),
            "source": "https://sites.google.com/view/thermalseminars",
            "talks": [{**asdict(talk), "date": talk.date.isoformat()} for talk in talks],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        self.path.chmod(0o600)
