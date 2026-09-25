import os
import yt_dlp


def get_video_info(url):

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(
            url,
            download=False
        )


def get_quality_formats(info):

    formats = []

    for fmt in info.get("formats", []):

        # Video formats only
        if fmt.get("vcodec") == "none":
            continue

        height = fmt.get("height")

        if not height:
            continue

        # Avoid duplicate resolutions
        if height not in [
            item["height"]
            for item in formats
        ]:

            formats.append({
                "height": height,
                "format_id": fmt["format_id"]
            })

    formats.sort(
        key=lambda x: x["height"]
    )

    return formats


def download_video(
    url,
    output_path,
    max_height
):

    options = {

        # Prefer non-watermarked video formats
        "format": (
            f"bv*[height<={max_height}]"
            "[vcodec!=none]"
            "+ba/b"
        ),

        "outtmpl": output_path,

        "merge_output_format": "mp4",

        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        ydl.download([
            url
        ])

    return output_path