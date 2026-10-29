"""Fetch and parse the public Thermal Seminars schedule."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from events import Event

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_LINE = re.compile(rf"^(?P<month>{MONTHS})\s+(?P<day>\d{{1,2}})(?:\s*:\s*|\s+|$)(?P<details>.*)$")
ARXIV_LINE = re.compile(r"(?:arxiv|\d{4}\.\d{4,5})", re.IGNORECASE)
BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
EMBEDDED_DATE = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(?P<year>\d{4})\s*·\s*(?P<time>\d{1,2}:\d{2})\s*\(Berlin time\)",
    re.IGNORECASE,
)
EMBEDDED_TIME = re.compile(r"(?P<time>\d{1,2}:\d{2})\s*\(Berlin time\)", re.IGNORECASE)


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


def apps_script_url(html: str, page_url: str) -> str:
    """Find the public schedule embed on the Google Sites landing page."""
    for frame in BeautifulSoup(html, "html.parser").select("iframe[src]"):
        url = urljoin(page_url, frame["src"])
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname == "script.google.com" and parsed.path.endswith("/exec"):
            return url
    return ""


def apps_script_html(response_html: str) -> str:
    """Decode the HTML Google Apps Script passes to its sandbox iframe."""
    match = re.search(r'goog\.script\.init\(\s*("(?:\\.|[^"\\])*")', response_html, re.DOTALL)
    if not match:
        raise ValueError("Could not find the Apps Script schedule payload")
    js_string = re.sub(r"\\x([0-9a-fA-F]{2})", lambda item: "\\u00" + item[1], match[1])
    payload = json.loads(json.loads(js_string))
    html = payload.get("userHtml", "")
    if not html:
        raise ValueError("The Apps Script schedule payload has no HTML")
    return html


def _berlin_event_date_time(day: int, month: str, year: int, clock: str) -> tuple[date, str]:
    berlin = datetime.strptime(f"{day} {month} {year} {clock}", "%d %b %Y %H:%M").replace(tzinfo=BERLIN)
    local = berlin.astimezone(NEW_YORK)
    return local.date(), local.strftime("%I:%M %p").lstrip("0")


def _embedded_links(block: Tag) -> tuple[str, str]:
    zoom = ""
    paper = ""
    for anchor in block.select("a[href]"):
        url = anchor["href"].strip()
        if urlparse(url).scheme != "https":
            continue
        label = anchor.get_text(" ", strip=True).lower()
        if "zoom" in label:
            zoom = url
        elif "paper" in label:
            paper = url
    return zoom, paper


def _webinar_event(
    block: Tag, event_date: date, event_time: str, speaker_selector: str, title_selector: str
) -> Event | None:
    speaker = block.select_one(speaker_selector)
    title = block.select_one(title_selector)
    if not speaker or not title:
        return None
    affiliation = block.select_one(".affiliation")
    zoom, paper = _embedded_links(block)
    return Event(
        date=event_date,
        title=title.get_text(" ", strip=True),
        speaker=speaker.get_text(" ", strip=True),
        affiliation=affiliation.get_text(" ", strip=True) if affiliation else "",
        time=event_time,
        location="Online (Zoom)" if zoom else "Online",
        description=f"Paper: {paper}" if paper else "",
        link=zoom or paper,
        source="thermal",
    )


def parse_embedded_schedule(html: str, today: date | None = None) -> list[Event]:
    """Parse the current Apps Script card layout into New York event times."""
    today = today or date.today()
    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []
    cards = []
    for card in soup.select("section.upcoming article.card"):
        date_node = card.select_one(".card-date")
        match = EMBEDDED_DATE.search(date_node.get_text(" ", strip=True)) if date_node else None
        if match:
            cards.append((card, match))

    hero = soup.select_one("section.hero")
    if hero:
        day_node = hero.select_one(".day")
        month_node = hero.select_one(".month")
        time_node = hero.select_one(".time")
        if all((day_node, month_node, time_node)):
            month = month_node.get_text(" ", strip=True).split()[-1]
            day = int(day_node.get_text(" ", strip=True))
            clock_match = EMBEDDED_TIME.search(time_node.get_text(" ", strip=True))
            if clock_match:
                year = today.year
                if cards:
                    next_date = cards[0][1]
                    year = int(next_date["year"])
                    if month.lower() == "dec" and next_date["month"].lower() == "jan":
                        year -= 1
                elif date(year, datetime.strptime(month, "%b").month, day) < today:
                    year += 1
                event_date, event_time = _berlin_event_date_time(day, month, year, clock_match["time"])
                event = _webinar_event(hero, event_date, event_time, ".speaker", ".title")
                if event:
                    events.append(event)

    for card, match in cards:
        event_date, event_time = _berlin_event_date_time(
            int(match["day"]), match["month"], int(match["year"]), match["time"]
        )
        event = _webinar_event(card, event_date, event_time, "h3", ".talk-title")
        if event:
            events.append(event)
    return sorted(events, key=lambda event: (event.date, event.time, event.title.lower()))


class WebsiteTalkSource:
    name = "thermal"

    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self) -> list[Event]:
        def get_html(url: str) -> str:
            response = requests.get(url, timeout=30, headers={"User-Agent": "SCGPMessageBot/1.0"})
            response.raise_for_status()
            return response.text

        html = get_html(self.url)
        if urlparse(self.url).hostname == "script.google.com":
            embed_url = self.url
        else:
            embed_url = apps_script_url(html, self.url)
        if embed_url:
            if embed_url != self.url:
                html = get_html(embed_url)
            talks = parse_embedded_schedule(apps_script_html(html))
        else:
            talks = parse_schedule(html)
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
