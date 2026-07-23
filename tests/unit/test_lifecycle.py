"""Unit tests for `lifecycle.py`: the shared effective-status predicate
(status-aware-retrieval, MVP-3 gap #8 · S1, Phase 1/PR1).

`deprecated_concept_ids` is the single canonical-layer leaf every retrieval
input and candidate-load surface filters against (design: "single shared
predicate", `retrieval/answer.py` + `resolution/{contradiction,candidates}.py`
are wired in later phases — this module has no consumer yet). `filter_hits`
is the generic `.concept_id` filter helper reused at every seam.
"""

from dataclasses import dataclass
from pathlib import Path

from openkos import lifecycle


def _write_doc(
    path: Path,
    *,
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    relations_raw: str | None = None,
    body: str = "",
) -> None:
    """Write a minimal concept `.md` file with optional `status:` and
    `relations:` frontmatter. `relations` is a list of `(target, type)`
    pairs encoded as the standard `{target, type}` mapping shape;
    `relations_raw` overrides it with a hand-written frontmatter block (for
    malformed-shape cases)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: Stub"]
    if status is not None:
        lines.append(f"status: {status}")
    if relations_raw is not None:
        lines.append(relations_raw)
    elif relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


def test_own_status_deprecated_marks_concept_deprecated(tmp_path: Path) -> None:
    """A concept with `status: deprecated` and no supersedes edges is
    deprecated (spec: "status field alone marks deprecated")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "old.md", status="deprecated")

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset({"concepts/old"})


def test_superseded_target_deprecated_regardless_of_own_status(
    tmp_path: Path,
) -> None:
    """B is the target of A's outbound `supersedes` edge; B is deprecated
    even though its own `status` is `active`, and A (the superseder) stays
    live (spec: "superseded concept is deprecated regardless of its own
    status")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="active")

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset({"concepts/b"})


def test_self_referencing_supersedes_edge_is_guarded_to_live(
    tmp_path: Path,
) -> None:
    """A `supersedes` edge whose target is its own source never marks the
    concept deprecated (spec: "self-reference and cycles are guarded to
    live")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset()


def test_mutual_two_cycle_marks_both_concepts_deprecated(tmp_path: Path) -> None:
    """A supersedes B and B supersedes A: both are targets of a non-self
    `supersedes` edge, so the fail-safe rule marks BOTH deprecated (spec:
    contradictory/cyclic supersession is treated as unresolved and hidden,
    not exempted by reciprocal cancellation)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset({"concepts/a", "concepts/b"})


def test_three_cycle_marks_all_three_deprecated(tmp_path: Path) -> None:
    """R2 (PINNED): a `supersedes` cycle of ANY length — including length 3
    (A -> B -> C -> A) — is treated as fully deprecated (fail-safe hide).
    None of the three pairs has a reciprocal edge, so reciprocal
    cancellation does not exempt any of them, unlike the 2-cycle case."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        status="active",
        relations=[("concepts/c", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "c.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset({"concepts/a", "concepts/b", "concepts/c"})


def test_four_cycle_with_mutual_chord_marks_all_four_deprecated(
    tmp_path: Path,
) -> None:
    """Regression for a CONFIRMED CRITICAL false-negative: edges
    a->b, b->c, b->a, c->d, d->a form the 4-cycle a->b->c->d->a, PLUS a
    mutual pair (a,b)/(b,a) chording that cycle. Per-edge-pair reciprocal
    cancellation used to cancel the (a,b)/(b,a) pair and let `b` escape
    deprecation even though `b` is a genuine member of the 4-cycle. The
    fail-safe rule (any concept targeted by a non-self supersedes edge is
    deprecated) has no such escape hatch: all four members are deprecated,
    `b` included."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="active",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        status="active",
        relations=[("concepts/c", "supersedes"), ("concepts/a", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "c.md",
        status="active",
        relations=[("concepts/d", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "d.md",
        status="active",
        relations=[("concepts/a", "supersedes")],
    )

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset(
        {"concepts/a", "concepts/b", "concepts/c", "concepts/d"}
    )


def test_malformed_relations_list_contributes_no_edges_and_does_not_crash(
    tmp_path: Path,
) -> None:
    """A `relations:` value that is not a list (fails `okf.decode_relations`
    with `ValueError`) contributes no supersedes edges for that document and
    does not raise out of `deprecated_concept_ids`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "broken.md",
        status="active",
        relations_raw="relations: not-a-list",
    )
    _write_doc(bundle_dir / "concepts" / "fine.md", status="deprecated")

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset({"concepts/fine"})


def test_malformed_relations_entry_contributes_no_edges_and_does_not_crash(
    tmp_path: Path,
) -> None:
    """A `relations:` list whose entry is missing a required field also
    fails closed inside `okf.decode_relations` (`ValueError`) — same
    no-crash, no-edges contract as a non-list value."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "broken.md",
        status="active",
        relations_raw="relations:\n  - type: supersedes",
    )

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset()


def test_all_live_bundle_returns_empty_frozenset(tmp_path: Path) -> None:
    """A bundle with no `status: deprecated` concept and no supersedes edges
    returns an empty set (spec: "all-live bundle is unaffected")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", status="active")
    _write_doc(bundle_dir / "concepts" / "b.md")

    deprecated = lifecycle.deprecated_concept_ids(bundle_dir)

    assert deprecated == frozenset()


@dataclass(frozen=True)
class _FakeHit:
    """Minimal stand-in for `FtsHit`/`VecHit`/`GraphHit`: `filter_hits` only
    ever reads `.concept_id`, so a local fake avoids importing
    `retrieval`/`state` types into this canonical-leaf test module."""

    concept_id: str


def test_filter_hits_removes_deprecated_ids() -> None:
    """`filter_hits` drops any hit whose `concept_id` is in `deprecated`,
    preserving the relative order of the remaining hits."""
    hits = [_FakeHit("concepts/a"), _FakeHit("concepts/b"), _FakeHit("concepts/c")]

    result = lifecycle.filter_hits(hits, frozenset({"concepts/b"}))

    assert result == [_FakeHit("concepts/a"), _FakeHit("concepts/c")]


def test_filter_hits_with_empty_deprecated_set_returns_all_hits_unchanged() -> None:
    """An empty `deprecated` set (all-live bundle) leaves every hit in
    place, in the same order."""
    hits = [_FakeHit("concepts/a"), _FakeHit("concepts/b")]

    result = lifecycle.filter_hits(hits, frozenset())

    assert result == hits
