"""Current-week seminar email content and explicit delivery lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from events import Event


SEMINAR_RECIPIENTS = (
    "fpopov@scgp.stonybrook.edu",
    "zeqi.zhang@stonybrook.edu",
    "nnekrasov@scgp.stonybrook.edu",
    "siwei.zhong@stonybrook.edu",
    "zkomargodski@scgp.stonybrook.edu",
    "aabanov@scgp.stonybrook.edu",
    "leonardo.rastelli@gmail.com",
    "martin.rocek@stonybrook.edu",
    "anirudh.deb@stonybrook.edu",
    "yaman.sanghavi@stonybrook.edu",
    "lalvarezgaume@scgp.stonybrook.edu",
    "araviv-moshe@scgp.stonybrook.edu",
    "fyan.hepth@gmail.com",
    "siqi.chen.1@stonybrook.edu",
    "dilara.kosva@stonybrook.edu",
    "mathieu.boisvert@stonybrook.edu",
    "spinor87@gmail.com",
    "vasilii.iugov@stonybrook.edu",
    "lin.ziyi@stonybrook.edu",
    "jesse.caojiaxi@gmail.com",
    "shihab.fadda@stonybrook.edu",
    "ramtin.mohasselyazdi@stonybrook.edu",
    "han.ma@stonybrook.edu",
    "yashvinder.singh@stonybrook.edu",
    "gurulakshmi.subramanian@stonybrook.edu",
    "marcelo.barbosa@stonybrook.edu",
    "nevillejoshua.rajappa@stonybrook.edu",
    "yanmong.chan@stonybrook.edu",
    "dmitrii.pavshinkin@stonybrook.edu",
    "luke.martin@stonybrook.edu",
    "nathanan.tantivasadakarn@stonybrook.edu",
    "jacob.mcnamara@scgp.stonybrook.edu",
    "alessio.miscioscia@stonybrook.edu",
    "mnocchi@scgp.stonybrook.edu",
    "arkya.chat@gmail.com",
    "joseph.helfer@gmail.com",
    "msacchi@scgp.stonybrook.edu",
)

SEMINAR_TEST_RECIPIENTS = (
    "fpopov@scgp.stonybrook.edu",
    "frenkelalexander1@gmail.com",
    "alessio.miscioscia@stonybrook.edu",
)


@dataclass(frozen=True)
class SeminarEmail:
    subject: str
    body: str


def compose_subscriber_report(recipients: tuple[str, ...], subscribers: tuple[str, ...]) -> SeminarEmail:
    lines = [
        "SCGP seminar email subscribers",
        "",
        "This report is sent only to Fedor Popov, Alexander Frenkel, and Alessio Miscioscia.",
        "",
        f"Full announcement recipient list ({len(recipients)} unique addresses):",
        *recipients,
        "",
        f"Addresses added through Subscribe ({len(subscribers)}):",
        *(subscribers or ("No additional subscribers yet.",)),
        "",
    ]
    return SeminarEmail("[TEST] SCGP seminar subscriber list", "\n".join(lines))


def current_week_events(
    events: Iterable[Event], today: date, weekday: int, source_names: Iterable[str],
) -> list[Event]:
    """Select only the requested day and series in today's Monday–Sunday week."""
    target = today - timedelta(days=today.weekday()) + timedelta(days=weekday)
    sources = set(source_names)
    unique = {
        (event.title.casefold(), event.speaker.casefold(), event.time): event
        for event in events
        if event.date == target and event.source in sources
    }
    return sorted(unique.values(), key=lambda event: (event.time, event.title.casefold(), event.speaker.casefold()))


def compose_seminar_email(events: list[Event], series: str, *, test: bool = False) -> SeminarEmail:
    """Build one plaintext announcement per series, including all its talks."""
    if not events:
        raise ValueError("Cannot compose a reminder without events")
    event_date = events[0].date
    subject = f"Reminder: {series} — {event_date:%B} {event_date.day}, {event_date.year}"
    lines = ["Dear all,", "", f"This is a reminder for this week's {series}."]
    for event in events:
        lines.extend(["", f"Date: {event.date:%A, %B} {event.date.day}, {event.date.year}"])
        if event.time:
            lines.append(f"Time: {event.time} (New York time)")
        if event.location:
            lines.append(f"Location: {event.location}")
        if event.speaker:
            speaker = event.speaker + (f" ({event.affiliation})" if event.affiliation else "")
            lines.append(f"Speaker: {speaker}")
        elif event.affiliation:
            lines.append(f"Affiliation: {event.affiliation}")
        if event.title:
            lines.append(f"Title: {event.title}")
        if event.description:
            lines.extend(["", "Abstract:", event.description])
        if event.link:
            lines.extend(["", f"More information: {event.link}"])
    lines.extend(["", "Best wishes,", "SCGP Seminar Organizers", ""])
    if test:
        subject = f"[TEST] {subject}"
        lines = ["TEST PREVIEW — sent only to Fedor Popov, Alexander Frenkel, and Alessio Miscioscia.", "", *lines]
    return SeminarEmail(subject, "\n".join(lines))
