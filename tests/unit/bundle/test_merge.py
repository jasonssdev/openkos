"""Unit tests for pure merge/unmerge planning (`bundle/merge.py`).

`plan_merge`/`plan_unmerge` are pure text-in/text-out: no bundle file is
read or written here, there is no confirm gate, and inbound-link scanning
is a later unit (U3). This proves only the PLANNING + ledger are correct
(spec: Reversibility Ledger, Unmerge Achieves Round-Trip Parity).
"""

import pytest

from openkos.bundle import merge as bundle_merge
from openkos.model import okf

_INDEX_TEXT = okf.dump_frontmatter(
    {"okf_version": okf.OKF_VERSION}, "# Concepts\n\n* entry\n"
)
_LOG_TEXT = "# Directory Update Log\n\n## 2026-07-19\n\n* Entry.\n"


def _survivor_text(**overrides: object) -> str:
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Stoicism",
        "description": "Survivor description.",
        "tags": ["philosophy"],
        "timestamp": "2026-07-10T09:00:00Z",
        "freshness": "snapshot",
        "sensitivity": "private",
        "provenance": ["sources/call-a"],
    }
    metadata.update(overrides)
    return okf.dump_frontmatter(metadata, "# Stoicism\n\nSurvivor body.")


def _absorbed_text(**overrides: object) -> str:
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Stoic Philosophy",
        "description": "Absorbed description.",
        "tags": ["ethics"],
        "timestamp": "2026-07-14T09:00:00Z",
        "freshness": "verified",
        "sensitivity": "confidential",
        "provenance": ["sources/call-b"],
    }
    metadata.update(overrides)
    return okf.dump_frontmatter(metadata, "# Stoic Philosophy\n\nAbsorbed body.")


def test_plan_merge_body_appends_absorbed_content() -> None:
    """Requirement: Merge Fuses Two Distinct Concept-IDs -- body is APPEND,
    never overwrite."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    _, body = okf.load_frontmatter(plan.merged_survivor)

    assert "Survivor body." in body
    assert "## Merged content (concepts/absorbed)" in body
    assert "Absorbed body." in body


def test_plan_merge_provenance_unioned_deduped_order_preserving() -> None:
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(provenance=["sources/call-a", "sources/shared"]),
        absorbed_text=_absorbed_text(provenance=["sources/shared", "sources/call-b"]),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.merged_survivor)

    assert metadata["provenance"] == [
        "sources/call-a",
        "sources/shared",
        "sources/call-b",
    ]


def test_plan_merge_frontmatter_conflicts_scalar_list_freshness() -> None:
    """Requirement: Frontmatter-Conflict Resolution, exercised through the
    full `plan_merge` -> survivor frontmatter path."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(
            title="Stoicism", tags=["philosophy"], timestamp="2026-07-10T09:00:00Z"
        ),
        absorbed_text=_absorbed_text(
            title="Stoic Philosophy",
            tags=["philosophy", "ethics"],
            timestamp="2026-07-15T09:00:00Z",
            freshness="verified",
        ),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.merged_survivor)

    assert metadata["title"] == "Stoicism"
    assert metadata["tags"] == ["philosophy", "ethics"]
    assert metadata["timestamp"] == "2026-07-15T09:00:00Z"
    assert metadata["freshness"] == "verified"


def test_plan_merge_sensitivity_recomputed() -> None:
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(sensitivity="private"),
        absorbed_text=_absorbed_text(sensitivity="confidential"),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.merged_survivor)

    assert metadata["sensitivity"] == "confidential"


def test_plan_merge_ledger_entry_captures_full_pre_merge_snapshot_set() -> None:
    """Requirement: Reversibility Ledger -- every field the spec requires is
    present on the new entry."""
    survivor_text = _survivor_text()
    absorbed_text = _absorbed_text()
    link_rewrite = okf.LinkRewrite(
        file="concepts/other.md",
        old_link="/concepts/absorbed.md",
        new_link="/concepts/survivor.md",
        offset=42,
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=survivor_text,
        absorbed_text=absorbed_text,
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        link_rewrites=[link_rewrite],
    )

    entry = plan.ledger_entry
    assert entry.absorbed_id == "concepts/absorbed"
    assert entry.absorbed_snapshot == absorbed_text
    assert entry.survivor_before == survivor_text
    assert entry.index_before == _INDEX_TEXT
    assert entry.log_before == _LOG_TEXT
    assert entry.link_rewrites == [link_rewrite]
    assert entry.sensitivity_after == "confidential"
    assert entry.merged_at == "2026-07-20T00:00:00Z"


