import os
import asyncio
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import (
    Update,
    ReplyKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
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

BOT_TOKEN = os.environ.get("8645536260:AAG6iyze2ukVTXCdJ8lsZckDmA443Lm6VDg")

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
# MAIN MENU
# ==================================================

def get_main_keyboard():

    keyboard = [
        ["📷 QR CODE SCANNER"],
        ["🔳 QR CODE GENERATOR"],
        ["🎵 TIKTOK DOWNLOADER"]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


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

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Turn off all modes
    reset_modes(context)

    start_text = """
🤖 **Welcome to ASSISTANT BOT!**

Your smart all-in-one Telegram assistant 🚀

✨ **Available Tools:**

📷 **QR Code Scanner** — Scan and read QR codes instantly

🔲 **QR Code Generator** — Create QR codes from text or links

🎵 **TikTok Downloader** — Download TikTok videos from links

🛠️ **More Tools** — More useful features coming soon!

👇 **Choose an option below to get started:**

⚡ Fast • Simple • Easy to Use
"""

    await update.message.reply_text(
        start_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ==================================================
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

    # ==========================================
    # QR SCANNER MODE CHECK
    # ==========================================

    if not context.user_data.get(
        "qr_mode",
        False
    ):

        await update.message.reply_text(
            "ℹ️ **Please select 📷 QR CODE SCANNER first.**",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )

        return

    # ==========================================
    # SHOW DETECTING MESSAGE
    # ==========================================

    detecting_message = await update.message.reply_text(
        "🔍 **Detecting QR Code...**\n\n"
        "⏳ Please wait...",
        parse_mode="Markdown"
    )

    # ==========================================
    # GET IMAGE
    # ==========================================

    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    image_path = (
        f"qr_{update.message.from_user.id}.jpg"
    )

    # Download image
    await file.download_to_drive(
        image_path
    )

    try:

        # ==========================================
        # 2 SECOND DETECTING DELAY
        # ==========================================

        await asyncio.sleep(2)

        # ==========================================
        # SCAN QR CODE
        # ==========================================

        result = scan_qr(
            image_path
        )

        # ==========================================
        # QR FOUND
        # ==========================================

        if result:

            await detecting_message.edit_text(
                "✅ **QR CODE DETECTED!**\n\n"
                "🔗 **Result:**\n\n"
                f"`{result}`\n\n"
                "📷 Send another QR image to scan again.",
                parse_mode="Markdown"
            )

        # ==========================================
        # QR NOT FOUND
        # ==========================================

        else:

            await detecting_message.edit_text(
                "❌ **QR CODE NOT FOUND**\n\n"
                "I couldn't find a readable QR Code in this image.\n\n"
                "💡 Try sending a clearer image.",
                parse_mode="Markdown"
            )

    except Exception as e:

        print(
            "QR Scanner Error:",
            e
        )

        await detecting_message.edit_text(
            "❌ **QR SCAN FAILED**\n\n"
            "Something went wrong while scanning the image.",
            parse_mode="Markdown"
        )

    finally:

        # Delete temporary image
        if os.path.exists(image_path):

            os.remove(
                image_path
            )


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

    data = update.message.text.strip()

    # Empty text
    if not data:

        await update.message.reply_text(
            "❌ Please send some text or a link.",
            reply_markup=get_main_keyboard()
        )

        return

    # Show generating message
    generating_message = await update.message.reply_text(
        "🔳 **Generating QR Code...**\n\n"
        "⏳ Please wait...",
        parse_mode="Markdown"
    )

    user_id = update.message.from_user.id

    logo_path = (
        f"bot_logo_{user_id}.jpg"
    )

    qr_path = (
        f"generated_qr_{user_id}.png"
    )

    try:

        # ==========================================
        # DOWNLOAD BOT PROFILE PICTURE
        # ==========================================

        logo_found = await download_bot_profile_picture(
            context,
            logo_path
        )

        # ==========================================
        # GENERATE QR WITH BOT LOGO
        # ==========================================

        if logo_found:

            generate_qr(
                data,
                qr_path,
                logo_path
            )

        else:

            # If bot has no profile picture
            generate_qr(
                data,
                qr_path
            )

        # Delete generating message
        await generating_message.delete()

        # ==========================================
        # SEND QR CODE
        # ==========================================

        with open(
            qr_path,
            "rb"
        ) as qr_file:

            await update.message.reply_photo(
                photo=qr_file,

                caption=(
                    "✅ **QR CODE GENERATED!**\n\n"
                    "🔗 **Data:**\n"
                    f"`{data}`"
                ),

                reply_markup=get_main_keyboard(),

                parse_mode="Markdown"
            )

    except Exception as e:

        print(
            "QR Generator Error:",
            e
        )

        await generating_message.edit_text(
            "❌ **QR GENERATION FAILED**\n\n"
            "Something went wrong. Please try again.",
            parse_mode="Markdown"
        )

    finally:

        # Delete temporary logo
        if os.path.exists(logo_path):

            os.remove(
                logo_path
            )

        # Delete temporary QR
        if os.path.exists(qr_path):

            os.remove(
                qr_path
            )

        # Reset generator data
        context.user_data[
            "qr_generator_data"
        ] = None


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
        "🤖 The bot will automatically add "
        "its own profile picture to the QR Code.",
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

        print(
            "TikTok Info Error:",
            e
        )

        await checking_message.edit_text(
            "❌ **Could not process this TikTok link.**\n\n"
            "💡 Make sure the video is public and "
            "the link is correct.",
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
        f"tiktok_{user_id}.mp4"
    )

    try:

        # ==========================================
        # DOWNLOAD VIDEO
        # ==========================================

        await asyncio.to_thread(
            download_video,
            url,
            output_path,
            selected_height
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

        # Delete status message
        await downloading_message.delete()

    except Exception as e:

        print(
            "TikTok Download Error:",
            e
        )

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
    # TIKTOK LINK
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


# ==================================================
# MAIN
# ==================================================

def main():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

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
            filters.PHOTO,
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