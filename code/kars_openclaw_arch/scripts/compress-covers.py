#!/usr/bin/env python
"""Recompress existing cover.png blobs to the web load budget in place.

Future runs already emit a light cover (see media_claw_agent.overlay.save_web_cover,
wired into the pipeline), but covers uploaded before that change are still ~2MB.
This one-off remediation downloads every `**/cover.png`, shrinks it with the same
helper, and re-uploads it under the same blob name and `image/png` content type,
so manifests and SAS URLs keep working unchanged.

Usage:
  AZURE_STORAGE_ACCOUNT_URL="https://<acct>.blob.core.windows.net" \
  python scripts/compress-covers.py [--container media] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents/media-claw-agent/src"))
from media_claw_agent.overlay import WEB_COVER_MAX_BYTES, save_web_cover  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default=os.getenv("AZURE_STORAGE_CONTAINER", "media"))
    parser.add_argument("--suffix", default="cover.png", help="blob name suffix to match")
    parser.add_argument("--max-bytes", type=int, default=WEB_COVER_MAX_BYTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    account_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL")
    if not account_url:
        parser.error("set AZURE_STORAGE_ACCOUNT_URL to the target storage account")

    service = BlobServiceClient(account_url, DefaultAzureCredential())
    container = service.get_container_client(args.container)

    targets = [
        blob
        for blob in container.list_blobs()
        if blob.name.endswith(args.suffix)
    ]
    if not targets:
        print(f"no blobs ending in {args.suffix!r} in {args.container}")
        return 0

    changed = 0
    with TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for blob in sorted(targets, key=lambda b: b.name):
            before = blob.size or 0
            if before <= args.max_bytes:
                print(f"skip  {blob.name}  {before/1024:.1f}KB (already within budget)")
                continue

            data = container.download_blob(blob.name).readall()
            image = Image.open(BytesIO(data))
            out = workdir / "cover.png"
            after = save_web_cover(image, out, max_bytes=args.max_bytes)

            marker = "DRY-RUN" if args.dry_run else "upload"
            print(f"{marker} {blob.name}  {before/1024:.1f}KB -> {after/1024:.1f}KB")
            if not args.dry_run:
                container.upload_blob(
                    name=blob.name,
                    data=out.read_bytes(),
                    overwrite=True,
                    content_settings=ContentSettings(content_type="image/png"),
                )
            changed += 1

    print(f"done: {changed} cover(s) {'would be ' if args.dry_run else ''}recompressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
