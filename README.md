# Telegram talks bot

This bot reads the public [Thermal Seminars](https://sites.google.com/view/thermalseminars) website, refreshes a local JSON cache every Saturday, and exposes `/today` and `/week`. Chats that send `/start` receive the cached weekly announcement every Monday at 10:00 in `BOT_TIMEZONE`; `/stop` unsubscribes.

## Website data

The parser reads the `Future Seminars schedule` section and stops at `Past Seminars`. It extracts the date, speaker, affiliation, title, abstract, and arXiv link when present. Short dates such as `Sep 15` are interpreted using the current year.

## Setup

1. Create a bot with `@BotFather` and copy its token.
2. Put the token in `.env` (copy `.env.example`). No Google credentials are needed.
3. Install dependencies and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

The first run fetches the website immediately and writes `talks-cache.json`. Later runs read the cache until the next Saturday refresh. Keep `.env`, `talks-cache.json`, and `subscribers.json` out of version control. The bot uses polling, so it can run on a terminal-only remote server.
