import cv2
import numpy as np


def _text_like_mask(gray, x0, y0, x1, y1):
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    # Detect high-contrast, compact text/label-like strokes.
    black = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    white = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    mask = cv2.bitwise_or(black, white)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)), iterations=1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    h, w = mask.shape
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 8 or area > w * h * 0.12:
            continue
        if 2 <= bh <= max(80, int(h * 0.35)) and 2 <= bw <= max(250, int(w * 0.8)):
            # Text-like components tend to be short and clustered.
            if bw / max(bh, 1) >= 0.8 or bh / max(bw, 1) >= 0.3:
                out[labels == i] = 255
    out = cv2.dilate(out, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 5)), 1)
    return out


def remove_ai_label(input_path: str, output_path: str) -> bool:
    image = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read image")

    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = np.zeros((h, w), dtype=np.uint8)

    # Most generated-image labels/overlays are placed near an edge/corner.
    regions = [
        (0, 0, w, max(1, int(h * 0.22))),
        (0, max(0, int(h * 0.72)), w, h),
        (0, 0, max(1, int(w * 0.28)), h),
        (max(0, int(w * 0.72)), 0, w, h),
    ]
    for x0, y0, x1, y1 in regions:
        m = _text_like_mask(gray, x0, y0, x1, y1)
        if m is not None:
            mask[y0:y1, x0:x1] = cv2.bitwise_or(mask[y0:y1, x0:x1], m)

    # Keep only clustered components to avoid destroying ordinary image detail.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)))
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), 1)

    # Safety: if the detector becomes too broad, return the original image.
    coverage = float(np.count_nonzero(mask)) / float(mask.size)
    if coverage > 0.08 or coverage < 0.00005:
        cv2.imwrite(output_path, image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return False

    cleaned = cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)
    cv2.imwrite(output_path, cleaned, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return True
