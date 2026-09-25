# ASSISTANT BOT - V5

Telegram bot with:
- QR Code Scanner
- QR Code Generator
- TikTok Downloader

## V5 QR improvements
- QR scanner uses OpenCV first and pyzbar/ZBar fallback.
- Tries contrast, grayscale and threshold variants for difficult images.
- Supports multiple QR codes in one image.
- Supports Telegram photos and image documents.
- Keeps the Telegram event loop responsive by running QR decoding/generation in a worker thread.
- Uses unique temporary files so multiple users/scans do not overwrite each other.
- QR generator keeps the bot logo small enough to preserve scan reliability.
- QR generator supports Unicode text and arbitrary links.
- TikTok fixes from V4 are preserved.

## Deployment
The Dockerfile installs `libzbar0`, which is required by pyzbar on Debian/Ubuntu-style Linux systems.
