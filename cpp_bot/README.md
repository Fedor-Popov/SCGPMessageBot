# SCGP Telegram bot — C++ implementation

This is an additional implementation of the bot. The existing Python bot is
unchanged. This directory builds a single native executable with Telegram
long polling, schedule commands/buttons, local caching, Thermal Seminars and
YITP calendar readers, Google Sheets input, and Monday announcements.

## Ubuntu installation

```bash
sudo apt update
sudo apt install -y build-essential libcurl4-openssl-dev pkg-config
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

The executable uses the current directory for cache and data files. `make
build` only compiles; it does not start Telegram.

The public schedule readers and Telegram schedule behavior are implemented in
C++. The Python deployment remains available for the advanced protected
`/add` and `/delete` workflow, LIRR route planning, and Google Calendar/Alessio
write integrations while those integrations are migrated separately.
