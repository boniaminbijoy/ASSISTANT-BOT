import os
import asyncio
import html
import tempfile
import uuid
import sqlite3
import traceback
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps
from threading import Thread, Lock

from telegram import (
    Update,
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters, CallbackQueryHandler, ApplicationHandlerStop
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

# Runtime health / recovery state. This does NOT prevent Render Free from
# sleeping; it helps recover from an unexpected polling/event-loop failure
# while the service is running.
HEARTBEAT_LOCK = Lock()
LAST_HEARTBEAT = time.time()
RECOVERY_RESTARTING = False

def touch_heartbeat():
    global LAST_HEARTBEAT
    with HEARTBEAT_LOCK:
        LAST_HEARTBEAT = time.time()

def heartbeat_age():
    with HEARTBEAT_LOCK:
        return time.time() - LAST_HEARTBEAT


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
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, added_by INTEGER, created_at TEXT NOT NULL
        )""")
        for admin_id in ADMIN_IDS:
            conn.execute("INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES(?,?,?)", (admin_id, admin_id, utc_now()))
        conn.execute("""CREATE TABLE IF NOT EXISTS broadcast_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
            kind TEXT NOT NULL, target_count INTEGER DEFAULT 0, sent INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
            message TEXT NOT NULL, run_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TEXT NOT NULL, sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, error_type TEXT,
            error_text TEXT, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS more_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            added_by INTEGER NOT NULL, created_at TEXT NOT NULL, enabled INTEGER DEFAULT 1
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            message TEXT NOT NULL, created_at TEXT NOT NULL, delivered INTEGER DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_messages_created ON support_messages(id DESC)")
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
        row = conn.execute("""SELECT COUNT(*),
            SUM(CASE WHEN julianday(last_seen) >= julianday('now','-1 day') THEN 1 ELSE 0 END),
            COALESCE(SUM(messages),0), COALESCE(SUM(qr_scans),0), COALESCE(SUM(qr_generated),0),
            COALESCE(SUM(tiktok_downloads),0) FROM users""").fetchone()
    return tuple(x or 0 for x in row)

def get_period_stats(days):
    with db_connect() as conn:
        return conn.execute("""SELECT COUNT(*), COALESCE(SUM(messages),0),
            COALESCE(SUM(qr_scans),0), COALESCE(SUM(qr_generated),0),
            COALESCE(SUM(tiktok_downloads),0)
            FROM users WHERE last_seen >= ?""", ((datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec='seconds'),)).fetchone()

def get_user_by_id(user_id):
    with db_connect() as conn:
        return conn.execute("SELECT user_id, username, first_name, joined_at, last_seen, messages, qr_scans, qr_generated, tiktok_downloads, is_blocked FROM users WHERE user_id=?", (user_id,)).fetchone()

def search_users(term, limit=10):
    with db_connect() as conn:
        like=f"%{term}%"
        return conn.execute("SELECT user_id, username, first_name, last_seen, is_blocked FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ? ORDER BY last_seen DESC LIMIT ?", (like,like,like,limit)).fetchall()

def set_blocked(user_id, blocked):
    with db_connect() as conn:
        cur = conn.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))
        conn.commit()
        return cur.rowcount > 0

def maintenance_enabled():
    with db_connect() as conn:
        row=conn.execute("SELECT value FROM bot_settings WHERE key='maintenance' LIMIT 1").fetchone()
    return bool(row and row[0]=='1')

