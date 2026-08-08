"""Unit tests for `lint.scan_non_nfc_entries` / `NonNfcEntry` /
`lint.scan_stranded_rename_temps` (issue #474 part 2, design D1).

`scan_non_nfc_entries` is the ONE walk that defines "offending entry" for
both `openkos lint` (via `check_non_nfc_names`'s thin projection) and
`openkos normalize-names` -- the walk body moves verbatim from the #490
`check_non_nfc_names` implementation: `bundle_dir.rglob("*")`, pulled one
entry at a time inside a guarded `while True`, never fed through
`sorted(...)` (review R4-001; a mid-walk `OSError` must degrade to the
findings collected so far, not discard them by consuming the whole
generator up front).

Every non-ASCII test name is built with `unicodedata.normalize("NFD",
...)` -- never a pasted raw decomposed string, which an editor or VCS
normalization pass could silently recompose into a test that asserts
nothing.
"""

import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import fsio, lint

NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")


def test_raw_path_and_names_preserved(tmp_path: Path) -> None:
    """Each `NonNfcEntry` carries the raw on-disk `Path`, the raw `name`
    exactly as the filesystem returned it, the NFC target `nfc_name`, and
    the NFC bundle-relative `rel_posix` (design D1: the richer type
    `LintFinding` cannot carry, which a rename verb needs to locate the
    entry on a byte-exact filesystem)."""
    bundle_dir = tmp_path / "bundle"
    concepts = bundle_dir / "concepts"
    concepts.mkdir(parents=True)
    raw_path = concepts / f"{NFD_CAFE}.md"
    raw_path.write_text("body\n", encoding="utf-8")

    entries = lint.scan_non_nfc_entries(bundle_dir)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.path == raw_path
    assert entry.raw_name == f"{NFD_CAFE}.md"
    assert entry.nfc_name == f"{NFC_CAFE}.md"
    assert entry.rel_posix == f"concepts/{NFC_CAFE}.md"


def test_depth_matches_relative_parts_length(tmp_path: Path) -> None:
    """`depth` is `len(path.relative_to(bundle_dir).parts)` -- the sort
    key `normalize-names` uses to apply renames deepest-first (design
    D4)."""
    bundle_dir = tmp_path / "bundle"
    nested = bundle_dir / "concepts" / "nested"
    nested.mkdir(parents=True)
    (nested / f"{NFD_CAFE}.md").write_text("body\n", encoding="utf-8")

    entries = lint.scan_non_nfc_entries(bundle_dir)

    assert len(entries) == 1
    assert entries[0].depth == 3


def test_is_dir_and_is_symlink_computed_only_for_offending_entries(
    tmp_path: Path,
) -> None:
    """`is_dir`/`is_symlink` are populated for entries that failed the NFC
    test; a decomposed directory reports `is_dir=True`, and a decomposed
    symlink reports `is_symlink=True` (design D1)."""
    bundle_dir = tmp_path / "bundle"
    real_target = bundle_dir / "target.md"
    bundle_dir.mkdir()
    real_target.write_text("body\n", encoding="utf-8")
    nfd_dir = bundle_dir / NFD_CAFE
    nfd_dir.mkdir()
    nfd_link = bundle_dir / unicodedata.normalize("NFD", "liga-café.md")
    nfd_link.symlink_to(real_target)

    entries = {entry.raw_name: entry for entry in lint.scan_non_nfc_entries(bundle_dir)}

    assert entries[NFD_CAFE].is_dir is True
    assert entries[NFD_CAFE].is_symlink is False
    link_name = unicodedata.normalize("NFD", "liga-café.md")
    assert entries[link_name].is_symlink is True
    assert entries[link_name].is_dir is False


def test_results_sorted_by_rel_posix(tmp_path: Path) -> None:
    """Results are sorted by `rel_posix`, matching the deterministic order
    `check_non_nfc_names`'s projection relies on (design D1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    first = unicodedata.normalize("NFD", "alpha-café.md")
    second = unicodedata.normalize("NFD", "beta-café.md")
    (bundle_dir / second).write_text("body\n", encoding="utf-8")
    (bundle_dir / first).write_text("body\n", encoding="utf-8")

    entries = lint.scan_non_nfc_entries(bundle_dir)

    assert [entry.rel_posix for entry in entries] == [
        unicodedata.normalize("NFC", first),
        unicodedata.normalize("NFC", second),
    ]


def test_degraded_walk_collects_partial_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-walk `OSError` degrades to the entries collected so far --
    review R4-001's fix, pinned at the scan level now that
    `check_non_nfc_names` no longer owns the walk directly."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    seen_entry = bundle_dir / f"{NFD_CAFE}.md"

    def broken_walk(self: Path, pattern: str) -> Iterator[Path]:
        assert pattern == "*"
        yield seen_entry
        raise OSError("permission wall mid-walk")

    monkeypatch.setattr(Path, "rglob", broken_walk)

    entries = lint.scan_non_nfc_entries(bundle_dir)

    assert [entry.rel_posix for entry in entries] == [f"{NFC_CAFE}.md"]


def test_walk_pulls_incrementally_never_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan calls `rglob("*")` and pulls it one entry at a time via
    `next()` -- NEVER feeding it through `sorted(...)`, which would
    materialize the whole generator up front and defeat the degraded-walk
    guarantee above (review R4-001)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    pulled: list[Path] = []
    real_rglob = Path.rglob

    def spying_walk(self: Path, pattern: str) -> Iterator[Path]:
        assert pattern == "*"
        for entry in real_rglob(self, pattern):
            pulled.append(entry)
            yield entry

    monkeypatch.setattr(Path, "rglob", spying_walk)
    (bundle_dir / f"{NFD_CAFE}.md").write_text("body\n", encoding="utf-8")

    lint.scan_non_nfc_entries(bundle_dir)

    assert len(pulled) == 1


def test_scan_stranded_rename_temps_finds_temp_prefixed_entries(
    tmp_path: Path,
) -> None:
    """`scan_stranded_rename_temps` yields every on-disk entry whose name
    starts with `fsio.RENAME_TEMP_PREFIX` -- a names-only walk mirroring
    `scan_non_nfc_entries`, used only by `normalize-names` to report a
    temp stranded by a hard kill between the two `rename_two_step` hops
    (design D3)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    stranded = bundle_dir / f"{fsio.RENAME_TEMP_PREFIX}deadbeef"
    stranded.write_text("body\n", encoding="utf-8")
    (bundle_dir / "clean.md").write_text("body\n", encoding="utf-8")

    stranded_paths = lint.scan_stranded_rename_temps(bundle_dir)

    assert stranded_paths == [stranded]
