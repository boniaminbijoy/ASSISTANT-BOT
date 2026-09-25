# ASSISTANT BOT — Phase 3 Professional

Features:
- TikTok downloader
- QR scanner
- QR generator
- SQLite user database and usage statistics
- Admin panel (`/admin`)
- `/stats` command
- `/broadcast Your message` broadcast

## Render Admin Setup
Add environment variable `ADMIN_IDS` with your Telegram numeric user ID. Example: `123456789`.

The user database is stored in `bot_data.db`. For persistent storage across Render restarts, use a persistent disk or migrate the database to PostgreSQL in a later production-hardening step.

## Admin commands
- `/admin` — admin panel
- `/stats` — statistics
- `/broadcast Your message` — broadcast to registered users


## V14 additions
- `/myid` (and `/id`) shows your Telegram numeric User ID for configuring `ADMIN_IDS`.
- Unauthorized `/admin` now also shows your current Telegram ID to make admin setup easier.
- `ADMIN_IDS` accepts comma-separated numeric IDs (semicolon separators are also accepted).


## V15 Professional UI
- Redesigned /start main menu
- Inline downloader selection
- Settings menu
- Persistent Bengali/English language preference
- Personal statistics menu
- Help menu
- Home/back-to-home navigation
- Legacy button labels remain supported
