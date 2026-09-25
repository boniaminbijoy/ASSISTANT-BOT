# Assistant Bot V25

V25 fixes and upgrades based on V24.

## Bug fixes
- `/support` now sends to admins from both the database `admins` table and `ADMIN_IDS` as a safety net.
- Support messages are stored in a local `support_messages` inbox table with delivery status.
- `/support` instruction follows the user's saved language (English/Bangla).
- If no admin can receive the message, the user gets a clear notice that an admin must first open the bot with `/start`.

## New admin tools
- `/status` — admin-only live registry of available commands and current button/feature states.
- `🧪 System Status` button in the Admin Panel.
- Feature Settings now use direct ON/OFF buttons and show the current state on each button.
- Existing V24 admin-only `🛡️ Admin Commands` button is preserved.

## Existing features preserved
- TikTok Downloader
- QR Scanner / Generator
- User Profile / Statistics / History
- Advanced Admin Panel
- Scheduled Broadcast
- User → Admin Support + `/reply`
- Error Monitoring
- Maintenance mode
- Feature toggles
- Automatic Telegram command menu setup

## Deployment
Deploy this ZIP to Render as the replacement for V24, then redeploy/restart the service.
