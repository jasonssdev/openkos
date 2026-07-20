"""Unit tests for `resolution/candidates.py`: the read-only, whole-bundle
entity-resolution candidate generator.

`find_candidates` walks a bundle via `okf._iter_docs` (mirroring
`state/fts.py`'s D2-shaped skip contract), partitions non-Source concept
documents by their EXACT declared OKF `type`, and proposes candidate
GROUPS within each partition: HIGH (an exact shared `normalize_key`) and
LOW (a `similarity.is_near_match`, excluding any pair already HIGH). Output
is ephemeral -- frozen dataclasses only -- and this module never writes a
byte of the bundle. The full good-life-demo integration proof is Unit 2;
this file's read-only proof uses a small fixture bundle only.
"""

import dataclasses
from pathlib import Path

import pytest

from openkos.resolution.candidates import CandidateGroup, Tier, find_candidates


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


# --- scaffold ---------------------------------------------------------------


def test_candidate_group_is_a_frozen_dataclass() -> None:
    """`CandidateGroup` carries `okf_type`/`member_ids`/`tier`/`trigger`, and
    is immutable."""
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stoicism",
    )

    assert group.okf_type == "Concept"
    assert group.member_ids == ("concepts/a", "concepts/b")
    assert group.tier is Tier.HIGH
    assert group.trigger == "stoicism"
    with pytest.raises(dataclasses.FrozenInstanceError):
        group.trigger = "other"  # type: ignore[misc]


def test_tier_has_high_and_low_values() -> None:
    """`Tier` is the HIGH/LOW confidence enum."""
    assert Tier.HIGH.value == "high"
    assert Tier.LOW.value == "low"


# --- whole-bundle scan + concept_id/type/trigger reporting -----------------


def test_high_tier_exact_key_reports_concept_ids_type_and_trigger(
    tmp_path: Path,
) -> None:
    """Two same-type docs with an identical normalized key form a HIGH
    candidate carrying both concept_ids, the shared type, and the
    triggering normalized key."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Café Society")
    _write_doc(bundle_dir / "concepts" / "b.md", title="cafe   society")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    group = groups[0]
    assert group.okf_type == "Concept"
    assert group.member_ids == ("concepts/a", "concepts/b")
    assert group.tier is Tier.HIGH
    assert group.trigger == "cafe society"


def test_high_tier_group_can_have_more_than_two_members(tmp_path: Path) -> None:
    """Three same-type docs sharing one normalized key form a single
    N-member HIGH group, not three separate pairs."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")
    _write_doc(bundle_dir / "concepts" / "c.md", title="stoicism  ")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].tier is Tier.HIGH
    assert groups[0].member_ids == ("concepts/a", "concepts/b", "concepts/c")


def test_low_tier_near_match_reports_similarity_trigger(tmp_path: Path) -> None:
    """A near-match, non-identical pair forms a LOW candidate carrying a
    numeric similarity trigger."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    group = groups[0]
    assert group.tier is Tier.LOW
    assert group.member_ids == ("concepts/a", "concepts/b")
    assert float(group.trigger) >= 0.75


def test_dissimilar_same_type_titles_form_no_candidate(tmp_path: Path) -> None:
    """Two same-type, clearly dissimilar titles produce nothing."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Quantum Electrodynamics")

    assert find_candidates(bundle_dir) == []


# --- strict per-type partitioning --------------------------------------------


def test_partitions_by_exact_type_concept_vs_concept(tmp_path: Path) -> None:
    """Two Concepts near-match; a candidate is produced (baseline same-type
    case, contrasted with the cross-type case below)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", doc_type="Concept", title="Stoicism")
    _write_doc(
        bundle_dir / "concepts" / "b.md", doc_type="Concept", title="Stoic Philosophy"
    )

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].okf_type == "Concept"


def test_cross_type_identical_normalized_title_produces_no_candidate(
    tmp_path: Path,
) -> None:
    """A Concept and an Entity with IDENTICAL normalized titles never form a
    candidate -- type partitioning is strict regardless of similarity."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", doc_type="Concept", title="Stoicism")
    _write_doc(bundle_dir / "entities" / "b.md", doc_type="Entity", title="Stoicism")

    assert find_candidates(bundle_dir) == []


def test_two_different_types_each_with_their_own_matching_pair(
    tmp_path: Path,
) -> None:
    """A same-type match in one type does not leak into a different type's
    partition; each type's matching pair is reported independently."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", doc_type="Concept", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", doc_type="Concept", title="STOICISM")
    _write_doc(bundle_dir / "entities" / "c.md", doc_type="Entity", title="Epictetus")
    _write_doc(bundle_dir / "entities" / "d.md", doc_type="Entity", title="EPICTETUS")

    groups = find_candidates(bundle_dir)

    assert {g.okf_type for g in groups} == {"Concept", "Entity"}
    assert len(groups) == 2


def test_source_documents_are_excluded(tmp_path: Path) -> None:
    """A `Source` document never participates in candidate generation, even
    when it would otherwise match another `Source`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "sources" / "a.md", doc_type="Source", title="A Call")
    _write_doc(bundle_dir / "sources" / "b.md", doc_type="Source", title="A Call")

    assert find_candidates(bundle_dir) == []


# --- no self-pair; pair once; HIGH/LOW disjoint ------------------------------


