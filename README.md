# Gmail Campaign Bot

A Telegram bot starter for admin-managed campaign settings, with an optional separate WhatsApp Web session service.

## Current status

- The Telegram bot stores admin configuration in SQLite.
- Gmail sending is **not implemented**.
- The optional WhatsApp service uses the community-maintained, unofficial Baileys library. It can request a WhatsApp Web pairing code, send direct text messages, and forward incoming direct text messages to the Telegram admin.
- WhatsApp linking is not an official WhatsApp Business API integration. Account restrictions are possible. Do not use it for unsolicited or bulk messages.

## Telegram bot features

- Public `/start` and phone-number registration
- Admin-only configuration commands: `/admin`, `/setsubject`, `/settemplate`, `/addrecipient`, `/delrecipient`, `/recipients`, `/setinterval`, `/setduration`, `/settings`
- WhatsApp admin commands: `/wa`, `/wa_pair 93700123456`, `/wa_status`, `/wa_send 93700123456 Hello`
- WhatsApp pairing codes and messages are handled only by the admin commands; incoming direct text messages are forwarded to the admin's Telegram chat. Group messages are ignored.

## Deploy the Telegram bot

1. Connect this repository to a Railway service using the repository root as the root directory.
2. Add `BOT_TOKEN` and numeric `ADMIN_ID` under Variables.
3. Set `DATABASE_PATH=bot_data.sqlite3`. Add a persistent volume if you need SQLite data to survive deployments.
4. Deploy the service.

## Deploy the WhatsApp service separately

This is a monorepo: deploy a second Railway service from the same GitHub repository.

1. In the Railway project, create a new service from this same GitHub repository.
2. In that service's Settings, set the **Root Directory** to `/whatsapp`.
3. Set these Variables in the WhatsApp service:
   - `BOT_TOKEN`: same Telegram bot token used by the Telegram service, so incoming WhatsApp messages can be forwarded to the admin.
   - `ADMIN_ID`: the numeric Telegram user ID of the admin.
   - `WHATSAPP_SERVICE_TOKEN`: a long random secret. Use the exact same value in both Railway services. Do not post it in chats or commit it to GitHub.
   - `AUTH_DIR=/data/auth_info`
   - `PORT`: Railway normally provides this automatically; otherwise use `3000`.
4. Attach a Railway persistent volume to the WhatsApp service and set its mount path to `/data`. This is required to preserve the WhatsApp session across restarts and deployments.
5. Deploy. Confirm the service logs show it is listening and the `/health` route responds. Do not expose or share the contents of `/data/auth_info`.
6. In the WhatsApp service, generate a public domain using Railway Networking. Copy the base URL (for example, `https://your-service.up.railway.app`).
7. In the Telegram bot service, set:
   - `WHATSAPP_SERVICE_URL`: the WhatsApp service base URL, without a trailing slash.
   - `WHATSAPP_SERVICE_TOKEN`: exactly the same secret used by the WhatsApp service.
8. Redeploy the Telegram bot service.

## Pair and use WhatsApp

In the Telegram bot, as the admin:

- `/wa` — show WhatsApp commands
- `/wa_pair 93700123456` — request a pairing code. Enter the country code and digits only, without `+`, spaces, or dashes. In WhatsApp, open Settings → Linked devices → Link a device → Link with phone number, then enter the code if that option is available.
- `/wa_status` — check whether the session is connected
- `/wa_send 93700123456 Hello` — send a direct text message to a number that has agreed to receive it.

Incoming direct text messages are forwarded to the Telegram admin. Group messages are ignored. The service does not intentionally log message bodies or pairing codes.

## Security and reliability

- Never put real secrets, pairing codes, or WhatsApp session files in GitHub.
- Only the admin Telegram ID can request pairing codes or send messages through bot commands.
- Use messages only with recipients who expect them; honor requests to stop.
- Baileys is unofficial and can break when WhatsApp changes. Linking may result in account restrictions. Use the official WhatsApp Business Platform for business-critical messaging.
- The WhatsApp service requires Node.js 20 or later. It pins Baileys 6.7.18 to avoid the breaking API changes introduced in Baileys 7.
- Gmail API/OAuth integration remains a separate future task; this repository currently does not send email.


## Editable WhatsApp admin policy (initial configuration)

The Telegram admin can now configure these stored settings:
- `/wa_enable on` or `/wa_enable off` — set the user-facing WhatsApp feature flag.
- `/wa_template <text>` — save the default WhatsApp message template.
- `/wa_user_limit <number>` — save a per-user daily request limit from 1 to 20.
- `/wa_policy` — inspect the saved policy.

These commands store configuration in SQLite. They are an initial admin configuration layer; they do not yet implement multi-account sessions, public-user sending, per-user quota enforcement, or an interactive approval flow. The current Baileys service supports only one linked WhatsApp session. Do not treat the feature flag as an enforcement boundary until the public-user workflow and backend checks are implemented. Repeated automated block/unblock actions and bulk reporting are not provided. Only send messages to recipients who have agreed to receive them.
