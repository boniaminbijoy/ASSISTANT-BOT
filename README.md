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
