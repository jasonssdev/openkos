"""The committed OpenAPI snapshot of the local API: generator and writer.

`python -m tests.unit.localapi.openapi_snapshot --write` regenerates
`openapi.json` in the same commit as the change that moves the contract, as
`python -m tests.unit.api.surface --write` does for the Python surface. Never
hand-merge the JSON: rebase onto `main` and regenerate.
"""

import json
import sys
from pathlib import Path

from openkos.localapi._app import openapi_document

SNAPSHOT_PATH = Path(__file__).with_name("openapi.json")


def dumps(document: dict[str, object]) -> str:
    """Canonical JSON: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    live = dumps(openapi_document())
    if "--write" in argv:
        SNAPSHOT_PATH.write_text(live, encoding="utf-8")
        print(f"wrote {SNAPSHOT_PATH}")
    elif (
        not SNAPSHOT_PATH.exists() or SNAPSHOT_PATH.read_text(encoding="utf-8") != live
    ):
        print("the OpenAPI contract differs from the snapshot; pass --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
