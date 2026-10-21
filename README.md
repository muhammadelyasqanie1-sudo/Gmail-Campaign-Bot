# Gmail Campaign Bot

A Telegram bot starter for an admin-managed, consent-based Gmail campaign workflow.

## Current status

This repository is a starter scaffold. It supports public `/start`, phone-number registration, and admin-only configuration stored in SQLite. **It does not send emails yet.** Gmail sending must be connected through Google OAuth 2.0 and the Gmail API before use.

## Features in this starter

- Public Telegram bot entry point
- Phone number input in international format (for example, `+93700123456`)
- Admin-only settings commands
- Manually managed recipient allowlist
- Editable subject and message template, including `{{user_phone}}`
- Editable interval in seconds and duration in minutes
- SQLite persistence
- Secrets supplied via environment variables, not source code

## Commands

- `/start` — public entry point
- `/admin` — admin command guide
- `/setsubject <subject>` — set email subject (admin)
- `/settemplate <text>` — set body template (admin); use `{{user_phone}}` for the user's registered phone
- `/addrecipient <email>` — add a recipient to the allowlist (admin; only add people who agreed to receive mail)
- `/delrecipient <email>` — remove an allowlisted recipient (admin)
- `/recipients` — list allowlisted recipients (admin)
- `/setinterval <seconds>` — set requested interval (admin; sending must still follow Gmail quotas and anti-abuse controls)
- `/setduration <minutes>` — set campaign duration in minutes (admin)
- `/settings` — view current settings (admin)

## Run locally

1. Create a Telegram bot using BotFather.
2. Copy `.env.example` to `.env` and fill in `BOT_TOKEN` and your numeric Telegram `ADMIN_ID`.
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python app.py`

## Railway

Connect this GitHub repository to Railway and add `BOT_TOKEN` and `ADMIN_ID` under Variables. `DATABASE_PATH` can be set to `bot_data.sqlite3`, but a persistent volume is recommended for SQLite data. Do not place real credentials in GitHub.

## Gmail integration

The next phase must use Google OAuth 2.0 and Gmail API with encrypted/protected token storage. Do not ask users for Gmail passwords or verification codes. Sending must be limited to explicitly authorized, consenting recipients, honor unsubscribes and Gmail limits, and stop on quota or abuse errors. This starter intentionally does not implement repeated bulk sending.
