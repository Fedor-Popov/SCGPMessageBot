"""Parser for the current Simons Center cafe lunch menu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup
import json
from pathlib import Path


@dataclass(frozen=True)
class LunchItem:
    name: str
    description: str = ""


@dataclass(frozen=True)
class LunchMenu:
    date: date
    sections: tuple[tuple[str, tuple[LunchItem, ...]], ...]


class LunchCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, menu: LunchMenu) -> None:
        payload = {
            "date": menu.date.isoformat(),
            "sections": [
                {"name": name, "items": [{"name": item.name, "description": item.description} for item in items]}
                for name, items in menu.sections
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2) + "\n")
        self.path.chmod(0o600)

    def load(self) -> LunchMenu | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text())
        sections = tuple(
            (section["name"], tuple(LunchItem(**item) for item in section.get("items", [])))
            for section in payload.get("sections", [])
        )
        return LunchMenu(date.fromisoformat(payload["date"]), sections)


class LessingsLunchSource:
    name = "lessings-lunch"

    def __init__(self, url: str = "https://www.lessings.com/my/lfsm/weekly-menu/simons-center") -> None:
        self.url = url

    def fetch(self) -> LunchMenu:
        response = requests.get(self.url, timeout=30, headers={"User-Agent": "SCGPMessageBot/1.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        menu_box = soup.select_one(".menu_box")
        if menu_box is None:
            raise ValueError("Lessings page does not contain a menu")
        header = menu_box.select_one(".menu_header h2")
        if header is None:
            raise ValueError("Lessings menu has no date")
        menu_date = _parse_menu_date(header.get_text(" ", strip=True))
        content = menu_box.select_one(".menu_box_content")
        if content is None:
            return LunchMenu(menu_date, ())

        sections: list[tuple[str, tuple[LunchItem, ...]]] = []
        for heading in content.find_all("h3", recursive=False):
            items: list[LunchItem] = []
            listing = heading.find_next_sibling("ul")
            if listing is None:
                continue
            for row in listing.select("li.row_item"):
                name_node = row.select_one("h4")
                if name_node is None:
                    continue
                description_node = row.select_one("p")
                items.append(LunchItem(
                    name=name_node.get_text(" ", strip=True),
                    description=description_node.get_text(" ", strip=True) if description_node else "",
                ))
            sections.append((heading.get_text(" ", strip=True), tuple(items)))
        return LunchMenu(menu_date, tuple(sections))


def _parse_menu_date(text: str) -> date:
    return datetime.strptime(text, "%A, %B %d").replace(year=date.today().year).date()
