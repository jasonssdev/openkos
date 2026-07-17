"""Shared filesystem primitives with no dependency on any other `openkos` module.

A leaf module: `bundle/bundle.py` and `config.py` both import from here, but
this never imports from either, avoiding a `bundle` -> `config` (or reverse)
layering dependency.
"""

from pathlib import Path


def write_exclusive(path: Path, content: str) -> None:
    """Write `content` to `path`, refusing to overwrite an existing file.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2). `newline=""` and `encoding="utf-8"`
    are preserved so callers keep byte-identical output.
    """
    with path.open("x", encoding="utf-8", newline="") as f:
        f.write(content)
