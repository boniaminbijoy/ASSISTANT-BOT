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
    """Generate a small, fast set of QR-friendly variants.

    The old scanner used many heavy variants and could appear to stop around
    50% while OpenCV was processing the second pass. Keep the set bounded so
    Telegram users get a predictable completion time.
    """
    if image is None:
        return

    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()

    yield image
    yield gray
    yield _add_border(gray, max(20, min(h, w) // 40))

    # Contrast/lighting correction.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    yield enhanced

    # One adaptive threshold and one Otsu threshold are usually enough.
    try:
        yield cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 5
        )
    except Exception:
        pass
    try:
        _, otsu = cv2.threshold(
            enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        yield otsu
    except Exception:
        pass

    # Upscale only genuinely small images.
    longest = max(h, w)
    if longest < 1400:
        scale = 2.5 if longest < 800 else 1.8
        up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        yield up
        yield _add_border(up, 40)

    # Orientation variants.
    for code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
        rot = cv2.rotate(image, code)
        yield rot
        if len(rot.shape) == 3:
            yield cv2.cvtColor(rot, cv2.COLOR_BGR2GRAY)


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


def scan_qr(image_path: str, progress_callback=None) -> list[str]:
    if not image_path or not os.path.exists(image_path):
        return []

    image = _load_image(image_path)
    if image is None:
        return []

    h, w = image.shape[:2]
    longest = max(h, w)
    if longest > 2200:
        scale = 2200 / longest
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    variants = list(_variants(image))
    total = max(1, len(variants))

    def report(done):
        if progress_callback:
            try:
                progress_callback(min(99, int(done * 99 / total)))
            except Exception:
                pass

    # Fast path: OpenCV first. This avoids the old long 0-50% ZBar pass.
    detector = cv2.QRCodeDetector()
    for index, variant in enumerate(variants, 1):
        try:
            data, _, _ = detector.detectAndDecode(variant)
            if data:
                if progress_callback:
                    progress_callback(100)
                return _unique([data])
        except Exception:
            pass

        try:
            ok, decoded_info, _, _ = detector.detectAndDecodeMulti(variant)
            if ok and decoded_info:
                results = _unique([x for x in decoded_info if x])
                if results:
                    if progress_callback:
                        progress_callback(100)
                    return results
        except Exception:
            pass
        report(index)

    # Fallback: ZBar/pyzbar on the same bounded set.
    for index, variant in enumerate(variants, 1):
        results = _zbar_decode(variant)
        if results:
            if progress_callback:
                progress_callback(100)
            return results
        report(index)

    if progress_callback:
        progress_callback(100)
    return []

