# SCGP Telegram bot — C++ implementation

This is an additional implementation of the bot. The existing Python bot is
unchanged. This directory builds a single native executable with Telegram
long polling, schedule commands/buttons, local caching, Thermal Seminars and
YITP calendar readers, Google Sheets input, protected `/add` and `/delete`,
direct cached LIRR searches, Monday announcements, public Google Calendar
rebuilds, and GitHub Pages schedule publishing.

## Ubuntu installation

```bash
sudo apt update
sudo apt install -y build-essential libcurl4-openssl-dev libssl-dev pkg-config
cd ~/SCGPMessageBot/cpp_bot
make build
cp .env.example .env
vim .env
./build/scgp_bot
```

Set `TELEGRAM_BOT_TOKEN` before starting. For Sheets, authenticate once as the
Google account that can read the sheets:

```bash
gcloud auth application-default login --no-browser
```

Alternatively, copy the Python bot's authorized `google-token.json` to the
repository root. When run from `cpp_bot`, the native bot reads
`../google-token.json` and refreshes it through Google OAuth. Set
`GOOGLE_OAUTH_TOKEN_FILE` in `.env` if the file is elsewhere.

To enable protected native `/add` and `/delete`, store a SHA-256 digest in
`CPP_ADMIN_PASSWORD_SHA256`. For example, `printf ADSCFT | sha256sum` produces
the digest to place in `.env`.

The executable uses the current directory for cache and data files. `make
build` only compiles; it does not start Telegram.

The native LIRR search reports direct trips from the cached GTFS feed. The
native Google Sheets reader, Alessio spreadsheet rebuild, Google Calendar
writer, and website publisher are independent of the Python deployment.
