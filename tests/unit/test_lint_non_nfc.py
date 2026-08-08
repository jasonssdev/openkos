"""Unit tests for `lint.check_non_nfc_names` (issue #474).

Concept ids are canonically NFC (`okf.concept_id_for` normalizes, #430),
and `okf.concept_path_for` tolerantly resolves an NFC id against a
decomposed on-disk spelling -- its docstring ends by promising that a
caller who wants to KNOW a bundle carries decomposed names should ask
`lint`. This check delivers that promise: a `"non-nfc-name"` finding for
every on-disk entry (file OR directory) whose own name is not NFC, with
the NFC rename as remediation.

Deliberately a NAMES-ONLY walk, not the `collect_docs` walk: design D3's
no-fifth-walk guard protects the read+parse walk, and this walk opens no
file -- also `collect_docs` only sees readable `.md` docs, while a
decomposed name on a directory, a non-`.md` file, or an unreadable doc is
exactly what this check must still see. Detection ONLY: openkos never
renames (human curates, engine maintains).

Every non-ASCII test name is built with `unicodedata.normalize("NFD",
...)` -- never a pasted raw decomposed string, which an editor or VCS
normalization pass could silently recompose into a test that asserts
nothing.
"""

import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import lint

NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")


def test_nfd_named_md_file_is_flagged_with_escaped_name_and_nfc_target(
    tmp_path: Path,
) -> None:
    """An NFD-named `.md` file produces exactly one `non-nfc-name` finding
    whose `path` is the NFC bundle-relative spelling (the canonical id,
    matching how every other verb spells the object, #247) and whose
    detail shows BOTH the raw on-disk name in escaped form (the combining
    mark is invisible otherwise) and the NFC rename target."""
    bundle_dir = tmp_path / "bundle"
    concepts = bundle_dir / "concepts"
    concepts.mkdir(parents=True)
    (concepts / f"{NFD_CAFE}.md").write_text("body\n", encoding="utf-8")

    findings = lint.check_non_nfc_names(bundle_dir)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "non-nfc-name"
    assert finding.path == f"concepts/{NFC_CAFE}.md"
    assert "\\u0301" in finding.detail
    assert f"{NFC_CAFE}.md" in finding.detail


def test_nfd_named_directory_yields_one_finding_not_one_per_descendant(
    tmp_path: Path,
) -> None:
    """A decomposed DIRECTORY name fires exactly once, for the directory
    itself: the check keys on each entry's OWN name, so NFC-named children
    inside a decomposed directory are clean entries, not repeat offenders
    -- one rename fixes the whole subtree, and the report says so once."""
    bundle_dir = tmp_path / "bundle"
    nfd_dir = bundle_dir / NFD_CAFE
    nfd_dir.mkdir(parents=True)
    (nfd_dir / "one.md").write_text("body\n", encoding="utf-8")
    (nfd_dir / "two.md").write_text("body\n", encoding="utf-8")

    findings = lint.check_non_nfc_names(bundle_dir)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "non-nfc-name"
    assert finding.path == NFC_CAFE


def test_fully_nfc_bundle_yields_no_findings(tmp_path: Path) -> None:
    """A bundle whose every on-disk name is already NFC (including plain
    ASCII, which is trivially NFC) produces no findings."""
    bundle_dir = tmp_path / "bundle"
    concepts = bundle_dir / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "plain.md").write_text("body\n", encoding="utf-8")
    (concepts / f"{NFC_CAFE}.md").write_text("body\n", encoding="utf-8")

    assert lint.check_non_nfc_names(bundle_dir) == []


def test_non_md_nfd_named_file_is_still_flagged(tmp_path: Path) -> None:
    """A decomposed name on a non-`.md` file is still flagged -- this is
    one of the cases that forces the names-only walk: `collect_docs` only
    ever sees readable `.md` docs, so the doc walk could never report
    this entry at all."""
    bundle_dir = tmp_path / "bundle"
    raw = bundle_dir / "raw"
    raw.mkdir(parents=True)
    nfd_notes = unicodedata.normalize("NFD", "notas-café.txt")
    (raw / nfd_notes).write_text("raw text\n", encoding="utf-8")

    findings = lint.check_non_nfc_names(bundle_dir)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "non-nfc-name"
    assert finding.path == unicodedata.normalize("NFC", f"raw/{nfd_notes}")


def test_findings_come_out_in_sorted_walk_order(tmp_path: Path) -> None:
    """Two decomposed entries come out sorted by finding `path` --
    deterministic across runs and filesystems, the same stability every
    other check in this module pins for its output."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    first = unicodedata.normalize("NFD", "alpha-café.md")
    second = unicodedata.normalize("NFD", "beta-café.md")
    (bundle_dir / second).write_text("body\n", encoding="utf-8")
    (bundle_dir / first).write_text("body\n", encoding="utf-8")

    findings = lint.check_non_nfc_names(bundle_dir)

    assert [finding.path for finding in findings] == [
        unicodedata.normalize("NFC", first),
        unicodedata.normalize("NFC", second),
    ]


def test_check_never_opens_files_unreadable_garbage_doc_is_flagged(
    tmp_path: Path,
) -> None:
    """An NFD-named doc that is BOTH permission-denied and full of bytes no
    UTF-8 decode would survive is still flagged: the walk reads names
    only, never content, so nothing about the file's readability can
    suppress or crash the finding (read-only-never-fail)."""
    bundle_dir = tmp_path / "bundle"
    concepts = bundle_dir / "concepts"
    concepts.mkdir(parents=True)
    garbage = concepts / f"{NFD_CAFE}.md"
    garbage.write_bytes(b"\x00\xff\xfe not utf-8")
    garbage.chmod(0o000)
    try:
        findings = lint.check_non_nfc_names(bundle_dir)
    finally:
        garbage.chmod(0o644)

    assert len(findings) == 1
    assert findings[0].kind == "non-nfc-name"
    assert findings[0].path == f"concepts/{NFC_CAFE}.md"


def test_broken_walk_degrades_to_findings_collected_so_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError raised MID-walk keeps every finding already collected
    (review R4-001): the walk is pulled one entry at a time, never
    materialized through `sorted()` -- which would consume the whole
    generator before the first finding exists, so a permission wall after
    a real finding would discard it and render a silently empty,
    false-clean report."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    seen_entry = bundle_dir / f"{NFD_CAFE}.md"

    def broken_walk(self: Path, pattern: str) -> Iterator[Path]:
        assert pattern == "*"
        yield seen_entry
        raise OSError("permission wall mid-walk")

    monkeypatch.setattr(Path, "rglob", broken_walk)

    findings = lint.check_non_nfc_names(bundle_dir)

    assert [finding.path for finding in findings] == [f"{NFC_CAFE}.md"]
