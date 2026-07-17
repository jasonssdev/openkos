"""Writes the OKF bundle root: `index.md` and `log.md`.

The only module in `bundle/` that touches the filesystem — `index.py` and
`log.py` stay pure renderers.
"""

from datetime import date
from pathlib import Path

from openkos.bundle.index import render_index
from openkos.bundle.log import render_log


def create(bundle_dir: Path, today: date) -> None:
    """Create a fresh bundle at `bundle_dir`: `index.md` then `log.md`.

    Both files are written with exclusive-create mode ("x"): a colliding
    file raises `FileExistsError` instead of being overwritten (D2). This is
    the guarantee itself, not a pre-flight convenience on top of it — closing
    the Phase-A -> B TOCTOU window a caller's own pre-flight check leaves
    open. No cleanup path on a mid-write failure (D3): a partially written
    bundle is left as-is for manual recovery.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with (bundle_dir / "index.md").open("x", encoding="utf-8") as index_file:
        index_file.write(render_index())
    with (bundle_dir / "log.md").open("x", encoding="utf-8") as log_file:
        log_file.write(render_log(today))
