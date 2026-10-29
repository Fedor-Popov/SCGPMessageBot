"""Checks for the Thermal Seminars Apps Script schedule."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

from website import WebsiteTalkSource, apps_script_html, parse_embedded_schedule


def test_apps_script_thermal_schedule_and_timezones():
    embedded_html = """
    <section class="hero"><div class="date-panel"><span class="day">3</span>
    <span class="month">Tue Nov</span><span class="time">17:00 (Berlin time)</span></div>
    <div class="hero-content"><p class="speaker">Elizabeth Helfenberger</p>
    <p class="affiliation">TCD</p><h2 class="title">Thermal two-point functions</h2>
    <a href="https://stonybrook.zoom.us/j/123">Join on Zoom</a>
    <a href="https://arxiv.org/pdf/2607.05258">Open paper</a></div></section>
    <section class="upcoming"><article class="card">
    <p class="card-date">Tue 10 Nov 2026 · 17:00 (Berlin time)</p>
    <h3>Nathan Benjamin</h3><p class="affiliation">USC</p>
    <p class="talk-title">From Z to a</p>
    <a href="https://stonybrook.zoom.us/j/123">Zoom</a></article></section>
    """
    payload = json.dumps({"userHtml": embedded_html})
    wrapper = f'<script>goog.script.init({json.dumps(payload).replace("<", r"\x3c")}, "");</script>'
    site = '<h2>Future Seminars schedule</h2><iframe src="https://script.google.com/macros/s/test/exec"></iframe>'
    responses = [MagicMock(text=site), MagicMock(text=wrapper)]
    with patch("website.requests.get", side_effect=responses) as get:
        talks = WebsiteTalkSource("https://sites.google.com/view/thermalseminars").fetch()
    assert get.call_args_list[1].args[0] == "https://script.google.com/macros/s/test/exec"
    assert apps_script_html(wrapper) == embedded_html
    assert len(talks) == 2
    assert talks[0].date == date(2026, 11, 3)
    assert talks[0].time == "11:00 AM"
    assert talks[0].location == "Online (Zoom)"
    assert talks[0].link == "https://stonybrook.zoom.us/j/123"
    assert talks[0].description == "Paper: https://arxiv.org/pdf/2607.05258"
    assert talks[1].speaker == "Nathan Benjamin"


def test_berlin_time_differs_during_dst_transition():
    html = """<section class="upcoming"><article class="card">
    <p class="card-date">Tue 27 Oct 2026 · 17:00 (Berlin time)</p>
    <h3>A Speaker</h3><p class="talk-title">A Talk</p></article></section>"""
    assert parse_embedded_schedule(html)[0].time == "12:00 PM"