def test_plan_merge_link_rewrites_default_to_empty_list() -> None:
    """`link_rewrites` may be omitted at this layer -- the actual bundle
    scan is U3 -- and defaults to an empty, injectable list."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    assert plan.ledger_entry.link_rewrites == []


def test_plan_merge_survivor_carries_no_merged_from_key() -> None:
    """Durable-derived-state slice 1a: the ledger now lives in a sidecar
    (`bundle/ledger.py`), never the survivor's own frontmatter (spec: "No
    `merged_from` key remains in survivor frontmatter")."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.merged_survivor)

    assert okf.MERGED_FROM_KEY not in metadata
    assert plan.ledger_entries == [plan.ledger_entry]


def test_plan_merge_sequential_survivor_before_retains_prior_entry() -> None:
    """Sequential-merge setup (LIFO groundwork): merging a THIRD object into
    a survivor that already absorbed one produces a `survivor_before` that
    is the survivor's FULL bytes from the first merge, and `ledger_entries`
    retains the prior sidecar entry verbatim, never stripping it -- the
    caller (`bundle.ledger.read_entries`) is what supplies `existing_entries`
    now that the ledger lives in a sidecar rather than the survivor's own
    frontmatter."""
    first_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-b",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-18T00:00:00Z",
    )
    survivor_after_first_merge = first_plan.merged_survivor

    second_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-c",
        survivor_text=survivor_after_first_merge,
        absorbed_text=_absorbed_text(title="Third"),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=first_plan.ledger_entries,
    )

    assert second_plan.ledger_entry.survivor_before == survivor_after_first_merge
    assert len(second_plan.ledger_entries) == 2
    assert second_plan.ledger_entries[0].absorbed_id == "concepts/absorbed-b"
    assert second_plan.ledger_entries[1].absorbed_id == "concepts/absorbed-c"
    assert second_plan.ledger_entries[0] == first_plan.ledger_entry


def test_plan_merge_rejects_duplicate_absorbed_id() -> None:
    """Requirement: Reversibility Ledger -- a survivor that already has
    `absorbed_id` in its `merged_from` list must refuse a second merge of the
    same absorbed-id, since `plan_unmerge`'s LIFO-tail (id-keyed) targeting
    cannot disambiguate two same-id entries."""
    first_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-18T00:00:00Z",
    )

    with pytest.raises(ValueError, match="already merged"):
        bundle_merge.plan_merge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed",
            survivor_text=first_plan.merged_survivor,
            absorbed_text=_absorbed_text(title="Absorbed Again"),
            index_text=_INDEX_TEXT,
            log_text=_LOG_TEXT,
            merged_at="2026-07-20T00:00:00Z",
            existing_entries=first_plan.ledger_entries,
        )


def test_plan_merge_rejects_self_merge() -> None:
    """Requirement: Merge Fuses Two Distinct Concept-IDs -- same-id refused."""
    with pytest.raises(ValueError, match="distinct"):
        bundle_merge.plan_merge(
            survivor_id="concepts/same",
            absorbed_id="concepts/same",
            survivor_text=_survivor_text(),
            absorbed_text=_absorbed_text(),
            index_text=_INDEX_TEXT,
            log_text=_LOG_TEXT,
            merged_at="2026-07-20T00:00:00Z",
        )


def test_plan_merge_rejects_blank_survivor_id() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bundle_merge.plan_merge(
            survivor_id="  ",
            absorbed_id="concepts/absorbed",
            survivor_text=_survivor_text(),
            absorbed_text=_absorbed_text(),
            index_text=_INDEX_TEXT,
            log_text=_LOG_TEXT,
            merged_at="2026-07-20T00:00:00Z",
        )


