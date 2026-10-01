"""Load shared Google credentials without narrowing their saved permissions."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Iterable

import google.auth
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar",)
GOOGLE_SCOPES = (*SHEETS_SCOPES, *CALENDAR_SCOPES)
_TOKEN_LOCK = Lock()
_REAUTHORIZE = (
    "Rerun authorize_google.py, approve the requested Google permissions, "
    "and copy the new token file to the bot server."
)


def _prepare_user_credentials(credentials: Credentials, required_scopes: tuple[str, ...]):
    # Loading with scopes=required_scopes would override the saved scopes without
    # changing the existing access token, making has_scopes() misleading.
    missing_scopes = not credentials.has_scopes(required_scopes)
    if missing_scopes:
        # Recover files narrowed by older code. Google can only grant scopes
        # already authorized by the user; otherwise reauthorization is needed.
        scopes = tuple(dict.fromkeys((*(credentials.scopes or ()), *required_scopes)))
        credentials = Credentials.from_authorized_user_info(json.loads(credentials.to_json()), scopes=scopes)
    refreshed = missing_scopes or not credentials.valid
    if refreshed:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise RuntimeError(f"Could not refresh Google authorization. {_REAUTHORIZE}") from exc
    if not credentials.valid:
        raise RuntimeError(f"Google authorization is invalid or expired. {_REAUTHORIZE}")
    granted = credentials.granted_scopes
    if granted is not None and not set(required_scopes).issubset(granted):
        raise RuntimeError(f"Google authorization lacks the required permissions. {_REAUTHORIZE}")
    return credentials, refreshed


def _save_credentials(path: Path, credentials: Credentials) -> None:
    payload = json.loads(credentials.to_json())
    if credentials.granted_scopes is not None:
        # Keep the server's actual grant, not merely the scopes requested.
        payload["scopes"] = list(credentials.granted_scopes)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_google_credentials(token_file: Path | None, required_scopes: Iterable[str]):
    """Preserve saved scopes and refresh before reusing a token for another API."""
    required = tuple(required_scopes)
    # Source refreshes and calendar exports may run in different worker threads.
    with _TOKEN_LOCK:
        if token_file and token_file.exists():
            credentials = Credentials.from_authorized_user_file(token_file)
            credentials, refreshed = _prepare_user_credentials(credentials, required)
            if refreshed:
                _save_credentials(token_file, credentials)
            return credentials
        try:
            credentials, _ = google.auth.default(scopes=required)
        except DefaultCredentialsError as exc:
            raise RuntimeError(f"Google credentials not found. {_REAUTHORIZE}") from exc
        if isinstance(credentials, Credentials):
            credentials, _ = _prepare_user_credentials(credentials, required)
        return credentials
