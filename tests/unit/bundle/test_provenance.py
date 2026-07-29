"""Unit tests for `bundle/provenance.py`: the pure orphan-closure helper
used by `forget --scope source`'s Phase A resolution (spec: "Provenance
Descendant Resolution"), AND the find/apply/reverse inbound-provenance
rewrite trio used by `merge`/`unmerge` (spec: "Reversible Inbound-Provenance
Rewiring"; design: rewrite-provenance-on-merge).

`find_provenance_descendants` computes the orphan-after-delete closure: a
candidate concept joins the purge set iff its `provenance` frontmatter list
is NON-EMPTY and a subset of the current purge set. The non-empty guard is
the CRITICAL over-deletion barrier -- see
`test_find_provenance_descendants_empty_provenance_does_not_join` below.

`provenance_reachable` is the deliberately WIDER reporting-scope sibling of
that closure (issue #232): a candidate joins on a NON-EMPTY INTERSECTION
with the accumulated set rather than being a subset of it, so the partial
citer the subset rule fail-closed EXCLUDES is included. See
`test_provenance_reachable_includes_a_citer_provenance_closure_excludes`,
which asserts both relations on one input to pin the divergence.

`find_inbound_provenance_rewrites`/`apply_provenance_rewrites`/
`reverse_provenance_rewrites` mirror `bundle/relations.py`'s trio shape
(whole-file snapshot, drift-checked absolute reversal), scanning `files`
already in memory."""

import pytest

from openkos.bundle import links as bundle_links
from openkos.bundle import provenance, relations
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


# -- provenance_reachable: the REPORTING scope relation (#232) --------------


def test_provenance_reachable_includes_a_citer_provenance_closure_excludes() -> None:
    """THE contrast that motivates the second relation (#232): a citer of
    `[sources/a, ghost]` is fail-closed EXCLUDED from `provenance_closure`
    (the subset rule can never admit it, because `ghost` never enters the
    set), yet it is exactly the concept whose dangling `ghost` entry must be
    reported when `sources/a` is raised. Both functions are asserted on the
    SAME input so the divergence is pinned, not implied."""
    provenance_by_id = {
        "sources/a": frozenset({"raw/a.txt"}),
        "concepts/citer": frozenset({"sources/a", "ghost"}),
    }

    assert provenance.provenance_closure(provenance_by_id, root_ids=["sources/a"]) == [
        "sources/a"
    ]
    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a"]
    ) == [
        "concepts/citer",
        "sources/a",
    ]


def test_provenance_reachable_is_transitive_through_an_excluded_citer() -> None:
    """Reachability keeps walking THROUGH a fail-closed-excluded citer: the
    citer's own descendant is in the reporting scope too, even though
    neither joins `provenance_closure`."""
    provenance_by_id = {
        "concepts/citer": frozenset({"sources/a", "ghost"}),
        "concepts/grandchild": frozenset({"concepts/citer"}),
    }

    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a"]
    ) == [
        "concepts/citer",
        "concepts/grandchild",
        "sources/a",
    ]


def test_provenance_reachable_excludes_an_unrelated_sources_subtree() -> None:
    """An unrelated Source cites its own raw resource -- disjoint from the
    invoked Source's reachable set -- so neither it nor anything derived
    from it enters the reporting scope (#232's whole point)."""
    provenance_by_id = {
        "sources/a": frozenset({"raw/a.txt"}),
        "sources/b": frozenset({"raw/b.txt"}),
        "concepts/only-b": frozenset({"sources/b", "ghost"}),
    }

    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a"]
    ) == ["sources/a"]


def test_provenance_reachable_empty_provenance_never_joins() -> None:
    """An empty provenance set has an empty intersection with ANY set, so
    the non-empty-intersection rule keeps a provenance-less concept out --
    the same barrier `provenance_closure`'s non-empty guard provides."""
    provenance_by_id: dict[str, frozenset[str]] = {
        "concepts/no-provenance": frozenset(),
    }

    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a"]
    ) == ["sources/a"]


