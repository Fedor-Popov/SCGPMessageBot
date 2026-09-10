"""Parser for the current Simons Center cafe lunch menu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class LunchItem:
    name: str
    description: str = ""


@dataclass(frozen=True)
class LunchMenu:
    date: date
    sections: tuple[tuple[str, tuple[LunchItem, ...]], ...]


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
