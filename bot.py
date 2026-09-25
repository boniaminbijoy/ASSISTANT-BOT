import os
import asyncio
import html
import tempfile
import uuid
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import (
    Update,
    ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters, CallbackQueryHandler
)

from qr_scanner import scan_qr
from qr_generator import generate_qr

from tiktok_downloader import (
    get_video_info,
    get_quality_formats,
    download_video
)


# ==================================================
# BOT TOKEN
# ==================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing. Add BOT_TOKEN in Render Environment Variables.")

# ==================================================
# PHASE 3 - USER DATABASE / ADMIN CONFIG
# ==================================================
DB_PATH = os.environ.get("BOT_DB_PATH", "bot_data.db")
ADMIN_IDS = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",") if x.strip().isdigit()}


async def myid_command(update, context):
    """Show the Telegram numeric user ID needed for ADMIN_IDS."""
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        f"🆔 Your Telegram User ID is:\n\n`{user.id}`\n\n"
        "Use this number in Render → Environment → ADMIN_IDS, then redeploy the bot.",
        parse_mode="Markdown"
    )

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_database():
    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, messages INTEGER DEFAULT 0,
            qr_scans INTEGER DEFAULT 0, qr_generated INTEGER DEFAULT 0,
            tiktok_downloads INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "language" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'")
        conn.execute("""CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            platform TEXT NOT NULL, url TEXT NOT NULL, quality TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'success', created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS qr_scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS qr_generate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            action TEXT NOT NULL, details TEXT DEFAULT '', created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_download_history_user ON download_history(user_id, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qr_scan_history_user ON qr_scan_history(user_id, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qr_generate_history_user ON qr_generate_history(user_id, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_activity_user ON user_activity(user_id, id DESC)")
        conn.commit()

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def register_user(user, count_message=False):
    if not user:
        return
    now = utc_now()
    with db_connect() as conn:
        conn.execute("""INSERT INTO users(user_id, username, first_name, joined_at, last_seen, messages)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            first_name=excluded.first_name, last_seen=excluded.last_seen,
            messages=users.messages + excluded.messages""",
            (user.id, user.username or "", user.first_name or "", now, now, 1 if count_message else 0))
        conn.commit()

def increment_stat(user_id, field):
    if field not in {"qr_scans", "qr_generated", "tiktok_downloads"}:
        return
    with db_connect() as conn:
        conn.execute(f"UPDATE users SET {field} = {field} + 1, last_seen = ? WHERE user_id = ?", (utc_now(), user_id))
        conn.commit()

def get_user_language(user_id):
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row and row[0] in {"bn", "en"} else "en"
    except Exception:
        return "en"

def set_user_language(user_id, language):
    if language not in {"bn", "en"}:
        return
    with db_connect() as conn:
        conn.execute("UPDATE users SET language = ? WHERE user_id = ?", (language, user_id))
        conn.commit()

def get_user_stats(user_id):
    with db_connect() as conn:
        return conn.execute("SELECT messages, qr_scans, qr_generated, tiktok_downloads FROM users WHERE user_id = ?", (user_id,)).fetchone() or (0, 0, 0, 0)

def log_activity(user_id, action, details=""):
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO user_activity(user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                         (user_id, action[:80], str(details)[:500], utc_now()))
            conn.commit()
    except Exception as exc:
        print("Activity log error:", repr(exc))

def add_download_history(user_id, url, quality="", status="success"):
    with db_connect() as conn:
        conn.execute("INSERT INTO download_history(user_id, platform, url, quality, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (user_id, "TikTok", url[:1000], str(quality)[:50], status[:30], utc_now()))
        conn.commit()

def add_qr_scan_history(user_id, content):
    with db_connect() as conn:
        conn.execute("INSERT INTO qr_scan_history(user_id, content, created_at) VALUES (?, ?, ?)",
                     (user_id, str(content)[:4000], utc_now()))
        conn.commit()

def add_qr_generate_history(user_id, content):
    with db_connect() as conn:
        conn.execute("INSERT INTO qr_generate_history(user_id, content, created_at) VALUES (?, ?, ?)",
                     (user_id, str(content)[:4000], utc_now()))
        conn.commit()

def get_user_profile(user_id):
    with db_connect() as conn:
        return conn.execute("SELECT user_id, username, first_name, joined_at, last_seen, language FROM users WHERE user_id = ?", (user_id,)).fetchone()

def get_history(user_id, kind, limit=10):
    tables = {
        "downloads": ("download_history", "platform, url, quality, status, created_at"),
        "scans": ("qr_scan_history", "content, created_at"),
        "generates": ("qr_generate_history", "content, created_at"),
        "activity": ("user_activity", "action, details, created_at"),
    }
    table, fields = tables[kind]
    with db_connect() as conn:
        return conn.execute(f"SELECT {fields} FROM {table} WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()

def format_dt(value):
    try:
        return value.replace("T", " ").replace("+00:00", " UTC")
    except Exception:
        return str(value)

def get_stats():
    with db_connect() as conn:
        row = conn.execute("""SELECT COUNT(*), SUM(CASE WHEN julianday(last_seen) >= julianday('now','-1 day') THEN 1 ELSE 0 END),
            COALESCE(SUM(messages),0), COALESCE(SUM(qr_scans),0), COALESCE(SUM(qr_generated),0),
            COALESCE(SUM(tiktok_downloads),0) FROM users""").fetchone()
    return row

def is_admin(user_id):
    return user_id in ADMIN_IDS

def admin_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
        InlineKeyboardButton("👥 Users", callback_data="admin_users")
    ], [InlineKeyboardButton("📢 Broadcast Help", callback_data="admin_broadcast")]])

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            f"⛔ You are not authorized to use the admin panel.\n\n"
            f"🆔 Your Telegram ID: `{update.effective_user.id}`\n\n"
            "Add this number to Render → Environment → ADMIN_IDS, then redeploy.",
            parse_mode="Markdown"
        )
        return
    await update.effective_message.reply_text(
        "🛠️ *ADMIN PANEL*\n\nChoose an option:", reply_markup=admin_keyboard(), parse_mode="Markdown")

async def stats_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    total, active24, messages, scans, generated, downloads = get_stats()
    await update.effective_message.reply_text(
        f"📊 *BOT STATISTICS*\n\n👥 Total users: *{total}*\n🟢 Active (24h): *{active24}*\n💬 Messages: *{messages}*\n📷 QR scans: *{scans}*\n🔳 QR generated: *{generated}*\n🎵 TikTok downloads: *{downloads}*",
        parse_mode="Markdown")

async def broadcast_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text("📢 Usage: /broadcast Your message here")
        return
    with db_connect() as conn:
        users = [r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent = failed = 0
    status = await update.effective_message.reply_text(f"📢 Broadcasting to {len(users)} users...\n`0%`", parse_mode="Markdown")
    for i, uid in enumerate(users, 1):
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception as exc:
            failed += 1
            if "blocked" in str(exc).lower() or "chat not found" in str(exc).lower():
                with db_connect() as conn:
                    conn.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (uid,))
                    conn.commit()
        if i == len(users) or i % max(1, len(users)//10) == 0:
            try:
                await status.edit_text(f"📢 Broadcasting...\n`{i*100//max(1,len(users))}%`", parse_mode="Markdown")
            except Exception:
                pass
    await status.edit_text(f"✅ *Broadcast complete*\n\n📨 Sent: *{sent}*\n⚠️ Failed: *{failed}*", parse_mode="Markdown")

async def admin_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Admin only.")
        return
    if query.data == "admin_stats":
        total, active24, messages, scans, generated, downloads = get_stats()
        await query.edit_message_text(f"📊 *BOT STATISTICS*\n\n👥 Total users: *{total}*\n🟢 Active (24h): *{active24}*\n💬 Messages: *{messages}*\n📷 QR scans: *{scans}*\n🔳 QR generated: *{generated}*\n🎵 TikTok downloads: *{downloads}*", parse_mode="Markdown", reply_markup=admin_keyboard())
    elif query.data == "admin_users":
        total, active24, *_ = get_stats()
        await query.edit_message_text(f"👥 *USER DATABASE*\n\nTotal registered users: *{total}*\nActive in last 24h: *{active24}*\n\nUse /broadcast <message> to send a broadcast.", parse_mode="Markdown", reply_markup=admin_keyboard())
    elif query.data == "admin_broadcast":
        await query.edit_message_text("📢 *BROADCAST*\n\nSend:\n`/broadcast Your message here`\n\nThe bot will send it to all registered users.", parse_mode="Markdown", reply_markup=admin_keyboard())

async def track_update(update, context):
    if update.effective_user:
        register_user(update.effective_user, count_message=bool(update.effective_message))
        try:
            if update.callback_query:
                action = "button_click"
                details = update.callback_query.data or ""
            elif update.message:
                action = "message"
                details = (update.message.text or "media")[:500]
            else:
                action = "update"
                details = type(update).__name__
            log_activity(update.effective_user.id, action, details)
        except Exception:
            pass


# ==================================================
# RENDER WEB SERVICE PORT
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ASSISTANT BOT is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"🌐 Render health server listening on port {port}...")
    server.serve_forever()



# ==================================================
# USER PROFILE / HISTORY / ACTIVITY
# ==================================================

def user_features_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 My Profile", callback_data="user_profile"),
         InlineKeyboardButton("📊 My Statistics", callback_data="user_stats")],
        [InlineKeyboardButton("📥 Download History", callback_data="hist_downloads")],
        [InlineKeyboardButton("📷 QR Scan History", callback_data="hist_scans"),
         InlineKeyboardButton("🔲 QR Generate History", callback_data="hist_generates")],
        [InlineKeyboardButton("🕘 Activity", callback_data="hist_activity")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

async def user_profile_command(update, context):
    user = update.effective_user
    row = get_user_profile(user.id)
    if not row:
        register_user(user)
        row = get_user_profile(user.id)
    uid, username, first_name, joined, last_seen, language = row
    log_activity(uid, "view_profile")
    username_text = f"@{html.escape(username)}" if username else "—"
    text = (
        "👤 <b>MY PROFILE</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"👤 <b>Name:</b> {html.escape(first_name or '—')}\n"
        f"🔗 <b>Username:</b> {username_text}\n"
        f"📅 <b>Joined:</b> {html.escape(format_dt(joined))}\n"
        f"🟢 <b>Last active:</b> {html.escape(format_dt(last_seen))}\n"
        f"🌐 <b>Language:</b> {'বাংলা' if language == 'bn' else 'English'}"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=user_features_keyboard())

async def user_stats_view(update, context):
    user = update.effective_user
    messages, scans, generated, downloads = get_user_stats(user.id)
    activity_count = get_history(user.id, "activity", 999999)
    log_activity(user.id, "view_stats")
    text = (
        "📊 <b>MY STATISTICS</b>\n\n"
        f"💬 Messages: <b>{messages}</b>\n"
        f"🎵 TikTok Downloads: <b>{downloads}</b>\n"
        f"📷 QR Scans: <b>{scans}</b>\n"
        f"🔲 QR Generated: <b>{generated}</b>\n"
        f"🕘 Activity Records: <b>{len(activity_count)}</b>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=user_features_keyboard())

def history_text(user_id, kind):
    rows = get_history(user_id, kind, 10)
    titles = {
        "downloads": "📥 DOWNLOAD HISTORY", "scans": "📷 QR SCAN HISTORY",
        "generates": "🔲 QR GENERATE HISTORY", "activity": "🕘 USER ACTIVITY"
    }
    if not rows:
        return f"<b>{titles[kind]}</b>\n\nNo records yet."
    lines = [f"<b>{titles[kind]}</b>\n"]
    for i, row in enumerate(rows, 1):
        if kind == "downloads":
            platform, url, quality, status, created = row
            lines.append(f"<b>{i}.</b> 🎵 {html.escape(platform)} | {html.escape(status)} | {html.escape(quality or 'auto')}\n🔗 <code>{html.escape(url)}</code>\n🕘 {html.escape(format_dt(created))}")
        elif kind in {"scans", "generates"}:
            content, created = row
            lines.append(f"<b>{i}.</b> <code>{html.escape(content[:800])}</code>\n🕘 {html.escape(format_dt(created))}")
        else:
            action, details, created = row
            lines.append(f"<b>{i}.</b> {html.escape(action)} — {html.escape(details[:300])}\n🕘 {html.escape(format_dt(created))}")
    lines.append("\nShowing the latest 10 records.")
    return "\n\n".join(lines)

async def history_view(update, context, kind):
    user = update.effective_user
    log_activity(user.id, f"view_{kind}_history")
    await update.effective_message.reply_text(history_text(user.id, kind), parse_mode="HTML", reply_markup=user_features_keyboard())

async def user_features_command(update, context):
    log_activity(update.effective_user.id, "open_user_features")
    await update.effective_message.reply_text("👤 <b>MY ACCOUNT</b>\n\nChoose what you want to view:", parse_mode="HTML", reply_markup=user_features_keyboard())

# ==================================================
# MAIN MENU
# ==================================================

def get_main_keyboard(language="en"):
    keyboard = [
        ["📥 Downloader", "📷 QR Scanner"],
        ["🔲 QR Generator", "📊 My Stats"],
        ["👤 My Profile", "🕘 My History"],
        ["⚙️ Settings", "❓ Help"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def downloader_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 TikTok Downloader", callback_data="ui_tiktok")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang_bn"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

def help_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="ui_home")]])

async def show_home(update, context):
    user = update.effective_user
    lang = get_user_language(user.id) if user else "en"
    reset_modes(context)
    text = ("🤖 *ASSISTANT BOT-এ স্বাগতম!* 🚀\n\nআপনার All-in-One Telegram Utility Bot।\n\n📥 Downloader\n📷 QR Scanner\n🔲 QR Generator\n📊 Personal Statistics\n⚙️ Settings\n❓ Help\n\nনিচের Menu থেকে একটি অপশন নির্বাচন করুন।") if lang == "bn" else ("🤖 *Welcome to ASSISTANT BOT!* 🚀\n\nYour All-in-One Telegram Utility Bot.\n\n📥 Downloader\n📷 QR Scanner\n🔲 QR Generator\n📊 Personal Statistics\n⚙️ Settings\n❓ Help\n\nChoose an option from the menu below.")
    await update.effective_message.reply_text(text, reply_markup=get_main_keyboard(lang), parse_mode="Markdown")

async def ui_callback(update, context):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if query.data == "ui_home":
        await show_home(update, context)
    elif query.data == "ui_tiktok":
        reset_modes(context)
        context.user_data["tiktok_mode"] = True
        await query.message.reply_text(
            "🎵 *TIKTOK DOWNLOADER*\n\n🔗 Send me a TikTok video link.\n\nI will check the available video qualities and let you choose one.",
            reply_markup=get_main_keyboard(get_user_language(user.id)),
            parse_mode="Markdown"
        )
    elif query.data == "user_profile":
        row = get_user_profile(user.id)
        if not row:
            register_user(user)
            row = get_user_profile(user.id)
        uid, username, first_name, joined, last_seen, language = row
        log_activity(user.id, "view_profile")
        username_text = f"@{html.escape(username)}" if username else "—"
        text = (f"👤 <b>MY PROFILE</b>\n\n🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
                f"👤 <b>Name:</b> {html.escape(first_name or '—')}\n"
                f"🔗 <b>Username:</b> {username_text}\n"
                f"📅 <b>Joined:</b> {html.escape(format_dt(joined))}\n"
                f"🟢 <b>Last active:</b> {html.escape(format_dt(last_seen))}")
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=user_features_keyboard())
    elif query.data == "user_stats":
        messages, scans, generated, downloads = get_user_stats(user.id)
        log_activity(user.id, "view_stats")
        await query.message.reply_text(f"📊 <b>MY STATISTICS</b>\n\n💬 Messages: <b>{messages}</b>\n🎵 TikTok Downloads: <b>{downloads}</b>\n📷 QR Scans: <b>{scans}</b>\n🔲 QR Generated: <b>{generated}</b>", parse_mode="HTML", reply_markup=user_features_keyboard())
    elif query.data.startswith("hist_"):
        kind = query.data.replace("hist_", "", 1)
        if kind in {"downloads", "scans", "generates", "activity"}:
            await query.message.reply_text(history_text(user.id, kind), parse_mode="HTML", reply_markup=user_features_keyboard())
            log_activity(user.id, f"view_{kind}_history")
    elif query.data in {"lang_bn", "lang_en"}:
        lang = "bn" if query.data == "lang_bn" else "en"
        set_user_language(user.id, lang)
        await query.message.reply_text("✅ ভাষা বাংলা করা হয়েছে।" if lang == "bn" else "✅ Language changed to English.", reply_markup=get_main_keyboard(lang))

async def settings_command(update, context):
    await update.effective_message.reply_text("⚙️ *Settings*\n\nChoose your language:", reply_markup=settings_keyboard(), parse_mode="Markdown")

async def help_command(update, context):
    await update.effective_message.reply_text("❓ *Help*\n\n📥 Downloader: choose a downloader and send a supported link.\n📷 QR Scanner: select it, then send a QR image.\n🔲 QR Generator: select it, then send text/link.\n⚙️ Settings: change language.", reply_markup=help_keyboard(), parse_mode="Markdown")

async def ui_text_action(update, context, text):
    user = update.effective_user
    lang = get_user_language(user.id)
    if text == "📥 Downloader":
        reset_modes(context)
        await update.message.reply_text("📥 *Downloader*\n\nChoose a downloader:", reply_markup=downloader_keyboard(), parse_mode="Markdown")
        return True
    if text == "📷 QR Scanner":
        await qr_scanner_start(update, context); return True
    if text == "🔲 QR Generator":
        await qr_generator_start(update, context); return True
    if text == "📊 My Stats":
        messages, scans, generated, downloads = get_user_stats(user.id)
        msg = f"📊 *My Statistics*\n\n💬 Messages: *{messages}*\n📷 QR Scans: *{scans}*\n🔲 QR Generated: *{generated}*\n🎵 TikTok Downloads: *{downloads}*"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang), parse_mode="Markdown"); return True
    if text == "👤 My Profile":
        await user_profile_command(update, context); return True
    if text == "🕘 My History":
        await user_features_command(update, context); return True
    if text == "⚙️ Settings":
        await settings_command(update, context); return True
    if text == "❓ Help":
        await help_command(update, context); return True
    return False

def make_progress_bar(percent: int, width: int = 10) -> str:
    percent = max(0, min(100, int(percent)))
    filled = min(width, percent * width // 100)
    return "█" * filled + "░" * (width - filled)

# ==================================================
# RESET ALL MODES
# ==================================================

def reset_modes(context):

    context.user_data["qr_mode"] = False

    context.user_data["qr_generator_mode"] = False
    context.user_data["qr_generator_data"] = None

    context.user_data["tiktok_mode"] = False
    context.user_data["tiktok_url"] = None
    context.user_data["tiktok_formats"] = None


# ==================================================
# START
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)


# QR CODE SCANNER BUTTON
# ==================================================

async def qr_scanner_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_modes(context)

    # Scanner ON
    context.user_data["qr_mode"] = True

    await update.message.reply_text(
        "📷 **QR CODE SCANNER**\n\n"
        "Please send me a photo containing a QR Code. 📸\n\n"
        "💡 Make sure the QR Code is clear and fully visible.\n\n"
        "I will scan it and show you the result.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ==================================================
# QR CODE IMAGE PROCESSING
# ==================================================

async def handle_qr_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.user_data.get("qr_mode", False):
        await update.message.reply_text(
            "ℹ️ **Please select 📷 QR CODE SCANNER first.**",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        return

    detecting_message = await update.message.reply_text(
        "📷 **Scanning QR Code...**\n\n"
        f"`{make_progress_bar(0)}` **0%**\n\n"
        "🔎 Preparing image...",
        parse_mode="Markdown"
    )

    progress_loop = asyncio.get_running_loop()
    progress_state = {"last": -1}

    def qr_progress(percent):
        percent = int(percent)
        if percent != 100 and percent - progress_state["last"] < 5:
            return
        progress_state["last"] = percent
        stage = "🔎 Scanning image..." if percent < 70 else "🧩 Checking QR patterns..."
        if percent >= 95:
            stage = "✅ Finalizing..."
        text = (
            "📷 **Scanning QR Code...**\n\n"
            f"`{make_progress_bar(percent)}` **{percent}%**\n\n"
            f"{stage}"
        )
        fut = asyncio.run_coroutine_threadsafe(
            detecting_message.edit_text(text, parse_mode="Markdown"),
            progress_loop
        )
        fut.add_done_callback(lambda f: None)

    user_id = update.message.from_user.id
    temp_dir = tempfile.mkdtemp(prefix=f"qr_scan_{user_id}_")
    image_path = os.path.join(temp_dir, f"scan_{uuid.uuid4().hex}.jpg")

    try:
        if update.message.photo:
            media = update.message.photo[-1]
        elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
            media = update.message.document
        else:
            await detecting_message.edit_text(
                "❌ **Please send an image containing a QR code.**",
                parse_mode="Markdown"
            )
            return

        file = await context.bot.get_file(media.file_id)
        await file.download_to_drive(image_path)

        # QR decoding is CPU-bound; keep the Telegram event loop responsive.
        results = await asyncio.to_thread(scan_qr, image_path, qr_progress)

        if results:
            increment_stat(update.effective_user.id, "qr_scans")
            lines = []
            for index, result in enumerate(results, 1):
                add_qr_scan_history(update.effective_user.id, result)
                safe = html.escape(result)
                lines.append(f"**{index}.** <code>{safe}</code>")

            await detecting_message.edit_text(
                "✅ <b>QR CODE DETECTED!</b>\n\n"
                + "\n\n".join(lines)
                + "\n\n📷 Send another QR image to scan again.",
                parse_mode="HTML"
            )
        else:
            await detecting_message.edit_text(
                "❌ <b>QR CODE NOT FOUND</b>\n\n"
                "I couldn't find a readable QR Code in this image.\n\n"
                "💡 Try a clearer, brighter image or send the image as a photo.",
                parse_mode="HTML"
            )

    except Exception as e:
        print("QR Scanner Error:", repr(e))
        try:
            await detecting_message.edit_text(
                "❌ <b>QR SCAN FAILED</b>\n\n"
                f"<code>{html.escape(type(e).__name__ + ': ' + str(e))}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
            os.rmdir(temp_dir)
        except OSError:
            pass


# ==================================================
# QR CODE GENERATOR BUTTON
# ==================================================

async def qr_generator_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_modes(context)

    # Generator ON
    context.user_data["qr_generator_mode"] = True

    context.user_data["qr_generator_data"] = None

    await update.message.reply_text(
        "🔳 **QR CODE GENERATOR**\n\n"
        "✍️ Send me any text or link.\n\n"
        "Example:\n"
        "`https://example.com`\n\n"
        "🤖 Your QR Code will automatically "
        "include the ASSISTANT BOT logo.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ==================================================
# DOWNLOAD BOT PROFILE PICTURE
# ==================================================

async def download_bot_profile_picture(
    context: ContextTypes.DEFAULT_TYPE,
    output_path: str
):

    # Get bot information
    bot_info = await context.bot.get_me()

    # Get bot profile photos
    photos = await context.bot.get_user_profile_photos(
        user_id=bot_info.id,
        limit=1
    )

    # Bot has no profile picture
    if photos.total_count == 0:

        return False

    # Get highest resolution photo
    photo = photos.photos[0][-1]

    # Get Telegram file
    file = await context.bot.get_file(
        photo.file_id
    )

    # Download profile picture
    await file.download_to_drive(
        output_path
    )

    return True


# ==================================================
# QR GENERATOR - TEXT / LINK
# ==================================================

async def handle_qr_generator_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    data = (update.message.text or "").strip()
    if not data:
        await update.message.reply_text(
            "❌ Please send some text or a link.",
            reply_markup=get_main_keyboard()
        )
        return

    generating_message = await update.message.reply_text(
        "🔳 **Generating QR Code...**\n\n"
        f"`{make_progress_bar(0)}` **0%**\n\n"
        "📝 Preparing data...",
        parse_mode="Markdown"
    )

    progress_loop = asyncio.get_running_loop()
    progress_state = {"last": -1}

    def qr_gen_progress(percent):
        percent = int(percent)
        if percent != 100 and percent - progress_state["last"] < 5:
            return
        progress_state["last"] = percent
        if percent < 45:
            stage = "📝 Preparing data..."
        elif percent < 70:
            stage = "🔳 Building QR pattern..."
        elif percent < 95:
            stage = "🤖 Adding bot logo..."
        else:
            stage = "💾 Saving QR image..."
        text = (
            "🔳 **Generating QR Code...**\n\n"
            f"`{make_progress_bar(percent)}` **{percent}%**\n\n"
            f"{stage}"
        )
        fut = asyncio.run_coroutine_threadsafe(
            generating_message.edit_text(text, parse_mode="Markdown"),
            progress_loop
        )
        fut.add_done_callback(lambda f: None)

    user_id = update.message.from_user.id
    temp_dir = tempfile.mkdtemp(prefix=f"qr_gen_{user_id}_")
    logo_path = os.path.join(temp_dir, "bot_logo.jpg")
    qr_path = os.path.join(temp_dir, "generated_qr.png")

    try:
        logo_found = await download_bot_profile_picture(context, logo_path)

        await asyncio.to_thread(
            generate_qr,
            data,
            qr_path,
            logo_path if logo_found else None,
            qr_gen_progress
        )

        await generating_message.delete()

        safe_data = html.escape(data)
        with open(qr_path, "rb") as qr_file:
            increment_stat(update.effective_user.id, "qr_generated")
            add_qr_generate_history(update.effective_user.id, data)
            await update.message.reply_photo(
                photo=qr_file,
                caption=(
                    "✅ <b>QR CODE GENERATED!</b>\n\n"
                    "🔗 <b>Data:</b>\n"
                    f"<code>{safe_data}</code>"
                ),
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )

    except Exception as e:
        print("QR Generator Error:", repr(e))
        try:
            await generating_message.edit_text(
                "❌ <b>QR GENERATION FAILED</b>\n\n"
                f"<code>{html.escape(type(e).__name__ + ': ' + str(e))}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    finally:
        for path in (logo_path, qr_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass
        context.user_data["qr_generator_data"] = None


# ==================================================
# QR GENERATOR - IMAGE HANDLER
# ==================================================

async def handle_qr_generator_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "ℹ️ **QR CODE GENERATOR**\n\n"
        "You only need to send text or a link.\n\n"
        "🤖 The bot will automatically add its own profile picture to the QR Code.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ==================================================
# TIKTOK DOWNLOADER BUTTON
# ==================================================

async def tiktok_downloader_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_modes(context)

    # TikTok mode ON
    context.user_data["tiktok_mode"] = True

    await update.message.reply_text(
        "🎵 **TIKTOK DOWNLOADER**\n\n"
        "🔗 Send me a TikTok video link.\n\n"
        "I will check the available video "
        "qualities and let you choose one.\n\n"
        "🚀 A watermark-free source will be "
        "preferred when available.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ==================================================
# CHECK TIKTOK URL
# ==================================================

def is_tiktok_url(url):

    try:

        parsed = urlparse(url)

        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        if host.startswith("m."):
            host = host[2:]

        return (
            host == "tiktok.com"
            or host.endswith(".tiktok.com")
        )

    except Exception:

        return False


# ==================================================
# TIKTOK LINK HANDLER
# ==================================================

async def handle_tiktok_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    url = update.message.text.strip()

    # ==========================================
    # URL CHECK
    # ==========================================

    if not is_tiktok_url(url):

        await update.message.reply_text(
            "❌ **Invalid TikTok link.**\n\n"
            "Please send a valid TikTok video URL.",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )

        return

    # ==========================================
    # CHECKING MESSAGE
    # ==========================================

    checking_message = await update.message.reply_text(
        "🔍 **Checking TikTok video...**\n\n"
        "⏳ Please wait...",
        parse_mode="Markdown"
    )

    try:

        # yt-dlp can block the bot while extracting.
        # Run it in another thread.
        info = await asyncio.to_thread(
            get_video_info,
            url
        )

        # Get available qualities
        formats = get_quality_formats(
            info
        )

        if not formats:

            await checking_message.edit_text(
                "❌ **No downloadable video found.**\n\n"
                "The video may be unavailable or "
                "not supported.",
                parse_mode="Markdown"
            )

            return

        # Save TikTok information
        context.user_data[
            "tiktok_url"
        ] = url

        context.user_data[
            "tiktok_formats"
        ] = formats

        # ==========================================
        # QUALITY KEYBOARD
        # ==========================================

        keyboard = []

        for fmt in formats:

            height = fmt["height"]

            keyboard.append(
                [f"🎬 {height}p"]
            )

        keyboard.append(
            ["❌ CANCEL"]
        )

        quality_keyboard = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )

        # ==========================================
        # VIDEO TITLE
        # ==========================================

        title = info.get(
            "title",
            "TikTok Video"
        )

        # Keep title reasonably short
        if len(title) > 100:

            title = title[:100] + "..."

        quality_text = (
            "✅ **VIDEO FOUND!**\n\n"
            f"🎵 **Title:** {title}\n\n"
            "📥 **Choose video quality:**"
        )

        await checking_message.edit_text(
            quality_text,
            parse_mode="Markdown"
        )

        await update.message.reply_text(
            "👇 **Select your preferred quality:**",
            reply_markup=quality_keyboard,
            parse_mode="Markdown"
        )

    except Exception as e:

        error_text = str(e).strip()
        print("TikTok Info Error:", repr(e))

        # Keep the Telegram message useful without dumping a huge traceback.
        if "IP address is blocked" in error_text:
            user_error = (
                "TikTok is blocking the server IP. "
                "A fresh TikTok cookies file or a different outbound IP is required."
            )
        elif "impersonat" in error_text.lower() or "curl_cffi" in error_text.lower():
            user_error = (
                "TikTok browser impersonation is unavailable. "
                "Install the updated requirements and redeploy."
            )
        elif error_text:
            user_error = error_text[:700]
        else:
            user_error = (
                f"{type(e).__name__}: {repr(e)}"
            )

        await checking_message.edit_text(
            "❌ **Could not process this TikTok link.**\n\n"
            f"**Error:** `{user_error}`",
            parse_mode="Markdown"
        )


# ==================================================
# TIKTOK QUALITY HANDLER
# ==================================================

async def handle_tiktok_quality(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip()

    # ==========================================
    # CANCEL
    # ==========================================

    if text == "❌ CANCEL":

        context.user_data[
            "tiktok_mode"
        ] = False

        context.user_data[
            "tiktok_url"
        ] = None

        context.user_data[
            "tiktok_formats"
        ] = None

        await update.message.reply_text(
            "❌ **Download cancelled.**",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )

        return

    # ==========================================
    # QUALITY BUTTON CHECK
    # ==========================================

    if not text.startswith("🎬 "):

        return

    try:

        selected_height = int(
            text
            .replace("🎬 ", "")
            .replace("p", "")
        )

    except ValueError:

        return

    # ==========================================
    # GET URL
    # ==========================================

    url = context.user_data.get(
        "tiktok_url"
    )

    if not url:

        await update.message.reply_text(
            "❌ Please send a TikTok link first.",
            reply_markup=get_main_keyboard()
        )

        return

    # ==========================================
    # DOWNLOADING MESSAGE
    # ==========================================

    downloading_message = await update.message.reply_text(
        f"📥 **Downloading {selected_height}p video...**\n\n"
        "⏳ Please wait...",
        parse_mode="Markdown"
    )

    user_id = update.message.from_user.id

    output_path = os.path.join(
        os.getcwd(),
        f"tiktok_{user_id}_{update.message.message_id}.mp4"
    )

    # ==========================================
    # LIVE DOWNLOAD PROGRESS
    # ==========================================

    progress_loop = asyncio.get_running_loop()
    progress_state = {"last_update": 0.0, "last_percent": -1}

    def progress_callback(data):
        if data.get("status") != "downloading":
            return

        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        downloaded = data.get("downloaded_bytes") or 0
        if not total:
            return

        percent = max(0, min(100, int(downloaded * 100 / total)))
        # Throttle Telegram edits: at most once every 1.5 seconds, or when
        # the displayed percentage changes by at least 5 points.
        import time as _time
        current_time = _time.monotonic()
        if (current_time - progress_state["last_update"] < 1.5
                and percent - progress_state["last_percent"] < 5):
            return

        progress_state["last_update"] = current_time
        progress_state["last_percent"] = percent

        speed = data.get("speed")
        eta = data.get("eta")
        speed_text = ""
        if speed:
            if speed >= 1024 * 1024:
                speed_text = f"{speed / (1024 * 1024):.1f} MB/s"
            else:
                speed_text = f"{speed / 1024:.0f} KB/s"

        eta_text = f"{int(eta)}s" if eta is not None else "--"
        filled = min(10, percent // 10)
        bar = "█" * filled + "░" * (10 - filled)
        status = (
            f"📥 **Downloading {selected_height}p**\n\n"
            f"`{bar}` **{percent}%**\n\n"
            f"⚡ Speed: **{speed_text or '--'}**\n"
            f"⏱ ETA: **{eta_text}**"
        )

        future = asyncio.run_coroutine_threadsafe(
            downloading_message.edit_text(status, parse_mode="Markdown"),
            progress_loop,
        )

        def _ignore_edit_error(f):
            try:
                f.result()
            except Exception:
                pass

        future.add_done_callback(_ignore_edit_error)

    try:

        # ==========================================
        # DOWNLOAD VIDEO
        # ==========================================

        await asyncio.to_thread(
            download_video,
            url,
            output_path,
            selected_height,
            progress_callback
        )

        # Check file
        if not os.path.exists(output_path):

            raise Exception(
                "Downloaded file not found."
            )

        # ==========================================
        # SEND VIDEO
        # ==========================================

        await downloading_message.edit_text(
            "📤 **Download complete!**\n\n"
            "🚀 Sending video...",
            parse_mode="Markdown"
        )

        with open(
            output_path,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,

                caption=(
                    "🎵 **TikTok Video**\n\n"
                    f"🎬 Quality: **{selected_height}p**\n"
                    "🚀 Watermark-free source "
                    "preferred when available."
                ),

                reply_markup=get_main_keyboard(),

                supports_streaming=True,

                parse_mode="Markdown"
            )

        increment_stat(update.effective_user.id, "tiktok_downloads")
        add_download_history(update.effective_user.id, context.user_data.get("tiktok_url") or "", selected_height, "success")

        # Delete status message
        await downloading_message.delete()

    except Exception as e:

        print(
            "TikTok Download Error:",
            e
        )
        try:
            add_download_history(update.effective_user.id, context.user_data.get("tiktok_url") or "", selected_height if 'selected_height' in locals() else "", "failed")
        except Exception:
            pass

        await downloading_message.edit_text(
            "❌ **Download failed.**\n\n"
            "The selected quality may not be available, "
            "the video may be restricted, or the file "
            "may be too large for Telegram.",
            parse_mode="Markdown"
        )

    finally:

        # ==========================================
        # DELETE TEMPORARY VIDEO
        # ==========================================

        if os.path.exists(
            output_path
        ):

            os.remove(
                output_path
            )

        # ==========================================
        # RESET TIKTOK MODE
        # ==========================================

        context.user_data[
            "tiktok_url"
        ] = None

        context.user_data[
            "tiktok_formats"
        ] = None

        context.user_data[
            "tiktok_mode"
        ] = False


# ==================================================
# TEXT / BUTTON HANDLER
# ==================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip()

    if await ui_text_action(update, context, text):
        return

    # ==========================================
    # QR SCANNER BUTTON
    # ==========================================

    if text == "📷 QR CODE SCANNER":

        await qr_scanner_start(
            update,
            context
        )

        return

    # ==========================================
    # QR GENERATOR BUTTON
    # ==========================================

    if text == "🔳 QR CODE GENERATOR":

        await qr_generator_start(
            update,
            context
        )

        return

    # ==========================================
    # TIKTOK DOWNLOADER BUTTON
    # ==========================================

    if text == "🎵 TIKTOK DOWNLOADER":

        await tiktok_downloader_start(
            update,
            context
        )

        return

    # ==========================================
    # TIKTOK LINK (must be checked before quality)
    # ==========================================
    # After the first video, tiktok_url may still exist while the
    # downloader is waiting for/handling the next message. If we check
    # tiktok_url first, a new TikTok URL is mistakenly sent to the
    # quality handler and silently ignored. Detect a new TikTok URL first.

    # A TikTok URL always starts a fresh download flow, even if the
    # previous download has already finished and state was reset.
    # This is intentionally checked before quality/state handling.
    if is_tiktok_url(text):

        context.user_data["tiktok_mode"] = True

        await handle_tiktok_link(
            update,
            context
        )

        return

    # ==========================================
    # TIKTOK QUALITY
    # ==========================================

    if context.user_data.get(
        "tiktok_url"
    ):

        await handle_tiktok_quality(
            update,
            context
        )

        return

    # ==========================================
    # TIKTOK MODE (non-URL input)
    # ==========================================

    if context.user_data.get(
        "tiktok_mode",
        False
    ):

        await handle_tiktok_link(
            update,
            context
        )

        return

    # ==========================================
    # QR GENERATOR TEXT / LINK
    # ==========================================

    if context.user_data.get(
        "qr_generator_mode",
        False
    ):

        await handle_qr_generator_text(
            update,
            context
        )

        return

    # QR scanner mode + text input
    if context.user_data.get("qr_mode", False):
        await update.message.reply_text(
            "📷 **QR CODE SCANNER**\n\nPlease send a photo/image containing the QR code.",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        return


# ==================================================
# MAIN
# ==================================================

def main():

    init_database()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Phase 3: track every update before normal handlers
    app.add_handler(MessageHandler(filters.ALL, track_update), group=-1)

    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("id", myid_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", user_profile_command))
    app.add_handler(CommandHandler("mystats", user_stats_view))
    app.add_handler(CommandHandler("history", user_features_command))
    app.add_handler(CallbackQueryHandler(ui_callback, pattern=r"^(ui_|lang_)"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_"))

    # ==========================================
    # /start
    # ==========================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # ==========================================
    # TEXT BUTTONS + TEXT INPUT
    # ==========================================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    # ==========================================
    # QR IMAGES
    # ==========================================

    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            handle_qr_image
        )
    )

    # ==========================================
    # START BOT
    # ==========================================

    print(
        "🤖 Assistant Bot is running..."
    )

    # Render Web Service needs an open port.
    Thread(target=start_web_server, daemon=True).start()

    app.run_polling()


# ==================================================
# START
# ==================================================

if __name__ == "__main__":

    main()
