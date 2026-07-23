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

from openkos import lifecycle
from openkos.resolution.candidates import CandidateGroup, Tier, find_candidates


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
) -> None:
    """Write a minimal `doc_type` document. Optional `status`/`relations`
    lifecycle frontmatter (status-aware-retrieval, Phase 3) -- `relations`
    is a list of `(target, type)` pairs, mirroring
    `test_answer.py`/`test_contradiction.py`'s equivalent helper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {doc_type}", f"title: {title}"]
    if status is not None:
        lines.append(f"status: {status}")
    if relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}# {title}\n", encoding="utf-8")


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


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_BUNDLE = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"


def test_real_bundle_readonly() -> None:
    """Running `find_candidates` over the real `examples/good-life-demo`
    bundle never raises, never changes any bundle file's bytes or mtime,
    creates no new file or directory, and returns a coherent (deterministic,
    self-consistent) result -- the Unit 2 integration proof (tasks.md 5.1)."""
    assert _GOOD_LIFE_BUNDLE.is_dir(), f"missing fixture bundle: {_GOOD_LIFE_BUNDLE}"

    before_paths = set(_GOOD_LIFE_BUNDLE.rglob("*"))
    before_bytes = {p: p.read_bytes() for p in before_paths if p.is_file()}
    before_mtimes = {p: p.stat().st_mtime_ns for p in before_paths if p.is_file()}

    groups = find_candidates(_GOOD_LIFE_BUNDLE)
    groups_again = find_candidates(_GOOD_LIFE_BUNDLE)

    after_paths = set(_GOOD_LIFE_BUNDLE.rglob("*"))
    after_bytes = {p: p.read_bytes() for p in after_paths if p.is_file()}
    after_mtimes = {p: p.stat().st_mtime_ns for p in after_paths if p.is_file()}

    # Read-only: nothing under the bundle changed or was created.
    assert after_paths == before_paths
    assert after_bytes == before_bytes
    assert after_mtimes == before_mtimes

    # Deterministic repeated runs (spec: Repeated runs are deterministic).
    assert groups == groups_again

    # Coherent result: every group is well-formed per its own invariants.
    for group in groups:
        assert isinstance(group, CandidateGroup)
        assert group.okf_type != "Source"
        assert len(group.member_ids) >= 2
        assert len(set(group.member_ids)) == len(group.member_ids)
        assert group.member_ids == tuple(sorted(group.member_ids))
        assert group.trigger


# ---------------------------------------------------------------------------
# Phase 3 (status-aware-retrieval, PR3): `include_deprecated` on
# `find_candidates` -- deprecated/superseded concepts are excluded BEFORE
# HIGH/LOW pairing by default (design: "exclude deprecated ids from
# `find_candidates` before pairing"), `include_deprecated=True` restores
# them and skips the predicate walk, and an all-live bundle is unaffected.
# ---------------------------------------------------------------------------


def test_deprecated_own_status_excluded_from_high_group(tmp_path: Path) -> None:
    """A concept deprecated via its own `status` field never joins a HIGH
    exact-key group -- with only one live member left for that key, no group
    forms at all."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM", status="deprecated")

    assert find_candidates(bundle_dir) == []


def test_concept_superseded_by_another_excluded_from_low_group(
    tmp_path: Path,
) -> None:
    """A concept that is the TARGET of another concept's `supersedes` edge
    is deprecated regardless of its own status, and is excluded from a LOW
    near-match group the same way."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="Stoicism",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")

    assert find_candidates(bundle_dir) == []


def test_live_ids_paired_normally_excluding_a_deprecated_group_member(
    tmp_path: Path,
) -> None:
    """GIVEN three same-key concepts, one deprecated, WHEN `find_candidates`
    runs, THEN the HIGH group contains only the two live members -- the
    deprecated member is dropped, not the whole group."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")
    _write_doc(bundle_dir / "concepts" / "c.md", title="stoicism", status="deprecated")

    groups = find_candidates(bundle_dir)

    assert len(groups) == 1
    assert groups[0].tier is Tier.HIGH
    assert groups[0].member_ids == ("concepts/a", "concepts/b")


def test_include_deprecated_true_restores_the_excluded_member(
    tmp_path: Path,
) -> None:
    """`include_deprecated=True` restores a deprecated/superseded concept to
    full participation in candidate grouping."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="Stoicism",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")

    groups = find_candidates(bundle_dir, include_deprecated=True)

    assert len(groups) == 1
    assert groups[0].tier is Tier.LOW
    assert groups[0].member_ids == ("concepts/a", "concepts/b")


def test_include_deprecated_true_never_calls_the_predicate_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_deprecated=True` skips `lifecycle.deprecated_concept_ids`
    entirely (spy) -- the escape flag is the zero-cost / status-blind path
    (design R1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="Stoicism",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    find_candidates(bundle_dir, include_deprecated=True)

    assert walk_calls == []


def test_default_include_deprecated_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_deprecated=False` DOES call
    `lifecycle.deprecated_concept_ids` exactly once per `find_candidates`
    call (mirrors `answer()`/`find_contradictions`'s equivalent contract)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="STOICISM")
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    find_candidates(bundle_dir)

    assert walk_calls == [bundle_dir]


def test_all_live_bundle_is_identical_with_and_without_include_deprecated(
    tmp_path: Path,
) -> None:
    """A bundle where every concept's effective status is live produces the
    identical candidate-group result whether `include_deprecated` is `False`
    (the default) or `True` (spec: All-live bundle is unaffected)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Stoic Philosophy")

    default_groups = find_candidates(bundle_dir)
    included_groups = find_candidates(bundle_dir, include_deprecated=True)

    assert default_groups == included_groups
    assert len(default_groups) == 1
