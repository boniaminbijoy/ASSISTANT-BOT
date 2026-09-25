import cv2


def scan_qr(image_path):

    image = cv2.imread(image_path)

    if image is None:
        return None

    detector = cv2.QRCodeDetector()

    data, points, _ = detector.detectAndDecode(image)

    if data:
        return data

    return None