def test_no_self_pair_and_pair_appears_once(tmp_path: Path) -> None:
    """A matching pair is reported exactly once -- never duplicated as both
    A-B and B-A, and never against itself."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].member_ids[0] != groups[0].member_ids[1]


def test_high_and_low_tiers_are_disjoint_for_a_shared_pair(tmp_path: Path) -> None:
    """A single pair of concept_ids never appears in BOTH a HIGH and a LOW
    candidate: an exact-key pair is reported HIGH only."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")

    groups = find_candidates(bundle_dir)

    high_pairs = {g.member_ids for g in groups if g.tier is Tier.HIGH}
    low_pairs = {g.member_ids for g in groups if g.tier is Tier.LOW}
    assert high_pairs & low_pairs == set()
    assert ("concepts/a", "concepts/b") in high_pairs


# --- ordering + determinism --------------------------------------------------


def test_stable_ordering_ties_broken_by_concept_id(tmp_path: Path) -> None:
    """Regardless of filesystem write order, groups come out ordered by
    type, then HIGH before LOW, then ascending `member_ids`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "z.md", title="Stoic Philosophy")
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "m.md", title="STOICISM")

    groups = find_candidates(bundle_dir)

    # concepts/a + concepts/m share an exact key -> HIGH; concepts/z is a
    # near-match to both -> two LOW pairs. HIGH must precede LOW.
    assert [g.tier for g in groups] == [Tier.HIGH, Tier.LOW, Tier.LOW]
    assert groups[0].member_ids == ("concepts/a", "concepts/m")
    assert groups[1].member_ids == ("concepts/a", "concepts/z")
    assert groups[2].member_ids == ("concepts/m", "concepts/z")


def test_determinism_repeated_runs_yield_identical_results(tmp_path: Path) -> None:
    """Given an unchanged bundle, calling `find_candidates` twice returns
    the same candidate set in the same order."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")

    first = find_candidates(bundle_dir)
    second = find_candidates(bundle_dir)

    assert first == second


# --- degrade, not crash ------------------------------------------------------


def test_degrade_not_crash_on_unreadable_document(tmp_path: Path) -> None:
    """An unreadable (undecodable) document is skipped; a matching pair
    among the remaining valid documents is still returned."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")
    unreadable = bundle_dir / "concepts" / "broken.md"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].member_ids == ("concepts/a", "concepts/b")


def test_degrade_not_crash_on_malformed_frontmatter(tmp_path: Path) -> None:
    """A document with no parseable frontmatter is skipped; a matching pair
    among the remaining valid documents is still returned."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")
    (bundle_dir / "concepts" / "broken.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].member_ids == ("concepts/a", "concepts/b")


def test_missing_type_document_is_skipped(tmp_path: Path) -> None:
    """A document with no (or empty) `type` is excluded from candidate
    consideration, never crashing the pass."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntitle: Stoicism\n---\n# Stoicism\n", encoding="utf-8"
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoicism")

    assert find_candidates(bundle_dir) == []


def test_blank_title_document_is_skipped(tmp_path: Path) -> None:
    """A document with a blank/whitespace-only `title` is excluded."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="   ")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoicism")

    assert find_candidates(bundle_dir) == []


# --- trivial bundles ----------------------------------------------------------


def test_empty_bundle_yields_no_candidates_and_does_not_raise(tmp_path: Path) -> None:
    """A bundle directory with zero documents returns `[]`."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert find_candidates(bundle_dir) == []


def test_single_document_bundle_yields_no_candidates(tmp_path: Path) -> None:
    """A bundle with exactly one concept document of a given type yields no
    candidates for that type."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")

    assert find_candidates(bundle_dir) == []


def test_reserved_filenames_never_participate(tmp_path: Path) -> None:
    """`index.md`/`log.md` are never scanned into candidate consideration."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n---\n", encoding="utf-8"
    )
    (bundle_dir / "log.md").write_text("# 2026-01-01\n", encoding="utf-8")
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")

    assert find_candidates(bundle_dir) == []


# --- read-only proof (core-level, small fixture) -----------------------------


def test_find_candidates_over_small_fixture_bundle_is_read_only(
    tmp_path: Path,
) -> None:
    """Running `find_candidates` over a small fixture bundle never changes
    any file's bytes or mtime, and creates no new file or directory. (The
    full good-life-demo integration proof belongs to Unit 2.)"""
    bundle_dir = tmp_path / "bundle"
    doc_a = bundle_dir / "concepts" / "a.md"
    doc_b = bundle_dir / "concepts" / "b.md"
    _write_doc(doc_a, title="Stoicism")
    _write_doc(doc_b, title="Stoic Philosophy")

    before_paths = set(tmp_path.rglob("*"))
    before_bytes = {p: p.read_bytes() for p in before_paths if p.is_file()}
    before_mtimes = {p: p.stat().st_mtime_ns for p in before_paths if p.is_file()}

    groups = find_candidates(bundle_dir)

    after_paths = set(tmp_path.rglob("*"))
    after_bytes = {p: p.read_bytes() for p in after_paths if p.is_file()}
    after_mtimes = {p: p.stat().st_mtime_ns for p in after_paths if p.is_file()}

    assert len(groups) == 1
    assert after_paths == before_paths
    assert after_bytes == before_bytes
    assert after_mtimes == before_mtimes
