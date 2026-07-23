"""Unit tests for the pure orphan-closure helper (`bundle/provenance.py`),
used by `forget --scope source`'s Phase A resolution (spec: "Provenance
Descendant Resolution").

`find_provenance_descendants` computes the orphan-after-delete closure: a
candidate concept joins the purge set iff its `provenance` frontmatter list
is NON-EMPTY and a subset of the current purge set. The non-empty guard is
the CRITICAL over-deletion barrier -- see
`test_find_provenance_descendants_empty_provenance_does_not_join` below.
"""

from openkos.bundle import provenance
from openkos.model import okf


def _doc(metadata: dict[str, object], body: str = "Body.") -> str:
    base: dict[str, object] = {"type": "Concept"}
    base.update(metadata)
    return okf.dump_frontmatter(base, body)


def test_find_provenance_descendants_single_source_child_joins() -> None:
    """Requirement: Provenance Descendant Resolution -- a concept whose sole
    `provenance` entry is the root joins the purge set."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/child.md": _doc({"provenance": ["sources/x"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["concepts/child", "sources/x"]


def test_find_provenance_descendants_empty_provenance_does_not_join() -> None:
    """THE CRITICAL over-deletion barrier: an empty/absent `provenance` is
    vacuously a subset of ANY set, including the purge set. Without the
    non-empty guard, every concept with no provenance would join and the
    cascade would delete the whole bundle."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/unrelated.md": _doc({}),
        "concepts/also_unrelated.md": _doc({"provenance": []}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]


def test_find_provenance_descendants_multi_source_child_not_joined() -> None:
    """Orphan invariant: a concept with MULTIPLE provenance entries, only
    one of which is in the purge set, must NOT join -- it is not actually
    orphaned by deleting just one of its sources."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "sources/y.md": _doc({"type": "Source"}),
        "concepts/child.md": _doc({"provenance": ["sources/x", "sources/y"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]


def test_find_provenance_descendants_multi_source_child_joins_when_both_purged() -> (
    None
):
    """A concept with multiple provenance entries joins once ALL of its
    sources are already in the purge set."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "sources/y.md": _doc({"type": "Source"}),
        "concepts/child.md": _doc({"provenance": ["sources/x", "sources/y"]}),
    }

    result = provenance.find_provenance_descendants(
        files, root_ids=["sources/x", "sources/y"]
    )

    assert result == ["concepts/child", "sources/x", "sources/y"]


