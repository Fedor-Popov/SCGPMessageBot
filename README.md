# SCGP Message Bot

Telegram bot that announces events from multiple sources. It refreshes a local `talks-cache.json` every Saturday, supports `/today`, `/week`, and `/nextweek`, and sends the cached current-week schedule on Monday.

## Event source API

Every source implements the interface in `events.py`:

```python
class EventSource(Protocol):
    name: str

    def fetch(self) -> list[Event]:
        """Fetch current/upcoming events from this source."""
```

To add another event series, create `sources/my_event.py` with a class implementing `name` and `fetch() -> list[Event]`, then add it to the `sources` list in `build_application()` in `bot.py`. The bot combines results and deduplicates by date, title, and speaker. A failed source does not overwrite the previous cache.

Current modules:

- `sources/thermal.py` reads the public Thermal Seminars website.
- `sources/google_sheets.py` reads any number of Google Sheets by header names, so column positions may differ.
- `cache.py` stores the combined normalized events locally as JSON.

## Configuration

Copy `.env.example` to `.env` and set the Telegram token. The website source works without Google credentials. To enable Sheets, set two comma-separated IDs:

```env
GOOGLE_SPREADSHEET_IDS=first_sheet_id,second_sheet_id
GOOGLE_OAUTH_TOKEN_FILE=google-token.json
```

The Sheets must have headers named `Dates`/`Date`, `Name`/`Speaker`, `Talk Title`/`Title`, and `Talk Abstract`/`Abstract`. Extra fields such as `Time`, `Location`, and `Link` are optional. Column positions may differ between spreadsheets.

For a terminal-only server, the simplest authentication is Google Application Default Credentials. Run this once as the Google account that can read the sheets:

```bash
gcloud auth application-default login --no-launch-browser
```

Open the printed URL in a browser, complete the sign-in, and paste the verification code into the terminal. The bot automatically uses the resulting ADC credentials when `google-token.json` is not present. Google stores the credentials in its local configuration directory; do not commit or share that file.

Authorize Sheets on a computer with a browser, not on the terminal-only server:

```bash
GOOGLE_OAUTH_CLIENT_FILE=/path/to/oauth-client.json \
GOOGLE_OAUTH_TOKEN_FILE=google-token.json \
uv run python authorize_google.py
```

Sign in with the Google account that can access both spreadsheets. Copy `google-token.json` securely to the remote server. The server uses the refresh token and never opens a browser.

## Run

```bash
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
uv run python bot.py
```

The first start schedules an immediate refresh if no cache exists. Later refreshes run on Saturday at `REFRESH_HOUR` in `BOT_TIMEZONE`.
