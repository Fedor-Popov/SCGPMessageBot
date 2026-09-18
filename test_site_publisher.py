import json
import subprocess
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from site_publisher import SitePublisher, public_snapshot, series_from_env


def test_only_public_fields_and_safe_links_are_exported():
    cache = {"updated_at": "2026-09-18T12:00:00-04:00", "token": "secret", "events": [
        {"date": "2026-09-21", "title": "A < B", "speaker": "Speaker", "source": "google:private-id",
         "token": "secret", "chat_id": 123, "link": "javascript:alert(1)"},
    ]}
    result = public_snapshot(cache, {"google:private-id": "Journal Club"})
    event, = result["events"]
    assert event["series"] == "Journal Club"
    assert event["title"] == "A < B"
    assert event["description"] == ""
    assert event["link"] == ""
    assert result["updated_at"] == cache["updated_at"]
    text = json.dumps(result)
    for private in ("secret", "private-id", "chat_id", "token", "source"):
        assert private not in text


@pytest.mark.parametrize("link", ["https://docs.google.com/spreadsheets/d/private/edit", "https://user:secret@example.org", "file:///tmp/local"])
def test_private_links_are_not_exported(link):
    assert public_snapshot({"events": [{"date": "2026-09-21", "link": link}]})["events"][0]["link"] == ""


def test_empty_snapshot_and_invalid_date():
    assert public_snapshot({"events": []})["events"] == []
    with pytest.raises(ValueError):
        public_snapshot({"events": [{"date": "2026-02-30"}]})


def test_series_mapping(monkeypatch):
    for key in ("GOOGLE_WEDNESDAY_SPREADSHEET_IDS", "GOOGLE_JOURNAL_CLUB_SPREADSHEET_IDS", "GOOGLE_THERMAL_SPREADSHEET_IDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_IDS", " first , second ")
    assert series_from_env() == {"google:first": "Wednesday Seminar", "google:second": "Journal Club"}
    monkeypatch.setenv("GOOGLE_THERMAL_SPREADSHEET_IDS", "third")
    assert series_from_env() == {"google:third": "Thermal Seminar"}


def test_publisher_pushes_only_snapshot_and_handles_deletion(tmp_path):
    def git(*args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", "-c", "commit.gpgsign=false", *args],
                              cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    git("init", "--bare", str(remote))
    git("init", "-b", "main", str(seed))
    (seed / "index.html").write_text("Website")
    git("add", "index.html", cwd=seed)
    git("commit", "-m", "Initial site", cwd=seed)
    git("remote", "add", "origin", str(remote), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    publisher = SitePublisher(str(remote))
    snapshot = public_snapshot({"updated_at": "2026-09-18T12:00:00-04:00", "events": [{"date": "2026-09-21", "title": "Talk"}]})
    assert publisher.publish(snapshot)
    assert not publisher.publish(snapshot)
    assert git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main").splitlines() == ["data/events.json", "index.html"]
    assert json.loads(git("--git-dir", str(remote), "show", "main:data/events.json")) == snapshot
    assert publisher.publish(public_snapshot({"events": []}))
    assert json.loads(git("--git-dir", str(remote), "show", "main:data/events.json"))["events"] == []
    assert git("--git-dir", str(remote), "show", "main:index.html") == "Website"


def test_failed_git_operation_is_clear_without_exposing_url(tmp_path):
    with pytest.raises(RuntimeError, match="check SSH write access") as error:
        SitePublisher(str(tmp_path / "missing-repo-secret")).publish(public_snapshot({"events": []}))
    assert "missing-repo-secret" not in str(error.value)


@pytest.mark.parametrize("fail_publish", [False, True])
def test_cache_refresh_publishes_independently_of_sheets(tmp_path, monkeypatch, fail_publish):
    from bot import Settings, build_application
    from events import Event
    from sources.thermal import ThermalSeminarsSource
    from sources.yitp_calendar import YITPCalendarSource

    settings = Settings(
        telegram_token="123456:test-token", website_url="https://example.test", lunch_menu_url="https://example.test",
        lunch_cache_file=tmp_path / "lunch.json", cache_file=tmp_path / "cache.json", manual_events_file=tmp_path / "manual.json",
        google_spreadsheet_ids=(), wednesday_spreadsheet_ids=(), journal_club_spreadsheet_ids=(), thermal_spreadsheet_ids=(),
        additional_spreadsheet_ids=(), cache_export_spreadsheet_id="", google_token_file=tmp_path / "token.json",
        timezone="America/New_York", refresh_hour=3, refresh_interval_hours=1, announcement_hour=10,
        subscribers_file=tmp_path / "subscribers.json", website_repo_url="test-only-remote",
    )
    monkeypatch.setattr(ThermalSeminarsSource, "fetch", lambda self: [Event(date(2026, 9, 21), "Talk", source="thermal")])
    monkeypatch.setattr(YITPCalendarSource, "fetch", lambda self: [])
    published = []
    def publish(self, snapshot):
        published.append(snapshot)
        if fail_publish:
            raise RuntimeError("test failure")
        return True
    monkeypatch.setattr(SitePublisher, "publish", publish)
    app = build_application(settings)
    refresh = app.job_queue.get_jobs_by_name("initial-refresh-and-export")[0].callback
    async def exercise():
        tasks = []
        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task
        context = SimpleNamespace(application=SimpleNamespace(create_task=create_task))
        await refresh(context, sync_spreadsheet=False)
        await asyncio.gather(*tasks)
    asyncio.run(exercise())
    assert published[0]["events"][0]["series"] == "Thermal Seminar"
    assert json.loads(settings.cache_file.read_text())["events"][0]["title"] == "Talk"
