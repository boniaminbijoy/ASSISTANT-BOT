# Assistant Bot V8

V8 keeps the QR and TikTok fixes and hardens sequential TikTok downloads.

- Every TikTok URL starts a fresh flow, regardless of previous state.
- Each download gets a unique temporary output filename.
- TikTok yt-dlp extraction/download is serialized and retried up to 3 times.
- QR scanner/generator behavior from V7 is preserved.
