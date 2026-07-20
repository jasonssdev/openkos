"""Unit tests for third-party inbound-relation rewrite/reverse primitives
(`bundle/relations.py`) -- mirrors `tests/unit/bundle/test_links.py`.

`find_inbound_relation_rewrites` is pure scan-only (spec: "Third-party
inbound relations retarget to the survivor"): it reads bundle-relative
text bodies already in memory and records a whole-file `RelationRewrite`
snapshot for each file whose `relations:` targets the absorbed id -- it
never writes anything. A file with malformed/unparseable frontmatter is
skipped, mirroring the deleted `find_relation_conflicts`'s identical
broad-except behavior (PR1). `apply_relation_rewrites` retargets a single
file's own `relations:` (absorbed id -> survivor id), drops a resulting
self-loop (target equal to that file's OWN concept-id, mirroring `relate`
self-id refusal), and dedupes a resulting collision -- pure text-in/
text-out, a no-op unless `file` is recorded in `rewrites`.
`reverse_relation_rewrites` restores a file's recorded whole-file snapshot
verbatim -- an ABSOLUTE overwrite, never offset math (design D1/D3),
unlike the link-rewrite trio.
"""

import pytest

from openkos.bundle import links as bundle_links
from openkos.bundle import relations
from openkos.model import okf


def _doc(metadata: dict[str, object], body: str = "Body.") -> str:
    base: dict[str, object] = {"type": "Concept"}
    base.update(metadata)
    return okf.dump_frontmatter(base, body)


# -- find_inbound_relation_rewrites (task 2.5-2.6) -------------------------


def test_find_inbound_relation_rewrites_records_whole_file_snapshot() -> None:
    """Requirement: "Third-party inbound relations retarget to the
    survivor" -- a file whose `relations:` targets the absorbed id gets one
    `RelationRewrite` recording that file's ORIGINAL, pre-merge full text."""
    text = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Other body.",
    )
    files = {"concepts/other.md": text}

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == [okf.RelationRewrite(file="concepts/other.md", snapshot=text)]


def test_find_inbound_relation_rewrites_ignores_non_matching_relation() -> None:
    """A file whose `relations:` targets an unrelated id is left out."""
    text = _doc(
        {"relations": [{"target": "concepts/unrelated", "type": "related_to"}]},
        "Other body.",
    )
    files = {"concepts/other.md": text}

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == []


def test_find_inbound_relation_rewrites_ignores_file_with_no_relations_key() -> None:
    """Absent `relations:` key is valid and contributes no rewrite."""
    files = {"concepts/other.md": _doc({}, "Other body.")}

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == []


def test_find_inbound_relation_rewrites_across_multiple_files() -> None:
    """The scan walks every file in the mapping, aggregating matches, in
    `files` iteration order."""
    matching_a = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "A body.",
    )
    matching_b = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "related_to"}]},
        "B body.",
    )
    non_matching = _doc({}, "C body.")
    files = {
        "concepts/a.md": matching_a,
        "concepts/b.md": matching_b,
        "concepts/c.md": non_matching,
    }

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == [
        okf.RelationRewrite(file="concepts/a.md", snapshot=matching_a),
        okf.RelationRewrite(file="concepts/b.md", snapshot=matching_b),
    ]


def test_find_inbound_relation_rewrites_skips_malformed_frontmatter() -> None:
    """A file with malformed/unparseable frontmatter -- unrelated to this
    merge -- is SKIPPED rather than crashing or refusing the scan (mirrors
    the deleted `find_relation_conflicts`'s identical broad-except skip;
    task 2.6)."""
    files = {
        "concepts/broken.md": "---\nnot: [valid: yaml: at all\n---\nBody.\n",
        "concepts/clean.md": _doc(
            {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
            "Clean body.",
        ),
    }

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert [rw.file for rw in rewrites] == ["concepts/clean.md"]


def test_find_inbound_relation_rewrites_excludes_survivor_and_absorbed_files() -> None:
    """Correction batch, finding 1 (CRITICAL): the scan is for GENUINE third
    parties only -- the survivor's own relations are handled by
    `build_merged_document`/`merge_relations`, and the absorbed file is
    deleted -- so a `files` mapping that happens to include the survivor's
    or the absorbed's own text (e.g. because the caller scans the whole
    bundle) must never record either of them, even when their own
    `relations:` targets the absorbed id."""
    survivor_doc = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "cites"}]},
        "Survivor body.",
    )
    absorbed_doc = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "self-ref"}]},
        "Absorbed body.",
    )
    third_party_doc = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Foo body.",
    )
    files = {
        "concepts/survivor.md": survivor_doc,
        "concepts/absorbed.md": absorbed_doc,
        "notes/foo.md": third_party_doc,
    }

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert [rw.file for rw in rewrites] == ["notes/foo.md"]