def set_maintenance(enabled):
    with db_connect() as conn:
        conn.execute("INSERT INTO bot_settings(key,value) VALUES('maintenance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('1' if enabled else '0',))
        conn.commit()

def get_setting(key, default='1'):
    with db_connect() as conn:
        row=conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def set_setting(key, value):
    with db_connect() as conn:
        conn.execute("INSERT INTO bot_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        conn.commit()

def feature_enabled(key):
    return get_setting(key, '1') == '1'

def save_error(user_id, error_type, error_text):
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO error_logs(user_id,error_type,error_text,created_at) VALUES(?,?,?,?)", (user_id, error_type, str(error_text)[:4000], utc_now()))
            conn.commit()
    except Exception:
        pass

def save_broadcast_report(admin_id, kind, target, sent, failed, blocked):
    with db_connect() as conn:
        conn.execute("INSERT INTO broadcast_reports(admin_id,kind,target_count,sent,failed,blocked,created_at) VALUES(?,?,?,?,?,?,?)", (admin_id,kind,target,sent,failed,blocked,utc_now()))
        conn.commit()

def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE user_id=? LIMIT 1", (user_id,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False

def get_more_bots(enabled_only=True):
    with db_connect() as conn:
        if enabled_only:
            return conn.execute("SELECT id, username, name, description, enabled FROM more_bots WHERE enabled=1 ORDER BY id DESC").fetchall()
        return conn.execute("SELECT id, username, name, description, enabled FROM more_bots ORDER BY id DESC").fetchall()

def add_more_bot(username, name, description, added_by):
    username = username.strip().lstrip('@')
    with db_connect() as conn:
        conn.execute("INSERT INTO more_bots(username,name,description,added_by,created_at,enabled) VALUES(?,?,?,?,?,1) ON CONFLICT(username) DO UPDATE SET name=excluded.name, description=excluded.description, added_by=excluded.added_by, enabled=1", (username, name.strip()[:100], description.strip()[:1000], added_by, utc_now()))
        conn.commit()

def delete_more_bot(bot_id):
    with db_connect() as conn:
        cur=conn.execute("DELETE FROM more_bots WHERE id=?", (bot_id,))
        conn.commit()
        return cur.rowcount > 0

def more_bots_keyboard():
    rows=get_more_bots(True)
    buttons=[]
    for bot_id, username, name, desc, enabled in rows:
        buttons.append([InlineKeyboardButton(f"🤖 {name[:32]}", callback_data=f"morebot_view_{bot_id}")])
    buttons.append([InlineKeyboardButton("🏠 Home", callback_data="ui_home")])
    return InlineKeyboardMarkup(buttons)

async def more_bots_command(update, context):
    rows=get_more_bots(True)
    if not rows:
        await update.effective_message.reply_text(
            "🤖 <b>MORE BOTS</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✨ No additional bots are available right now.\n"
            "━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="ui_home")]])
        )
        return
    lines=[
        "🤖 <b>MORE BOTS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "✨ <i>Explore useful bots from our collection.</i>",
        "",
        "👇 <b>Select a bot</b> to view its details:"
    ]
    for _, username, name, desc, _ in rows:
        short=html.escape(desc[:90] + ("…" if len(desc)>90 else ""))
        lines.append(f"\n🤖 <b>{html.escape(name)}</b>\n   📝 {short}")
    lines.append("\n━━━━━━━━━━━━━━━━━━")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=more_bots_keyboard())

async def addbot_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    raw=update.message.text.partition(' ')[2].strip()
    parts=[x.strip() for x in raw.split('|', 2)]
    if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
        await update.effective_message.reply_text("🧩 <b>ADD MORE BOT</b>\n\nUsage:\n<code>/addbot @BotUsername | Bot Name | Description</code>\n\nExample:\n<code>/addbot @ExampleBot | Example Bot | A useful utility bot.</code>", parse_mode="HTML")
        return
    username=parts[0].lstrip('@')
    if not username.replace('_','').isalnum() or len(username) < 5 or len(username) > 32:
        await update.effective_message.reply_text("❌ Invalid bot username.")
        return
    add_more_bot(username, parts[1], parts[2], update.effective_user.id)
    await update.effective_message.reply_text(f"✅ <b>Bot added to More Bots</b>\n\n🤖 @{html.escape(username)}\n🏷️ {html.escape(parts[1])}\n📝 {html.escape(parts[2])}", parse_mode="HTML", reply_markup=admin_keyboard())

async def bots_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    rows=get_more_bots(False)
    if not rows:
        await update.effective_message.reply_text("🤖 <b>MORE BOTS MANAGER</b>\n\nNo bots added yet.", parse_mode="HTML", reply_markup=admin_keyboard())
        return
    lines=["🤖 <b>MORE BOTS MANAGER</b>", ""]
    buttons=[]
    for bot_id, username, name, desc, enabled in rows:
        state='🟢' if enabled else '🔴'
        lines.append(f"{state} <b>{html.escape(name)}</b> — @{html.escape(username)}\n   {html.escape(desc[:180])}")
        buttons.append([InlineKeyboardButton(f"🗑️ Remove {name[:25]}", callback_data=f"morebot_delete_{bot_id}")])
    buttons.append([InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel_home")])
    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

async def morebot_callback(update, context):
    query=update.callback_query
    await query.answer()

    # Back button from a bot details page. It must be handled before
    # parsing a bot id because callback_data is simply "more_bots".
    if query.data == "more_bots":
        rows=get_more_bots(True)
        if not rows:
            await query.edit_message_text(
                "🤖 <b>MORE BOTS</b>\n\nNo additional bots are available right now.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="ui_home")]])
            )
            return
        lines=[
            "🤖 <b>MORE BOTS</b>",
            "━━━━━━━━━━━━━━━━━━",
            "✨ <i>Explore useful bots from our collection.</i>",
            "",
            "👇 <b>Select a bot</b> to view its details:"
        ]
        for _, username, name, desc, _ in rows:
            short=html.escape(desc[:90] + ("…" if len(desc)>90 else ""))
            lines.append(f"\n🤖 <b>{html.escape(name)}</b>\n   📝 {short}")
        lines.append("\n━━━━━━━━━━━━━━━━━━")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=more_bots_keyboard())
        return

    try: bot_id=int(query.data.rsplit('_',1)[1])
    except (ValueError, IndexError):
        return
    with db_connect() as conn:
        row=conn.execute("SELECT id,username,name,description,enabled FROM more_bots WHERE id=?", (bot_id,)).fetchone()
    if not row:
        await query.edit_message_text("❌ This bot is no longer available.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 𝗠𝗼𝗿𝗲 𝗕𝗼𝘁𝘀", callback_data="more_bots")],[InlineKeyboardButton("🏠 Home", callback_data="ui_home")]]))
        return
    _, username, name, description, enabled=row
    if query.data.startswith('morebot_delete_'):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Admin only.")
            return
        delete_more_bot(bot_id)
        await query.edit_message_text("🗑️ <b>Bot removed.</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Manage More Bots", callback_data="admin_more_bots")],[InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel_home")]]))
        return
    text=(f"🤖 <b>{html.escape(name)}</b>\n\n"
          f"📝 <b>Description</b>\n{html.escape(description)}\n\n"
          f"🔗 <b>@{html.escape(username)}</b>\n\n"
          "✨ Tap below to open and use this bot.")
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Open Bot", url=f"https://t.me/{username}")],
        [InlineKeyboardButton("⬅️ Back to More Bots", callback_data="more_bots")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard"), InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Search User", callback_data="admin_search"), InlineKeyboardButton("📈 Reports", callback_data="admin_reports")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"), InlineKeyboardButton("🔧 Maintenance", callback_data="admin_maintenance")],
        [InlineKeyboardButton("📣 Announcement", callback_data="admin_announce"), InlineKeyboardButton("⚙️ 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀", callback_data="admin_settings")],
        [InlineKeyboardButton(f"🔧 M:{'ON' if maintenance_enabled() else 'OFF'}", callback_data="set_maintenance_toggle"), InlineKeyboardButton(f"🎵 D:{'ON' if feature_enabled('downloader') else 'OFF'}", callback_data="set_downloader_toggle")],
        [InlineKeyboardButton(f"📷 S:{'ON' if feature_enabled('qr_scanner') else 'OFF'}", callback_data="set_scanner_toggle"), InlineKeyboardButton(f"🔲 G:{'ON' if feature_enabled('qr_generator') else 'OFF'}", callback_data="set_generator_toggle")],
        [InlineKeyboardButton("🧪 System Status", callback_data="admin_status")],
        [InlineKeyboardButton("🤖 More Bots Manager", callback_data="admin_more_bots")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(f"⛔ Admin only.\n\n🆔 Your Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown")
        return
    await update.effective_message.reply_text("🛠️ *ADVANCED ADMIN PANEL*\n\nChoose an option:", reply_markup=admin_keyboard(), parse_mode="Markdown")

async def dashboard_text():
    total, active24, messages, scans, generated, downloads=get_stats()
    d=get_period_stats(1); w=get_period_stats(7); m=get_period_stats(30)
    with db_connect() as conn:
        scheduled=conn.execute("SELECT COUNT(*) FROM scheduled_broadcasts WHERE status='scheduled'").fetchone()[0]
        errors=conn.execute("SELECT COUNT(*) FROM error_logs WHERE created_at >= ?", ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds'),)).fetchone()[0]
        broadcasts=conn.execute("SELECT COUNT(*) FROM broadcast_reports").fetchone()[0]
    return (f"📊 *ADVANCED ADMIN DASHBOARD*\n\n"
            f"👥 Total Users: *{total}*\n🟢 Active 24h: *{active24}*\n💬 Messages: *{messages}*\n\n"
            f"📅 *Daily* — Users {d[0]} | DL {d[4]} | QR Scan {d[2]} | QR Gen {d[3]}\n"
            f"📅 *Weekly* — Users {w[0]} | DL {w[4]} | QR Scan {w[2]} | QR Gen {w[3]}\n"
            f"📅 *Monthly* — Users {m[0]} | DL {m[4]} | QR Scan {m[2]} | QR Gen {m[3]}\n\n"
            f"🎵 Total Downloads: *{downloads}*\n📷 Total QR Scans: *{scans}*\n🔲 Total QR Generated: *{generated}*\n📢 Broadcasts: *{broadcasts}*\n⏰ Scheduled: *{scheduled}*\n🚨 Errors (24h): *{errors}*\n🔧 Maintenance: *{'ON' if maintenance_enabled() else 'OFF'}*")

async def stats_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only."); return
    await update.effective_message.reply_text(await dashboard_text(), parse_mode="Markdown", reply_markup=admin_keyboard())

async def users_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only."); return
    with db_connect() as conn:
        rows=conn.execute("SELECT user_id, username, first_name, last_seen, is_blocked FROM users ORDER BY last_seen DESC LIMIT 20").fetchall()
    lines=["👥 *LATEST USERS*\n"]
    for r in rows:
        lines.append(f"• `{r[0]}` {('@'+r[1]) if r[1] else r[2] or 'User'} — {'🚫 Blocked' if r[4] else '🟢 Active'}")
    await update.effective_message.reply_text("\n".join(lines) if rows else "No users yet.", parse_mode="Markdown", reply_markup=admin_keyboard())

async def user_details_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    arg=update.message.text.partition(' ')[2].strip()
    if not arg or not arg.isdigit():
        await update.effective_message.reply_text("Usage: /user <telegram_id>"); return
    r=get_user_by_id(int(arg))
    if not r: await update.effective_message.reply_text("❌ User not found."); return
    uid, username, name, joined, last, msgs, scans, gen, dl, blocked=r
    await update.effective_message.reply_text(f"👤 *USER DETAILS*\n\n🆔 `{uid}`\n👤 {name or '—'}\n🔗 @{username or '—'}\n📅 Joined: {format_dt(joined)}\n🟢 Last active: {format_dt(last)}\n💬 Messages: {msgs}\n🎵 Downloads: {dl}\n📷 QR Scans: {scans}\n🔲 QR Generates: {gen}\n🚫 Blocked: {'Yes' if blocked else 'No'}\n\nUse /block {uid} or /unblock {uid}", parse_mode="Markdown", reply_markup=admin_keyboard())

async def searchuser_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    term=update.message.text.partition(' ')[2].strip()
    if not term: await update.effective_message.reply_text("Usage: /searchuser <ID or username>"); return
    rows=search_users(term)
    if not rows: await update.effective_message.reply_text("❌ No users found."); return
    lines=["🔍 *SEARCH RESULTS*\n"]
    for uid,un,name,last,blocked in rows:
        lines.append(f"• `{uid}` {('@'+un) if un else name or 'User'} — {'🚫' if blocked else '🟢'}")
    await update.effective_message.reply_text("\n".join(lines)+"\n\nUse /user <id> for details.", parse_mode="Markdown")

async def block_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    arg=update.message.text.partition(' ')[2].strip()
    if not arg.isdigit():
        await update.effective_message.reply_text("Usage: /block <telegram_id>")
        return
    uid=int(arg)
    if uid in ADMIN_IDS:
        await update.effective_message.reply_text("⚠️ Admin accounts cannot be blocked.")
        return
    if not set_blocked(uid, True):
        await update.effective_message.reply_text(f"❌ User `{arg}` not found in database.", parse_mode="Markdown")
        return
    log_activity(update.effective_user.id,"admin_block",arg)
    await update.effective_message.reply_text(f"🚫 User `{arg}` blocked successfully.", parse_mode="Markdown")

async def unblock_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    arg=update.message.text.partition(' ')[2].strip()
    if not arg.isdigit():
        await update.effective_message.reply_text("Usage: /unblock <telegram_id>")
        return
    uid=int(arg)
    if not set_blocked(uid, False):
        await update.effective_message.reply_text(f"❌ User `{arg}` not found in database.", parse_mode="Markdown")
        return
    log_activity(update.effective_user.id,"admin_unblock",arg)
    await update.effective_message.reply_text(f"🔓 User `{arg}` unblocked successfully.", parse_mode="Markdown")

# Friendly aliases for the same user-control system.
async def ban_command(update, context):
    await block_command(update, context)

async def unban_command(update, context):
    await unblock_command(update, context)


async def admin_list_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id, added_by, created_at FROM admins ORDER BY user_id").fetchall()
    lines = ["👑 *ADMIN LIST*\n"]
    for uid, added_by, created in rows:
        source = "ENV" if uid in ADMIN_IDS else f"Added by `{added_by}`"
        lines.append(f"• `{uid}` — {source}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=admin_keyboard())

async def add_admin_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    arg = update.effective_message.text.partition(' ')[2].strip()
    if not arg.isdigit():
        await update.effective_message.reply_text("Usage: /addadmin <telegram_id>")
        return
    uid = int(arg)
    if is_admin(uid):
        await update.effective_message.reply_text(f"ℹ️ `{uid}` is already an admin.", parse_mode="Markdown")
        return
    with db_connect() as conn:
        conn.execute("INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES(?,?,?)", (uid, update.effective_user.id, utc_now()))
        conn.commit()
    await update.effective_message.reply_text(f"✅ Admin added successfully.\n\n🆔 `{uid}`", parse_mode="Markdown")

async def del_admin_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    arg = update.effective_message.text.partition(' ')[2].strip()
    if not arg.isdigit():
        await update.effective_message.reply_text("Usage: /deladmin <telegram_id>")
        return
    uid = int(arg)
    if uid in ADMIN_IDS:
        await update.effective_message.reply_text("⚠️ This admin is configured in Render `ADMIN_IDS`. Remove the ID from the Environment Variable to revoke it.")
        return
    with db_connect() as conn:
        cur = conn.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        conn.commit()
    if cur.rowcount == 0:
        await update.effective_message.reply_text("❌ Admin not found.")
        return
    await update.effective_message.reply_text(f"✅ Admin removed successfully.\n\n🆔 `{uid}`", parse_mode="Markdown")

async def about_command(update, context):
    await update.effective_message.reply_text(
        "🤖 *ASSISTANT BOT*\n\nAll-in-One Telegram Utility Bot.\n\n📥 TikTok Downloader\n📷 QR Scanner\n🔲 QR Generator\n📊 User Statistics\n👤 Profile & History\n🛡️ Advanced Admin Panel",
        parse_mode="Markdown")

async def support_command(update, context):
    await support_start(update, context)

async def helpuser_command(update, context):
    await update.effective_message.reply_text(
        "❓ *USER HELP*\n\n/start — Main menu\n/profile — My Profile\n/mystats — My Statistics\n/history — My History\n/settings — Settings\n/id — Your Telegram ID\n/about — About Bot\n/support — Support",
        parse_mode="Markdown")

async def helpadmin_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    await update.effective_message.reply_text(
        "🛡️ *ADMIN HELP*\n\n"
        "/admin — Admin Panel\n/dashboard — Dashboard\n/adminlist — Admin List\n/addadmin <id> — Add Admin\n/deladmin <id> — Delete Admin\n"
        "/activeusers — Active Users (24h)\n/newuser — New Users (today)\n/topuser — Top Users\n/usercount — Total Users\n"
        "/users — Recent Users\n/searchuser <id|username> — Search User\n/user <id> — User Details\n"
        "/block <id> — Block User\n/unblock <id> — Unblock User\n/ban <id> — Ban User\n/unban <id> — Unban User\n"
        "/broadcast — Text Broadcast\n/reports — Broadcast Reports\n/maintenance on|off — Maintenance\n/announce <msg> — Announcement\n/restart — Restart Bot\n/ping — Bot Ping\n/status — Live Command & Feature Status",
        parse_mode="Markdown", reply_markup=admin_keyboard())

async def status_command(update, context):
    """Admin-only live registry/status page for commands and button features."""
    if not is_admin(update.effective_user.id):
        # /status is intentionally silent for normal users.
        return

    commands = [
        ('/start', 'Main Menu'), ('/help', 'Help'), ('/profile', 'My Profile'),
        ('/mystats', 'My Statistics'), ('/history', 'My History'), ('/settings', 'Language Settings'),
        ('/support', 'User → Admin Support'), ('/about', 'About'), ('/id', 'User ID'),
        ('/admin', 'Admin Panel'), ('/stats', 'Dashboard'), ('/users', 'User List'),
        ('/user', 'User Details'), ('/searchuser', 'User Search'), ('/block /unblock', 'Block Control'),
        ('/broadcast', 'Broadcast'), ('/reports', 'Broadcast Reports'), ('/schedule', 'Scheduled Broadcast'),
        ('/scheduled /cancelschedule', 'Schedule Control'), ('/reply', 'Admin → User Reply'),
        ('/maintenance', 'Maintenance'), ('/announce', 'Announcement'), ('/ping', 'Ping'),
        ('/restart', 'Restart'), ('/adminlist', 'Admin List'), ('/addadmin /deladmin', 'Admin Management'),
        ('/activeusers /newuser /topuser /usercount', 'User Statistics'),
    ]
    features = [
        ('📥 Downloader', feature_enabled('downloader')),
        ('📷 QR Scanner', feature_enabled('qr_scanner')),
        ('🔲 QR Generator', feature_enabled('qr_generator')),
        ('🆘 User Support', True),
        ('📢 Broadcast', True),
        ('⏰ Scheduled Broadcast', True),
        ('🚨 Error Monitoring', True),
        ('🛡️ Admin Commands', True),
        ('⚙️ Feature Settings', True),
        ('🔧 Maintenance', not maintenance_enabled()),
    ]
    lines=['🧪 <b>BOT SYSTEM STATUS</b>\n', '<b>Commands</b>']
    for cmd, desc in commands:
        lines.append(f'🟢 <code>{html.escape(cmd)}</code> — {html.escape(desc)}')
    lines.append('\n<b>Buttons / Features</b>')
    for name, enabled in features:
        state = '🟢 ON' if enabled else '🔴 OFF'
        if name == '🔧 Maintenance':
            state = '🟢 OFF' if enabled else '🔴 ON'
        lines.append(f'{state} — {html.escape(name)}')
    lines.append('\nℹ️ This page shows registered commands and current feature switches. A command marked 🟢 is registered in the bot.')
    await update.effective_message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=admin_keyboard())

async def ping_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    start = datetime.now(timezone.utc)
    msg = await update.effective_message.reply_text("🏓 Pinging...")
    ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    await msg.edit_text(f"🏓 *PONG*\n\n⚡ Response: `{ms} ms`\n🟢 Bot is online.", parse_mode="Markdown")

async def activeusers_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id, username, first_name, last_seen FROM users WHERE last_seen >= ? ORDER BY last_seen DESC LIMIT 50", ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds'),)).fetchall()
    lines = [f"🟢 *ACTIVE USERS (24H)* — {len(rows)}\n"]
    for uid, un, name, last in rows:
        lines.append(f"• `{uid}` {('@'+un) if un else (name or 'User')} — {format_dt(last)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=admin_keyboard())

async def newuser_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    today = datetime.now(timezone.utc).date().isoformat()
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id, username, first_name, joined_at FROM users WHERE substr(joined_at,1,10)=? ORDER BY joined_at DESC LIMIT 50", (today,)).fetchall()
    lines = [f"🆕 *NEW USERS (TODAY)* — {len(rows)}\n"]
    for uid, un, name, joined in rows:
        lines.append(f"• `{uid}` {('@'+un) if un else (name or 'User')} — {format_dt(joined)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=admin_keyboard())

async def topuser_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id, username, first_name, messages, tiktok_downloads, qr_scans, qr_generated FROM users ORDER BY (messages + tiktok_downloads + qr_scans + qr_generated) DESC LIMIT 20").fetchall()
    lines = ["🏆 *TOP USERS*\n"]
    for i, (uid, un, name, messages, dl, scans, gen) in enumerate(rows, 1):
        total = messages + dl + scans + gen
        lines.append(f"{i}. `{uid}` {('@'+un) if un else (name or 'User')} — {total} activities")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=admin_keyboard())

async def usercount_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
    await update.effective_message.reply_text(f"👥 *USER COUNT*\n\nTotal Users: *{total}*\n🚫 Blocked: *{blocked}*\n🟢 Active: *{total-blocked}*", parse_mode="Markdown", reply_markup=admin_keyboard())

async def restart_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    await update.effective_message.reply_text("♻️ Bot restart requested. Render will restart the service.")
    await asyncio.sleep(1)
    # Let the hosting platform manage the process lifecycle; exiting is safer than spawning a second polling instance.
    os._exit(0)

async def maintenance_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    arg=update.message.text.partition(' ')[2].strip().lower()
    if arg in {'on','1','enable','enabled'}: set_maintenance(True)
    elif arg in {'off','0','disable','disabled'}: set_maintenance(False)
    else:
        await update.effective_message.reply_text(f"🔧 Maintenance is currently *{'ON' if maintenance_enabled() else 'OFF'}*\n\nUse /maintenance on or /maintenance off", parse_mode="Markdown"); return
    await update.effective_message.reply_text(f"🔧 Maintenance mode *{'ON' if maintenance_enabled() else 'OFF'}*", parse_mode="Markdown", reply_markup=admin_keyboard())

async def announce_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    text=update.message.text.partition(' ')[2].strip()
    if not text: await update.effective_message.reply_text("Usage: /announce <message>"); return
    with db_connect() as conn: users=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent=failed=blocked=0
    for uid in users:
        try: await context.bot.send_message(uid, f"📣 *ADMIN ANNOUNCEMENT*\n\n{text}", parse_mode="Markdown"); sent+=1
        except Exception as exc:
            failed+=1
            if 'blocked' in str(exc).lower() or 'chat not found' in str(exc).lower(): blocked+=1
    save_broadcast_report(update.effective_user.id,'announcement',len(users),sent,failed,blocked)
    await update.effective_message.reply_text(f"📣 Announcement complete.\n\n📨 Sent: {sent}\n⚠️ Failed: {failed}\n🚫 Blocked: {blocked}")

async def broadcast_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    text=update.message.text.partition(' ')[2].strip()
    if not text:
        await update.effective_message.reply_text("📢 Usage: /broadcast <text>\n\nFor photo/video/document: reply to that media and use /broadcast_media\nFor button: /broadcast_button Button Text | https://example.com"); return
    with db_connect() as conn: users=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent=failed=blocked=0
    for uid in users:
        try: await context.bot.send_message(uid,text=text); sent+=1
        except Exception as exc:
            failed+=1
            if 'blocked' in str(exc).lower() or 'chat not found' in str(exc).lower(): blocked+=1
    save_broadcast_report(update.effective_user.id,'text',len(users),sent,failed,blocked)
    pct=(sent*100/len(users)) if users else 0
    await update.effective_message.reply_text(f"✅ *Broadcast delivery report*\n\n👥 Target: {len(users)}\n📨 Sent: {sent}\n⚠️ Failed: {failed}\n🚫 Blocked: {blocked}\n📈 Delivery: {pct:.1f}%", parse_mode="Markdown")

async def broadcast_media_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    reply=update.message.reply_to_message
    if not reply or not (reply.photo or reply.video or reply.document):
        await update.effective_message.reply_text("Reply to a photo, video, or document with /broadcast_media"); return
    with db_connect() as conn: users=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent=failed=blocked=0
    for uid in users:
        try:
            if reply.photo: await context.bot.send_photo(uid, reply.photo[-1].file_id, caption=reply.caption or '')
            elif reply.video: await context.bot.send_video(uid, reply.video.file_id, caption=reply.caption or '')
            else: await context.bot.send_document(uid, reply.document.file_id, caption=reply.caption or '')
            sent+=1
        except Exception as exc:
            failed+=1
            if 'blocked' in str(exc).lower() or 'chat not found' in str(exc).lower(): blocked+=1
    kind='photo' if reply.photo else 'video' if reply.video else 'document'
    save_broadcast_report(update.effective_user.id,kind,len(users),sent,failed,blocked)
    await update.effective_message.reply_text(f"✅ *{kind.title()} broadcast report*\n\nTarget: {len(users)}\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",parse_mode='Markdown')

async def broadcast_button_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    arg=update.message.text.partition(' ')[2].strip()
    if '|' not in arg: await update.effective_message.reply_text("Usage: /broadcast_button Button Text | https://example.com"); return
    label,url=[x.strip() for x in arg.split('|',1)]
    if not label or not url.startswith(('http://','https://')): await update.effective_message.reply_text("❌ Invalid button or URL."); return
    with db_connect() as conn: users=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent=failed=blocked=0
    kb=InlineKeyboardMarkup([[InlineKeyboardButton(label,url=url)]])
    for uid in users:
        try: await context.bot.send_message(uid,"📢 *ADMIN BROADCAST*",parse_mode='Markdown',reply_markup=kb); sent+=1
        except Exception as exc:
            failed+=1
            if 'blocked' in str(exc).lower() or 'chat not found' in str(exc).lower(): blocked+=1
    save_broadcast_report(update.effective_user.id,'button',len(users),sent,failed,blocked)
    await update.effective_message.reply_text(f"✅ Button broadcast report\n\nTarget: {len(users)}\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}")

async def send_broadcast_to_users(bot, text, admin_id, kind='scheduled'):
    with db_connect() as conn:
        users=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]
    sent=failed=blocked=0
    for uid in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception as exc:
            failed += 1
            if 'blocked' in str(exc).lower() or 'chat not found' in str(exc).lower(): blocked += 1
    save_broadcast_report(admin_id, kind, len(users), sent, failed, blocked)
    return len(users), sent, failed, blocked

async def scheduled_broadcast_job(context):
    job=context.job
    data=job.data
    try:
        target,sent,failed,blocked=await send_broadcast_to_users(context.bot, data['message'], data['admin_id'], 'scheduled')
        with db_connect() as conn:
            conn.execute("UPDATE scheduled_broadcasts SET status='sent', sent=?, failed=?, blocked=? WHERE id=?", (sent,failed,blocked,data['id']))
            conn.commit()
        try:
            await context.bot.send_message(data['admin_id'], f"⏰ Scheduled broadcast #{data['id']} completed.\n\n📨 Sent: {sent}\n⚠️ Failed: {failed}\n🚫 Blocked: {blocked}")
        except Exception:
            pass
    except Exception as exc:
        save_error(data.get('admin_id'), 'scheduled_broadcast', traceback.format_exc())
        with db_connect() as conn:
            conn.execute("UPDATE scheduled_broadcasts SET status='failed' WHERE id=?", (data['id'],))
            conn.commit()

async def schedule_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('⛔ Admin only.'); return
    arg=update.message.text.partition(' ')[2].strip()
    if '|' not in arg:
        await update.effective_message.reply_text('Usage: /schedule YYYY-MM-DD HH:MM | message\nTime zone: Asia/Dhaka'); return
    when,text=[x.strip() for x in arg.split('|',1)]
    try:
        local_dt=datetime.strptime(when,'%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('Asia/Dhaka'))
        run_at=local_dt.astimezone(timezone.utc)
        if run_at <= datetime.now(timezone.utc):
            await update.effective_message.reply_text('❌ Time must be in the future.'); return
    except Exception:
        await update.effective_message.reply_text('❌ Invalid date/time. Example: /schedule 2026-09-30 20:00 | Hello users'); return
    with db_connect() as conn:
        cur=conn.execute("INSERT INTO scheduled_broadcasts(admin_id,message,run_at,status,created_at) VALUES(?,?,?,?,?)", (update.effective_user.id,text,run_at.isoformat(timespec='seconds'),'scheduled',utc_now()))
        sid=cur.lastrowid; conn.commit()
    if context.job_queue:
        context.job_queue.run_once(scheduled_broadcast_job, when=run_at, data={'id':sid,'admin_id':update.effective_user.id,'message':text}, name=f'scheduled_{sid}')
    await update.effective_message.reply_text(f'⏰ Scheduled broadcast #{sid} created.\n🕘 {when} Asia/Dhaka')

async def scheduled_list_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text('⛔ Admin only.'); return
    with db_connect() as conn:
        rows=conn.execute("SELECT id,message,run_at,status FROM scheduled_broadcasts ORDER BY id DESC LIMIT 20").fetchall()
    if not rows: await update.effective_message.reply_text('⏰ No scheduled broadcasts.'); return
    lines=['⏰ *SCHEDULED BROADCASTS*\n']
    for sid,msg,run,status in rows: lines.append(f'#{sid} — {status} — {format_dt(run)}\n{html.escape(msg[:120])}')
    await update.effective_message.reply_text('\n\n'.join(lines),parse_mode='Markdown',reply_markup=admin_keyboard())

async def cancel_schedule_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text('⛔ Admin only.'); return
    arg=update.message.text.partition(' ')[2].strip()
    if not arg.isdigit(): await update.effective_message.reply_text('Usage: /cancelschedule <id>'); return
    sid=int(arg)
    with db_connect() as conn:
        row=conn.execute("SELECT status FROM scheduled_broadcasts WHERE id=?",(sid,)).fetchone()
        if not row: await update.effective_message.reply_text('❌ Schedule not found.'); return
        conn.execute("UPDATE scheduled_broadcasts SET status='cancelled' WHERE id=?",(sid,)); conn.commit()
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(f'scheduled_{sid}'): job.schedule_removal()
    await update.effective_message.reply_text(f'✅ Scheduled broadcast #{sid} cancelled.')

async def status_callback(update, context):
    q=update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer('⛔ Admin only.', show_alert=True)
        return
    await q.answer()
    # Reuse the same status content by generating it directly for callback context.
    commands = [
        ('/start', 'Main Menu'), ('/help', 'Help'), ('/profile', 'My Profile'), ('/mystats', 'My Statistics'),
        ('/history', 'My History'), ('/settings', 'Language Settings'), ('/support', 'User → Admin Support'),
        ('/admin', 'Admin Panel'), ('/stats', 'Dashboard'), ('/users', 'User List'), ('/user', 'User Details'),
        ('/searchuser', 'User Search'), ('/broadcast', 'Broadcast'), ('/reports', 'Reports'),
        ('/schedule /scheduled /cancelschedule', 'Scheduled Broadcast'), ('/reply', 'Admin Reply'),
        ('/maintenance', 'Maintenance'), ('/announce', 'Announcement'), ('/ping', 'Ping'), ('/restart', 'Restart'),
        ('/adminlist /addadmin /deladmin', 'Admin Management'), ('/activeusers /newuser /topuser /usercount', 'User Stats'),
    ]
    features = [
        ('📥 Downloader', feature_enabled('downloader')), ('📷 QR Scanner', feature_enabled('qr_scanner')),
        ('🔲 QR Generator', feature_enabled('qr_generator')), ('🆘 User Support', True),
        ('📢 Broadcast', True), ('⏰ Scheduled Broadcast', True), ('🚨 Error Monitoring', True),
        ('🛡️ Admin Commands', True), ('⚙️ Feature Settings', True),
    ]
    lines=['🧪 <b>BOT SYSTEM STATUS</b>\n', '<b>Commands</b>']
    lines += [f'🟢 <code>{html.escape(c)}</code> — {html.escape(d)}' for c,d in commands]
    lines.append('\n<b>Buttons / Features</b>')
    lines += [f"{'🟢 ON' if e else '🔴 OFF'} — {html.escape(n)}" for n,e in features]
    lines.append(f"\n🔧 Maintenance: <b>{'ON' if maintenance_enabled() else 'OFF'}</b>")
    await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=admin_keyboard())

async def settings_admin_callback(update, context):
    q=update.callback_query
    lines=['⚙️ *BOT SETTINGS*\n',f"🔧 Maintenance: *{'ON' if maintenance_enabled() else 'OFF'}*",f"🎵 Downloader: *{'ON' if feature_enabled('downloader') else 'OFF'}*",f"📷 QR Scanner: *{'ON' if feature_enabled('qr_scanner') else 'OFF'}*",f"🔲 QR Generator: *{'ON' if feature_enabled('qr_generator') else 'OFF'}*"]
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔧 Maintenance: {'ON' if maintenance_enabled() else 'OFF'}",callback_data='set_maintenance_toggle')],
        [InlineKeyboardButton(f"🎵 Downloader: {'ON' if feature_enabled('downloader') else 'OFF'}",callback_data='set_downloader_toggle')],
        [InlineKeyboardButton(f"📷 Scanner: {'ON' if feature_enabled('qr_scanner') else 'OFF'}",callback_data='set_scanner_toggle')],
        [InlineKeyboardButton(f"🔲 Generator: {'ON' if feature_enabled('qr_generator') else 'OFF'}",callback_data='set_generator_toggle')],
        [InlineKeyboardButton('🏠 Admin Panel',callback_data='admin_panel_home')]
    ])
    await q.edit_message_text('\n'.join(lines),parse_mode='Markdown',reply_markup=kb)

async def support_start(update, context):
    reset_modes(context)
    context.user_data['support_mode']=True
    user = update.effective_user
    lang = get_user_language(user.id) if user else 'en'
    if lang == 'bn':
        text = ('🆘 *সাপোর্ট*\n\nআপনার সমস্যাটি লিখে পাঠান। আপনার মেসেজটি অ্যাডমিনদের কাছে পাঠানো হবে।\n\n'
                'বাতিল করতে /start দিন।')
    else:
        text = ('🆘 *Support*\n\nPlease type your problem and send it. Your message will be sent to the admin.\n\n'
                'To cancel, send /start.')
    await update.effective_message.reply_text(text, parse_mode='Markdown')

async def support_message(update, context):
    if not context.user_data.get('support_mode'):
        return False
    user=update.effective_user
    text=(update.message.text or '').strip() if update.message else ''
    if not text:
        return True
    context.user_data['support_mode']=False

    # Keep a local support inbox record even if an admin chat cannot receive the message.
    with db_connect() as conn:
        cur = conn.execute(
            'INSERT INTO support_messages(user_id,message,created_at,delivered) VALUES(?,?,?,0)',
            (user.id, text[:4000], utc_now())
        )
        support_id = cur.lastrowid
        admin_rows = [r[0] for r in conn.execute('SELECT user_id FROM admins').fetchall()]
        conn.commit()

    # Include ADMIN_IDS directly as a safety net in case the database was created before an admin was added.
    admin_ids = set(admin_rows) | set(ADMIN_IDS)
    sent=0
    failed=[]
    for aid in sorted(admin_ids):
        try:
            await context.bot.send_message(
                aid,
                f'🆘 <b>NEW SUPPORT MESSAGE #{support_id}</b>\n\n'
                f'👤 {html.escape(user.first_name or "User")}\n'
                f'🆔 <code>{user.id}</code>\n'
                f'🔗 @{html.escape(user.username or "—")}\n\n'
                f'💬 {html.escape(text)}\n\n'
                f'<b>Reply:</b> /reply {user.id} &lt;message&gt;',
                parse_mode='HTML'
            )
            sent += 1
        except Exception as exc:
            failed.append((aid, repr(exc)))

    with db_connect() as conn:
        conn.execute('UPDATE support_messages SET delivered=? WHERE id=?', (1 if sent else 0, support_id))
        conn.commit()

    lang = get_user_language(user.id)
    if sent:
        msg = ('✅ আপনার মেসেজ অ্যাডমিনের কাছে পাঠানো হয়েছে।' if lang == 'bn'
               else '✅ Your message has been sent to the admin.')
    else:
        msg = ('⚠️ এই মুহূর্তে কোনো অ্যাডমিনের কাছে মেসেজ পৌঁছানো যায়নি।\n\n'
               'অ্যাডমিনকে অবশ্যই আগে এই বট-এ /start দিতে হবে।' if lang == 'bn' else
               '⚠️ The message could not be delivered to any admin right now.\n\n'
               'An admin must start the bot with /start first.')
    await update.message.reply_text(msg)
    return True

async def reply_user_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text('⛔ Admin only.'); return
    parts=update.message.text.partition(' ')[2].strip().split(' ',1)
    if len(parts)<2 or not parts[0].isdigit(): await update.effective_message.reply_text('Usage: /reply <user_id> <message>'); return
    uid=int(parts[0]); text=parts[1]
    try:
        await context.bot.send_message(uid,f'📩 *Admin Reply*\n\n{text}',parse_mode='Markdown')
        await update.effective_message.reply_text('✅ Reply sent.')
    except Exception as exc:
        await update.effective_message.reply_text(f'❌ Failed: {str(exc)[:300]}')

async def error_monitor(update, context):
    err=context.error
    uid=getattr(getattr(update,'effective_user',None),'id',None) if update else None
    save_error(uid, type(err).__name__, traceback.format_exc())
    with db_connect() as conn:
        admins=[r[0] for r in conn.execute('SELECT user_id FROM admins').fetchall()]
    snippet=str(err)[:700]
    for aid in admins:
        try: await context.bot.send_message(aid, f'🚨 *BOT ERROR*\n\nType: `{type(err).__name__}`\nUser: `{uid or "N/A"}`\n\n`{snippet}`',parse_mode='Markdown')
        except Exception: pass

async def reports_command(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text("⛔ Admin only."); return
    with db_connect() as conn: rows=conn.execute("SELECT kind,target_count,sent,failed,blocked,created_at FROM broadcast_reports ORDER BY id DESC LIMIT 10").fetchall()
    if not rows: await update.effective_message.reply_text("📈 No broadcast reports yet.",reply_markup=admin_keyboard()); return
    lines=['📈 *BROADCAST REPORTS*\n']
    for kind,target,sent,failed,blocked,created in rows: lines.append(f"• {kind} — {sent}/{target} sent, {failed} failed, {blocked} blocked\n  {format_dt(created)}")
    await update.effective_message.reply_text('\n'.join(lines),parse_mode='Markdown',reply_markup=admin_keyboard())

async def admin_callback(update, context):
    query=update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): await query.edit_message_text('⛔ Admin only.'); return
    d=query.data
    if d in {'admin_dashboard','admin_stats'}: await query.edit_message_text(await dashboard_text(),parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_users': await query.edit_message_text('👥 *USER MANAGEMENT*\n\n/searchuser <id or username>\n/user <telegram_id>\n/block <telegram_id>\n/unblock <telegram_id>',parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_search': await query.edit_message_text('🔍 *USER SEARCH*\n\nUse /searchuser <ID or username>',parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_reports':
        with db_connect() as conn: rows=conn.execute("SELECT kind,target_count,sent,failed,blocked,created_at FROM broadcast_reports ORDER BY id DESC LIMIT 10").fetchall()
        txt='📈 *BROADCAST REPORTS*\n\n'+('\n'.join(f'• {k}: {s}/{t} sent | {f} failed | {b} blocked\n  {format_dt(c)}' for k,t,s,f,b,c in rows) if rows else 'No reports yet.')
        await query.edit_message_text(txt,parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_broadcast': await query.edit_message_text('📢 *BROADCAST*\n\n/broadcast <text>\n/broadcast_media — reply to photo/video/document\n/broadcast_button Button Text | https://example.com',parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_maintenance': await query.edit_message_text(f"🔧 *MAINTENANCE*\n\nCurrent: *{'ON' if maintenance_enabled() else 'OFF'}*\n\n/maintenance on\n/maintenance off",parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_announce': await query.edit_message_text('📣 *ADMIN ANNOUNCEMENT*\n\n/announce <message>',parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d=='admin_settings': await settings_admin_callback(update, context)
    elif d=='admin_status': await status_callback(update, context)
    elif d=='admin_more_bots':
        await query.edit_message_text('🤖 <b>MORE BOTS MANAGER</b>\n\n/addbot @username | Bot Name | Description\n/bots — view and remove added bots\n\nOnly administrators can manage this list.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📋 View Added Bots', callback_data='admin_more_bots_list')],[InlineKeyboardButton('🏠 Admin Panel', callback_data='admin_panel_home')]]))
    elif d=='admin_more_bots_list':
        rows=get_more_bots(False)
        buttons=[]
        lines=['🤖 <b>MORE BOTS</b>\n']
        for bot_id,username,name,desc,enabled in rows:
            lines.append(f"{'🟢' if enabled else '🔴'} <b>{html.escape(name)}</b> — @{html.escape(username)}")
            buttons.append([InlineKeyboardButton(f'🗑️ Remove {name[:25]}', callback_data=f'morebot_delete_{bot_id}')])
        buttons.append([InlineKeyboardButton('🏠 Admin Panel', callback_data='admin_panel_home')])
        await query.edit_message_text('\n'.join(lines) if rows else '🤖 <b>MORE BOTS</b>\n\nNo bots added yet.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons))
    elif d=='admin_panel_home': await query.edit_message_text('🛠️ *ADVANCED ADMIN PANEL*\n\nChoose an option:',parse_mode='Markdown',reply_markup=admin_keyboard())
    elif d in {'set_maintenance_toggle','set_downloader_toggle','set_scanner_toggle','set_generator_toggle'}:
        keymap={'set_maintenance_toggle':'maintenance','set_downloader_toggle':'downloader','set_scanner_toggle':'qr_scanner','set_generator_toggle':'qr_generator'}
        key=keymap[d]
        if key=='maintenance': set_maintenance(not maintenance_enabled())
        else: set_setting(key,'0' if feature_enabled(key) else '1')
        await settings_admin_callback(update, context)

async def maintenance_guard(update, context):
    """Hard gate: blocked users and maintenance-mode users must not reach any later handler group."""
    user=update.effective_user
    if not user:
        return

    # Admins always retain access, including while maintenance mode is ON.
    if is_admin(user.id):
        return

    row = get_user_by_id(user.id)
    if row and row[9]:
        msg=update.effective_message
        if msg:
            await msg.reply_text("🚫 You are blocked from using this bot.\n\nPlease contact the administrator.")
        elif update.callback_query:
            await update.callback_query.answer("🚫 You are blocked from using this bot.", show_alert=True)
        raise ApplicationHandlerStop

    if maintenance_enabled():
        msg=update.effective_message
        if msg:
            await msg.reply_text("🔧 Bot maintenance mode is ON.\n\nPlease try again later.")
        elif update.callback_query:
            await update.callback_query.answer("🔧 Bot maintenance mode is ON.", show_alert=True)
        raise ApplicationHandlerStop

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
        if self.path.split("?", 1)[0] not in ("/", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        age = heartbeat_age()
        self.send_response(200 if age < 90 else 503)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"ASSISTANT BOT is running | heartbeat_age={age:.1f}s".encode())

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

def profile_keyboard():
    """Keyboard shown on My Profile: profile only, without history options."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

def user_features_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 𝗠𝘆 𝗣𝗿𝗼𝗳𝗶𝗹𝗲", callback_data="user_profile"),
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
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=profile_keyboard())

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
# IMAGE TOOLS
# ==================================================

def image_tools_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗜️ Compress", callback_data="image_compress"),
         InlineKeyboardButton("📐 Resize", callback_data="image_resize")],
        [InlineKeyboardButton("🔄 JPG", callback_data="image_convert_jpg"),
         InlineKeyboardButton("🟦 PNG", callback_data="image_convert_png"),
         InlineKeyboardButton("🌐 WebP", callback_data="image_convert_webp")],
        [InlineKeyboardButton("✂️ Crop Square", callback_data="image_crop")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

def image_mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Image Tools", callback_data="image_menu")],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])

async def image_tools_command(update, context):
    reset_modes(context)
    context.user_data["image_mode"] = "menu"
    await update.effective_message.reply_text(
        "🖼️ <b>IMAGE TOOLS</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🗜️ Compress image\n"
        "📐 Resize image\n"
        "🔄 Convert JPG / PNG / WebP\n"
        "✂️ Crop to square\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Choose an operation:",
        parse_mode="HTML", reply_markup=image_tools_keyboard()
    )

async def image_callback(update, context):
    query = update.callback_query
    await query.answer()
    d = query.data
    if d == "image_menu":
        context.user_data["image_mode"] = "menu"
        await query.edit_message_text(
            "🖼️ <b>IMAGE TOOLS</b>\n\nChoose an operation:",
            parse_mode="HTML", reply_markup=image_tools_keyboard()
        )
        return
    operations = {
        "image_compress": "compress",
        "image_resize": "resize",
        "image_convert_jpg": "jpg",
        "image_convert_png": "png",
        "image_convert_webp": "webp",
        "image_crop": "crop",
    }
    if d in operations:
        op = operations[d]
        context.user_data["image_mode"] = op
        if op == "resize":
            await query.edit_message_text(
                "📐 <b>RESIZE IMAGE</b>\n\nSend the target size first, like:\n<code>1280x720</code>\nThen send the image.\n\nThe image will be resized to fit inside those dimensions while keeping its aspect ratio.",
                parse_mode="HTML", reply_markup=image_mode_keyboard()
            )
        else:
            labels = {"compress":"🗜️ Compress", "jpg":"🔄 Convert to JPG", "png":"🟦 Convert to PNG", "webp":"🌐 Convert to WebP", "crop":"✂️ Crop Square"}
            await query.edit_message_text(
                f"{labels[op]}\n\n📸 Send the image you want to process.",
                reply_markup=image_mode_keyboard()
            )

def parse_resize(text):
    import re
    m = re.fullmatch(r"\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*", text or "")
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if not (16 <= w <= 10000 and 16 <= h <= 10000):
        return None
    return w, h

async def process_image(update, context):
    mode = context.user_data.get("image_mode")
    if not mode or mode == "menu":
        await image_tools_command(update, context)
        return
    msg = await update.effective_message.reply_text("🖼️ Processing image…")
    user_id = update.effective_user.id
    temp_dir = tempfile.mkdtemp(prefix=f"img_{user_id}_")
    src = os.path.join(temp_dir, f"source_{uuid.uuid4().hex}")
    out = None
    try:
        media = update.message.photo[-1] if update.message.photo else update.message.document
        if not media or not (update.message.photo or (update.message.document.mime_type or "").startswith("image/")):
            await msg.edit_text("❌ Please send a valid image.")
            return
        file = await context.bot.get_file(media.file_id)
        await file.download_to_drive(src)
        with Image.open(src) as im:
            im.load()
            original_size = im.size
            if mode == "resize":
                target = context.user_data.get("image_resize_target")
                if not target:
                    await msg.edit_text("📐 Send the target size first, for example: <code>1280x720</code>", parse_mode="HTML")
                    context.user_data["image_waiting_size"] = True
                    return
                tw, th = target
                image = ImageOps.contain(im, (tw, th), Image.Resampling.LANCZOS)
                ext, fmt, mime = "png", "PNG", "image/png"
                if im.mode in ("RGB", "L"):
                    ext, fmt, mime = "jpg", "JPEG", "image/jpeg"
                    image = image.convert("RGB")
            elif mode == "compress":
                image = im.copy()
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                ext, fmt, mime = "jpg", "JPEG", "image/jpeg"
            elif mode == "crop":
                image = ImageOps.fit(im, (min(im.size), min(im.size)), method=Image.Resampling.LANCZOS, centering=(0.5,0.5))
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                ext, fmt, mime = "jpg", "JPEG", "image/jpeg"
            else:
                image = im.copy()
                if mode == "jpg":
                    if image.mode not in ("RGB", "L"):
                        image = image.convert("RGB")
                    ext, fmt, mime = "jpg", "JPEG", "image/jpeg"
                elif mode == "png":
                    ext, fmt, mime = "png", "PNG", "image/png"
                else:
                    ext, fmt, mime = "webp", "WEBP", "image/webp"
                    if image.mode not in ("RGB", "RGBA", "L"):
                        image = image.convert("RGB")
            out = os.path.join(temp_dir, f"result_{uuid.uuid4().hex}.{ext}")
            save_kwargs = {"optimize": True}
            if fmt == "JPEG": save_kwargs.update(quality=72, progressive=True)
            elif fmt == "WEBP": save_kwargs.update(quality=80, method=6)
            image.save(out, format=fmt, **save_kwargs)
        before = os.path.getsize(src)
        after = os.path.getsize(out)
        await msg.edit_text(f"✅ <b>Done!</b>\n\n📏 {original_size[0]}×{original_size[1]}\n💾 {before/1024:.1f} KB → {after/1024:.1f} KB", parse_mode="HTML")
        with open(out, "rb") as fh:
            await update.effective_message.reply_document(document=fh, filename=os.path.basename(out), caption="🖼️ Image Tools • Completed")
        log_activity(user_id, "image_tool", mode)
    except Exception as exc:
        print("Image Tool Error:", repr(exc))
        try:
            await msg.edit_text("❌ <b>Image processing failed.</b>\n\nPlease try another image.", parse_mode="HTML")
        except Exception:
            pass
    finally:
        context.user_data["image_mode"] = None
        context.user_data["image_resize_target"] = None
        context.user_data["image_waiting_size"] = False
        try:
            for name in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, name))
            os.rmdir(temp_dir)
        except OSError:
            pass

# ==================================================
# MAIN MENU
# ==================================================

def get_main_keyboard(language="en", is_admin_user=False):
    # Clean, balanced home keyboard. Unicode bold keeps the labels distinctive
    # without relying on a custom font that Telegram clients may not support.
    labels = {
        "downloader": "📥 𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱𝗲𝗿",
        "scanner": "📷 𝗤𝗥 𝗦𝗰𝗮𝗻𝗻𝗲𝗿",
        "generator": "🔲 𝗤𝗥 𝗚𝗲𝗻𝗲𝗿𝗮𝘁𝗼𝗿",
        "image": "🖼️ 𝗜𝗺𝗮𝗴𝗲 𝗧𝗼𝗼𝗹𝘀",
        "stats": "📊 𝗠𝘆 𝗦𝘁𝗮𝘁𝘀",
        "profile": "👤 𝗠𝘆 𝗣𝗿𝗼𝗳𝗶𝗹𝗲",
        "more": "🤖 𝗠𝗼𝗿𝗲 𝗕𝗼𝘁𝘀",
        "history": "🕘 𝗠𝘆 𝗛𝗶𝘀𝘁𝗼𝗿𝘆",
        "settings": "⚙️ 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀",
        "help": "❓ 𝗛𝗲𝗹𝗽",
        "admin": "🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀",
    }

    def btn(text, style=None):
        return KeyboardButton(text, style=style) if style else KeyboardButton(text)

    keyboard = [
        [btn(labels["downloader"], "primary"), btn(labels["scanner"], "primary")],
        [btn(labels["generator"], "primary"), btn(labels["image"], "success")],
        [btn(labels["more"], "success"), btn(labels["profile"])],
        [btn(labels["stats"]), btn(labels["history"])],
        [btn(labels["settings"]), btn(labels["help"])],
    ]
    if is_admin_user:
        keyboard.append([btn(labels["admin"], "primary")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

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
    text = ("🤖 *𝗔𝗦𝗦𝗜𝗦𝗧𝗔𝗡𝗧 𝗕𝗢𝗧*-এ স্বাগতম! ✨\n\n━━━━━━━━━━━━━━━━━━\n🚀 আপনার *All-in-One Telegram Utility Hub*\n━━━━━━━━━━━━━━━━━━\n\n📥 *𝗩𝗜𝗗𝗘𝗢 𝗧𝗢𝗢𝗟𝗦*\nDownloader\n\n📷 *𝗤𝗥 𝗧𝗢𝗢𝗟𝗦*\nQR Scanner • QR Generator\n\n👤 *𝗔𝗖𝗖𝗢𝗨𝗡𝗧*\nProfile • Statistics • History\n\n🤖 *𝗠𝗢𝗥𝗘*\nMore Bots\n\n⚙️ *𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦*\nLanguage & preferences\n\n✨ নিচের সুন্দর Menu থেকে একটি অপশন নির্বাচন করুন।") if lang == "bn" else ("🤖 *𝗔𝗦𝗦𝗜𝗦𝗧𝗔𝗡𝗧 𝗕𝗢𝗧* — Welcome! ✨\n\n━━━━━━━━━━━━━━━━━━\n🚀 Your *All-in-One Telegram Utility Hub*\n━━━━━━━━━━━━━━━━━━\n\n📥 *𝗩𝗜𝗗𝗘𝗢 𝗧𝗢𝗢𝗟𝗦*\nDownloader\n\n📷 *𝗤𝗥 𝗧𝗢𝗢𝗟𝗦*\nQR Scanner • QR Generator\n\n👤 *𝗔𝗖𝗖𝗢𝗨𝗡𝗧*\nProfile • Statistics • History\n\n🤖 *𝗠𝗢𝗥𝗘*\nMore Bots\n\n⚙️ *𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦*\nLanguage & preferences\n\n✨ Choose an option from the menu below.")
    await update.effective_message.reply_text(text, reply_markup=get_main_keyboard(lang, bool(user and is_admin(user.id))), parse_mode="Markdown")

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
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
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
        await query.message.reply_text("✅ ভাষা বাংলা করা হয়েছে।" if lang == "bn" else "✅ Language changed to English.", reply_markup=get_main_keyboard(lang, is_admin(user.id)))

def admin_help_keyboard(page=1):
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_help_{page-1}"))
    if page < 3:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_help_{page+1}"))
    rows = [buttons] if buttons else []
    rows.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin_dashboard"),
                 InlineKeyboardButton("🏠 Home", callback_data="ui_home")])
    return InlineKeyboardMarkup(rows)

ADMIN_HELP_PAGES = {
    1: (
        "🛡️ <b>ADMIN COMMANDS — 1/3</b>\n\n"
        "📊 <b>Dashboard & Statistics</b>\n"
        "/admin — Open Admin Panel\n"
        "/stats — Dashboard/statistics overview\n"
        "/users — Latest users list\n"
        "/activeusers — Users active in last 24h\n"
        "/newuser — New users today\n"
        "/topuser — Most active users\n"
        "/usercount — Total and blocked user count\n"
        "/reports — Broadcast delivery reports\n\n"
        "👥 <b>User Management</b>\n"
        "/searchuser &lt;id|username&gt; — Search a user\n"
        "/user &lt;id&gt; — View user details\n\n"
        "🚫 <b>User Control</b>\n"
        "/block &lt;id&gt; — Block a user\n"
        "/unblock &lt;id&gt; — Remove block\n"
        "/ban &lt;id&gt; — Ban (same block system)\n"
        "/unban &lt;id&gt; — Remove ban\n"
    ),
    2: (
        "🛡️ <b>ADMIN COMMANDS — 2/3</b>\n\n"
        "👑 <b>Admin Management</b>\n"
        "/adminlist — Show admin list\n"
        "/addadmin &lt;id&gt; — Add an admin\n"
        "/deladmin &lt;id&gt; — Remove a database admin\n\n"
        "📢 <b>Broadcast</b>\n"
        "/broadcast &lt;text&gt; — Text broadcast\n"
        "/broadcast_media — Broadcast replied photo/video/document\n"
        "/broadcast_button — Broadcast with URL button\n"
        "/announce &lt;message&gt; — Send admin announcement\n"
        "/reports — View recent broadcast reports\n\n"
        "🔧 <b>Bot Control</b>\n"
        "/maintenance on|off — Enable/disable maintenance mode\n/status — Live command & button feature status\n"
        "/restart — Restart the Render service process\n"
        "/ping — Check bot response time\n"
    ),
    3: (
        "🛡️ <b>ADMIN COMMANDS — 3/3</b>\n\n"
        "ℹ️ <b>Information & Help</b>\n"
        "/about — Bot information\n"
        "/support — Support information\n"
        "/helpuser — Show user commands\n"
        "/helpadmin — Show admin command list\n"
        "/id or /myid — Show your Telegram ID\n\n"
        "📌 <b>Quick Examples</b>\n"
        "/block 123456789\n"
        "/unblock 123456789\n"
        "/user 123456789\n"
        "/searchuser 123456789\n"
        "/addadmin 123456789\n"
        "/maintenance on\n"
        "/broadcast Hello everyone!\n"
        "/announce Important notice\n\n"
        "🔐 <b>Security:</b> These commands are available only to verified admins.\n"
        "⚠️ Admin IDs configured in Render <code>ADMIN_IDS</code> cannot be removed with /deladmin."
    ),
}

async def admin_help_button(update, context):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return True
    await update.effective_message.reply_text(ADMIN_HELP_PAGES[1], parse_mode="HTML", reply_markup=admin_help_keyboard(1))
    return True

async def admin_help_callback(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    try:
        page = int(query.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        page = 1
    page = max(1, min(3, page))
    await query.answer()
    await query.edit_message_text(ADMIN_HELP_PAGES[page], parse_mode="HTML", reply_markup=admin_help_keyboard(page))

async def settings_command(update, context):
    await update.effective_message.reply_text("⚙️ *Settings*\n\nChoose your language:", reply_markup=settings_keyboard(), parse_mode="Markdown")

async def help_command(update, context):
    await update.effective_message.reply_text("❓ *Help*\n\n📥 Downloader: choose a downloader and send a supported link.\n📷 QR Scanner: select it, then send a QR image.\n🔲 QR Generator: select it, then send text/link.\n🤖 More Bots: discover other bots added by the admin.\n⚙️ Settings: change language.", reply_markup=help_keyboard(), parse_mode="Markdown")

async def ui_text_action(update, context, text):
    user = update.effective_user
    lang = get_user_language(user.id)
    if text == "📥 𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱𝗲𝗿":
        reset_modes(context)
        await update.message.reply_text("📥 *Downloader*\n\nChoose a downloader:", reply_markup=downloader_keyboard(), parse_mode="Markdown")
        return True
    if text == "📷 𝗤𝗥 𝗦𝗰𝗮𝗻𝗻𝗲𝗿":
        await qr_scanner_start(update, context); return True
    if text == "🔲 𝗤𝗥 𝗚𝗲𝗻𝗲𝗿𝗮𝘁𝗼𝗿":
        await qr_generator_start(update, context); return True
    if text == "🖼️ 𝗜𝗺𝗮𝗴𝗲 𝗧𝗼𝗼𝗹𝘀":
        await image_tools_command(update, context); return True
    if text == "📊 𝗠𝘆 𝗦𝘁𝗮𝘁𝘀":
        messages, scans, generated, downloads = get_user_stats(user.id)
        msg = f"📊 *My Statistics*\n\n💬 Messages: *{messages}*\n📷 QR Scans: *{scans}*\n🔲 QR Generated: *{generated}*\n🎵 TikTok Downloads: *{downloads}*"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang, is_admin(user.id)), parse_mode="Markdown"); return True
    if text == "👤 𝗠𝘆 𝗣𝗿𝗼𝗳𝗶𝗹𝗲":
        await user_profile_command(update, context); return True
    if text == "🤖 𝗠𝗼𝗿𝗲 𝗕𝗼𝘁𝘀":
        await more_bots_command(update, context); return True
    if text == "🕘 𝗠𝘆 𝗛𝗶𝘀𝘁𝗼𝗿𝘆":
        await user_features_command(update, context); return True
    if text == "⚙️ 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀":
        await settings_command(update, context); return True
    if text == "❓ 𝗛𝗲𝗹𝗽":
        await help_command(update, context); return True
    if text == "🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀":
        return await admin_help_button(update, context)
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

    context.user_data["image_mode"] = None
    context.user_data["image_waiting_size"] = False
    context.user_data["image_resize_target"] = None


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

    if not feature_enabled('qr_scanner') and not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('📷 QR Scanner is temporarily disabled by Admin.')
        return
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

    if not feature_enabled('qr_generator') and not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('🔲 QR Generator is temporarily disabled by Admin.')
        return
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

    if not feature_enabled('downloader') and not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('🎵 Downloader is temporarily disabled by Admin.')
        return
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

    # UI buttons must be handled before support mode. Otherwise the button label itself
    # is forwarded to admins as if it were a support message.
    if await ui_text_action(update, context, text):
        return
    if await support_message(update, context):
        return

    if context.user_data.get("image_waiting_size"):
        target = parse_resize(text)
        if not target:
            await update.message.reply_text("❌ Invalid size. Use format like <code>1280x720</code>.", parse_mode="HTML")
            return
        context.user_data["image_resize_target"] = target
        context.user_data["image_waiting_size"] = False
        await update.message.reply_text(f"✅ Size set to <b>{target[0]}×{target[1]}</b>. Now send the image.", parse_mode="HTML", reply_markup=image_mode_keyboard())
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
# RUNTIME HEALTH / AUTO RECOVERY
# ==================================================

async def runtime_heartbeat(context):
    touch_heartbeat()

def recovery_watchdog():
    global RECOVERY_RESTARTING
    # Separate thread: if the asyncio loop/polling layer stops making progress
    # for a sustained period, replace the process so Render can bring it back.
    while True:
        time.sleep(30)
        if heartbeat_age() > 180 and not RECOVERY_RESTARTING:
            RECOVERY_RESTARTING = True
            print("🚨 Bot heartbeat stale for >180s; restarting process for recovery...")
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as exc:
                print(f"Recovery restart failed: {exc!r}")
                RECOVERY_RESTARTING = False


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

    # Phase 5: maintenance guard for non-admin users
    app.add_handler(MessageHandler(filters.ALL, maintenance_guard), group=-2)

    # Phase 3: track every update before normal handlers
    app.add_handler(MessageHandler(filters.ALL, track_update), group=-1)

    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("id", myid_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("broadcast_media", broadcast_media_command))
    app.add_handler(CommandHandler("broadcast_button", broadcast_button_command))
    app.add_handler(CommandHandler("reports", reports_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("user", user_details_command))
    app.add_handler(CommandHandler("searchuser", searchuser_command))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("adminlist", admin_list_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("deladmin", del_admin_command))
    app.add_handler(CommandHandler("activeusers", activeusers_command))
    app.add_handler(CommandHandler("newuser", newuser_command))
    app.add_handler(CommandHandler("topuser", topuser_command))
    app.add_handler(CommandHandler("usercount", usercount_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("helpuser", helpuser_command))
    app.add_handler(CommandHandler("helpadmin", helpadmin_command))
    app.add_handler(CommandHandler("maintenance", maintenance_command))
    app.add_handler(CommandHandler("announce", announce_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("scheduled", scheduled_list_command))
    app.add_handler(CommandHandler("cancelschedule", cancel_schedule_command))
    app.add_handler(CommandHandler("reply", reply_user_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", user_profile_command))
    app.add_handler(CommandHandler("mystats", user_stats_view))
    app.add_handler(CommandHandler("history", user_features_command))
    app.add_handler(CommandHandler("morebots", more_bots_command))
    app.add_handler(CommandHandler("imagetools", image_tools_command))
    app.add_handler(CommandHandler("addbot", addbot_command))
    app.add_handler(CommandHandler("bots", bots_command))
    app.add_handler(CallbackQueryHandler(ui_callback, pattern=r"^(ui_|lang_|user_profile$|user_stats$|hist_(downloads|scans|generates|activity)$)"))
    app.add_handler(CallbackQueryHandler(admin_help_callback, pattern=r"^admin_help_[123]$"))
    app.add_handler(CallbackQueryHandler(morebot_callback, pattern=r"^morebot_(view|delete)_\d+$|^more_bots$"))
    app.add_handler(CallbackQueryHandler(image_callback, pattern=r"^image_(menu|compress|resize|convert_jpg|convert_png|convert_webp|crop)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^(admin_|set_)"))

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

    async def handle_media_image(update, context):
        if context.user_data.get("image_mode") in {"compress", "resize", "jpg", "png", "webp", "crop"}:
            if context.user_data.get("image_mode") == "resize" and not context.user_data.get("image_resize_target"):
                await update.effective_message.reply_text("📐 Please send the target size first, e.g. <code>1280x720</code>.", parse_mode="HTML")
                context.user_data["image_waiting_size"] = True
                return
            await process_image(update, context)
        else:
            await handle_qr_image(update, context)

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media_image))

    # ==========================================
    # START BOT
    # ==========================================

    print(
        "🤖 Assistant Bot is running..."
    )

    # Render Web Service needs an open port.
    Thread(target=start_web_server, daemon=True).start()
    Thread(target=recovery_watchdog, daemon=True).start()

    app.add_error_handler(error_monitor)
    # Automatic command menu: users do not need /start to discover the latest commands.
    async def post_init(application):
        from telegram import BotCommand, BotCommandScopeChat
        touch_heartbeat()
        if application.job_queue:
            application.job_queue.run_repeating(runtime_heartbeat, interval=30, first=0, name="runtime_heartbeat")
        default_commands = [
            BotCommand("start", "Open main menu"), BotCommand("help", "Help"), BotCommand("profile", "My Profile"),
            BotCommand("mystats", "My Statistics"), BotCommand("history", "My History"), BotCommand("settings", "Settings"),
            BotCommand("support", "Contact Admin"), BotCommand("morebots", "More Bots"), BotCommand("imagetools", "Image Tools")
        ]
        await application.bot.set_my_commands(default_commands)
        admin_commands = default_commands + [BotCommand("admin", "Admin Panel"), BotCommand("status", "Admin system status"), BotCommand("addbot", "Add More Bot"), BotCommand("bots", "Manage More Bots")]
        for aid in ADMIN_IDS:
            try:
                await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=aid))
            except Exception:
                pass
        if application.job_queue:
            # Restore pending schedules after a Render restart.
            with db_connect() as conn:
                rows=conn.execute("SELECT id,admin_id,message,run_at FROM scheduled_broadcasts WHERE status='scheduled'").fetchall()
            now=datetime.now(timezone.utc)
            for sid,aid,msg,run in rows:
                try:
                    when=datetime.fromisoformat(run)
                    if when <= now:
                        when=now+timedelta(seconds=2)
                    application.job_queue.run_once(scheduled_broadcast_job, when=when, data={'id':sid,'admin_id':aid,'message':msg}, name=f'scheduled_{sid}')
                except Exception as exc:
                    save_error(aid, "schedule_restore", repr(exc))
    app.post_init = post_init
    try:
        app.run_polling()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        save_error(0, "polling_crash", traceback.format_exc())
        print(f"🚨 Polling stopped unexpectedly: {exc!r}")
        # Replace the process so Render sees a fresh bot process.
        os.execv(sys.executable, [sys.executable] + sys.argv)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":

    main()
