# 🤖 Assistant Bot — V37

> **All-in-One Telegram Utility Bot** with downloader, QR tools, image tools, user accounts, support, admin controls, broadcasts, More Bots, monitoring, and Render deployment support.

![Version](https://img.shields.io/badge/version-V37-blue)
![Platform](https://img.shields.io/badge/platform-Telegram-2CA5E0)
![Python](https://img.shields.io/badge/Python-3.13+-3776AB)
![Deployment](https://img.shields.io/badge/deployment-Render-46E3B7)

---

## ✨ V37 Highlights

V37 focuses on a more complete utility-bot experience and includes the latest **Image Tools fixes** plus runtime recovery support.

### 🖼️ Image Tools

Available from **Image Tools** or `/imagetools`:

- 🗜️ **Compress Image**
- 📐 **Resize Image** with exact dimensions such as `1280x720`
- 🔄 **Convert to JPG**
- 🟦 **Convert to PNG**
- 🌐 **Convert to WebP**
- ✂️ **Crop to Square**
- ⏳ Processing status/progress messages during image processing
- 🧵 Pillow processing runs outside the Telegram event loop so the bot can remain responsive
- 🪄 Better handling of transparent images and PNG/WebP alpha channels
- 📎 Supports image uploads sent as Telegram photos or supported image documents

### 🛠️ Image Tools V37 fixes

- Fixed the resize flow so the bot correctly waits for the requested dimensions.
- Resize uses the exact dimensions entered by the user.
- Added staged processing updates: starting → downloading → reading → processing → optimizing → preparing → completed.
- Improved handling of transparent images.
- Added stronger validation and clearer error messages.
- Temporary files are cleaned up after processing.

---

## 🚀 Main User Features

### 🎵 TikTok Downloader

- Download supported TikTok links.
- Quality selection where available.
- Download activity is stored in user history.
- Downloader can be enabled/disabled by an administrator.

### 📷 QR Scanner

- Send a QR image to scan it.
- Scan history is stored for the user.
- QR Scanner can be enabled/disabled by an administrator.

### 🔲 QR Generator

- Generate QR codes from text or links.
- Generated QR activity is stored in history.
- QR Generator can be enabled/disabled by an administrator.

### 👤 User Account

Users can access:

- Profile
- Statistics
- Download History
- QR Scan History
- QR Generate History
- Activity History
- Language/settings

### 🤖 More Bots

Admins can add other Telegram bots to the More Bots directory.

Admin format:

```text
/addbot @BotUsername | Bot Name | Description
```

Users can:

- View the bots added by admins
- Open a bot from the directory
- Return to the More Bots list or Home

Management commands:

```text
/morebots
/addbot @BotUsername | Bot Name | Description
/bots
```

Only administrators can manage the More Bots list.

### 🆘 User → Admin Support

Users can contact administrators through `/support`.

Support messages are stored locally with delivery status, and admins can reply to users with:

```text
/reply <user_id> <message>
```

The support flow also protects against normal menu/button text accidentally being forwarded as a support message.

### ⚙️ Settings & Languages

- বাংলা / English language selection
- User preferences are stored locally
- Home, Help, Settings, Profile, Statistics and History are available through the UI

---

# 👑 Admin Panel

Open the admin panel with:

```text
/admin
```

The panel includes:

### 📊 Dashboard & User Management

- Dashboard statistics
- User list
- User details
- User search
- User count
- Active users
- New users
- Top users
- Block / unblock
- Ban / unban
- Admin list
- Add admin / delete admin

### 📢 Broadcast System

#### Text broadcast

```text
/broadcast <text>
```

#### Media broadcast

Reply to a photo, video or document and use:

```text
/broadcast_media
```

#### URL-button broadcast

```text
/broadcast_button Button Text | https://example.com
```

Broadcast reports include target, sent, failed and blocked counts.

### ⏰ Scheduled Broadcast

Create a scheduled broadcast with:

```text
/schedule <date/time> | <message>
```

Manage schedules with:

```text
/scheduled
/cancelschedule <id>
```

Scheduled jobs use the bot's job queue.

### 📣 Announcement

```text
/announce <message>
```

### 🔧 Maintenance & Feature Controls

Admins can control:

- Maintenance mode
- TikTok Downloader
- QR Scanner
- QR Generator

The Admin Panel shows the current ON/OFF state directly on the buttons.

### 🧪 System Status

```text
/status
```

`/status` is intentionally **admin-only**. Normal users do not receive a status response.

The status page provides a live registry of available commands and feature/button states.

### 📈 Reports

```text
/reports
```

Shows recent broadcast reports.

### 🔄 Runtime Controls

```text
/ping
/restart
```

V37 also contains runtime heartbeat/watchdog logic intended to detect failures and attempt recovery.

---

# 📋 Command Reference

## 👤 User Commands

| Command | Purpose |
|---|---|
| `/start` | Open the main menu |
| `/help` | User help |
| `/profile` | View profile |
| `/mystats` | View personal statistics |
| `/history` | View history menu |
| `/settings` | Open settings |
| `/support` | Contact support/admin |
| `/about` | About the bot |
| `/id` | Show Telegram user ID |
| `/myid` | Show Telegram user ID |
| `/morebots` | Open More Bots |
| `/imagetools` | Open Image Tools |

## 👑 Admin Commands

| Command | Purpose |
|---|---|
| `/admin` | Open Admin Panel |
| `/stats` | Admin statistics |
| `/users` | User list |
| `/user` | User details |
| `/searchuser` | Search users |
| `/usercount` | User count |
| `/activeusers` | Active-user statistics |
| `/newuser` | New-user statistics |
| `/topuser` | Top-user statistics |
| `/block` | Block a user |
| `/unblock` | Unblock a user |
| `/ban` | Ban a user |
| `/unban` | Unban a user |
| `/adminlist` | Admin list |
| `/addadmin` | Add an admin |
| `/deladmin` | Remove an admin |
| `/broadcast` | Text broadcast |
| `/broadcast_media` | Media broadcast |
| `/broadcast_button` | URL-button broadcast |
| `/reports` | Broadcast reports |
| `/announce` | Announcement |
| `/schedule` | Schedule broadcast |
| `/scheduled` | List scheduled broadcasts |
| `/cancelschedule` | Cancel a scheduled broadcast |
| `/reply` | Reply to a user |
| `/maintenance` | Maintenance mode |
| `/status` | Live command/feature status |
| `/ping` | Bot ping |
| `/restart` | Restart/recovery command |
| `/addbot` | Add a bot to More Bots |
| `/bots` | Manage More Bots |
| `/helpadmin` | Admin help |

---

# 🗃️ Data & Storage

The bot uses a local SQLite database for operational data, including areas such as:

- Users
- Download history
- QR scan history
- QR generation history
- Broadcast reports
- Scheduled broadcasts
- Support messages
- Error/activity records
- Admin and feature settings
- More Bots entries

The exact database schema is created/updated by `bot.py` when the bot starts.

---

# 🛡️ Runtime Health & Recovery

V37 includes a lightweight runtime monitoring system:

- Runtime heartbeat
- Watchdog checks for event-loop problems
- Polling crash recovery attempts
- Error logging
- Health endpoints for Render

Health endpoints:

```text
/health
/healthz
```

> **Important:** application-level recovery cannot prevent every platform-level sleep/restart. Render plan behavior still applies to the deployed service.

---

# 📁 Project Structure

```text
assistant-bot/
├── bot.py
├── qr_scanner.py
├── qr_generator.py
├── tiktok_downloader.py
├── requirements.txt
├── Dockerfile
└── README.md
```

### File roles

- `bot.py` — main Telegram bot, database, UI, admin system, Image Tools, monitoring and handlers
- `qr_scanner.py` — QR decoding support
- `qr_generator.py` — QR generation support
- `tiktok_downloader.py` — TikTok download helper
- `requirements.txt` — Python dependencies
- `Dockerfile` — container build configuration
- `README.md` — project documentation and version notes

---

# 📦 Requirements

The current dependency set includes:

- Python 3.13+
- `python-telegram-bot[job-queue]`
- Pillow
- OpenCV Headless
- qrcode
- pyzbar
- yt-dlp
- curl-cffi
- FFmpeg
- ZBar (`libzbar0`)

The included Dockerfile installs the required system packages for the bot container.

---

# 🔐 Environment Configuration

The bot expects its Telegram token and admin configuration through environment variables used by the code.

Typical deployment values include:

```text
BOT_TOKEN=<your-telegram-bot-token>
ADMIN_IDS=<comma-separated-admin-user-ids>
```

> Keep tokens and private credentials in Render Environment Variables or another secret store. Do **not** commit real bot tokens to GitHub.

---

# 🚀 Render Deployment

This project includes a Dockerfile and can be deployed as a Render service.

### Basic deployment flow

1. Push the project files to GitHub.
2. Create/select the Render service.
3. Connect the GitHub repository.
4. Use the included `Dockerfile`.
5. Add the required environment variables.
6. Deploy.
7. Check the service logs.
8. Open the Telegram bot and run `/start`.

After an update:

1. Replace the project files with the new version.
2. Commit and push to GitHub.
3. Let Render deploy the new commit, or trigger a manual deploy.
4. Check logs for startup errors.
5. Test `/start`, the Admin Panel and the newly changed feature.

---

# 🧪 Recommended Post-Update Test

After every bot update, test these core areas:

- `/start`
- Home menu buttons
- `/help`
- TikTok Downloader
- QR Scanner
- QR Generator
- `/profile`
- `/mystats`
- `/history`
- `/support`
- `/morebots`
- `/imagetools`
- Admin Panel
- `/status` as admin and non-admin
- Broadcast system if used
- Render logs / health endpoints

For Image Tools, test at least:

1. Compress
2. Resize with `1280x720`
3. JPG conversion
4. PNG conversion
5. WebP conversion
6. Crop Square
7. A transparent PNG if available

---

# 📝 Version History

## V37 — Image Tools Fixes + Processing Updates

### Added / improved

- Image Tools menu
- Compress
- Exact-dimension Resize
- JPG / PNG / WebP conversion
- Square Crop
- Processing progress stages
- Background-thread image processing using `asyncio.to_thread()`
- Better transparency handling
- Better image validation
- Runtime heartbeat/watchdog and health endpoints retained

### Fixed

- Resize mode not correctly entering the size-input flow
- Image processing blocking the Telegram event loop
- Several transparency/output-format edge cases
- Image-processing error handling and cleanup

## V36 — Image Tools

- Initial Image Tools implementation
- Image compression
- Resize
- Format conversion
- Crop Square
- Image Tools UI

## V35 — Auto Recovery / Health Monitor

- Runtime heartbeat
- Watchdog/recovery logic
- Polling crash recovery attempt
- `/health` and `/healthz`
- Render health support

## V34 — Home UI + Admin Button Fix

- Redesigned Home UI
- Styled section headings
- Admin button visibility fixed
- `/admin` refresh support

## V33 — Redesigned Home UI

- New Home layout
- More structured utility sections
- Updated visual style

## V32 — More Bots UI Fixes

- More Bots navigation improvements
- Back/Home controls

## V31 — More Bots System

- Admin-managed bot directory
- User-facing More Bots menu
- Add/remove bot management

## V30 — AI Label Remover Removed

- AI Label Remover feature removed from the bot.

## V29 and earlier

Previous releases introduced and refined the Admin Panel, user statistics/history, broadcasts, support, feature toggles, command menu, QR tools, downloader and other core bot functions.

---

# ⚠️ Notes

- Telegram, TikTok and other third-party services can change their behavior independently of this project.
- Downloader compatibility may require dependency updates when external platforms change.
- Do not expose your bot token or admin credentials.
- Keep regular backups of the SQLite database if the bot's stored history/data is important.

---

# 📌 Current Version

**Assistant Bot V37**

README last refreshed with the complete V37 feature set and deployment/documentation details.

---

## 📄 License

No separate license file is included in this package unless added by the project owner. Add an appropriate `LICENSE` file before distributing the project publicly if licensing terms are required.
