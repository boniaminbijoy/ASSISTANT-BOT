"""QR-code generation with an optional centered bot logo."""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageOps, ImageDraw


def generate_qr(data: str, output_path: str, logo_path: str | None = None) -> str:
    data = str(data).strip()
    if not data:
        raise ValueError("QR data cannot be empty")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    qr_image = qr.make_image(
        fill_color="black",
        back_color="white",
    ).convert("RGBA")

    if logo_path and Path(logo_path).exists():
        logo = Image.open(logo_path)
        logo = ImageOps.exif_transpose(logo).convert("RGBA")

        # Keep the logo deliberately small. ERROR_CORRECT_H can recover from
        # some occlusion, but a large logo can still make a QR unreadable.
        max_logo = int(min(qr_image.size) * 0.14)
        logo.thumbnail((max_logo, max_logo), Image.Resampling.LANCZOS)

        padding = max(10, max_logo // 8)
        box_size = logo.width + padding * 2

        logo_box = Image.new("RGBA", (box_size, box_size), "white")
        # A subtle rounded white plate gives the QR modules a clean quiet area.
        mask = Image.new("L", (box_size, box_size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, box_size - 1, box_size - 1), radius=max(8, padding), fill=255)
        logo_box.putalpha(mask)
        logo_box.paste(logo, (padding, padding), logo)

        x = (qr_image.width - logo_box.width) // 2
        y = (qr_image.height - logo_box.height) // 2
        qr_image.alpha_composite(logo_box, (x, y))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    qr_image.convert("RGB").save(output, "PNG", optimize=True)
    return str(output)
