#!/bin/sh
set -eu

BLOG_DIR="/usr/share/nginx/html/blog"
STATUS_FILE="${BLOG_DIR}/sidecar-status.txt"

# Ensure blog directory exists
mkdir -p "$BLOG_DIR"

echo "blog-viewer sidecar started at $(date)" > "$STATUS_FILE"

while true; do
  BLOG_COUNT=$(find "$BLOG_DIR" -name "blog-*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "[$(date)] sidecar heartbeat | blog files: ${BLOG_COUNT}" >> "$STATUS_FILE"

  # Keep status file from growing too large (keep last 100 lines)
  if [ "$(wc -l < "$STATUS_FILE")" -gt 100 ]; then
    tail -50 "$STATUS_FILE" > "${STATUS_FILE}.tmp" && mv "${STATUS_FILE}.tmp" "$STATUS_FILE"
  fi

  sleep 10
done