def test_provenance_reachable_normalizes_a_md_suffixed_root_id() -> None:
    """A `.md`-suffixed `root_ids` entry compares on the canonical id,
    exactly as `provenance_closure` normalizes its own roots (the
    `provenance_by_id` values arrive already normalized from
    `_parse_provenance_by_id`, so neither function re-normalizes them)."""
    provenance_by_id = {
        "concepts/child": frozenset({"sources/a"}),
    }

    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a.md"]
    ) == ["concepts/child", "sources/a"]


def test_provenance_reachable_terminates_on_a_provenance_cycle() -> None:
    """A cycle disjoint from every root never joins, and a cycle reachable
    from a root joins once and stops -- the accumulated set only grows over
    a finite universe, so the fixpoint always halts."""
    provenance_by_id = {
        "concepts/x": frozenset({"concepts/y"}),
        "concepts/y": frozenset({"concepts/x", "sources/a"}),
    }

    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/a"]
    ) == [
        "concepts/x",
        "concepts/y",
        "sources/a",
    ]
    assert provenance.provenance_reachable(
        provenance_by_id, root_ids=["sources/unrelated"]
    ) == ["sources/unrelated"]


# -- find_inbound_provenance_rewrites (tasks 2.1-2.4) -----------------------


def test_find_inbound_provenance_rewrites_records_whole_file_snapshot() -> None:
    """Requirement: "Reversible Inbound-Provenance Rewiring" -- a file whose
    `provenance:` names the absorbed id gets one `ProvenanceRewrite`
    recording that file's ORIGINAL, pre-merge full text."""
    text = _doc({"provenance": ["sources/absorbed"]}, "Other body.")
    files = {"concepts/other.md": text}

    rewrites = provenance.find_inbound_provenance_rewrites(
        files, absorbed_id="sources/absorbed", survivor_id="sources/survivor"
    )

    assert rewrites == [okf.ProvenanceRewrite(file="concepts/other.md", snapshot=text)]


def test_find_inbound_provenance_rewrites_ignores_non_matching_provenance() -> None:
    """A file whose `provenance:` names an unrelated id is left out."""
    text = _doc({"provenance": ["sources/unrelated"]}, "Other body.")
    files = {"concepts/other.md": text}

    rewrites = provenance.find_inbound_provenance_rewrites(
        files, absorbed_id="sources/absorbed", survivor_id="sources/survivor"
    )

    assert rewrites == []


def test_find_inbound_provenance_rewrites_excludes_survivor_and_absorbed_files() -> (
    None
):
    """Neither merge participant's own `provenance:` is this scan's concern
    -- the absorbed file is deleted, and the survivor's own provenance is
    handled by `build_merged_document`'s generic list-union."""
    survivor_doc = _doc({"provenance": ["sources/absorbed"]}, "Survivor body.")
    absorbed_doc = _doc({"provenance": ["sources/absorbed"]}, "Absorbed body.")
    third_party_doc = _doc({"provenance": ["sources/absorbed"]}, "Foo body.")
    files = {
        "concepts/survivor.md": survivor_doc,
        "sources/absorbed.md": absorbed_doc,
        "notes/foo.md": third_party_doc,
    }

    rewrites = provenance.find_inbound_provenance_rewrites(
        files, absorbed_id="sources/absorbed", survivor_id="concepts/survivor"
    )

    assert [rw.file for rw in rewrites] == ["notes/foo.md"]


def test_find_inbound_provenance_rewrites_skips_malformed_frontmatter() -> None:
    """A file with malformed/unparseable frontmatter is SKIPPED rather than
    crashing or refusing the scan (mirrors
    `find_inbound_relation_rewrites`'s identical broad-except skip)."""
    files = {
        "concepts/broken.md": "---\nnot: [valid: yaml: at all\n---\nBody.\n",
        "concepts/clean.md": _doc({"provenance": ["sources/absorbed"]}, "Clean body."),
    }

    rewrites = provenance.find_inbound_provenance_rewrites(
        files, absorbed_id="sources/absorbed", survivor_id="sources/survivor"
    )

    assert [rw.file for rw in rewrites] == ["concepts/clean.md"]


def test_find_inbound_provenance_rewrites_skips_malformed_provenance_shape() -> None:
    """A file whose frontmatter parses but whose `provenance:` is not a list
    is likewise skipped -- same broad-except rationale as above."""
    files = {
        "concepts/malformed-provenance.md": _doc({"provenance": "not-a-list"}, "Body.")
    }

    rewrites = provenance.find_inbound_provenance_rewrites(
        files, absorbed_id="sources/absorbed", survivor_id="sources/survivor"
    )

    assert rewrites == []


