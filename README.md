# 🤖 Assistant Bot — V43

A Telegram all-in-one utility bot with downloader, QR tools, Image Tools, Video → Audio extraction, user account features, admin controls, support, broadcasts, scheduled tasks, monitoring, and recovery helpers.

## ✨ V43 — Video Link → Audio Upgrade

### 🖼️ Image Tools fixes
- After an Image Tool operation finishes, the selected operation stays active.
- Users can send another image immediately without reopening Image Tools.
- Resize target is preserved for repeated batch resizing.
- Temporary progress messages are deleted after a successful result instead of remaining in the chat.
- Unrelated video/document messages are no longer incorrectly routed through QR/Image Tools.
- QR mode is not triggered by Image Tools media accidentally.

### 🎵 Video → Audio improvements
- Added real FFmpeg extraction progress updates while the audio is being processed.
- Progress now moves through download → read → extraction → finalization instead of staying at a single extraction percentage.
- Uses `ffprobe` to estimate video duration and calculate extraction progress when available.
- Explicitly selects the first audio stream and gives a clear error when no readable audio track exists.
- Temporary processing files are cleaned up safely after success or failure.

### 🎵 Video → Audio
- New Home menu: **🎵 Video To Audio**
- Command: `/audioextract`
- Send a video file **or paste a video link** and the bot extracts its audio as **MP3**.
- Link mode uses yt-dlp site extractors plus its generic extractor for broad public-web coverage, then FFmpeg converts the audio to MP3.
- Supports common video containers such as MP4, MKV, MOV, WebM, AVI, M4V, 3GP, FLV, TS and MTS.
- The extracted MP3 is returned as Telegram audio with a clean `.mp3` filename.
- Users can send another video immediately after extraction.
- If a video has no readable audio stream, the bot reports the error instead of failing silently.

## 🧪 V41 stability audit
- Re-checked Python syntax with `python -m py_compile`.
- Re-checked Image Tools routing so image/video documents are handled only by the active tool mode.
- Image Tools keep the selected operation active after a successful result and remove temporary progress UI.
- Video → Audio keeps its mode active for consecutive videos.
- Polling, error handling, and recovery code remain compatible with the current `python-telegram-bot` application lifecycle.


### 📢 Monetization model
- Premium/subscription/quota monetization is intentionally **not included**.
- The bot is designed to remain free for all users.
- Monetization is through Telegram's official sponsored ads shown in chats with eligible bots.
- The bot does **not** create fake ads, custom ad-gates, or claim that a user watched an ad before allowing a tool to run.
- Official sponsored ads are displayed and tracked by Telegram clients; the Bot API does not provide a supported way for this bot to force an ad before a specific tool or verify that a user watched one.
- Telegram states that bot owners can receive 50% of revenue from ads displayed in their bots, subject to Telegram's eligibility and monetization rules.

### 💡 Important
There is no bot-side implementation required to make Telegram's official bot ad bar appear. After the bot is eligible for Telegram Ads revenue sharing, Telegram handles the sponsored-message delivery/display in supported clients.

## 🧰 Features

- 📥 TikTok Downloader
- 📷 QR Code Scanner
- 🔲 QR Code Generator
- 🖼️ Image Tools
  - Compress
  - Resize
  - JPG
  - PNG
  - WebP
  - Crop Square
- 🎵 Video → Audio (MP3)
- 🤖 More Bots
- 👤 Profile / Statistics / History
- 🆘 User → Admin Support
- 📢 Broadcast
- 📅 Scheduled Broadcast
- 👑 Admin Panel
- 🛡️ User management
- 📊 Dashboard / reports
- 🔧 Maintenance controls
- ❤️ Health / recovery monitoring

## 🎵 Video → Audio Usage

1. Open **🎵 Video To Audio** from Home, or use `/audioextract`.
2. Send a video.
3. Wait for the extraction progress.
4. The bot returns the extracted audio as an MP3.
5. Send another video immediately if needed.

### FFmpeg
The Docker image installs FFmpeg automatically, so no manual FFmpeg installation is required when deploying with the included `Dockerfile`.

## 🖼️ Image Tools Usage

1. Open **🖼️ Image Tools**.
2. Select an operation.
3. Send an image.
4. After the result arrives, the same operation remains active for the next image.
5. Use the Image Tools menu or Home button to switch/exit.

For Resize:
- First send a size such as `1280x720`.
- The target size remains active so multiple images can be resized consecutively.

## 👤 User Commands

- `/start` — Home
- `/help` — Help
- `/profile` — Profile
- `/mystats` — Statistics
- `/history` — History
- `/morebots` — More Bots
- `/imagetools` — Image Tools
- `/audioextract` — Video → Audio
- `/support` — Contact Admin
- `/myid` — Show Telegram user ID

## 👑 Admin Commands

- `/admin` — Admin Panel
- `/status` — System status
- `/stats` — Statistics
- `/users` — User management
- `/searchuser` — Search users
- `/user` — User details
- `/block` / `/unblock` — User control
- `/broadcast` — Text broadcast
- `/broadcast_media` — Media broadcast
- `/broadcast_button` — Button broadcast
- `/schedule` — Scheduled broadcast
- `/scheduled` — Scheduled list
- `/cancelschedule` — Cancel schedule
- `/reports` — Broadcast reports
- `/settings` — Admin settings
- `/addbot` — Add a More Bot
- `/bots` — Manage More Bots
- `/maintenance` — Maintenance mode
- `/restart` — Restart/recovery helper

## ⚙️ Environment Variables

