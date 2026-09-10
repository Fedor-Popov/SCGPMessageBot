from datetime import date
from pathlib import Path

from bot import format_talks, week_start
from cache import EventCache
from events import Event
from website import parse_schedule
from sources.google_sheets import parse_rows


def test_parse_schedule():
    html = """
    <h2>Future Seminars schedule</h2>
    <div>Sep 15: Francesco Russo (Ecole Polytechnique, CPHT) on</div>
    <div>2606.17167</div>
    <div>Thermal One-point Functions</div>
    <div>Abstract: A talk about thermal one-point functions.</div>
    <h2>Past Seminars</h2>
    <div>01/01/26: Old talk</div>
    """
    talks = parse_schedule(html, year=2026)
    assert len(talks) == 1
    assert talks[0].date == date(2026, 9, 15)
    assert talks[0].speaker == "Francesco Russo"
    assert talks[0].affiliation == "Ecole Polytechnique, CPHT"
    assert talks[0].title == "Thermal One-point Functions"
    assert talks[0].link == "https://arxiv.org/abs/2606.17167"


def test_cache_round_trip(tmp_path: Path):
    cache = EventCache(tmp_path / "talks.json")
    talks = [Event(date(2026, 9, 15), "A Talk", speaker="A Speaker")]
    cache.save(talks)
    assert cache.load() == talks


def test_week_start():
    assert week_start(date(2026, 9, 16)) == date(2026, 9, 14)


def test_format_empty():
    assert format_talks([], "Talks") == "Talks\n\nNo talks found."


def test_format_talks_uses_html_emphasis():
    message = format_talks([
        Event(date(2026, 9, 15), "A <Talk>", speaker="A Speaker"),
        Event(date(2026, 9, 16), "Another Talk", speaker="Another Speaker"),
    ], "Talks")
    assert "<b>A &lt;Talk&gt;</b>" in message
    assert "<i>A Speaker</i>" in message
    assert "</i>)\n\n• <b>Another Talk</b>" in message


def test_sheet_rows_keep_missing_title_and_abstract():
    talks = parse_rows([["Dates", "Name", "Talk Title", "Talk Abstract"], ["09/17/2026", "A Speaker", "", ""]], "test")
    assert len(talks) == 1
    assert talks[0].title == ""
    assert talks[0].description == ""
    assert "A Speaker" in format_talks(talks, "Talks")
