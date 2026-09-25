import os
import shutil
import yt_dlp


def _base_options():
    """Common yt-dlp options for TikTok."""
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            )
        },
    }


def get_video_info(url):
    options = _base_options()
    options["skip_download"] = True

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def get_quality_formats(info):
    """
    Return unique video heights that yt-dlp actually reports.
    Prefer formats containing both video and audio when possible.
    """
    formats = {}

    for fmt in info.get("formats", []):
        if fmt.get("vcodec") in (None, "none"):
            continue

        height = fmt.get("height")
        if not height:
            continue

        # Prefer a combined video+audio format for the same resolution.
        has_audio = fmt.get("acodec") not in (None, "none")

        if height not in formats or has_audio:
            formats[height] = {
                "height": height,
                "format_id": fmt.get("format_id"),
                "has_audio": has_audio,
            }

    return sorted(formats.values(), key=lambda x: x["height"])


def _has_ffmpeg():
    return bool(shutil.which("ffmpeg"))


def download_video(url, output_path, max_height):
    """
    Download a TikTok video up to max_height.

    If ffmpeg is installed, use separate video/audio streams when available.
    If ffmpeg is not installed, fall back to a single combined stream so the
    bot can still download videos on hosts such as Render without ffmpeg.
    """
    options = _base_options()
    options["outtmpl"] = output_path
    options["merge_output_format"] = "mp4"

    if _has_ffmpeg():
        # Best video up to requested resolution + best audio, with a
        # single-file fallback. This follows yt-dlp's documented format
        # selection pattern.
        options["format"] = (
            f"bv*[height<={int(max_height)}]+ba/"
            f"b[height<={int(max_height)}]/"
            "bv*+ba/b"
        )
    else:
        # No ffmpeg: do NOT request a video-only + audio-only merge.
        # Select a single format that already contains both streams.
        options["format"] = (
            f"b[height<={int(max_height)}]/"
            f"b[height<={int(max_height)}][ext=mp4]/"
            "b"
        )

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    # yt-dlp may adjust the final extension during post-processing.
    if os.path.exists(output_path):
        return output_path

    base, _ = os.path.splitext(output_path)
    for ext in (".mp4", ".webm", ".mkv", ".mov"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError("yt-dlp finished but the downloaded video file was not found.")
