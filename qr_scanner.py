"""Robust QR-code image scanner for Telegram.

Uses OpenCV and pyzbar/ZBar with multiple preprocessing passes, rotations,
upscaling and border/contrast variants so consecutive Telegram images are
handled independently and difficult QR images have a better chance to decode.
"""
from __future__ import annotations

import os
import cv2
from PIL import Image, ImageOps

try:
    from pyzbar.pyzbar import ZBarSymbol, decode as zbar_decode
    PYZBAR_AVAILABLE = True
except Exception:
    ZBarSymbol = None
    zbar_decode = None
    PYZBAR_AVAILABLE = False


def _unique(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _opencv_decode(image) -> list[str]:
    out = []
    detector = cv2.QRCodeDetector()
    try:
        data, _, _ = detector.detectAndDecode(image)
        if data:
            out.append(data)
    except Exception:
        pass
    try:
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(image)
        if ok and decoded_info:
            out.extend(x for x in decoded_info if x)
    except Exception:
        pass
    return _unique(out)


def _zbar_decode(image) -> list[str]:
    if not PYZBAR_AVAILABLE:
        return []
    out = []
    try:
        for item in zbar_decode(image, symbols=[ZBarSymbol.QRCODE]):
            try:
                out.append(item.data.decode("utf-8", errors="replace"))
            except Exception:
                out.append(str(item.data))
    except Exception:
        pass
    return _unique(out)


def _add_border(img, px=40):
    if img.ndim == 2:
        return cv2.copyMakeBorder(img, px, px, px, px, cv2.BORDER_CONSTANT, value=255)
    return cv2.copyMakeBorder(img, px, px, px, px, cv2.BORDER_CONSTANT, value=(255,255,255))


def _variants(image):
    """Generate bounded, independent variants. No shared mutable state."""
    if image is None:
        return

    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()

    # Original + grayscale + border.
    yield image
    yield gray
    yield _add_border(gray, max(20, min(h, w)//40))

    # Correct uneven lighting / contrast.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    yield enhanced

    # Light denoise + sharpen.
    blur = cv2.GaussianBlur(enhanced, (3, 3), 0)
    sharp = cv2.addWeighted(enhanced, 1.6, blur, -0.6, 0)
    yield sharp

    # Threshold variants.
    for block, c in ((21, 3), (31, 5), (51, 7)):
        try:
            yield cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, block, c)
        except Exception:
            pass
    try:
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield otsu
        yield cv2.bitwise_not(otsu)
    except Exception:
        pass

    # Upscale small/medium images.
    longest = max(h, w)
    if longest < 1800:
        scale = 3.0 if longest < 800 else 2.0
        up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        yield up
        yield _add_border(up, 50)

    # Rotations help when Telegram/photo orientation is unusual.
    for angle in (90, 180, 270):
        rot = cv2.rotate(image, {90: cv2.ROTATE_90_CLOCKWISE,
                                 180: cv2.ROTATE_180,
                                 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[angle])
        yield rot
        yield cv2.cvtColor(rot, cv2.COLOR_BGR2GRAY) if len(rot.shape) == 3 else rot


def _load_image(image_path: str):
    # Pillow path handles EXIF orientation and formats OpenCV may reject.
    try:
        pil = Image.open(image_path)
        pil = ImageOps.exif_transpose(pil).convert("RGB")
        import numpy as np
        return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        image = cv2.imread(image_path, cv2.IMREAD_COLOR)
        return image


def scan_qr(image_path: str) -> list[str]:
    if not image_path or not os.path.exists(image_path):
        return []

    image = _load_image(image_path)
    if image is None:
        return []

    h, w = image.shape[:2]
    longest = max(h, w)
    if longest > 2800:
        scale = 2800 / longest
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    # First pass: pyzbar is often better on skewed/photographed QR codes.
    # It returns all symbols it can decode.
    for variant in _variants(image):
        results = _zbar_decode(variant)
        if results:
            return results

    # Second pass: OpenCV handles UTF-8 and many clean QR screenshots well.
    for variant in _variants(image):
        results = _opencv_decode(variant)
        if results:
            return results

    return []
