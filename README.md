# Telegram talks bot

This bot reads a Google Sheet and exposes `/today`. Chats that send `/start` receive the weekly announcement every Monday at 10:00 in `BOT_TIMEZONE`; `/stop` unsubscribes.

## Sheet format

The first row must contain `date` and `title`. Optional columns are `time`, `speaker`, `location`, `description`, and `link`. Dates should use `YYYY-MM-DD`, for example:

```text
date       | title                    | time  | speaker      | location
2026-09-14 | Distributed Systems      | 10:00 | A. Researcher| Room 101
```

## Setup

1. Create a bot with `@BotFather` and copy its token.
2. In Google Cloud, enable the Google Sheets API, create a service account, and download its JSON key.
3. Share the spreadsheet with the service account email as a viewer. Put the sheet ID and key path in `.env` (copy `.env.example`).
4. Install dependencies and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
python bot.py
```

The bot uses polling, so it can run on a small VM or container without a public webhook endpoint. Keep `.env`, the service-account JSON, and `subscribers.json` out of version control.
