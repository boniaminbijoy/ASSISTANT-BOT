"""Robust QR-code image scanner for the Telegram bot.

Uses OpenCV first and ZBar/pyzbar as a fallback. Several lightweight
pre-processing passes are attempted so screenshots, slightly blurred images,
and rotated QR codes have a better chance of being decoded.
"""

from __future__ import annotations

import os
from typing import List

import cv2
from PIL import Image, ImageOps
# pyzbar is optional at import time.
# Render's native Python runtime does not provide the system zbar library
# unless it is installed separately. OpenCV remains the primary scanner.
try:
    from pyzbar.pyzbar import ZBarSymbol, decode as zbar_decode
    PYZBAR_AVAILABLE = True
except Exception:
    ZBarSymbol = None
    zbar_decode = None
    PYZBAR_AVAILABLE = False


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _opencv_decode(image) -> list[str]:
    results: list[str] = []
    detector = cv2.QRCodeDetector()

    # Single QR.
    try:
        data, points, _ = detector.detectAndDecode(image)
        if data:
            results.append(data)
    except Exception:
        pass

    # Multiple QR codes in one image.
    try:
        ok, decoded_info, points, _ = detector.detectAndDecodeMulti(image)
        if ok and decoded_info:
            results.extend(x for x in decoded_info if x)
    except Exception:
        pass

    return _unique(results)


def _zbar_decode(image) -> list[str]:
    if not PYZBAR_AVAILABLE or zbar_decode is None or ZBarSymbol is None:
        return []
    results: list[str] = []
    try:
        decoded = zbar_decode(image, symbols=[ZBarSymbol.QRCODE])
        for item in decoded:
            try:
                results.append(item.data.decode("utf-8", errors="replace"))
            except Exception:
                results.append(str(item.data))
    except Exception:
        pass
    return _unique(results)


def _variants(image):
    """Yield a small number of useful decode variants without exploding CPU usage."""
    if image is None:
        return

    original = image
    yield original

    gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
    yield gray

    # Improve local contrast.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    yield enhanced

    # Otsu threshold for screenshots / low contrast images.
    try:
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield otsu
    except Exception:
        pass

    # Adaptive threshold for uneven lighting.
    try:
        adaptive = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 5
        )
        yield adaptive
    except Exception:
        pass

    # Upscale smaller QR images. Limit the long edge to avoid excessive memory.
    h, w = gray.shape[:2]
    if max(h, w) < 1400:
        scale = 2.0 if max(h, w) < 900 else 1.5
        up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        yield up


def scan_qr(image_path: str) -> list[str]:
    """Return all unique QR payloads found in an image."""
    if not image_path or not os.path.exists(image_path):
        return []

    image = cv2.imread(image_path)
    if image is None:
        # Fallback through Pillow for unusual image encodings.
        try:
            pil = Image.open(image_path).convert("RGB")
            pil = ImageOps.exif_transpose(pil)
            image = cv2.cvtColor(__import__("numpy").array(pil), cv2.COLOR_RGB2BGR)
        except Exception:
            return []

    # Keep memory/CPU predictable for very large Telegram images.
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest > 2400:
        scale = 2400 / longest
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    results: list[str] = []

    # OpenCV is preferred because it preserves UTF-8 payloads correctly on
    # typical Telegram images. Only use ZBar when OpenCV cannot decode anything.
    for variant in _variants(image):
        opencv_results = _opencv_decode(variant)
        if opencv_results:
            return _unique(opencv_results)

    # ZBar fallback for QR images that OpenCV cannot decode.
    for variant in _variants(image):
        zbar_results = _zbar_decode(variant)
        if zbar_results:
            return _unique(zbar_results)

    return []
