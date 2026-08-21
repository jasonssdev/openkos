"""Unit tests for `bundle/decisions.py` (pending-work design, Decisions 3-4;
tasks A2.1-A2.4): `decision_key_for`'s stable identity, and the
`bundle/.state/decisions/<pair_ids[0]>.decisions.okf` path mapping mirroring
`okf.concept_path_for`'s own suite (NFC/NFD on a byte-exact filesystem).

Deliberately UNWIRED to any CLI verb in this slice (tasks.md slicing
rationale, maintainer decision D6) -- these tests build fixtures directly
via `write_decisions`, never through a command."""

import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.bundle import decisions
from openkos.model import okf

_NFC_STEM = "Café"  # "e" + COMBINING ACUTE ACCENT is what NFC folds
_NFD_STEM = unicodedata.normalize("NFD", _NFC_STEM)
_NFC_STEM = unicodedata.normalize("NFC", _NFC_STEM)


# --- A2.1: decision_key_for -------------------------------------------------


def test_decision_key_for_is_a_stable_32_hex_char_digest() -> None:
    key = decisions.decision_key_for(("concepts/a", "concepts/b"), None)
    assert len(key) == 32
    int(key, 16)  # raises ValueError if not hex
    assert key == decisions.decision_key_for(("concepts/a", "concepts/b"), None)


def test_decision_key_for_differs_by_merged_absorbed_id() -> None:
    """Decision 3: `merged_absorbed_id` is the SOLE discriminator between a
    typed-edge and a merged-body candidate over the same `pair_ids` --
    `pair_ids` shape alone is not a safe stand-in."""
    typed_edge_key = decisions.decision_key_for(("concepts/a", "concepts/a"), None)
    merged_body_key = decisions.decision_key_for(
        ("concepts/a", "concepts/a"), "concepts/absorbed"
    )
    assert typed_edge_key != merged_body_key


def test_decision_key_for_differs_by_pair_ids() -> None:
    assert decisions.decision_key_for(
        ("concepts/a", "concepts/b"), None
    ) != decisions.decision_key_for(("concepts/a", "concepts/c"), None)


# --- A2.3: decisions_path_for (mirrors concept_path_for's own suite) -------


