# Telegram talks bot

This bot reads a Google Sheet and exposes `/today`. Chats that send `/start` receive the weekly announcement every Monday at 10:00 in `BOT_TIMEZONE`; `/stop` unsubscribes.

## Sheet format

The first row must contain `date` and `title`. Optional columns are `time`, `speaker`, `location`, `description`, and `link`. The parser also accepts the current layout: `Dates` (`MM/DD`, interpreted in the current year), `Name`, `Talk Title`, and `Talk Abstract`.

For the current sheet, the bot reads columns A:M:

```text
Dates | Name | ... | Talk Title | Talk Abstract
09/14 | A. Researcher | ... | Distributed Systems | An introduction to the topic
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
