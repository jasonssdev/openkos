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


def test_plan_merge_ledger_persisted_in_survivor_frontmatter() -> None:
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
    decoded = okf.decode_merged_from(metadata)

    assert decoded == [plan.ledger_entry]


def test_plan_merge_sequential_survivor_before_retains_prior_entry() -> None:
    """Sequential-merge setup (LIFO groundwork): merging a THIRD object into
    a survivor that already absorbed one produces a `survivor_before` that
    is the survivor's FULL bytes from the first merge -- RETAINING that
    prior `merged_from` entry verbatim, never stripping it."""
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
    )

    assert second_plan.ledger_entry.survivor_before == survivor_after_first_merge
    metadata, _ = okf.load_frontmatter(second_plan.merged_survivor)
    decoded = okf.decode_merged_from(metadata)
    assert len(decoded) == 2
    assert decoded[0].absorbed_id == "concepts/absorbed-b"
    assert decoded[1].absorbed_id == "concepts/absorbed-c"
    assert decoded[0] == first_plan.ledger_entry


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
        survivor_text=plan.merged_survivor,
    )

    assert unmerge_plan.restored_survivor == survivor_text
    assert unmerge_plan.restored_absorbed == absorbed_text
    assert unmerge_plan.restored_index == _INDEX_TEXT
    assert unmerge_plan.restored_log == _LOG_TEXT
    assert unmerge_plan.link_rewrites == []


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
    )

    with pytest.raises(ValueError, match="LIFO tail"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/absorbed-b",
            survivor_text=second_plan.merged_survivor,
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
    )

    tail_unmerge = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-c",
        survivor_text=second_plan.merged_survivor,
    )
    assert tail_unmerge.restored_survivor == first_plan.merged_survivor

    prior_unmerge = bundle_merge.plan_unmerge(
        survivor_id="concepts/survivor",
        absorbed_id="concepts/absorbed-b",
        survivor_text=tail_unmerge.restored_survivor,
    )
    assert prior_unmerge.restored_survivor == _survivor_text()
    assert prior_unmerge.restored_absorbed == _absorbed_text()


def test_plan_unmerge_rejects_non_merged_pair() -> None:
    """Scenario: Unmerge of a non-merged pair."""
    with pytest.raises(ValueError, match="no merged_from entries"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/survivor",
            absorbed_id="concepts/never-merged",
            survivor_text=_survivor_text(),
        )


def test_plan_unmerge_rejects_self_merge_ids() -> None:
    with pytest.raises(ValueError, match="distinct"):
        bundle_merge.plan_unmerge(
            survivor_id="concepts/same",
            absorbed_id="concepts/same",
            survivor_text=_survivor_text(),
        )


def test_plan_unmerge_rejects_blank_ids() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bundle_merge.plan_unmerge(
            survivor_id="  ",
            absorbed_id="concepts/absorbed",
            survivor_text=_survivor_text(),
        )
