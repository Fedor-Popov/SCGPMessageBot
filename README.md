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

- `sources/thermal.py` reads the public Thermal Seminars website and supplies the default 11:00 AM / room 102 metadata, also used for Thermal Seminar sheets.
- `sources/wednesday.py` reads Wednesday Seminar sheets and supplies the default 2:00 PM / room 313 metadata.
- `sources/journal_club.py` reads Journal Club sheets and supplies the default 2:00 PM / Common Room metadata.
- `sources/bouncing.py` adds a recurring Bouncing Seminar every Friday at 11:00 AM in the Common Room.
- `sources/manual.py` persists events entered through the `/add` command.
- `sources/google_sheets.py` provides the reusable Google Sheets adapter; column positions may differ.
- `lunch.py` reads and caches the current Lessings Simons Center cafe menu for the `Lunch` button and `/lunch` command. It refreshes every 10 minutes.
- `cache.py` stores the combined normalized events locally as JSON.
- `trains.py` reads and caches MTA's LIRR timetable and plans train journeys; `trains_ui.py` supplies the Telegram station selectors.

## LIRR trains

Select **Trains** (or `/trains`), choose **From**, then **To**: Ronkonkoma, Stony Brook, NYC (Penn Station), or Jamaica. Results show departures in the next three hours in New York time, with train numbers, arrival times and any changes. NYC means Penn Station; Grand Central is not included. The same station cannot be selected as both endpoints.

Every train screen has a **Main menu** button, including the station selectors, loading screen and results. Returning to the main menu dismisses pending lookup results so they cannot replace the menu when they arrive.

The module uses MTA's [public LIRR GTFS schedule feed](https://www.mta.info/developers), without an API key or new Python dependencies. It downloads the timetable at startup and every six hours, keeping `lirr-schedule.zip` locally (override with `LIRR_CACHE_FILE`). Queries run in the background so the bot remains responsive. During an outage, a valid cached feed up to 48 hours old can be used with a visible notice. An expired timetable reports unavailable rather than an empty schedule.

These are **scheduled times**, not live delay/cancellation predictions. The planner respects service dates, holiday exceptions, after-midnight services, pickup/drop-off restrictions and MTA's station/trip transfer rules. It searches journeys with up to two changes within the same stations and a maximum total duration of six hours, preferring the fastest connection for each initial train and removing slower detours. Results are paginated five journeys at a time, with refresh and new-search buttons. Use the linked MTA TrainTime for live service conditions.

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

Use `/add` or `/delete` in a private chat with the bot and enter the password when prompted. Only a salted scrypt hash is stored in `access.py`; entered passwords are hashed and compared with `hmac.compare_digest`. The bot attempts to delete the password message. Authorization lasts 15 minutes and permits one operation. Five incorrect attempts temporarily block further attempts for that user for five minutes. `/cancel` revokes authorization; each new command requires the password again.

After authentication, `/add` asks for date, title, speaker, abstract, time, and location. Enter `-` for an optional blank field or `/cancel` to stop. Manual events are saved in `manual-events.json`, merged into the local cache, and then trigger a background rebuild of the output spreadsheet. The spreadsheet rebuild finishes after the required two-minute unpublished period. `/delete` displays one button for every manual event dated today or later; selecting one removes it from `manual-events.json`, refreshes the local cache, and triggers another background spreadsheet rebuild. The buttons are tied to the authenticated user and expire with the authorization. Events obtained from website or Google Sheets modules cannot be deleted with this command. Every hourly source refresh also rebuilds the output spreadsheet. The startup and daily 1:00 AM rebuilds remain as safeguards. Recurring Bouncing Seminar entries end before October 1, 2026.