def test_find_inbound_provenance_rewrites_not_gated_on_absorbed_type() -> None:
    """Requirement: "This scan MUST NOT be gated on the absorbed concept's
    `type`" -- `query --save` can file ANY cited concept id, Source or not,
    as another object's `provenance`. Absorbing a non-Source concept must
    still retarget a third party's provenance entry naming it (dedicated
    scenario, NOT a clause of the whole-file-snapshot test above)."""
    text = _doc({"provenance": ["concepts/absorbed-decision"]}, "Other body.")
    files = {
        "concepts/absorbed-decision.md": _doc({"type": "Decision"}, "Absorbed."),
        "concepts/other.md": text,
    }

    rewrites = provenance.find_inbound_provenance_rewrites(
        files,
        absorbed_id="concepts/absorbed-decision",
        survivor_id="concepts/survivor",
    )

    assert [rw.file for rw in rewrites] == ["concepts/other.md"]


# -- apply_provenance_rewrites: retarget-then-dedupe matrix (tasks 2.6-2.7) -


def _make_provenance_doc(entries: list[str]) -> str:
    return _doc({"provenance": entries}, "Other body.")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(["sources/absorbed"], ["sources/survivor"], id="single-absorbed"),
        pytest.param(
            ["sources/absorbed", "x", "sources/survivor"],
            ["sources/survivor", "x"],
            id="absorbed-then-x-then-survivor",
        ),
        pytest.param(
            ["sources/survivor", "x", "sources/absorbed"],
            ["sources/survivor", "x"],
            id="survivor-then-x-then-absorbed",
        ),
        pytest.param(
            ["sources/absorbed", "x", "sources/absorbed.md"],
            ["sources/survivor", "x"],
            id="absorbed-repeated-with-md-suffix-variant",
        ),
        pytest.param(["x", "y"], ["x", "y"], id="no-rewrite-when-not-cited"),
    ],
)
def test_apply_provenance_rewrites_retarget_then_dedupe_matrix(
    before: list[str], after: list[str]
) -> None:
    """Design: retarget-then-dedupe, first-occurrence-wins, keyed on the
    normalized id. A list naming both survivor and absorbed collapses to one
    survivor entry at the EARLIER position; every other entry keeps its
    relative order; the absorbed id repeated (including `.md`-suffixed)
    collapses to one entry."""
    text = _make_provenance_doc(before)
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=text)
    rewrites = [rewrite] if before != ["x", "y"] else []

    result = provenance.apply_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=rewrites,
    )

    metadata, _ = okf.load_frontmatter(result)
    assert metadata["provenance"] == after


def test_apply_provenance_rewrites_no_op_when_file_not_in_rewrites() -> None:
    """A file not recorded by `find_inbound_provenance_rewrites` is
    returned byte-identical -- nothing to retarget."""
    text = _make_provenance_doc(["sources/absorbed"])
    other_rewrite = okf.ProvenanceRewrite(file="concepts/unrelated.md", snapshot=text)

    result = provenance.apply_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[other_rewrite],
    )

    assert result == text


def test_apply_provenance_rewrites_never_empty_no_pop_key_branch() -> None:
    """Design: the result is provably never empty (find_* only records files
    holding >= 1 absorbed entry), so there is no pop-the-key branch --
    `provenance:` always stays present after a retarget."""
    text = _make_provenance_doc(["sources/absorbed"])
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=text)

    result = provenance.apply_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert "provenance" in metadata
    assert metadata["provenance"] == ["sources/survivor"]


def test_apply_provenance_rewrites_retained_entries_keep_original_string_form() -> None:
    """Retained (non-retargeted) entries keep their original string form --
    e.g. a `.md`-suffixed unrelated entry is not normalized away."""
    text = _make_provenance_doc(["sources/absorbed", "sources/other.md"])
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=text)

    result = provenance.apply_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert metadata["provenance"] == ["sources/survivor", "sources/other.md"]


# -- reverse_provenance_rewrites (tasks 2.8-2.9) -----------------------------


