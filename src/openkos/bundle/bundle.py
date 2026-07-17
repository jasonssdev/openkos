"""Writes the OKF bundle root: `index.md` and `log.md`.

The only module in `bundle/` that touches the filesystem — `index.py` and
`log.py` stay pure renderers.
"""

from datetime import date
from pathlib import Path

from openkos import fsio
from openkos.bundle.index import render_index
from openkos.bundle.log import render_log


def create(bundle_dir: Path, today: date) -> None:
    """Create a fresh bundle at `bundle_dir`: `index.md` then `log.md`.

    Both files are written with exclusive-create mode ("x"): a colliding
    file raises `FileExistsError` instead of being overwritten (D2). This
    closes the Phase-A -> B TOCTOU window only for these two leaf files —
    NOT for `bundle_dir.mkdir(parents=True, exist_ok=True)` above, where a
    racing writer adding a non-colliding file is silently absorbed (known,
    accepted limitation, D2). No cleanup path on a mid-write failure (D3): a
    partially written bundle is left as-is for manual recovery.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    fsio.write_exclusive(bundle_dir / "index.md", render_index())
    fsio.write_exclusive(bundle_dir / "log.md", render_log(today))
