from datetime import date
from pathlib import Path

from bot import Talk, format_talks, week_start
from website import JsonTalkCache, parse_schedule


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
    assert talks[0].location == "Ecole Polytechnique, CPHT"
    assert talks[0].title == "Thermal One-point Functions"
    assert talks[0].link == "https://arxiv.org/abs/2606.17167"


def test_cache_round_trip(tmp_path: Path):
    cache = JsonTalkCache(tmp_path / "talks.json")
    talks = [Talk(date(2026, 9, 15), "A Talk", speaker="A Speaker")]
    cache.save(talks)
    assert cache.load() == talks


def test_week_start():
    assert week_start(date(2026, 9, 16)) == date(2026, 9, 14)


def test_format_empty():
    assert format_talks([], "Talks") == "Talks\n\nNo talks found."
