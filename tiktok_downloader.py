import os
import shutil
import time
import threading
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget


TIKTOK_USER_AGENT = os.environ.get(
    "TIKTOK_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
)

# TikTok has recently had TLS/browser-fingerprint related breakages.
# chrome-131 is used because it is a broadly available curl-cffi target
# and avoids newer TikTok fingerprint regressions.
TIKTOK_IMPERSONATE = os.environ.get("TIKTOK_IMPERSONATE", "chrome-131")
TIKTOK_COOKIES_FILE = os.environ.get("TIKTOK_COOKIES_FILE")

# yt-dlp/TikTok can intermittently reject sequential requests. Serialize
# extraction/download per process and retry with a fresh YoutubeDL instance.
_TIKTOK_LOCK = threading.Lock()



def _base_options():
    options = {
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout": 45,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "http_headers": {
            "User-Agent": TIKTOK_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    # Requires curl-cffi (included in the updated requirements).
    # If the target is unavailable, yt-dlp will raise a clear error rather
    # than silently pretending the extraction succeeded.
    if TIKTOK_IMPERSONATE:
        options["impersonate"] = ImpersonateTarget.from_str(TIKTOK_IMPERSONATE.lower())

    # On some server IPs TikTok blocks anonymous extraction. A Netscape
    # cookies file from a TikTok session can be supplied through Render as
    # TIKTOK_COOKIES_FILE=/app/secrets/tiktok.txt
    if TIKTOK_COOKIES_FILE and os.path.isfile(TIKTOK_COOKIES_FILE):
        options["cookiefile"] = TIKTOK_COOKIES_FILE

    return options


def get_video_info(url):
    last_error = None
    for attempt in range(1, 4):
        try:
            with _TIKTOK_LOCK:
                options = _base_options()
                options["skip_download"] = True
                with yt_dlp.YoutubeDL(options) as ydl:
                    return ydl.extract_info(url, download=False)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.5 * attempt)
    raise last_error

def get_quality_formats(info):
    formats = {}

    for fmt in info.get("formats", []):
        if fmt.get("vcodec") in (None, "none"):
            continue

        height = fmt.get("height")
        if not height:
            continue

        # Prefer a combined video+audio format for the same resolution.
        has_audio = fmt.get("acodec") not in (None, "none")
        current = formats.get(height)

        if current is None or (has_audio and not current["has_audio"]):
            formats[height] = {
                "height": int(height),
                "format_id": fmt.get("format_id"),
                "has_audio": has_audio,
            }

    return sorted(formats.values(), key=lambda x: x["height"])


def _has_ffmpeg():
    return shutil.which("ffmpeg") is not None


def download_video(url, output_path, max_height, progress_callback=None):
    max_height = int(max_height)
    last_error = None

    for attempt in range(1, 4):
        try:
            with _TIKTOK_LOCK:
                options = _base_options()
                options["outtmpl"] = output_path
                options["merge_output_format"] = "mp4"

                if progress_callback:
                    options["progress_hooks"] = [progress_callback]

                if _has_ffmpeg():
                    options["format"] = (
                        f"bv*[height<={max_height}]+ba/"
                        f"b[height<={max_height}]/"
                        "bv*+ba/b"
                    )
                else:
                    options["format"] = f"b[height<={max_height}]/b"

                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.download([url])

            if os.path.exists(output_path):
                return output_path

            base, _ = os.path.splitext(output_path)
            for ext in (".mp4", ".webm", ".mkv", ".mov"):
                candidate = base + ext
                if os.path.exists(candidate):
                    return candidate

            raise FileNotFoundError(
                "yt-dlp finished but the downloaded video file was not found."
            )
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2.0 * attempt)

    raise last_error