def test_find_inbound_relation_rewrites_skips_malformed_relations_shape() -> None:
    """A file whose frontmatter parses but whose `relations:` shape itself
    is malformed (fails `decode_relations`) is likewise skipped -- same
    broad-except rationale as the previous test (task 2.6)."""
    files = {
        "concepts/malformed-relations.md": _doc({"relations": "not-a-list"}, "Body.")
    }

    rewrites = relations.find_inbound_relation_rewrites(
        files, absorbed_id="concepts/absorbed", survivor_id="concepts/survivor"
    )

    assert rewrites == []


# -- apply_relation_rewrites (task 2.7) -------------------------------------


def test_apply_relation_rewrites_retargets_absorbed_to_survivor() -> None:
    """`apply_relation_rewrites` retargets a `relations:` entry pointing at
    the absorbed id to the survivor id (spec: "Third-party inbound
    relations retarget to the survivor")."""
    text = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Other body.",
    )
    rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=text)

    result = relations.apply_relation_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert okf.decode_relations(metadata) == [
        okf.Relation(target="concepts/survivor", type="depends_on")
    ]


def test_apply_relation_rewrites_preserves_preexisting_third_party_self_loop() -> None:
    """Correction batch, finding 1 (CRITICAL): a genuine third-party file
    `F != survivor_id, F != absorbed_id` cannot ever have its retarget
    create a self-loop -- `F -> absorbed` always becomes `F -> survivor`,
    which can never equal `F` itself. The ONLY way `apply_relation_rewrites`
    could previously observe `retargeted.target == file_id` is a
    PRE-EXISTING, merge-UNRELATED `F -> F` self-loop already on that file --
    which must be left untouched (mirrors `okf.merge_relations`'s explicit
    preservation of a pre-existing survivor-side self-loop), never silently
    dropped as if it were a byproduct of this merge. The absorbed-targeting
    edge is retargeted normally alongside it."""
    text = _doc(
        {
            "relations": [
                {"target": "notes/foo", "type": "self-ref"},
                {"target": "concepts/absorbed", "type": "cites"},
            ]
        },
        "Foo body.",
    )
    rewrite = okf.RelationRewrite(file="notes/foo.md", snapshot=text)

    result = relations.apply_relation_rewrites(
        text,
        file="notes/foo.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    # encode_relations re-sorts by (target, type); "concepts/survivor" sorts
    # before "notes/foo".
    assert okf.decode_relations(metadata) == [
        okf.Relation(target="concepts/survivor", type="cites"),
        okf.Relation(target="notes/foo", type="self-ref"),
    ]


def test_apply_relation_rewrites_dedupes_resulting_collision() -> None:
    """Requirement: "Duplicate edge is deduped, non-silently" -- a file
    that already holds an edge to the survivor, of the same type the
    absorbed-targeting edge retargets to, collapses to one entry."""
    text = _doc(
        {
            "relations": [
                {"target": "concepts/absorbed", "type": "depends_on"},
                {"target": "concepts/survivor", "type": "depends_on"},
            ]
        },
        "Other body.",
    )
    rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=text)

    result = relations.apply_relation_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert okf.decode_relations(metadata) == [
        okf.Relation(target="concepts/survivor", type="depends_on")
    ]


def test_apply_relation_rewrites_no_op_when_file_not_in_rewrites() -> None:
    """A file not recorded by `find_inbound_relation_rewrites` is returned
    byte-identical -- nothing to retarget."""
    text = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Other body.",
    )
    other_rewrite = okf.RelationRewrite(file="concepts/unrelated.md", snapshot=text)

    result = relations.apply_relation_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[other_rewrite],
    )

    assert result == text