def test_plan_merge_rejects_blank_absorbed_id() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bundle_merge.plan_merge(
            survivor_id="concepts/survivor",
            absorbed_id="",
            survivor_text=_survivor_text(),
            absorbed_text=_absorbed_text(),
            index_text=_INDEX_TEXT,
            log_text=_LOG_TEXT,
            merged_at="2026-07-20T00:00:00Z",
        )


def test_plan_unmerge_restores_survivor_and_absorbed_from_snapshots() -> None:
    """Requirement: Unmerge Achieves Round-Trip Parity."""
    survivor_text = _survivor_text()
    absorbed_text = _absorbed_text()
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=survivor_text,
        absorbed_text=absorbed_text,
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    unmerge_plan = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        entries=plan.ledger_entries,
    )

    assert unmerge_plan.restored_survivor == survivor_text
    assert unmerge_plan.restored_absorbed == absorbed_text
    assert unmerge_plan.restored_index == _INDEX_TEXT
    assert unmerge_plan.restored_log == _LOG_TEXT
    assert unmerge_plan.link_rewrites == []
    assert unmerge_plan.remaining_entries == []


def test_plan_unmerge_lifo_tail_targeting() -> None:
    """Scenario: Absorbed-id is not the LIFO tail -- unmerging the
    FIRST-absorbed id while a SECOND merge is still on top must refuse;
    only the tail is reversible."""
    first_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-b",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-18T00:00:00Z",
    )
    second_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-c",
        survivor_text=first_plan.merged_survivor,
        absorbed_text=_absorbed_text(title="Third"),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=first_plan.ledger_entries,
    )

    with pytest.raises(ValueError, match="LIFO tail"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed-b",
            entries=second_plan.ledger_entries,
        )


def test_plan_unmerge_sequential_lifo_tail_then_prior_entry() -> None:
    """Sequential parity groundwork: the SECOND-absorbed id unmerges cleanly
    first (it is the tail), restoring the survivor to its post-first-merge
    state -- from which the FIRST-absorbed id becomes the new tail."""
    first_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-b",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-18T00:00:00Z",
    )
    second_plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-c",
        survivor_text=first_plan.merged_survivor,
        absorbed_text=_absorbed_text(title="Third"),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=first_plan.ledger_entries,
    )

    tail_unmerge = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-c",
        entries=second_plan.ledger_entries,
    )
    assert tail_unmerge.restored_survivor == first_plan.merged_survivor
    assert tail_unmerge.remaining_entries == first_plan.ledger_entries

    prior_unmerge = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-b",
        entries=tail_unmerge.remaining_entries,
    )
    assert prior_unmerge.restored_survivor == _survivor_text()
    assert prior_unmerge.restored_absorbed == _absorbed_text()
    assert prior_unmerge.remaining_entries == []


def test_plan_unmerge_rejects_non_merged_pair() -> None:
    """Scenario: Unmerge of a non-merged pair."""
    with pytest.raises(ValueError, match="no merged_from entries"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/never-merged",
            entries=[],
        )


def test_plan_unmerge_rejects_self_merge_ids() -> None:
    with pytest.raises(ValueError, match="distinct"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/same",
            absorbed_id="concepts/same",
            entries=[],
        )


def test_relation_conflict_guard_removed_no_residual_refusal_path() -> None:
    """`RelationConflict`/`find_relation_conflicts` no longer exist on
    `bundle.merge` -- slice 2a REPLACES the refuse-or-warn guard with
    reversible rewiring (spec: "Merge of an edge-bearing object always
    succeeds"; REMOVED Requirement: Non-Silent Guard For Edge-Bearing
    Merge; task 1.5)."""
    assert not hasattr(bundle_merge, "RelationConflict")
    assert not hasattr(bundle_merge, "find_relation_conflicts")


