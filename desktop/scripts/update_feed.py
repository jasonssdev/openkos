"""Write the `latest.json` the desktop app's updater reads (ADR-0079).

The release job uploads it, with the signed update archive, to the tag's GitHub
release; every installed app fetches it from
`https://github.com/jasonssdev/openkos/releases/latest/download/latest.json`.
The Tauri updater rejects the whole file when `version` or a platform's `url`
or `signature` is missing, and compares `version` with the installed app's as
semver, so both are checked here before anything is uploaded. Only Apple
Silicon ships, so the one platform is `darwin-aarch64`.

Standard library only: the release job runs it with no project environment.

Usage:
  update_feed.py --version 0.7.0 --signature-file OpenKOS.app.tar.gz.sig \
      --url https://github.com/.../OpenKOS.app.tar.gz --notes "..." --out latest.json
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM = "darwin-aarch64"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_URL_PREFIX = "https://github.com/"


def build_feed(
    version: str, signature: str, url: str, notes: str, now: datetime
) -> dict[str, Any]:
    """The feed for one release, or `ValueError` naming the field that is wrong."""
    if not _SEMVER.match(version):
        raise ValueError(f"version {version!r} is not semver (no leading 'v')")
    signature = signature.strip()
    if not signature:
        raise ValueError("signature is empty")
    if not url.startswith(_URL_PREFIX):
        raise ValueError(f"url {url!r} is not a GitHub https address")
    return {
        "version": version,
        "notes": notes,
        "pub_date": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {PLATFORM: {"signature": signature, "url": url}},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--signature-file", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        body = build_feed(
            args.version,
            args.signature_file.read_text(encoding="utf-8"),
            args.url,
            args.notes,
            datetime.now(UTC),
        )
    except (OSError, ValueError) as error:
        print(f"update_feed.py: {error}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
