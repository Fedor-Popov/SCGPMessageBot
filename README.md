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
- `sources/wednesday.py` reads Wednesday Seminar sheets and supplies the default 2:00 PM / room 313 metadata.
- `sources/journal_club.py` reads Journal Club sheets and supplies the default 2:00 PM / Common Room metadata.
- `sources/bouncing.py` adds a recurring Bouncing Seminar every Friday at 11:00 AM in the Common Room.
- `sources/manual.py` persists events entered through the `/add` command.
- `sources/google_sheets.py` provides the reusable Google Sheets adapter; column positions may differ.
- `lunch.py` reads and caches the current Lessings Simons Center cafe menu for the `Lunch` button and `/lunch` command. It refreshes every 10 minutes.
- `cache.py` stores the combined normalized events locally as JSON.

## Configuration

Copy `.env.example` to `.env` and set the Telegram token. The website source works without Google credentials. To enable Sheets, set two comma-separated IDs:

```env
GOOGLE_WEDNESDAY_SPREADSHEET_IDS=first_sheet_id
GOOGLE_JOURNAL_CLUB_SPREADSHEET_IDS=
GOOGLE_THERMAL_SPREADSHEET_IDS=
GOOGLE_ADDITIONAL_SPREADSHEET_IDS=
GOOGLE_CACHE_EXPORT_SPREADSHEET_ID=
GOOGLE_OAUTH_TOKEN_FILE=google-token.json
```

The Sheets can use headers named `Dates`/`Date`, `Name`/`Speaker`, `Talk Title`/`Title`, and `Talk Abstract`/`Abstract`. Calendar-style sheets using `Title`, `Start`, `Description`, `Location`, and optional `Publish` are also supported. In that format, `Speaker:`, `Title:`, and `Abstract:` may be placed on separate lines in `Description`. Column positions may differ between spreadsheets.

Use `GOOGLE_ADDITIONAL_SPREADSHEET_IDS` for additional input sheets. Set `GOOGLE_CACHE_EXPORT_SPREADSHEET_ID` to Alessio's Google Calendar-style output sheet. During each sync, the bot sets every existing `Publish` checkbox to false, waits two minutes, clears the sheet, and rebuilds it from the complete local event cache with `Publish` checked. Events are exported only when a title or description is present. `Start` and `End` use formulas such as `=DATE(2026;9;11)+TIME(11;0;0)`. For backward compatibility, if the export variable is empty, the first additional spreadsheet ID is used as the output sheet.

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

The bot refreshes immediately at startup and then every `REFRESH_INTERVAL_HOURS` (one hour by default). The Monday announcement is sent at `ANNOUNCEMENT_HOUR` in `BOT_TIMEZONE`.

The `/add` command asks for date, title, speaker, abstract, time, and location. Enter `-` for an optional blank field or `/cancel` to stop. Manual events are saved in `manual-events.json`, merged into the local cache, and then trigger a background rebuild of the output spreadsheet. The bot replies immediately; the spreadsheet rebuild finishes after the required two-minute unpublished period. `/deleteadd` displays one button for every future manual event; selecting one removes it from `manual-events.json`, refreshes the local cache, and triggers another background spreadsheet rebuild. Events obtained from website or Google Sheets modules cannot be deleted with this command. Every hourly source refresh also rebuilds the output spreadsheet. The startup and daily 1:00 AM rebuilds remain as safeguards. Recurring Bouncing Seminar entries end before October 1, 2026.
