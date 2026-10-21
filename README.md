# Gmail Campaign Bot

A Telegram bot starter for admin-managed campaign settings, with an optional separate WhatsApp Web session service.

## Current status

- The Telegram bot stores admin configuration in SQLite.
- Gmail sending is **not implemented**.
- The optional WhatsApp service uses the community-maintained, unofficial Baileys library. It now has isolated session folders and API support for multiple admin-owned accounts, pairing codes, direct text sending, and forwarding incoming direct text messages to the Telegram admin. This change has not yet been verified by a successful Railway deployment.
- WhatsApp linking is not an official WhatsApp Business API integration. Account restrictions are possible. Do not use it for unsolicited or bulk messages.

## Telegram bot features

- Public `/start` and phone-number registration
- Admin-only configuration commands: `/admin`, `/setsubject`, `/settemplate`, `/addrecipient`, `/delrecipient`, `/recipients`, `/setinterval`, `/setduration`, `/settings`
- WhatsApp admin commands: `/wa`, `/wa_pair work 93700123456`, `/wa_accounts`, `/wa_status`, `/wa_send work 93700123456 Hello`, `/wa_allow_user 123456789 work`, `/wa_deny_user 123456789 work`
- WhatsApp pairing and account listing are admin-only. Public users see only the send-message button; they enter a recipient number, preview the configured template, and must confirm before sending. The bot selects only connected accounts assigned to that Telegram user by the admin. Incoming direct text messages are forwarded to the admin's Telegram chat. Group messages are ignored.

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
- `/wa_pair work 93700123456` — create a session named `work` and request a pairing code. Use a different ID for each account (letters, digits, `_`, or `-`). Enter the country code and digits only, without `+`, spaces, or dashes. In WhatsApp, open Settings → Linked devices → Link a device → Link with phone number, then enter the code.
- `/wa_accounts` — list account IDs and connection status (admin only)
- `/wa_status` — show statuses for all accounts, or `/wa_status work` for one account
- `/wa_allow_user 123456789 work` — allow a Telegram user ID to use the `work` account
- `/wa_deny_user 123456789 work` — remove that permission
- `/wa_send work 93700123456 Hello` — admin-only test send to a recipient who has agreed to receive the message.

Public users only see the send button, enter a recipient number, preview the admin-configured message, and confirm. The service round-robins among connected accounts assigned to that user. Users do not see the account IDs or connection list.

Incoming direct text messages are forwarded to the Telegram admin. Group messages are ignored. The service does not intentionally log message bodies or pairing codes.

## Security and reliability

- Never put real secrets, pairing codes, or WhatsApp session files in GitHub.
- Only the admin Telegram ID can request pairing codes, inspect account status, grant/revoke account permissions, or use admin send commands. Public users cannot list connected accounts and can only use accounts explicitly assigned to their Telegram ID.
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

These commands store configuration in SQLite. The latest repository revision adds the initial public send flow, daily quota tracking, multi-account API support, and per-user account permissions. These changes still require syntax checks and a successful Railway deployment before they should be considered working. Repeated automated block/unblock actions and bulk reporting are not provided. Only send messages to recipients who have agreed to receive them.
