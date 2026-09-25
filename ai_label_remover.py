import cv2
import numpy as np


def _text_like_mask(gray, x0, y0, x1, y1):
    """Find compact, high-contrast text/label-like regions without treating the whole ROI as a mask."""
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    # The old detector OR-ed inverse and normal Otsu masks, which makes
    # virtually every pixel white. Use edges + adaptive dark/bright strokes.
    blur = cv2.GaussianBlur(roi, (3, 3), 0)
    edges = cv2.Canny(blur, 60, 160)

    local = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 21, 5
    )
    local = cv2.morphologyEx(
        local, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    )

    mask = cv2.bitwise_or(edges, local)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    )

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    h, w = mask.shape

    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 10:
            continue
        if bw > max(320, int(w * 0.9)) or bh > max(100, int(h * 0.45)):
            continue
        # Text/label strokes usually form small elongated components.
        if bw >= 3 and bh >= 3 and area <= max(2500, int(w * h * 0.04)):
            out[labels == i] = 255

    # Join nearby letters into a label region.
    out = cv2.dilate(
        out, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)), 1
    )
    return out


def remove_ai_label(input_path: str, output_path: str) -> bool:
    image = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read image")

    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = np.zeros((h, w), dtype=np.uint8)

    # AI labels/overlays are commonly close to an image edge. Keep the
    # search constrained so normal central image details are not removed.
    regions = [
        (0, 0, w, max(1, int(h * 0.25))),
        (0, max(0, int(h * 0.75)), w, h),
        (0, 0, max(1, int(w * 0.30)), h),
        (max(0, int(w * 0.70)), 0, w, h),
    ]

    for x0, y0, x1, y1 in regions:
        m = _text_like_mask(gray, x0, y0, x1, y1)
        if m is not None:
            mask[y0:y1, x0:x1] = cv2.bitwise_or(mask[y0:y1, x0:x1], m)

    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (11, 7))
    )
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), 1
    )

    # Reject obviously noisy detections. For a genuine small label, this
    # threshold is intentionally much lower than the previous 0.00005.
    coverage = float(np.count_nonzero(mask)) / float(mask.size)
    if coverage < 0.00001 or coverage > 0.12:
        cv2.imwrite(output_path, image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return False

    cleaned = cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)
    ok = cv2.imwrite(output_path, cleaned, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return bool(ok)