def test_decisions_path_for_returns_the_direct_path_when_it_exists(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    decisions_dir = bundle_dir / okf.STATE_DIRNAME / "decisions" / "concepts"
    decisions_dir.mkdir(parents=True)
    expected = decisions_dir / "a.decisions.okf"
    expected.write_text("body", encoding="utf-8")

    assert decisions.decisions_path_for("concepts/a", bundle_dir) == expected


def test_decisions_path_for_falls_back_to_the_direct_path_when_nothing_matches(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    resolved = decisions.decisions_path_for("concepts/absent", bundle_dir)

    assert resolved == bundle_dir / okf.STATE_DIRNAME / "decisions" / "concepts" / (
        "absent.decisions.okf"
    )


def test_decisions_path_for_finds_a_decomposed_name_from_an_nfc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    decisions_dir = bundle_dir / okf.STATE_DIRNAME / "decisions" / "concepts"
    decisions_dir.mkdir(parents=True)
    on_disk = decisions_dir / f"{_NFD_STEM}.decisions.okf"
    on_disk.write_text("body", encoding="utf-8")

    monkeypatch.setattr(Path, "exists", lambda self: False)

    resolved = decisions.decisions_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved.name == f"{_NFD_STEM}.decisions.okf"
    assert unicodedata.normalize("NFC", resolved.name) == f"{_NFC_STEM}.decisions.okf"


def test_decisions_path_for_tolerates_an_unreadable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    decisions_dir = bundle_dir / okf.STATE_DIRNAME / "decisions" / "concepts"
    decisions_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "exists", lambda self: False)

    def _boom(self: Path) -> Iterator[Path]:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", _boom)

    resolved = decisions.decisions_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved == decisions_dir / f"{_NFC_STEM}.decisions.okf"


def test_decisions_path_for_never_resolves_a_symlink_through_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    decisions_dir = bundle_dir / okf.STATE_DIRNAME / "decisions" / "concepts"
    decisions_dir.mkdir(parents=True)
    outside = tmp_path / "outside.decisions.okf"
    outside.write_text("secret", encoding="utf-8")
    (decisions_dir / f"{_NFD_STEM}.decisions.okf").symlink_to(outside)

    monkeypatch.setattr(Path, "exists", lambda self: False)

    resolved = decisions.decisions_path_for(f"concepts/{_NFC_STEM}", bundle_dir)

    assert resolved == decisions_dir / f"{_NFC_STEM}.decisions.okf"
    assert resolved.name != f"{_NFD_STEM}.decisions.okf"


# --- A2.4: decisions_root, read_decisions, write_decisions, iter_decisions -


def test_decisions_root_is_state_decisions(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    assert decisions.decisions_root(bundle_dir) == (
        bundle_dir / okf.STATE_DIRNAME / "decisions"
    )


def test_write_then_read_decisions_round_trips(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    record = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )

    path = decisions.write_decisions("concepts/a", bundle_dir, records=[record])

    assert path.is_file()
    assert path.suffix == ".okf"
    assert path.name.endswith(".decisions.okf")

    read_back = decisions.read_decisions("concepts/a", bundle_dir)
    assert read_back == [record]


def test_write_decisions_container_is_a_frontmatter_document_with_empty_body(
    tmp_path: Path,
) -> None:
    """ADR-0002 invariant 3, preserved literally: the container is a
    frontmatter document over an empty body, written via
    `okf.dump_frontmatter`."""
    bundle_dir = tmp_path / "bundle"
    record = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )

    path = decisions.write_decisions("concepts/a", bundle_dir, records=[record])
    _, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    assert body == ""


def test_write_decisions_replaces_the_full_record_set(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    first = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    second = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(
            ("concepts/a", "concepts/c"), "concepts/absorbed"
        ),
        pair_ids=("concepts/a", "concepts/c"),
        merged_absorbed_id="concepts/absorbed",
        state="open",
        decided_at="2026-08-12T01:00:00Z",
    )

    decisions.write_decisions("concepts/a", bundle_dir, records=[first])
    decisions.write_decisions("concepts/a", bundle_dir, records=[first, second])

    read_back = decisions.read_decisions("concepts/a", bundle_dir)
    assert read_back == [first, second]


def test_write_decisions_with_empty_list_removes_the_file(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    record = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    path = decisions.write_decisions("concepts/a", bundle_dir, records=[record])
    assert path.is_file()

    decisions.write_decisions("concepts/a", bundle_dir, records=[])
    assert not path.is_file()


def test_read_decisions_for_a_concept_with_no_file_returns_empty_list(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    assert decisions.read_decisions("concepts/absent", bundle_dir) == []


def test_iter_decisions_lists_every_committed_file_sorted(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    record_a = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    record_z = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/z", "concepts/y"), None),
        pair_ids=("concepts/y", "concepts/z"),
        merged_absorbed_id=None,
        state="open",
        decided_at="2026-08-12T00:00:00Z",
    )
    decisions.write_decisions("concepts/z", bundle_dir, records=[record_z])
    decisions.write_decisions("concepts/a", bundle_dir, records=[record_a])

    paths = decisions.iter_decisions(bundle_dir)
    assert paths == sorted(paths)
    assert [p.name for p in paths] == ["a.decisions.okf", "z.decisions.okf"]


def test_iter_decisions_with_no_decisions_root_returns_empty_list(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert decisions.iter_decisions(bundle_dir) == []


# --- #797: identity decisions, the store's second kind ----------------------


def _identity(
    *,
    member_ids: tuple[str, ...] = ("events/a", "events/b"),
    state: decisions.DecisionState = "declined",
    decided_at: str = "2026-08-20T00:00:00+00:00",
) -> decisions.IdentityDecisionRecord:
    return decisions.IdentityDecisionRecord(
        decision_key=decisions.identity_decision_key_for(member_ids),
        member_ids=member_ids,
        state=state,
        decided_at=decided_at,
    )


def test_identity_decision_key_is_a_stable_32_hex_char_digest() -> None:
    key = decisions.identity_decision_key_for(("events/a", "events/b"))
    assert len(key) == 32
    int(key, 16)


def test_identity_decision_key_is_order_independent() -> None:
    """A group is a SET of members; the same two concepts must key equally
    whichever order the candidate report happened to list them in."""
    assert decisions.identity_decision_key_for(
        ("events/b", "events/a")
    ) == decisions.identity_decision_key_for(("events/a", "events/b"))


def test_identity_decision_key_differs_by_member_set() -> None:
    assert decisions.identity_decision_key_for(
        ("events/a", "events/b")
    ) != decisions.identity_decision_key_for(("events/a", "events/c"))
    # An N>2 group is its own ruling, never the 2-member one plus extras.
    assert decisions.identity_decision_key_for(
        ("events/a", "events/b")
    ) != decisions.identity_decision_key_for(("events/a", "events/b", "events/c"))


def test_identity_and_contradiction_keys_never_collide_on_the_same_pair() -> None:
    """ "These do not contradict each other" and "these are not the same
    entity" are OPPOSITE human rulings about one pair. Sharing a key would
    let one silently answer for the other."""
    pair = ("concepts/a", "concepts/b")
    assert decisions.identity_decision_key_for(pair) != decisions.decision_key_for(
        pair, None
    )


def test_write_then_read_identity_decisions_round_trips(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    record = _identity()

    decisions.write_identity_decisions("events/a", bundle_dir, records=[record])
    read_back = decisions.read_identity_decisions("events/a", bundle_dir)

    assert read_back == [record]


def test_identity_records_share_the_sidecar_without_disturbing_contradictions(
    tmp_path: Path,
) -> None:
    """Both kinds live in one file so one privacy sweep covers both, but in
    SEPARATE lists: `_decode_record` requires a 2-item `pair_ids`, so an
    identity row in the `decisions` list would break the contradiction
    reader outright."""
    bundle_dir = tmp_path / "bundle"
    contradiction = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("events/a", "events/b"), None),
        pair_ids=("events/a", "events/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-20T00:00:00+00:00",
    )
    decisions.write_decisions("events/a", bundle_dir, records=[contradiction])

    decisions.write_identity_decisions("events/a", bundle_dir, records=[_identity()])

    assert decisions.read_decisions("events/a", bundle_dir) == [contradiction]
    assert decisions.read_identity_decisions("events/a", bundle_dir) == [_identity()]


def test_writing_contradictions_preserves_existing_identity_records(
    tmp_path: Path,
) -> None:
    """The two writers each own their own list. A full-replace of one kind
    must not silently erase the other kind's rulings from the same file."""
    bundle_dir = tmp_path / "bundle"
    decisions.write_identity_decisions("events/a", bundle_dir, records=[_identity()])

    contradiction = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("events/a", "events/b"), None),
        pair_ids=("events/a", "events/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-20T00:00:00+00:00",
    )
    decisions.write_decisions("events/a", bundle_dir, records=[contradiction])

    assert decisions.read_identity_decisions("events/a", bundle_dir) == [_identity()]
    assert decisions.read_decisions("events/a", bundle_dir) == [contradiction]


def test_a_v1_sidecar_reads_as_no_identity_decisions(tmp_path: Path) -> None:
    """Backward compatibility: a sidecar written before this slice carries
    no identity list at all and must read as empty, never raise."""
    bundle_dir = tmp_path / "bundle"
    contradiction = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("events/a", "events/b"), None),
        pair_ids=("events/a", "events/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-20T00:00:00+00:00",
    )
    decisions.write_decisions("events/a", bundle_dir, records=[contradiction])

    assert decisions.read_identity_decisions("events/a", bundle_dir) == []


def test_empty_identity_records_leaves_the_file_when_contradictions_remain(
    tmp_path: Path,
) -> None:
    """Clearing one kind must not delete a sidecar the other kind still
    needs -- the file is only removed when BOTH lists are empty."""
    bundle_dir = tmp_path / "bundle"
    contradiction = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("events/a", "events/b"), None),
        pair_ids=("events/a", "events/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-20T00:00:00+00:00",
    )
    decisions.write_decisions("events/a", bundle_dir, records=[contradiction])
    decisions.write_identity_decisions("events/a", bundle_dir, records=[_identity()])

    decisions.write_identity_decisions("events/a", bundle_dir, records=[])

    assert decisions.decisions_path_for("events/a", bundle_dir).is_file()
    assert decisions.read_decisions("events/a", bundle_dir) == [contradiction]
    assert decisions.read_identity_decisions("events/a", bundle_dir) == []


def test_clearing_both_kinds_removes_the_sidecar(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    decisions.write_identity_decisions("events/a", bundle_dir, records=[_identity()])

    decisions.write_identity_decisions("events/a", bundle_dir, records=[])

    assert not decisions.decisions_path_for("events/a", bundle_dir).is_file()