def test_apply_relation_rewrites_omits_relations_key_when_result_empty() -> None:
    """An empty merged relation set omits the `relations:` key entirely,
    preserving "absent relations key is valid" -- exercised on a genuine
    third-party file whose (live) `relations:` is already an empty list at
    apply time (correction batch, finding 1: a survivor-only scenario is no
    longer this function's responsibility, since retargeting alone can
    never empty out a third party's relation set post-fix)."""
    text = _doc({"relations": []}, "Other body.")
    rewrite = okf.RelationRewrite(file="notes/foo.md", snapshot=text)

    result = relations.apply_relation_rewrites(
        text,
        file="notes/foo.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert okf.RELATIONS_KEY not in metadata


# -- reverse_relation_rewrites (task 2.8) -----------------------------------


def test_reverse_relation_rewrites_restores_recorded_snapshot_exactly() -> None:
    """`reverse_relation_rewrites` restores the recorded whole-file
    snapshot verbatim -- an ABSOLUTE overwrite, ignoring the passed-in
    (rewritten) text entirely (design D1/D3)."""
    original = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Other body.",
    )
    rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=original)
    rewritten = relations.apply_relation_rewrites(
        original,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
    )
    assert rewritten != original  # sanity: the rewrite actually changed the text

    restored = relations.reverse_relation_rewrites(
        rewritten,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
        link_rewrites=[],
    )

    assert restored == original


def test_reverse_relation_rewrites_no_op_when_file_not_in_rewrites() -> None:
    """A file with no matching recorded rewrite is returned unchanged
    (mirrors `reverse_link_rewrites`' ignore-other-files behavior); no drift
    check applies since there is nothing recorded to compare against."""
    text = "some arbitrary text\n"
    rewrite = okf.RelationRewrite(file="concepts/unrelated.md", snapshot="snapshot\n")

    result = relations.reverse_relation_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[rewrite],
        link_rewrites=[],
    )

    assert result == text


def test_reverse_relation_rewrites_rejects_more_than_one_snapshot_for_same_file() -> (
    None
):
    """More than one recorded snapshot for the SAME file within one ledger
    entry is a construction bug, not a legitimate multi-snapshot case --
    fails closed rather than picking one silently."""
    rewrites = [
        okf.RelationRewrite(file="concepts/other.md", snapshot="first\n"),
        okf.RelationRewrite(file="concepts/other.md", snapshot="second\n"),
    ]

    with pytest.raises(ValueError, match="more than one"):
        relations.reverse_relation_rewrites(
            "text\n",
            file="concepts/other.md",
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed",
            rewrites=rewrites,
            link_rewrites=[],
        )


def test_reverse_relation_rewrites_no_false_drift_when_file_also_link_rewritten() -> (
    None
):
    """Design D5 regression: a file present in BOTH `link_rewrites` and
    `rewrites` (relation_rewrites) -- e.g. a body link AND a typed relation
    to the absorbed id -- must NOT trigger a false drift positive. The
    expected-content recomputation applies the recorded link rewrite FORWARD
    on the snapshot before the relation retarget, exactly mirroring what
    `merge`'s Phase B write loop produced for this file."""
    original = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "See [Absorbed](/concepts/absorbed.md) for details.",
    )
    relation_rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=original)
    link_rewrite = okf.LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=original.index("/concepts/absorbed.md") - 1,
    )

    # What `merge`'s Phase B actually wrote to this file: link rewrite
    # applied first, then the relation retarget on top (same in-memory text).
    current_on_disk = relations.apply_relation_rewrites(
        bundle_links.apply_link_rewrites(
            original, file="concepts/other.md", rewrites=[link_rewrite]
        ),
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[relation_rewrite],
    )

    restored = relations.reverse_relation_rewrites(
        current_on_disk,
        file="concepts/other.md",
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        rewrites=[relation_rewrite],
        link_rewrites=[link_rewrite],
    )

    assert restored == original


def test_reverse_relation_rewrites_fails_closed_on_drifted_current_text() -> None:
    """CRITICAL fix (review correction batch): if `text` (the file's
    CURRENT on-disk content) does not match what this merge deterministically
    wrote to it -- a legitimate edit landed on the file after the merge --
    this raises `ValueError` instead of silently returning the stale
    pre-merge snapshot, symmetric with `reverse_link_rewrites`'s identical
    current-bytes drift check."""
    original = _doc(
        {"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
        "Other body.",
    )
    rewrite = okf.RelationRewrite(file="concepts/other.md", snapshot=original)
    drifted_current_text = _doc(
        {
            "relations": [
                {"target": "concepts/survivor", "type": "depends_on"},
                {"target": "concepts/elsewhere", "type": "related_to"},
            ]
        },
        "Other body.",
    )

    with pytest.raises(ValueError, match="drifted"):
        relations.reverse_relation_rewrites(
            drifted_current_text,
            file="concepts/other.md",
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed",
            rewrites=[rewrite],
            link_rewrites=[],
        )