def test_find_provenance_descendants_fixpoint_chain() -> None:
    """A hand-authored multi-level chain (X <- A <- B), each link a
    single-source subset, is fully pulled in by iterating to fixpoint."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/a.md": _doc({"provenance": ["sources/x"]}),
        "concepts/b.md": _doc({"provenance": ["concepts/a"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["concepts/a", "concepts/b", "sources/x"]


def test_find_provenance_descendants_root_with_no_descendants() -> None:
    """A root with no dependent concepts produces a purge set of just the
    root itself."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/unrelated.md": _doc({"provenance": ["sources/other"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]


def test_find_provenance_descendants_multiple_roots() -> None:
    """Multiple root_ids seed the purge set together, and their respective
    single-source children both join."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "sources/y.md": _doc({"type": "Source"}),
        "concepts/child_x.md": _doc({"provenance": ["sources/x"]}),
        "concepts/child_y.md": _doc({"provenance": ["sources/y"]}),
    }

    result = provenance.find_provenance_descendants(
        files, root_ids=["sources/x", "sources/y"]
    )

    assert result == [
        "concepts/child_x",
        "concepts/child_y",
        "sources/x",
        "sources/y",
    ]


def test_find_provenance_descendants_output_is_sorted_regardless_of_input_order() -> (
    None
):
    """Determinism: output is `sorted()`, independent of `files` dict
    insertion order."""
    files = {
        "concepts/z_child.md": _doc({"provenance": ["sources/x"]}),
        "concepts/a_child.md": _doc({"provenance": ["sources/x"]}),
        "sources/x.md": _doc({"type": "Source"}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == sorted(result)
    assert result == ["concepts/a_child", "concepts/z_child", "sources/x"]


def test_find_provenance_descendants_self_referential_provenance_terminates() -> None:
    """Defensive: a concept whose `provenance` names itself can never
    satisfy the subset-of-purge condition before it is already in the purge
    set, so it is never added and the fixpoint still terminates."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/self_ref.md": _doc({"provenance": ["concepts/self_ref"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]


def test_find_provenance_descendants_mutually_referential_provenance_terminates() -> (
    None
):
    """Defensive: two concepts whose `provenance` entries point at each
    other (a cycle disjoint from any root) never join and the fixpoint
    still terminates rather than looping forever."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/a.md": _doc({"provenance": ["concepts/b"]}),
        "concepts/b.md": _doc({"provenance": ["concepts/a"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]


def test_find_provenance_descendants_id_normalization_with_md_suffix() -> None:
    """A `provenance` entry written WITH a `.md` suffix still matches the
    purge set, since ids are normalized consistently on both sides."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/child.md": _doc({"provenance": ["sources/x.md"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["concepts/child", "sources/x"]


def test_find_provenance_descendants_id_normalization_root_with_md_suffix() -> None:
    """`root_ids` themselves are normalized the same way, so a root passed
    WITH a `.md` suffix still matches a `provenance` entry written without
    one."""
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/child.md": _doc({"provenance": ["sources/x"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x.md"])

    assert result == ["concepts/child", "sources/x"]


def test_find_provenance_descendants_empty_provenance_guard_holds_during_real_cascade() -> (
    None
):
    """Regression: the non-empty guard (test above) is only ever exercised
    against a trivial all-empty bundle there. Here a genuine multi-level
    cascade (`sources/s` <- `concepts/a` <- `concepts/b`) grows the purge
    set to a non-trivial `{sources/s, concepts/a, concepts/b}` WHILE an
    unrelated `concepts/loose` with explicit empty `provenance` coexists in
    the same `files` snapshot. A regression that only widened the guard for
    the all-empty case (e.g. by special-casing an empty `purge`) would let
    `loose` join here and would fail this assertion."""
    files = {
        "sources/s.md": _doc({"type": "Source"}),
        "concepts/a.md": _doc({"provenance": ["sources/s"]}),
        "concepts/b.md": _doc({"provenance": ["concepts/a"]}),
        "concepts/loose.md": _doc({"provenance": []}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/s"])

    assert result == ["concepts/a", "concepts/b", "sources/s"]
    assert "concepts/loose" not in result


def test_find_provenance_descendants_orphan_invariant_with_source_added_mid_fixpoint() -> (
    None
):
    """Orphan invariant, mid-fixpoint variant: `concepts/c`'s second
    provenance entry (`concepts/a`) is itself a FIXPOINT-DERIVED descendant,
    not a root. `concepts/c` cannot join on the sweep before `concepts/a`
    has joined `purge`, but MUST join once it has -- the fixpoint loop must
    keep iterating rather than stopping after a single pass.

    Companion assertion (the invariant's teeth): sibling `concepts/d`'s
    second provenance entry (`sources/external`) is a real, existing file
    that is NEVER purged (it is not a root and nothing pulls it in) -- so
    `concepts/d` must never join, even at fixpoint. A regression that
    dropped the subset requirement (e.g. treating ANY overlap with `purge`
    as sufficient) would let `concepts/d` join here and would fail that
    assertion."""
    files = {
        "sources/s.md": _doc({"type": "Source"}),
        "sources/external.md": _doc({"type": "Source"}),
        "concepts/a.md": _doc({"provenance": ["sources/s"]}),
        "concepts/c.md": _doc({"provenance": ["sources/s", "concepts/a"]}),
        "concepts/d.md": _doc({"provenance": ["sources/s", "sources/external"]}),
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/s"])

    assert result == ["concepts/a", "concepts/c", "sources/s"]
    assert "concepts/d" not in result


def test_find_provenance_descendants_unparseable_file_skipped_and_preserved() -> None:
    """A file whose frontmatter fails to parse is skipped rather than
    raising -- fail-safe against over-deletion, mirroring the analogous
    contract in `bundle/references.py`."""
    malformed = "---\ntype: Concept\nprovenance: [sources/x\n---\n\nBody.\n"
    files = {
        "sources/x.md": _doc({"type": "Source"}),
        "concepts/malformed.md": malformed,
    }

    result = provenance.find_provenance_descendants(files, root_ids=["sources/x"])

    assert result == ["sources/x"]
