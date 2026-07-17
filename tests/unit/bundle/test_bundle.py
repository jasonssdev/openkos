"""Unit tests for `bundle.create`: the only module that writes inside the OKF tree."""

from datetime import date
from pathlib import Path

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
