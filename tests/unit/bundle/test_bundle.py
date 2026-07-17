"""Unit tests for `bundle.create`: the only module that writes inside the OKF tree."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from openkos.bundle.bundle import create


def test_create_writes_exactly_index_and_log(tmp_path: Path) -> None:
    """A fresh bundle holds exactly `index.md` and `log.md` (scenario 6)."""
    bundle_dir = tmp_path / "bundle"

    create(bundle_dir, date(2026, 7, 16))

    written = sorted(path.name for path in bundle_dir.iterdir())
    assert written == ["index.md", "log.md"]


def test_create_raises_on_existing_index(tmp_path: Path) -> None:
    """Exclusive-create mode ("x") fails loudly on collision, never clobbers (D2)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create(bundle_dir, date(2026, 7, 16))


def test_create_raises_on_existing_log(tmp_path: Path) -> None:
    """The log write is independently exclusive-create too."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "log.md").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create(bundle_dir, date(2026, 7, 16))


def test_create_opens_files_with_newline_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create` opens both `index.md` and `log.md` with `newline=""`.

    Unlike `test_create_writes_no_cr_bytes` below, which passes on POSIX
    regardless of `newline=""` (no LF->CRLF translation there), this spies
    on `Path.open` directly, so removing the argument fails here even on
    Linux CI.
    """
    original_open = Path.open
    recorded: dict[str, dict[str, Any]] = {}

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name in ("index.md", "log.md"):
            recorded[self.name] = kwargs
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)

    create(tmp_path / "bundle", date(2026, 7, 16))

    assert recorded["index.md"].get("newline") == ""
    assert recorded["log.md"].get("newline") == ""


def test_create_writes_no_cr_bytes(tmp_path: Path) -> None:
    """`index.md` and `log.md` contain no `\\r`, so LF-only content is not
    translated to CRLF on write.

    This is a regression guard for non-LF platforms (Windows, where
    `os.linesep` is `\\r\\n` and text-mode writes without `newline=""`
    translate `\\n` to `\\r\\n`): it passes on Linux/macOS either way, since
    POSIX never performs that translation, and CI here is ubuntu-only. The
    assertion still documents the byte-identical contract `create`'s
    docstring makes.
    """
    bundle_dir = tmp_path / "bundle"

    create(bundle_dir, date(2026, 7, 16))

    for filename in ("index.md", "log.md"):
        raw_bytes = (bundle_dir / filename).read_bytes()
        assert b"\r" not in raw_bytes
