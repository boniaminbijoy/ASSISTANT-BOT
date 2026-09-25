ASSISTANT BOT - TikTok fixed build

Deploy the contents of this folder. The TikTok downloader uses yt-dlp nightly with curl-cffi 0.16.0 and chrome-131 impersonation. FFmpeg is installed by the Dockerfile.

If TikTok still rejects the Render IP, the bot will show the actual yt-dlp exception instead of "Unknown yt-dlp error". In that case a TikTok cookies file or a different outbound IP may be required.


## V3 fix
The TikTok downloader converts the impersonation target string to yt-dlp\'s `ImpersonateTarget` object before passing it to the Python API. This fixes the `AssertionError: AssertionError()` seen with newer yt-dlp builds.