```text
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789,987654321
```

Optional:

```text
BOT_DB_PATH=bot_data.db
```

## 🚀 Render Deployment

1. Push the project to GitHub.
2. Create/connect the Render service.
3. Set `BOT_TOKEN` in Render Environment Variables.
4. Set `ADMIN_IDS` with comma-separated Telegram numeric IDs.
5. Deploy.
6. Confirm `/start`, Image Tools and `/audioextract` work.

The included Dockerfile installs FFmpeg and the required system packages.

## 🧪 V43 Test Checklist

### Image Tools
- [ ] Compress image
- [ ] Resize image
- [ ] Send a second image without reopening Image Tools
- [ ] JPG → PNG produces a `.png` file
- [ ] PNG → JPG produces a `.jpg` file
- [ ] WebP conversion produces `.webp`
- [ ] Progress message disappears after successful processing
- [ ] Image Tools does not ask to select QR Scanner
- [ ] QR Scanner still works when explicitly selected

### Video → Audio link coverage
- Uses the current yt-dlp extractor library rather than a hard-coded platform list. yt-dlp includes many site-specific extractors and also a generic extractor for additional pages/embeds.
- A public URL is required. Private/login-only videos, DRM-protected streams, CAPTCHA challenges, geo-restricted content, or a site that has recently changed can still be unavailable.
- The bot does not bypass DRM, authentication, or access controls.

### Video → Audio
- [ ] Open from Home
- [ ] `/audioextract` works with an uploaded video
- [ ] `/audioextract` works with a supported public video URL
- [ ] MP4 with audio → MP3
- [ ] Send a second video immediately after the first
- [ ] Video without audio shows a useful error
- [ ] Unsupported document does not trigger audio extraction

### Admin / Core
- [ ] `/admin`
- [ ] `/status`
- [ ] Broadcast
- [ ] Scheduled broadcast
- [ ] More Bots
- [ ] Support
- [ ] Health endpoint / recovery

## 📝 Version History

### V43
- Upgraded Video → Audio to accept supported public video links in addition to uploaded video files.
- Added yt-dlp link extraction with retries and FFmpeg MP3 conversion.
- Added a Telegram 50 MB output-size guard for MP3 uploads.

### V42
- Removed any planned Premium/quota concept from the monetization design.
- Added Telegram Official Sponsored Ads monetization documentation.
- Confirmed the bot does not implement fake/custom ad-gates or ad-watch verification.

### V41
- Added live FFmpeg/FFprobe extraction progress for Video → Audio.
- Added safer first-audio-stream mapping and temporary-file cleanup.
- Re-audited Image Tools routing, repeated processing, polling/error handling, and syntax.
- Synchronized README with the V41 code.

### V40
- Fixed Image Tools session reset after successful processing.
- Removed completed Image Tools progress messages.
- Improved repeated-image workflow.
- Added Video → Audio MP3 extractor using FFmpeg.
- Added Video → Audio link support using yt-dlp + FFmpeg.
- Added `/audioextract`.
- Added Home menu button for Video → Audio.
- Updated command menu and documentation.

### V39
- Reduced noisy polling Conflict notifications.
- Improved Render restart/retry behavior.
- Preserved previous Image Tools and filename fixes.

### V38
- Fixed Image Tools routing issues.
- Fixed converted image filename extensions.
- Updated README.

### V37
- Added Image Tools processing stages.
- Moved Pillow processing off the Telegram event loop.
- Improved image format handling.
- Fixed Resize input flow.

### V35
- Added runtime heartbeat, watchdog, health endpoints and recovery helpers.

### V31–V34
- Added and refined More Bots.
- Redesigned Home UI.
- Restored Admin UI controls.

## ⚠️ Notes

- FFmpeg is required for Video → Audio and is installed by the included Dockerfile.
- Large videos can take longer to download and process.
- Telegram file-size and Bot API limits still apply.
- Only one polling instance should use the same bot token at a time.
- Render Free service sleep/restart behavior is separate from application-level recovery logic.


### Video Link → Audio coverage
- YouTube, TikTok and Instagram receive dedicated yt-dlp retry/format handling.
- Other public video hosts use yt-dlp site-specific extractors and a generic extractor fallback.
- Private/login-only, DRM-protected, CAPTCHA/geo-blocked content or sites that recently changed can still fail; the bot does not bypass access controls.

### V46 — Security Hardening
- Added per-user sliding-window anti-spam/rate limiting.
- Added per-user and global concurrent-job limits to reduce resource exhaustion.
- Added maximum text/URL length controls.
- Added public URL validation that rejects embedded credentials and obvious private/local/reserved network targets before yt-dlp processing.
- Added Telegram media size guard before and after download for Video → Audio.
- Kept temporary-file cleanup in `finally` blocks.
- Security controls are configurable with environment variables:
  - `SECURITY_RATE_WINDOW` (default 60 seconds)
  - `SECURITY_RATE_MAX` (default 20 requests/window)
  - `SECURITY_MAX_URL_LENGTH` (default 2048)
  - `SECURITY_MAX_INPUT_CHARS` (default 10000)
  - `SECURITY_MAX_UPLOAD_MB` (default 100)
  - `SECURITY_MAX_AUDIO_MB` (default 50)
  - `SECURITY_MAX_CONCURRENT_JOBS` (default 2/user)
  - `SECURITY_GLOBAL_JOB_LIMIT` (default 6)
- These controls are defensive limits; they do not bypass Telegram, platform authentication, DRM, CAPTCHA, or access controls.
