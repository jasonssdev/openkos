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


# --- doctor Check A: read-only torn-write preview (task 2.1, PR#3) --------


def test_iter_pending_returns_empty_list_when_ledger_root_is_missing(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.iter_pending(bundle_dir) == []


def test_iter_pending_finds_only_pending_suffixed_files_sorted(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    root = ledger.ledger_root(bundle_dir)
    root.mkdir(parents=True)
    (root / "b.ledger.okf.pending").write_text("x", encoding="utf-8")
    (root / "a.ledger.okf.pending").write_text("x", encoding="utf-8")
    # Must be excluded: a committed sidecar is not a pending marker.
    (root / "c.ledger.okf").write_text("x", encoding="utf-8")

    result = ledger.iter_pending(bundle_dir)

    assert result == [
        root / "a.ledger.okf.pending",
        root / "b.ledger.okf.pending",
    ]


def test_scan_torn_writes_reports_roll_forward_without_mutating_anything(
    tmp_path: Path,
) -> None:
    """A read-only preview of what `recover` WOULD do -- the SAME hash-bound
    truth table, but the pending marker and committed sidecar are left
    exactly as found (doctor stays read-only; Check A, task 2.1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True)
    survivor_text = "---\ntype: Concept\n---\nSurvivor body.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")
    entry = _make_entry()
    pending_path = ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256=ledger.survivor_sha256(survivor_text),
    )

    result = ledger.scan_torn_writes(bundle_dir)

    assert result == [(pending_path, "roll-forward")]
    # Read-only: nothing on disk changed.
    assert pending_path.is_file()
    assert not ledger.ledger_path_for("concepts/survivor", bundle_dir).is_file()


def test_scan_torn_writes_reports_roll_back_without_mutating_anything(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True)
    survivor_path.write_text(
        "---\ntype: Concept\n---\nUnmerged body.\n", encoding="utf-8"
    )
    pending_path = ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_entry()],
        expected_survivor_sha256="0" * 64,
    )

    result = ledger.scan_torn_writes(bundle_dir)

    assert result == [(pending_path, "roll-back")]
    assert pending_path.is_file()


def test_scan_torn_writes_returns_empty_list_when_no_pending_markers_exist(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.scan_torn_writes(bundle_dir) == []


# --- doctor Check B: nested-prefix equality (task 2.2, PR#3) --------------


def _write_ledger(
    bundle_dir: Path, survivor_id: str, entries: list[okf.MergeLedgerEntry]
) -> None:
    ledger.write_entries(
        survivor_id, bundle_dir, survivor_id=survivor_id, entries=entries
    )


def test_scan_nesting_violations_is_blind_to_a_single_entry_ledger(
    tmp_path: Path,
) -> None:
    """Design's documented honest false negative: a single-entry ledger has
    nothing nested to check against, so it is silently skipped, not
    flagged."""
    bundle_dir = tmp_path / "bundle"
    _write_ledger(bundle_dir, "concepts/survivor", [_make_entry()])

    assert ledger.scan_nesting_violations(bundle_dir) == []


def test_scan_nesting_violations_skips_a_post_relocation_entry_with_nothing_embedded(
    tmp_path: Path,
) -> None:
    """Every entry created AFTER the ledger relocation has a
    `survivor_before` that carries no `merged_from` key at all (it never
    lived in frontmatter) -- design Decision 5: the corruption class this
    check exists for is structurally extinct there, so a fresh multi-entry
    post-relocation ledger must NOT be falsely flagged."""
    bundle_dir = tmp_path / "bundle"
    plain_survivor_text = "---\ntype: Concept\ntitle: Survivor\n---\nBody.\n"
    entry_0 = _make_entry(absorbed_id="concepts/absorbed-0")
    entry_1 = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-21T00:00:00Z",
        absorbed_id="concepts/absorbed-1",
        absorbed_snapshot="absorbed text 2",
        survivor_before=plain_survivor_text,  # no merged_from key at all
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
    _write_ledger(bundle_dir, "concepts/survivor", [entry_0, entry_1])

    assert ledger.scan_nesting_violations(bundle_dir) == []


def test_scan_nesting_violations_flags_a_mutated_legacy_embedded_snapshot(
    tmp_path: Path,
) -> None:
    """A migration-era entry whose `survivor_before` DOES embed prior
    entries (pre-relocation, frontmatter-carried `merged_from`) is checked
    by nested-prefix equality: if the decoded embedded entries no longer
    match the sidecar's own current entries `0..k-1`, that is exactly
    #550 consequence 2 -- a later merge rewrote bytes inside an earlier
    embedded snapshot -- and must be flagged."""
    bundle_dir = tmp_path / "bundle"
    entry_0 = _make_entry(absorbed_id="concepts/absorbed-0")
    tampered_entry_0 = okf.MergeLedgerEntry(
        schema=entry_0.schema,
        merged_at=entry_0.merged_at,
        absorbed_id=entry_0.absorbed_id,
        absorbed_snapshot="TAMPERED -- this no longer matches the sidecar",
        survivor_before=entry_0.survivor_before,
        index_before=entry_0.index_before,
        log_before=entry_0.log_before,
        link_rewrites=entry_0.link_rewrites,
        sensitivity_before=entry_0.sensitivity_before,
        sensitivity_after=entry_0.sensitivity_after,
    )
    embedded_survivor_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Survivor",
        "merged_from": okf.encode_merged_from([tampered_entry_0]),
    }
    entry_1 = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-21T00:00:00Z",
        absorbed_id="concepts/absorbed-1",
        absorbed_snapshot="absorbed text 2",
        survivor_before=okf.dump_frontmatter(embedded_survivor_metadata),
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
    _write_ledger(bundle_dir, "concepts/survivor", [entry_0, entry_1])

    assert ledger.scan_nesting_violations(bundle_dir) == [("concepts/survivor", 1)]


def test_scan_nesting_violations_passes_when_the_embedded_snapshot_matches(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    entry_0 = _make_entry(absorbed_id="concepts/absorbed-0")
    embedded_survivor_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Survivor",
        "merged_from": okf.encode_merged_from([entry_0]),
    }
    entry_1 = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-21T00:00:00Z",
        absorbed_id="concepts/absorbed-1",
        absorbed_snapshot="absorbed text 2",
        survivor_before=okf.dump_frontmatter(embedded_survivor_metadata),
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
    _write_ledger(bundle_dir, "concepts/survivor", [entry_0, entry_1])

    assert ledger.scan_nesting_violations(bundle_dir) == []


def test_scan_nesting_violations_returns_empty_list_when_no_ledgers_exist(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.scan_nesting_violations(bundle_dir) == []


# --- repair verb primitives (task 3, PR#3) ---------------------------------


def test_scan_unmigrated_returns_empty_list_with_no_legacy_ledgers(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "a.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ntype: Concept\ntitle: A\n---\nBody.\n", encoding="utf-8")

    assert ledger.scan_unmigrated(bundle_dir) == []


def test_scan_unmigrated_finds_a_survivor_with_a_frontmatter_embedded_ledger(
    tmp_path: Path,
) -> None:
    """A pre-relocation survivor still carries `merged_from` in its OWN
    frontmatter -- the repair verb's migration source."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "survivor.md"
    path.parent.mkdir(parents=True)
    entry = _make_entry()
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor",
                "merged_from": okf.encode_merged_from([entry]),
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )

    result = ledger.scan_unmigrated(bundle_dir)

    assert result == [("concepts/survivor", [entry])]


def test_scan_unmigrated_skips_a_committed_sidecar_it_does_not_touch(
    tmp_path: Path,
) -> None:
    """A sidecar under `bundle/.state/ledger/` is not itself an unmigrated
    survivor concept -- `_iter_docs`'s `rglob("*.md")` walk never sees a
    non-`.md`-suffixed sidecar anyway (design Decision 2's free EXCLUDE),
    but this pins the reader-facing behavior explicitly."""
    bundle_dir = tmp_path / "bundle"
    _write_ledger(bundle_dir, "concepts/survivor", [_make_entry()])

    assert ledger.scan_unmigrated(bundle_dir) == []


def test_bundle_wide_max_entries_counts_unmigrated_and_migrated_together(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    assert ledger.bundle_wide_max_entries(bundle_dir) == 0

    _write_ledger(bundle_dir, "concepts/survivor-a", [_make_entry()])
    assert ledger.bundle_wide_max_entries(bundle_dir) == 1

    path = bundle_dir / "concepts" / "survivor-b.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor B",
                "merged_from": okf.encode_merged_from(
                    [
                        _make_entry("concepts/absorbed-x"),
                        _make_entry("concepts/absorbed-y"),
                    ]
                ),
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    assert ledger.bundle_wide_max_entries(bundle_dir) == 2


# --- find_absorber (issue #562) --------------------------------------------


def test_find_absorber_returns_the_survivor_whose_ledger_absorbed_the_id(
    tmp_path: Path,
) -> None:
    """Reverse lookup across every committed sidecar (issue #562): the
    chained-merge case -- `concepts/mid` absorbed `concepts/leaf`, then
    `concepts/top` absorbed `concepts/mid` -- must resolve `concepts/mid`'s
    absorber to `concepts/top`, because an absorbed ex-survivor's OWN
    sidecar survives its absorption on disk."""
    bundle_dir = tmp_path / "bundle"
    _write_ledger(bundle_dir, "concepts/mid", [_make_entry("concepts/leaf")])
    _write_ledger(bundle_dir, "concepts/top", [_make_entry("concepts/mid")])

    assert ledger.find_absorber("concepts/mid", bundle_dir) == "concepts/top"
    assert ledger.find_absorber("concepts/leaf", bundle_dir) == "concepts/mid"


def test_find_absorber_returns_none_when_no_ledger_mentions_the_id(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_ledger(bundle_dir, "concepts/survivor", [_make_entry("concepts/absorbed")])

    assert ledger.find_absorber("concepts/unrelated", bundle_dir) is None


def test_find_absorber_returns_none_without_a_ledger_root(tmp_path: Path) -> None:
    """A bundle where no merge has ever run has no ledger root at all --
    `None`, never a raised error (mirrors `iter_ledgers`)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert ledger.find_absorber("concepts/anything", bundle_dir) is None


def test_find_absorber_skips_a_sidecar_with_a_malformed_survivor_id(
    tmp_path: Path,
) -> None:
    """A sidecar whose `survivor_id` is missing or non-string is skipped
    defensively (mirrors `bundle_wide_max_entries`' posture), never raised
    over and never returned as an absorber."""
    bundle_dir = tmp_path / "bundle"
    malformed = ledger.ledger_path_for("concepts/broken", bundle_dir)
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text(
        okf.dump_frontmatter(
            {
                "schema": ledger.LEDGER_SIDECAR_SCHEMA,
                "merged_from": okf.encode_merged_from(
                    [_make_entry("concepts/absorbed")]
                ),
            }
        ),
        encoding="utf-8",
    )

    assert ledger.find_absorber("concepts/absorbed", bundle_dir) is None


def test_find_absorber_skips_a_sidecar_whose_entries_fail_to_decode(
    tmp_path: Path,
) -> None:
    """A sidecar whose `merged_from` entries fail to decode (here: an
    unsupported entry schema version, the exact fail-closed `ValueError`
    `okf.decode_merge_ledger_entry` raises) is skipped defensively -- the
    breadcrumb lookup must keep walking and still resolve an absorber from
    a HEALTHY sidecar, never let one broken unrelated ledger break the
    lookup for a different survivor (review finding, issue #562)."""
    bundle_dir = tmp_path / "bundle"
    undecodable = ledger.ledger_path_for("concepts/broken", bundle_dir)
    undecodable.parent.mkdir(parents=True, exist_ok=True)
    undecodable.write_text(
        okf.dump_frontmatter(
            {
                "schema": ledger.LEDGER_SIDECAR_SCHEMA,
                "survivor_id": "concepts/broken",
                "merged_from": [{"schema": "openkos.merge_ledger/v99"}],
            }
        ),
        encoding="utf-8",
    )
    _write_ledger(bundle_dir, "concepts/top", [_make_entry("concepts/mid")])

    assert ledger.find_absorber("concepts/mid", bundle_dir) == "concepts/top"
    assert ledger.find_absorber("concepts/never-absorbed", bundle_dir) is None


def test_find_absorber_skips_a_sidecar_with_unparseable_frontmatter(
    tmp_path: Path,
) -> None:
    """A sidecar whose frontmatter is not even valid YAML raises
    `yaml.YAMLError` from `okf.load_frontmatter` -- neither `OSError` nor
    `ValueError` -- so without a defensive skip it would escape as a raw
    traceback. The lookup must skip it and keep walking (review finding,
    issue #562)."""
    bundle_dir = tmp_path / "bundle"
    garbled = ledger.ledger_path_for("concepts/garbled", bundle_dir)
    garbled.parent.mkdir(parents=True, exist_ok=True)
    garbled.write_text("---\nsurvivor_id: [unclosed\n---\n", encoding="utf-8")
    _write_ledger(bundle_dir, "concepts/top", [_make_entry("concepts/mid")])

    assert ledger.find_absorber("concepts/mid", bundle_dir) == "concepts/top"


def test_find_absorber_skips_an_unreadable_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar whose bytes cannot be read at all (`OSError`, e.g. a
    permission-denied file) is skipped defensively like the unparseable and
    undecodable cases -- the walk keeps going and still resolves an
    absorber from a HEALTHY sidecar (review follow-up, issue #562)."""
    bundle_dir = tmp_path / "bundle"
    locked = ledger.ledger_path_for("concepts/locked", bundle_dir)
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text("irrelevant", encoding="utf-8")
    _write_ledger(bundle_dir, "concepts/top", [_make_entry("concepts/mid")])

    original_read_text = Path.read_text

    def denying_read_text(self: Path, encoding: str | None = None) -> str:
        if self.name == "locked.ledger.okf":
            raise OSError(13, "Permission denied")
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", denying_read_text)

    assert ledger.find_absorber("concepts/mid", bundle_dir) == "concepts/top"


# --- broken-sidecar resilience of the sibling walkers (#562 follow-up) ------


def _write_broken_sidecar_pair(bundle_dir: Path) -> None:
    """One sidecar with unparseable YAML frontmatter plus one whose entries
    carry an unsupported schema version -- the two failure classes a
    bundle-wide walk must survive."""
    garbled = ledger.ledger_path_for("concepts/garbled", bundle_dir)
    garbled.parent.mkdir(parents=True, exist_ok=True)
    garbled.write_text("---\nsurvivor_id: [unclosed\n---\n", encoding="utf-8")
    undecodable = ledger.ledger_path_for("concepts/broken", bundle_dir)
    undecodable.write_text(
        okf.dump_frontmatter(
            {
                "schema": ledger.LEDGER_SIDECAR_SCHEMA,
                "survivor_id": "concepts/broken",
                "merged_from": [{"schema": "openkos.merge_ledger/v99"}],
            }
        ),
        encoding="utf-8",
    )


def test_scan_nesting_violations_skips_a_broken_sidecar(tmp_path: Path) -> None:
    """One unparseable or undecodable sidecar anywhere in the bundle must
    not crash `doctor`'s Check B (or the merge-time gate that reuses it):
    the scan skips it defensively and still reports over every HEALTHY
    sidecar (review follow-up, issue #562)."""
    bundle_dir = tmp_path / "bundle"
    _write_broken_sidecar_pair(bundle_dir)
    _write_ledger(bundle_dir, "concepts/top", [_make_entry("concepts/mid")])

    assert ledger.scan_nesting_violations(bundle_dir) == []


def test_scan_nesting_violations_skips_only_the_undecodable_embedded_entry(
    tmp_path: Path,
) -> None:
    """An embedded snapshot that fails to parse mid-scan (garbled YAML at
    index 1) must skip ONLY that entry, never abort the rest of the same
    sidecar's scan: the real violation at index 2 is still reported
    (focus-lens correction, issue #562)."""
    bundle_dir = tmp_path / "bundle"

    def legacy_entry(absorbed_id: str, survivor_before: str) -> okf.MergeLedgerEntry:
        return okf.MergeLedgerEntry(
            schema=okf.MERGE_LEDGER_SCHEMA_V3,
            merged_at="2026-07-21T00:00:00Z",
            absorbed_id=absorbed_id,
            absorbed_snapshot="absorbed text",
            survivor_before=survivor_before,
            index_before="index text",
            log_before="log text",
            link_rewrites=[],
            sensitivity_before="private",
            sensitivity_after="private",
        )

    tampered_embedded = okf.dump_frontmatter(
        {
            "type": "Concept",
            "title": "Survivor",
            "merged_from": okf.encode_merged_from(
                [_make_entry(absorbed_id="concepts/not-the-real-prefix")]
            ),
        }
    )
    entries = [
        _make_entry(absorbed_id="concepts/absorbed-0"),
        legacy_entry(
            "concepts/absorbed-1", "---\nmerged_from: [unclosed\n---\nBody.\n"
        ),
        legacy_entry("concepts/absorbed-2", tampered_embedded),
    ]
    _write_ledger(bundle_dir, "concepts/survivor", entries)

    assert ledger.scan_nesting_violations(bundle_dir) == [("concepts/survivor", 2)]


def test_bundle_wide_max_entries_skips_a_broken_sidecar(tmp_path: Path) -> None:
    """The migration gate's bundle-wide max must survive a broken sidecar
    the same way: skip it, still count every HEALTHY ledger (review
    follow-up, issue #562)."""
    bundle_dir = tmp_path / "bundle"
    _write_broken_sidecar_pair(bundle_dir)
    _write_ledger(
        bundle_dir,
        "concepts/top",
        [_make_entry("concepts/mid"), _make_entry("concepts/leaf")],
    )

    assert ledger.bundle_wide_max_entries(bundle_dir) == 2