def test_reverse_provenance_rewrites_restores_recorded_snapshot_exactly() -> None:
    """`reverse_provenance_rewrites` restores the recorded whole-file
    snapshot verbatim -- an ABSOLUTE overwrite, ignoring the passed-in
    (rewritten) text entirely."""
    original = _make_provenance_doc(["sources/absorbed"])
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=original)
    rewritten = provenance.apply_provenance_rewrites(
        original,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
    )
    assert rewritten != original  # sanity: the rewrite actually changed the text

    restored = provenance.reverse_provenance_rewrites(
        rewritten,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
        link_rewrites=[],
        relation_rewrites=[],
    )

    assert restored == original


def test_reverse_provenance_rewrites_no_op_when_file_not_in_rewrites() -> None:
    """A file with no matching recorded rewrite is returned unchanged; no
    drift check applies since there is nothing recorded to compare
    against."""
    text = "some arbitrary text\n"
    rewrite = okf.ProvenanceRewrite(file="concepts/unrelated.md", snapshot="snapshot\n")

    result = provenance.reverse_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
        link_rewrites=[],
        relation_rewrites=[],
    )

    assert result == text


def test_reverse_provenance_rewrites_rejects_more_than_one_snapshot_for_same_file() -> (
    None
):
    """More than one recorded snapshot for the SAME file within one ledger
    entry is a construction bug -- fails closed rather than picking one
    silently."""
    rewrites = [
        okf.ProvenanceRewrite(file="concepts/other.md", snapshot="first\n"),
        okf.ProvenanceRewrite(file="concepts/other.md", snapshot="second\n"),
    ]

    with pytest.raises(ValueError, match="more than one"):
        provenance.reverse_provenance_rewrites(
            "text\n",
            file="concepts/other.md",
            survivor_id="sources/survivor",
            absorbed_id="sources/absorbed",
            rewrites=rewrites,
            link_rewrites=[],
            relation_rewrites=[],
        )


def test_reverse_provenance_rewrites_fails_closed_on_drifted_current_text() -> None:
    """A file's current on-disk content that does not match what this merge
    deterministically wrote raises `ValueError` rather than silently
    clobbering a legitimate post-merge edit."""
    original = _make_provenance_doc(["sources/absorbed"])
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=original)
    drifted_current_text = _make_provenance_doc(
        ["sources/survivor", "sources/elsewhere"]
    )

    with pytest.raises(ValueError, match="drifted"):
        provenance.reverse_provenance_rewrites(
            drifted_current_text,
            file="concepts/other.md",
            survivor_id="sources/survivor",
            absorbed_id="sources/absorbed",
            rewrites=[rewrite],
            link_rewrites=[],
            relation_rewrites=[],
        )


def test_reverse_provenance_rewrites_recomputes_forward_through_link_and_relation() -> (
    None
):
    """`reverse_provenance_rewrites` recomputes expected post-merge bytes
    FORWARD through `merge_core`'s exact chain order (link -> relation ->
    provenance) before comparing against the current on-disk `text` -- a
    file touched by all three rewrite kinds must not produce a false drift
    positive."""
    original = _doc(
        {
            "relations": [{"target": "concepts/absorbed", "type": "depends_on"}],
            "provenance": ["concepts/absorbed"],
        },
        "See [Absorbed](/concepts/absorbed.md) for details.",
    )
    provenance_rewrite = okf.ProvenanceRewrite(
        file="concepts/other.md", snapshot=original
    )
    relation_rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=original)
    link_rewrite = okf.LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=original.index("/concepts/absorbed.md") - 1,
    )

    # What `merge_core`'s chain actually writes: link -> relation ->
    # provenance, applied in order over the same in-memory text.
    after_link = bundle_links.apply_link_rewrites(
        original, file="concepts/other.md", rewrites=[link_rewrite]
    )
    after_relation = relations.apply_relation_rewrites(
        after_link,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[relation_rewrite],
    )
    current_on_disk = provenance.apply_provenance_rewrites(
        after_relation,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[provenance_rewrite],
    )

    restored = provenance.reverse_provenance_rewrites(
        current_on_disk,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[provenance_rewrite],
        link_rewrites=[link_rewrite],
        relation_rewrites=[relation_rewrite],
    )

    assert restored == original
