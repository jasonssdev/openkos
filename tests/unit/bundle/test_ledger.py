"""Unit tests for the merge-ledger sidecar store (`bundle/ledger.py`):
path mapping (mirroring `okf.concept_path_for`'s own suite, task 1.2) and
the two-phase-write recovery truth table (task 2.1; design Decision 1).
"""

import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.bundle import ledger
from openkos.model import okf

_NFC_STEM = "Café"  # "e" + COMBINING ACUTE ACCENT is what NFC folds
_NFD_STEM = unicodedata.normalize("NFD", _NFC_STEM)
_NFC_STEM = unicodedata.normalize("NFC", _NFC_STEM)


# --- path mapping (mirrors okf.concept_path_for's own suite) --------------


def test_ledger_path_for_returns_the_direct_path_when_it_exists(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)
    expected = ledger_dir / "stoicism.ledger.okf"
    expected.write_text("body", encoding="utf-8")

    assert ledger.ledger_path_for("concepts/stoicism", bundle_dir) == expected


def test_ledger_path_for_falls_back_to_the_direct_path_when_nothing_matches(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    resolved = ledger.ledger_path_for("concepts/absent", bundle_dir)

    assert resolved == bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts" / (
        "absent.ledger.okf"
    )


def test_ledger_path_for_finds_a_decomposed_name_from_an_nfc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger sidecar written on HFS+ and cloned onto a byte-exact
    filesystem must still resolve, exactly like the concept-file case
    (issue #430) -- this is `concept_path_for`'s (task 1.1) fallback,
    reused rather than reinvented (design Decision 2)."""
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)
    on_disk = ledger_dir / f"{_NFD_STEM}.ledger.okf"
    on_disk.write_text("body", encoding="utf-8")

    monkeypatch.setattr(Path, "exists", lambda self: False)

    resolved = ledger.ledger_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved.name == f"{_NFD_STEM}.ledger.okf"
    assert unicodedata.normalize("NFC", resolved.name) == f"{_NFC_STEM}.ledger.okf"


def test_ledger_path_for_tolerates_an_unreadable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "exists", lambda self: False)

    def _boom(self: Path) -> Iterator[Path]:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", _boom)

    resolved = ledger.ledger_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved == ledger_dir / f"{_NFC_STEM}.ledger.okf"


def test_ledger_path_for_never_resolves_a_symlink_through_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)
    outside = tmp_path / "outside.ledger.okf"
    outside.write_text("secret", encoding="utf-8")
    (ledger_dir / f"{_NFD_STEM}.ledger.okf").symlink_to(outside)

    monkeypatch.setattr(Path, "exists", lambda self: False)

    resolved = ledger.ledger_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved == ledger_dir / f"{_NFC_STEM}.ledger.okf"
    assert resolved.name != f"{_NFD_STEM}.ledger.okf"


def test_ledger_path_for_skips_the_scan_for_an_ascii_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)
    scanned: list[Path] = []
    real_iterdir = Path.iterdir

    def _recording_iterdir(self: Path) -> Iterator[Path]:
        scanned.append(self)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "iterdir", _recording_iterdir)

    ledger.ledger_path_for("concepts/dangling-ascii-id", bundle_dir)

    assert scanned == [], "an ASCII id must not pay a directory scan"


def test_pending_path_for_differs_only_by_suffix_from_ledger_path(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    ledger_path = ledger.ledger_path_for("concepts/stoicism", bundle_dir)
    pending_path = ledger.pending_path_for("concepts/stoicism", bundle_dir)

    assert pending_path.parent == ledger_path.parent
    assert pending_path.name == "stoicism.ledger.okf.pending"


# --- read_entries / iter_ledgers -------------------------------------------


def _make_entry(absorbed_id: str = "concepts/absorbed") -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="absorbed text",
        survivor_before="survivor text",
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def test_read_entries_returns_empty_list_when_no_sidecar_exists(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.read_entries("concepts/never-merged", bundle_dir) == []


def test_read_entries_decodes_a_committed_sidecar(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    entry = _make_entry()
    path = ledger.ledger_path_for("concepts/survivor", bundle_dir)
    path.parent.mkdir(parents=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "schema": ledger.LEDGER_SIDECAR_SCHEMA,
                "survivor_id": "concepts/survivor",
                "merged_from": okf.encode_merged_from([entry]),
            }
        ),
        encoding="utf-8",
    )

    assert ledger.read_entries("concepts/survivor", bundle_dir) == [entry]


def test_iter_ledgers_returns_empty_list_when_ledger_root_is_missing(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.iter_ledgers(bundle_dir) == []


def test_iter_ledgers_finds_only_ledger_suffixed_files_sorted(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    root = ledger.ledger_root(bundle_dir)
    root.mkdir(parents=True)
    (root / "b.ledger.okf").write_text("x", encoding="utf-8")
    (root / "a.ledger.okf").write_text("x", encoding="utf-8")
    # Must be excluded: a pending marker is not a committed sidecar.
    (root / "c.ledger.okf.pending").write_text("x", encoding="utf-8")

    result = ledger.iter_ledgers(bundle_dir)

    assert result == [root / "a.ledger.okf", root / "b.ledger.okf"]


# --- recovery truth table (design Decision 1; task 2.1) --------------------


def test_recover_is_none_when_no_pending_marker_exists(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.recover("concepts/survivor", bundle_dir) == "none"


def test_recover_rolls_forward_when_pending_hash_matches_survivor(
    tmp_path: Path,
) -> None:
    """`.pending` present, `sha256(survivor on disk)` matches
    `expected_survivor_sha256`: V landed, only S2 (the commit rename) was
    torn -- promote the pending container."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True)
    survivor_text = "---\ntype: Concept\n---\nSurvivor body.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")

    entry = _make_entry()
    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256=ledger.survivor_sha256(survivor_text),
    )

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-forward"
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert ledger.read_entries("concepts/survivor", bundle_dir) == [entry]


def test_recover_rolls_back_when_pending_hash_mismatches_survivor(
    tmp_path: Path,
) -> None:
    """`.pending` present, hash mismatch: V never landed -- discard the
    pending container, leaving no committed sidecar."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True)
    survivor_path.write_text(
        "---\ntype: Concept\n---\nUnmerged body.\n", encoding="utf-8"
    )

    entry = _make_entry()
    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256="0" * 64,  # never matches the on-disk survivor
    )

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-back"
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert not ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_recover_rolls_back_when_survivor_is_missing_entirely(
    tmp_path: Path,
) -> None:
    """`.pending` present but the survivor was never written at all (crash
    before V): also a roll-back, not a crash of `recover` itself."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    entry = _make_entry()
    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256="0" * 64,
    )

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-back"
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()


def test_write_pending_creates_the_ledger_directory_tree_on_demand(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    pending_path = ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_entry()],
        expected_survivor_sha256="a" * 64,
    )

    assert pending_path.is_file()
    assert pending_path.name == "survivor.ledger.okf.pending"


def test_commit_pending_promotes_pending_to_the_committed_sidecar(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    entry = _make_entry()
    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256="a" * 64,
    )

    committed_path = ledger.commit_pending("concepts/survivor", bundle_dir)

    assert committed_path == ledger.ledger_path_for("concepts/survivor", bundle_dir)
    assert committed_path.is_file()
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert ledger.read_entries("concepts/survivor", bundle_dir) == [entry]


def test_discard_pending_removes_only_the_pending_marker(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_entry()],
        expected_survivor_sha256="a" * 64,
    )

    ledger.discard_pending("concepts/survivor", bundle_dir)

    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert not ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()
