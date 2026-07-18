"""Unit tests for `fsio.py`: shared filesystem primitives.

`write_exclusive` stays create-only ("x"); `write_atomic` is the separate,
overwrite-safe sibling (D1); `copy_exclusive` is `write_exclusive`'s binary
counterpart for raw sources of any extension (D1 finding #1).
"""

import os
from pathlib import Path
from typing import Any

import pytest

from openkos import fsio


def test_write_exclusive_raises_on_existing_file(tmp_path: Path) -> None:
    """Regression: `write_exclusive` still refuses an existing file ("x",
    create-only, unchanged by this change)."""
    target = tmp_path / "file.md"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        fsio.write_exclusive(target, "new content")

    assert target.read_text(encoding="utf-8") == "original"


def test_write_atomic_overwrites_existing_file(tmp_path: Path) -> None:
    """`write_atomic` replaces an existing file's content."""
    target = tmp_path / "index.md"
    target.write_text("old content", encoding="utf-8")

    fsio.write_atomic(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"


def test_write_atomic_creates_new_file(tmp_path: Path) -> None:
    """`write_atomic` also succeeds when the target does not yet exist."""
    target = tmp_path / "log.md"

    fsio.write_atomic(target, "fresh content")

    assert target.read_text(encoding="utf-8") == "fresh content"


def test_write_atomic_leaves_no_temp_file_behind_on_success(tmp_path: Path) -> None:
    """A successful `write_atomic` leaves only the target file, no leftover temp."""
    target = tmp_path / "index.md"
    target.write_text("old", encoding="utf-8")

    fsio.write_atomic(target, "new")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.md"]


def test_write_atomic_interrupted_write_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `os.replace` failure mid-write leaves the original file byte-identical
    and unlinks the temp file -- no partial file replaces it (scenario:
    interrupted write leaves original intact)."""
    target = tmp_path / "index.md"
    target.write_text("original content", encoding="utf-8")

    def raising_replace(_src: str, _dst: str) -> None:
        raise OSError("simulated failure before rename completes")

    monkeypatch.setattr(os, "replace", raising_replace)

    with pytest.raises(OSError, match="simulated failure"):
        fsio.write_atomic(target, "new content that must not land")

    assert target.read_text(encoding="utf-8") == "original content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.md"]


def test_write_atomic_uses_unique_temp_name_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two `write_atomic` calls to the same target must not share a
    deterministic temp file name -- a shared name lets one call's in-flight
    temp write clobber another concurrent call's temp file for the same
    `path` before either reaches its rename."""
    target = tmp_path / "index.md"
    recorded_tmp_names: list[str] = []
    original_open = Path.open

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name != target.name and self.name.endswith(".tmp"):
            recorded_tmp_names.append(self.name)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)

    fsio.write_atomic(target, "first")
    fsio.write_atomic(target, "second")

    assert len(recorded_tmp_names) == 2
    assert recorded_tmp_names[0] != recorded_tmp_names[1]


def test_copy_exclusive_copies_binary_content(tmp_path: Path) -> None:
    """`copy_exclusive` copies `src`'s bytes to `dst` verbatim."""
    src = tmp_path / "source.bin"
    src.write_bytes(b"\x00\x01binary\xffcontent")
    dst = tmp_path / "raw" / "source.bin"
    dst.parent.mkdir()

    fsio.copy_exclusive(src, dst)

    assert dst.read_bytes() == b"\x00\x01binary\xffcontent"


def test_copy_exclusive_raises_on_existing_destination(tmp_path: Path) -> None:
    """`copy_exclusive` refuses to overwrite an existing destination file."""
    src = tmp_path / "source.txt"
    src.write_text("new", encoding="utf-8")
    dst = tmp_path / "dest.txt"
    dst.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        fsio.copy_exclusive(src, dst)

    assert dst.read_bytes() == b"original"


def test_copy_exclusive_unlinks_partial_dst_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write failure after `dst.open("xb")` has already created `dst` must
    not leave a partial file behind -- `dst` is create-only, so a leftover
    partial would make every retry raise `FileExistsError` and block
    recovery (mirrors `write_atomic`'s cleanup-on-failure, D1)."""
    src = tmp_path / "source.bin"
    src.write_bytes(b"content")
    dst = tmp_path / "dest.bin"
    original_open = Path.open

    class _RaisingFile:
        """Wraps the real (already-created) file handle; `write` fails."""

        def __init__(self, real: Any) -> None:
            self._real = real

        def __enter__(self) -> "_RaisingFile":
            return self

        def __exit__(self, *exc_info: object) -> None:
            self._real.__exit__(*exc_info)

        def write(self, _data: bytes) -> int:
            raise OSError("simulated failure mid-copy")

    def patched_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        real = original_open(self, *args, **kwargs)
        if self == dst:
            return _RaisingFile(real)
        return real

    monkeypatch.setattr(Path, "open", patched_open)

    with pytest.raises(OSError, match="simulated failure mid-copy"):
        fsio.copy_exclusive(src, dst)

    assert not dst.exists()