def test_plan_merge_moves_absorbed_outbound_relations_onto_survivor() -> None:
    """`plan_merge` no longer refuses on an edge-bearing absorbed object --
    the absorbed object's own outbound `relations:` are unioned onto the
    merged survivor instead (spec: "Outbound relations move to the
    survivor")."""
    absorbed_text = _absorbed_text(
        relations=[{"target": "concepts/other", "type": "depends_on"}]
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=absorbed_text,
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    merged_metadata, _ = okf.load_frontmatter(plan.merged_survivor)
    assert okf.decode_relations(merged_metadata) == [
        okf.Relation(target="concepts/other", type="depends_on")
    ]


def test_plan_unmerge_rejects_blank_ids() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bundle_merge.plan_unmerge(
            survivor_id="  ",
            absorbed_id="concepts/absorbed",
            entries=[],
        )


# -- v3 ledger: provenance_rewrites threading (tasks 3.1-3.2) --------------


def test_plan_merge_always_writes_v4_schema() -> None:
    """`plan_merge` always writes `MERGE_LEDGER_SCHEMA_V4` (#667; was V3
    from rewrite-provenance-on-merge, task 3.1)."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    assert plan.ledger_entry.schema == okf.MERGE_LEDGER_SCHEMA_V4


def test_plan_merge_provenance_rewrites_default_to_empty_list() -> None:
    """`provenance_rewrites` may be omitted at this layer -- the actual
    bundle scan (`bundle/provenance.py`) and CLI wiring are PR2's concern --
    and defaults to an empty, injectable list (task 3.1)."""
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
    )

    assert plan.ledger_entry.provenance_rewrites == []


def test_plan_merge_threads_provenance_rewrites_into_ledger_entry() -> None:
    """A caller-injected `provenance_rewrites` list is threaded into the new
    ledger entry exactly as `relation_rewrites` was threaded (task 3.2)."""
    provenance_rewrite = okf.ProvenanceRewrite(
        file="concepts/other.md",
        snapshot="---\ntype: Concept\nprovenance:\n- concepts/absorbed\n---\nBody.\n",
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        provenance_rewrites=[provenance_rewrite],
    )

    assert plan.ledger_entry.provenance_rewrites == [provenance_rewrite]
    assert plan.ledger_entry.schema == okf.MERGE_LEDGER_SCHEMA_V4
    assert plan.ledger_entries == [plan.ledger_entry]


def test_plan_unmerge_provenance_rewrites_round_trip() -> None:
    """`UnmergePlan` carries `provenance_rewrites`, threaded through exactly
    like `relation_rewrites` (task 3.1-3.2)."""
    provenance_rewrite = okf.ProvenanceRewrite(
        file="concepts/other.md",
        snapshot="---\ntype: Concept\nprovenance:\n- concepts/absorbed\n---\nBody.\n",
    )
    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        survivor_text=_survivor_text(),
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        provenance_rewrites=[provenance_rewrite],
    )

    unmerge_plan = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed",
        entries=plan.ledger_entries,
    )

    assert unmerge_plan.provenance_rewrites == [provenance_rewrite]


# --- unwind ergonomics (issue #562) ----------------------------------------


def _chained_entries(*absorbed_ids: str) -> list[okf.MergeLedgerEntry]:
    """Chain `plan_merge` once per absorbed id onto ONE survivor
    (`concepts/survivor`), threading `merged_survivor`/`ledger_entries`
    forward, and return the accumulated LIFO ledger (oldest first) -- the
    exact `entries` shape `bundle.ledger.read_entries` would hand the CLI."""
    survivor_text = _survivor_text()
    entries: list[okf.MergeLedgerEntry] = []
    for n, absorbed_id in enumerate(absorbed_ids):
        plan = bundle_merge.plan_merge(
            survivor_id="concepts/survivor",
            absorbed_id=absorbed_id,
            survivor_text=survivor_text,
            absorbed_text=_absorbed_text(title=f"Absorbed {n}"),
            index_text=_INDEX_TEXT,
            log_text=_LOG_TEXT,
            merged_at=f"2026-07-{18 + n:02d}T00:00:00Z",
            existing_entries=entries,
        )
        survivor_text = plan.merged_survivor
        entries = plan.ledger_entries
    return entries


def test_plan_unmerge_non_tail_error_lists_the_full_unwind_sequence() -> None:
    """Issue #562: a non-tail `absorbed_id` that IS present in the ledger
    refuses with the FULL LIFO unwind sequence -- every id from the tail
    down to and including the requested one, in execution order -- and
    names `--to` as the one-command alternative."""
    entries = _chained_entries(
        "concepts/absorbed-a", "concepts/absorbed-b", "concepts/absorbed-c"
    )

    with pytest.raises(ValueError, match="LIFO tail") as excinfo:
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed-a",
            entries=entries,
        )

    message = str(excinfo.value)
    assert "LIFO tail" in message
    assert (
        "'concepts/absorbed-c', 'concepts/absorbed-b', 'concepts/absorbed-a'" in message
    )
    assert "openkos unmerge concepts/survivor --to concepts/absorbed-a" in message


def test_plan_unmerge_unknown_absorbed_id_refuses_without_an_unwind_hint() -> None:
    """An `absorbed_id` present NOWHERE in the ledger keeps a plain
    "not merged into this survivor" refusal -- no unwind sequence, no
    `--to` hint, since there is nothing to unwind to (issue #562)."""
    entries = _chained_entries("concepts/absorbed-a")

    with pytest.raises(ValueError, match="never merged into") as excinfo:
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/never-merged",
            entries=entries,
        )

    message = str(excinfo.value)
    assert "concepts/never-merged" in message
    assert "merged into" in message
    assert "--to" not in message


def test_plan_unwind_sequence_returns_tail_to_target_in_execution_order() -> None:
    """`plan_unwind_sequence` returns the tail-to-target slice, tail FIRST
    (execution order), down to and INCLUDING the entry whose `absorbed_id`
    equals the target (issue #562)."""
    entries = _chained_entries(
        "concepts/absorbed-a", "concepts/absorbed-b", "concepts/absorbed-c"
    )

    sequence = bundle_merge.plan_unwind_sequence(
        survivor_id="concepts/survivor",
        to_absorbed_id="concepts/absorbed-b",
        entries=entries,
    )

    assert [entry.absorbed_id for entry in sequence] == [
        "concepts/absorbed-c",
        "concepts/absorbed-b",
    ]
    assert sequence[0] is entries[2]
    assert sequence[1] is entries[1]


def test_plan_unwind_sequence_to_the_tail_is_a_single_step() -> None:
    """`--to <tail-id>` degenerates to the classic single-entry unmerge."""
    entries = _chained_entries("concepts/absorbed-a", "concepts/absorbed-b")

    sequence = bundle_merge.plan_unwind_sequence(
        survivor_id="concepts/survivor",
        to_absorbed_id="concepts/absorbed-b",
        entries=entries,
    )

    assert sequence == [entries[-1]]


def test_plan_unwind_sequence_unknown_target_raises_value_error() -> None:
    entries = _chained_entries("concepts/absorbed-a")

    with pytest.raises(ValueError, match="never-merged"):
        bundle_merge.plan_unwind_sequence(
            survivor_id="concepts/survivor",
            to_absorbed_id="concepts/never-merged",
            entries=entries,
        )


def test_plan_unwind_sequence_empty_entries_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no merged_from entries"):
        bundle_merge.plan_unwind_sequence(
            survivor_id="concepts/survivor",
            to_absorbed_id="concepts/absorbed-a",
            entries=[],
        )


def test_plan_unwind_sequence_rejects_same_or_blank_ids() -> None:
    with pytest.raises(ValueError, match="distinct"):
        bundle_merge.plan_unwind_sequence(
            survivor_id="concepts/same",
            to_absorbed_id="concepts/same",
            entries=[],
        )


# --- #667: carried_content_ids annotation + redacted-snapshot refusal -------


def _prior_entry(absorbed_id: str) -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V4,
        merged_at="2026-07-19T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot=_absorbed_text(),
        survivor_before=_survivor_text(),
        index_before=_INDEX_TEXT,
        log_before=_LOG_TEXT,
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def test_plan_merge_writes_v4_and_annotates_reconciled_prior_content() -> None:
    """#667: a prior absorbed id whose `## Merged content (<id>)` heading is
    ABSENT from the survivor text being snapshotted (the #645-reconciled
    shape) is recorded in the new entry's `carried_content_ids` -- the
    forget sweep's only way to know this snapshot carries that content
    undelimited."""
    reconciled_survivor = okf.dump_frontmatter(
        {"type": "Concept", "title": "Stoicism", "sensitivity": "private"},
        "# Stoicism\n\nSurvivor body woven with the first absorbed content.",
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/second",
        survivor_text=reconciled_survivor,
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=[_prior_entry("concepts/first")],
    )

    assert plan.ledger_entry.schema == okf.MERGE_LEDGER_SCHEMA_V4
    assert plan.ledger_entry.carried_content_ids == ["concepts/first"]


def test_plan_merge_does_not_annotate_a_still_delimited_prior_section() -> None:
    """The ordinary un-reconciled stack keeps its delimiter, so the #602
    structural excision still reaches it -- no annotation, no over-eager
    wholesale redaction later."""
    stacked_survivor = okf.dump_frontmatter(
        {"type": "Concept", "title": "Stoicism", "sensitivity": "private"},
        "# Stoicism\n\nSurvivor body."
        "\n\n## Merged content (concepts/first)\n\nFirst absorbed body.",
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/second",
        survivor_text=stacked_survivor,
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=[_prior_entry("concepts/first")],
    )

    assert plan.ledger_entry.carried_content_ids == []


def test_plan_merge_does_not_annotate_an_empty_bodied_prior() -> None:
    """#685 item 3: a prior merge whose absorbed BODY was empty or
    whitespace-only contributed no content to the survivor, so the absence
    of its delimiter (a #645 reconciliation swept it away with the rest)
    must NOT annotate it as carried -- annotating it buys no privacy and
    costs a wholesale `survivor_before` redaction on a later forget."""
    import dataclasses

    reconciled_survivor = okf.dump_frontmatter(
        {"type": "Concept", "title": "Stoicism", "sensitivity": "private"},
        "# Stoicism\n\nSurvivor body, reconciled with no delimiters left.",
    )
    empty_prior = dataclasses.replace(
        _prior_entry("concepts/first"),
        absorbed_snapshot=okf.dump_frontmatter(
            {"type": "Concept", "title": "Empty", "sensitivity": "private"},
            "   \n\n  ",
        ),
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/second",
        survivor_text=reconciled_survivor,
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=[empty_prior],
    )

    assert plan.ledger_entry.carried_content_ids == []


def test_plan_merge_still_annotates_a_malformed_prior_snapshot() -> None:
    """Fail closed (#602's privacy-over-reversibility rule): a prior
    `absorbed_snapshot` this planner cannot parse cannot be PROVEN empty,
    so the carried annotation must survive -- under-redacting a body that
    might exist is the failure mode this annotation exists to prevent."""
    import dataclasses

    reconciled_survivor = okf.dump_frontmatter(
        {"type": "Concept", "title": "Stoicism", "sensitivity": "private"},
        "# Stoicism\n\nSurvivor body, reconciled with no delimiters left.",
    )
    malformed_prior = dataclasses.replace(
        _prior_entry("concepts/first"),
        absorbed_snapshot="not a frontmatter document at all",
    )

    plan = bundle_merge.plan_merge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/second",
        survivor_text=reconciled_survivor,
        absorbed_text=_absorbed_text(),
        index_text=_INDEX_TEXT,
        log_text=_LOG_TEXT,
        merged_at="2026-07-20T00:00:00Z",
        existing_entries=[malformed_prior],
    )

    assert plan.ledger_entry.carried_content_ids == ["concepts/first"]


def test_plan_unmerge_refuses_a_redacted_survivor_snapshot() -> None:
    """#667: forget's sweep replaces a carried-content snapshot with the
    redaction sentinel; restoring it would overwrite the live survivor
    body with the notice string -- `plan_unmerge` must refuse instead."""
    import dataclasses

    redacted_tail = dataclasses.replace(
        _prior_entry("concepts/first"),
        survivor_before=okf.REDACTED_SNAPSHOT_SENTINEL,
    )

    with pytest.raises(ValueError, match="redacted"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/first",
            entries=[redacted_tail],
        )
