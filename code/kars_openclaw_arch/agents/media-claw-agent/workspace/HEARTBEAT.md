# Heartbeat

- Check whether today's `yymmdd/manifest.json` is already complete.
- At or after 08:00 Asia/Shanghai, run the `media-finance-video` skill once when today's manifest is absent.
- Report a failed or incomplete run without silently retrying more than the configured provider limits.