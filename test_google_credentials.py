import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.oauth2.credentials import Credentials

import google_credentials
import sources.google_sheets
from google_calendar import GoogleCalendarPublisher
from google_credentials import CALENDAR_SCOPES, GOOGLE_SCOPES, SHEETS_SCOPES, load_google_credentials
from sources.google_sheets import GoogleSheetsSource


def token_payload(scopes, *, expired=False):
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return {
        "token": json.dumps(list(scopes)),
        "refresh_token": "test-refresh-token",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "scopes": list(scopes),
        "expiry": expiry.isoformat() + "Z",
    }


def save_token(path, scopes, *, expired=False):
    path.write_text(json.dumps(token_payload(scopes, expired=expired)))


@pytest.fixture
def refresh_calls(monkeypatch):
    calls = []

    def refresh(credentials, request):
        calls.append(tuple(credentials.scopes))
        credentials.token = json.dumps(list(credentials.scopes))
        credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        credentials._granted_scopes = list(credentials.scopes)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return calls


def test_sheets_refresh_then_calendar_and_restart_preserve_both_permissions(tmp_path, monkeypatch, refresh_calls):
    token = tmp_path / "google-token.json"
    save_token(token, GOOGLE_SCOPES, expired=True)
    build = Mock()
    monkeypatch.setattr(sources.google_sheets, "build", build)

    GoogleSheetsSource(["sheet-id"], token)._service()
    sheets_credentials = build.call_args.kwargs["credentials"]
    calendar_credentials = GoogleCalendarPublisher(token_file=token)._credentials()

    assert refresh_calls == [GOOGLE_SCOPES]
    assert set(json.loads(sheets_credentials.token)) == set(GOOGLE_SCOPES)
    assert set(json.loads(calendar_credentials.token)) == set(GOOGLE_SCOPES)
    assert set(json.loads(token.read_text())["scopes"]) == set(GOOGLE_SCOPES)
    assert token.stat().st_mode & 0o777 == 0o600

    # New source/publisher instances model restarting the bot with the saved file.
    GoogleSheetsSource(["sheet-id"], token)._service()
    assert set(json.loads(GoogleCalendarPublisher(token_file=token)._credentials().token)) == set(GOOGLE_SCOPES)
    assert len(refresh_calls) == 1


@pytest.mark.parametrize("saved,required", [(SHEETS_SCOPES, CALENDAR_SCOPES), (CALENDAR_SCOPES, SHEETS_SCOPES)])
def test_narrowed_but_unexpired_legacy_token_is_refreshed_before_reuse(tmp_path, refresh_calls, saved, required):
    token = tmp_path / "google-token.json"
    save_token(token, saved)

    credentials = load_google_credentials(token, required)

    assert len(refresh_calls) == 1
    assert set(refresh_calls[0]) == set(GOOGLE_SCOPES)
    assert set(json.loads(credentials.token)) == set(GOOGLE_SCOPES)
    assert set(json.loads(token.read_text())["scopes"]) == set(GOOGLE_SCOPES)


def test_calendar_refresh_preserves_sheets_and_other_saved_scopes(tmp_path, refresh_calls):
    token = tmp_path / "google-token.json"
    scopes = (*GOOGLE_SCOPES, "https://www.googleapis.com/auth/drive.file")
    save_token(token, scopes, expired=True)

    GoogleCalendarPublisher(token_file=token)._credentials()
    load_google_credentials(token, SHEETS_SCOPES)

    assert refresh_calls == [scopes]
    assert set(json.loads(token.read_text())["scopes"]) == set(scopes)


def test_sheets_only_configuration_does_not_request_calendar_access(tmp_path, refresh_calls):
    token = tmp_path / "google-token.json"
    save_token(token, SHEETS_SCOPES, expired=True)

    load_google_credentials(token, SHEETS_SCOPES)

    assert refresh_calls == [SHEETS_SCOPES]
    assert json.loads(token.read_text())["scopes"] == list(SHEETS_SCOPES)


def test_refresh_failure_requires_reauthorization_without_overwriting_token(tmp_path, monkeypatch):
    token = tmp_path / "google-token.json"
    save_token(token, SHEETS_SCOPES)
    original = token.read_bytes()
    monkeypatch.setattr(Credentials, "refresh", Mock(side_effect=RefreshError("invalid_scope")))

    with pytest.raises(RuntimeError, match="Rerun authorize_google.py"):
        load_google_credentials(token, CALENDAR_SCOPES)

    assert token.read_bytes() == original


def test_partial_scope_grant_is_not_mistaken_for_calendar_authorization(tmp_path, monkeypatch):
    token = tmp_path / "google-token.json"
    save_token(token, SHEETS_SCOPES)
    original = token.read_bytes()

    def refresh(credentials, request):
        credentials.token = "sheets-only-access-token"
        credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        credentials._granted_scopes = list(SHEETS_SCOPES)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    with pytest.raises(RuntimeError, match="lacks the required permissions"):
        load_google_credentials(token, CALENDAR_SCOPES)
    assert token.read_bytes() == original


def test_saved_scope_metadata_records_actual_grant(tmp_path, monkeypatch):
    token = tmp_path / "google-token.json"
    save_token(token, GOOGLE_SCOPES, expired=True)

    def refresh(credentials, request):
        credentials.token = "sheets-only-access-token"
        credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        credentials._granted_scopes = list(SHEETS_SCOPES)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    load_google_credentials(token, SHEETS_SCOPES)
    assert json.loads(token.read_text())["scopes"] == list(SHEETS_SCOPES)
    with pytest.raises(RuntimeError, match="lacks the required permissions"):
        load_google_credentials(token, CALENDAR_SCOPES)


def test_missing_access_token_refreshes_even_with_future_expiry(tmp_path, refresh_calls):
    token = tmp_path / "google-token.json"
    payload = token_payload(GOOGLE_SCOPES)
    payload.pop("token")
    token.write_text(json.dumps(payload))

    assert load_google_credentials(token, CALENDAR_SCOPES).valid
    assert refresh_calls == [GOOGLE_SCOPES]


def test_application_default_user_credentials_also_refresh_before_scope_change(monkeypatch, refresh_calls):
    credentials = Credentials.from_authorized_user_info(token_payload(SHEETS_SCOPES))
    monkeypatch.setattr(google_credentials.google.auth, "default", Mock(return_value=(credentials, "project")))

    result = load_google_credentials(None, CALENDAR_SCOPES)

    assert set(json.loads(result.token)) == set(GOOGLE_SCOPES)
    assert len(refresh_calls) == 1


def test_other_application_default_credentials_are_preserved(monkeypatch):
    credentials = object()
    default = Mock(return_value=(credentials, "project"))
    monkeypatch.setattr(google_credentials.google.auth, "default", default)

    assert load_google_credentials(None, CALENDAR_SCOPES) is credentials
    default.assert_called_once_with(scopes=CALENDAR_SCOPES)


def test_missing_credentials_give_actionable_error(monkeypatch):
    monkeypatch.setattr(google_credentials.google.auth, "default", Mock(side_effect=DefaultCredentialsError()))
    with pytest.raises(RuntimeError, match="Google credentials not found.*authorize_google.py"):
        load_google_credentials(None, CALENDAR_SCOPES)
