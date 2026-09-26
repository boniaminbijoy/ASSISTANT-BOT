# 🤖 Assistant Telegram Utility Bot — V39

A multi-purpose Telegram utility bot with downloader, QR tools, Image Tools, More Bots, user profile/statistics, support, admin controls, broadcasts, scheduled broadcasts, monitoring and Render recovery support.

## ✨ V39 — Conflict-Safe Deployment Update

### 🛠️ Fixed: Telegram `Conflict` error during deployment

You may see this Telegram error when Render is replacing an old bot process with a new one:

```text
Conflict: terminated by other getUpdates request; make sure that only one bot instance is running
```

Telegram permits only one active `getUpdates` polling consumer for a bot token. During a restart/deploy, the old and new Render processes can briefly overlap.

V39 changes:
- `Conflict` is treated as a transient polling condition instead of a normal user-facing bot error.
- Admins are no longer spammed with `🚨 BOT ERROR` for this specific condition.
- The conflict is still recorded in the bot error log for diagnostics.
- If the polling layer exits with `Conflict`, the bot waits before retrying instead of immediately forcing another process restart.
- Other genuine errors continue to use the existing admin error-monitoring system.

> **Important:** This code reduces false alarms and restart loops. If two separate services are permanently running the same bot token, only one should be kept active. Never run the same polling bot on two independent always-on services.

## 🖼️ Image Tools

Available operations:
- Compress
- Resize
- JPG
- PNG
- WebP
- Crop Square

Image Tools also includes progress/status messaging and background processing so Pillow work does not block the Telegram event loop.

### V38 fixes retained in V39
- Image Tools no longer incorrectly routes images to QR Scanner when Image Tools is active.
- JPG → PNG, PNG → JPG and other conversions use the correct output extension.
- Image documents with unusual/missing MIME types are handled more safely.
- Resize target input is validated before processing.

## 🤖 More Bots

Admins can add managed bots with:

```text
/addbot @BotUsername | Bot Name | Description
```

Manage bots with:

```text
/bots
```

Users can view the configured bots and open them from the More Bots interface.

## 👑 Admin Features

- Admin Panel
- Statistics and user management
- User search/details
- Block / unblock
- Broadcast
- Broadcast reports
- Scheduled broadcast
- Support/reply system
- Maintenance mode
- Downloader/QR feature controls
- System status
- More Bots management
- Error monitoring
- Runtime health/recovery
- Restart controls

## 👤 User Features

- TikTok Downloader
- QR Scanner
- QR Generator
- Image Tools
- More Bots
- Profile
- Statistics
- History/activity
- Settings
- Support

## 📋 Main Commands

### User
```text
/start
/help
/profile
/mystats
/history
/settings
/support
/morebots
/imagetools
```

### Admin
```text
/admin
/status
/stats
/users
/user <id>
/searchuser <id or query>
/block <id>
/unblock <id>
/broadcast
/reports
/schedule
/scheduled
/cancelschedule <id>
/reply <user_id> <message>
/maintenance on|off
/announce <message>
/addbot @BotUsername | Bot Name | Description
/bots
/restart
```

## 🚀 Render Deployment

1. Push the project to GitHub.
2. Deploy it as a Render Web Service.
3. Add environment variables:

```text
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789
```

Optional:

```text
BOT_DB_PATH=bot_data.db
```

4. Use the repository's Dockerfile or configured Python start command.
5. After deployment, send `/start` to the bot.
6. Test Image Tools, QR tools, More Bots and admin commands.

### ⚠️ One-instance rule

Do not run the same bot token with polling in two separate services at the same time. If another VPS, local machine, Render service, or duplicate deployment is still running the same bot, Telegram can terminate one polling connection with a `Conflict` error.

## 🧪 V39 Test Checklist

After deployment:

- [ ] `/start` opens the Home menu
- [ ] Admin Panel is visible only to admins
- [ ] Image Tools → PNG conversion produces `.png`
- [ ] Image Tools does not incorrectly ask for QR Scanner
- [ ] QR Scanner still works normally
- [ ] QR Generator still works normally
- [ ] More Bots works
- [ ] Support messages reach admin
- [ ] `/status` remains admin-only
- [ ] Deploy/restart does not send a false `BOT ERROR` Conflict alert
- [ ] Genuine application errors still notify admins

## 📝 Version History

### V39
- Fixed/suppressed false admin alerts for Telegram polling `Conflict` during deployment/restart overlap.
- Added safer retry behavior for polling Conflict.
- Preserved V38 Image Tools routing and output-extension fixes.
- Updated README with deployment troubleshooting and test checklist.

### V38
- Fixed Image Tools → QR Scanner routing issue.
- Fixed output filename extensions for image conversion.
- Improved Image Tools document handling.
- Updated README.

### V37
- Added Image Tools processing stages.
- Moved Pillow CPU-bound work to a background thread.
- Fixed resize flow.
- Improved transparency handling.
- Improved image validation and error handling.

### V35
- Added runtime heartbeat.
- Added watchdog/recovery logic.
- Added health endpoints for Render.

### Earlier Versions
- Admin panel and statistics
- Broadcast and scheduled broadcast
- Support system
- More Bots
- QR Scanner / Generator
- TikTok Downloader
- User profiles/history/settings

## 📁 Project Structure

```text
bot.py
qr_scanner.py
qr_generator.py
tiktok_downloader.py
requirements.txt
Dockerfile
README.md
```

## ⚠️ Important Notes

- Keep `BOT_TOKEN` private.
- Keep `ADMIN_IDS` restricted to trusted Telegram user IDs.
- Use only one active polling instance per bot token.
- Render restarts/deploys can briefly overlap processes; V39 handles the resulting transient Conflict more quietly.
- Runtime recovery helps with unexpected process/event-loop failures but cannot make a free Render service permanently awake.
