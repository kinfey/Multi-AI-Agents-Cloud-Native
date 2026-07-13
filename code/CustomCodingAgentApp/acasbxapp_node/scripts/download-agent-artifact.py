#!/usr/bin/env python3
import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile, ZipInfo


def _safe_member(member: ZipInfo) -> bool:
    member_path = PurePosixPath(member.filename)
    mode = member.external_attr >> 16
    return (
        not member_path.is_absolute()
        and ".." not in member_path.parts
        and not stat.S_ISLNK(mode)
    )


def _open_in_editor(destination: Path, editor: str = "auto") -> None:
    """Open ``destination`` in VS Code Insiders, falling back to stable VS Code.

    ``editor`` may be ``"insiders"``, ``"code"``, or ``"auto"`` (try Insiders
    first, then stable). Each preference tries the CLI (``code-insiders`` /
    ``code``) first, then the macOS ``open -a`` application fallback.
    """
    insiders = [
        (shutil.which("code-insiders"), None),
        (None, "Visual Studio Code - Insiders"),
    ]
    stable = [
        (shutil.which("code"), None),
        (None, "Visual Studio Code"),
    ]
    if editor == "insiders":
        candidates = insiders
    elif editor == "code":
        candidates = stable
    else:
        candidates = insiders + stable

    for cli, app in candidates:
        try:
            if cli:
                subprocess.run([cli, str(destination)], check=True)
                return
            if app and shutil.which("open"):
                subprocess.run(["open", "-a", app, str(destination)], check=True)
                return
        except subprocess.CalledProcessError:
            continue
    print(
        f"Could not open an editor automatically. Open it manually: {destination}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download, safely extract, and open a save-agent ZIP archive."
    )
    parser.add_argument("url", help="Authenticated save-agent artifact URL.")
    parser.add_argument("destination", type=Path, help="Local extraction directory.")
    parser.add_argument(
        "--token-env",
        default="OPENCLAW_GATEWAY_TOKEN",
        help="Environment variable containing the gateway Bearer token.",
    )
    parser.add_argument(
        "--editor",
        choices=("auto", "insiders", "code"),
        default="auto",
        help="Which editor to open after extraction (default: auto = Insiders then stable VS Code).",
    )
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is required")

    destination = args.destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        parser.error(f"destination must be empty: {destination}")

    request = Request(args.url, headers={"Authorization": f"Bearer {token}"})
    with tempfile.NamedTemporaryFile(suffix=".zip") as archive_file:
        with urlopen(request, timeout=120) as response:
            if response.headers.get_content_type() != "application/zip":
                raise RuntimeError("server response is not an application/zip artifact")
            shutil.copyfileobj(response, archive_file)
        archive_file.flush()

        try:
            with ZipFile(archive_file.name) as archive:
                members = archive.infolist()
                if not members or any(not _safe_member(member) for member in members):
                    raise RuntimeError("archive contains an unsafe path or symbolic link")
                archive.extractall(destination)
        except BadZipFile as exc:
            raise RuntimeError("downloaded artifact is not a valid ZIP archive") from exc

    if not args.no_open:
        _open_in_editor(destination, args.editor)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())