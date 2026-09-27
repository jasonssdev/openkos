"""Direct unit tests for `openkos.mcp.gate`'s `get`-facing surface
(mcp-read-surface slice 5, design Decisions 3-4): `disclose_get`'s
truth table, `withheld` counting per channel, and `finish`'s consistency
`warnings`/aggregated `not_run` rendering.
"""

from __future__ import annotations

from typing import cast

from openkos import read_outcome
from openkos.application import concept_read, list_service
from openkos.application import consistency as application_consistency
from openkos.bundle import listing
from openkos.mcp import gate
from openkos.model import okf


def _concept(result: dict[str, object]) -> dict[str, object]:
    concept = result["concept"]
    assert isinstance(concept, dict)
    return concept


def _raw_not_run(result: dict[str, object]) -> tuple[read_outcome.NotRun, ...]:
    """`disclose_get`'s OWN `not_run` field: raw `read_outcome.NotRun`
    entries, not yet rendered by `finish`."""
    return cast("tuple[read_outcome.NotRun, ...]", result["not_run"])


def _rendered_not_run(rendered: dict[str, object]) -> list[dict[str, object]]:
    """`finish`'s rendered `not_run`: `{label, reason}` dicts."""
    return cast("list[dict[str, object]]", rendered["not_run"])


def _rendered_warnings(rendered: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", rendered["warnings"])


def _record(
    *,
    concept_id: str = "concepts/target",
    sensitivity: object = "public",
    relations: tuple[okf.Relation, ...] = (),
    provenance: tuple[str, ...] = (),
    not_run: tuple[read_outcome.NotRun, ...] = (),
) -> concept_read.ConceptRecord:
    return concept_read.ConceptRecord(
        concept_id=concept_id,
        sensitivity=sensitivity,
        type="Concept",
        title="Target",
        description="",
        status="active",
        body="Body text.",
        relations=relations,
        provenance=provenance,
        not_run=not_run,
    )


def _sources(
    *,
    ancestors: tuple[str, ...] = (),
    rows: tuple[listing.BundleObject, ...] = (),
    not_run: tuple[read_outcome.NotRun, ...] = (),
) -> list_service.ProvenanceSources:
    return list_service.ProvenanceSources(
        ancestors=ancestors, rows=rows, not_run=not_run
    )


def _row(
    concept_id: str, *, title: str = "", sensitivity: str = "public"
) -> listing.BundleObject:
    return listing.BundleObject(
        concept_id=concept_id,
        link_dir=concept_id.split("/", 1)[0],
        title=title,
        sensitivity=sensitivity,
        status="active",
        readable=True,
    )


def _snapshot(
    allowed: frozenset[str], *, expose_confidential: bool = False
) -> gate.Snapshot:
    return gate.Snapshot(allowed=allowed, expose_confidential=expose_confidential)


# ---------------------------------------------------------------------------
# 5.5: disclose_get's truth table (design Decision 4)
# ---------------------------------------------------------------------------


def test_disclose_get_table_target_not_in_allowed_set() -> None:
    """A target not in the snapshot's allowed set is withheld regardless
    of its own record."""
    raw = gate.GetRaw(
        target_id="concepts/target",
        record=_record(sensitivity="public"),
        sources=_sources(),
    )
    result = gate.disclose_get(raw, _snapshot(frozenset()))
    assert result["concept"] is None
    assert result["withheld"] == 1
    assert result["not_run"] == ()


def test_disclose_get_table_conjunction_reverse_direction() -> None:
    """A target IN the allowed set whose own freshly-read sensitivity says
    confidential is still withheld (the conjunction's reverse direction)."""
    raw = gate.GetRaw(
        target_id="concepts/target",
        record=_record(sensitivity="confidential"),
        sources=_sources(),
    )
    result = gate.disclose_get(raw, _snapshot(frozenset({"concepts/target"})))
    assert result["concept"] is None
    assert result["withheld"] == 1
    assert result["not_run"] == ()


def test_disclose_get_table_unreadable_opt_in_on() -> None:
    """An `UnreadableConcept` target that IS in the allowed set (only
    possible with the opt-in on) is a success with a `concept_read`
    `not_run` entry, never withheld."""
    raw = gate.GetRaw(
        target_id="concepts/broken",
        record=concept_read.UnreadableConcept(concept_id="concepts/broken"),
        sources=_sources(),
    )
    result = gate.disclose_get(
        raw, _snapshot(frozenset({"concepts/broken"}), expose_confidential=True)
    )
    assert result["concept"] is None
    assert result["withheld"] == 0
    not_run = _raw_not_run(result)
    assert len(not_run) == 1
    assert not_run[0].label == "concept_read"


def test_disclose_get_table_disclosable_record() -> None:
    """A disclosable `ConceptRecord` is returned with `relations`/
    `provenance` filtered through the snapshot, and `source_ancestors =
    ancestors ∩ allowed`, each with `title`/`sensitivity` from the `rows`
    lookup."""
    record = _record(
        sensitivity="public",
        relations=(
            okf.Relation(target="concepts/allowed-rel", type="related_to"),
            okf.Relation(target="concepts/blocked-rel", type="related_to"),
        ),
        provenance=("sources/allowed-src", "sources/blocked-src"),
    )
    sources = _sources(
        ancestors=("sources/allowed-anc", "sources/blocked-anc"),
        rows=(
            _row(
                "sources/allowed-anc", title="Allowed Ancestor", sensitivity="private"
            ),
        ),
    )
    raw = gate.GetRaw(target_id="concepts/target", record=record, sources=sources)
    snapshot = _snapshot(
        frozenset(
            {
                "concepts/target",
                "concepts/allowed-rel",
                "sources/allowed-src",
                "sources/allowed-anc",
            }
        )
    )

    result = gate.disclose_get(raw, snapshot)

    concept = _concept(result)
    assert concept["id"] == "concepts/target"
    assert concept["relations"] == [
        {"target": "concepts/allowed-rel", "type": "related_to"}
    ]
    assert concept["provenance"] == ["sources/allowed-src"]
    assert concept["source_ancestors"] == [
        {
            "id": "sources/allowed-anc",
            "title": "Allowed Ancestor",
            "sensitivity": "private",
        }
    ]


# ---------------------------------------------------------------------------
# 5.6: withheld counts removed ENTRIES, not distinct objects
# ---------------------------------------------------------------------------


def test_disclose_get_withheld_counts_entries() -> None:
    """2 filtered relations, 1 filtered provenance id, and 1 filtered
    ancestor set `withheld == 4`, even when the SAME underlying id appears
    in more than one channel."""
    shared_id = "concepts/shared-blocked"
    record = _record(
        sensitivity="public",
        relations=(
            okf.Relation(target=shared_id, type="related_to"),
            okf.Relation(target="concepts/other-blocked", type="related_to"),
        ),
        provenance=(shared_id,),
    )
    sources = _sources(ancestors=(shared_id,), rows=())
    raw = gate.GetRaw(target_id="concepts/target", record=record, sources=sources)
    snapshot = _snapshot(frozenset({"concepts/target"}))

    result = gate.disclose_get(raw, snapshot)

    assert result["concept"] is not None
    assert result["withheld"] == 4


# ---------------------------------------------------------------------------
# 5.7: finish's not_run aggregation (design Decision 3)
# ---------------------------------------------------------------------------


def _consistency(
    *,
    in_flight_writes: int | None = 0,
    stale_stores: tuple[str, ...] = (),
    not_run: tuple[read_outcome.NotRun, ...] = (),
) -> application_consistency.Consistency:
    return application_consistency.Consistency(
        in_flight_writes=in_flight_writes, stale_stores=stale_stores, not_run=not_run
    )


def test_document_labelled_not_run_aggregated() -> None:
    """Several `NotRun` outcomes each labelled by a different document path
    are aggregated into exactly one `not_run` entry with a fixed,
    count-only reason and an allowlisted label."""
    payload = {
        "concept": None,
        "withheld": 0,
        "not_run": (
            read_outcome.NotRun(label="sources/a.md", reason="[Errno 13] denied"),
            read_outcome.NotRun(label="sources/b.md", reason="[Errno 13] denied"),
        ),
    }
    rendered = gate.finish(payload, _consistency())
    not_run = _rendered_not_run(rendered)
    assert len(not_run) == 1
    assert not_run[0]["label"] in {
        "in_flight_write",
        "stale_index",
        "concept_read",
        "relations",
        "provenance_walk",
        "graph_build",
    }
    reason = str(not_run[0]["reason"])
    assert "2" in reason
    assert "sources/a.md" not in reason
    assert "denied" not in reason


def test_unrecognized_label_aggregated_not_forwarded() -> None:
    """A `NotRun` with a label outside the fixed vocabulary is aggregated
    the same way, never forwarded with its original label."""
    payload = {
        "concept": None,
        "withheld": 0,
        "not_run": (read_outcome.NotRun(label="mystery_check", reason="oops"),),
    }
    rendered = gate.finish(payload, _consistency())
    not_run = _rendered_not_run(rendered)
    assert len(not_run) == 1
    assert not_run[0]["label"] != "mystery_check"
    assert "oops" not in str(not_run[0]["reason"])


def test_finish_replaces_a_single_entry_labels_own_raw_reason() -> None:
    """A fixed-vocabulary single-entry label's raw `reason` (e.g. real
    exception text from `okf.decode_relations`) is REPLACED by `finish`'s
    hardcoded message, never forwarded verbatim -- design Decision 3:
    "every `reason` is a fixed string"."""
    payload = {
        "concept": None,
        "withheld": 0,
        "not_run": (
            read_outcome.NotRun(
                label="relations", reason="'relations' must be a list, got str"
            ),
        ),
    }
    rendered = gate.finish(payload, _consistency())
    not_run = _rendered_not_run(rendered)
    assert len(not_run) == 1
    assert not_run[0]["label"] == "relations"
    reason = str(not_run[0]["reason"])
    assert reason != "'relations' must be a list, got str"
    assert "must be a list" not in reason


def test_finish_renders_in_flight_write_warning() -> None:
    """A positive `in_flight_writes` count becomes a count-only
    `in_flight_write` warning."""
    payload = {"concept": None, "withheld": 0, "not_run": ()}
    rendered = gate.finish(payload, _consistency(in_flight_writes=3))
    warnings = _rendered_warnings(rendered)
    assert warnings == [
        {
            "code": "in_flight_write",
            "count": 3,
            "message": warnings[0]["message"],
        }
    ]


def test_finish_never_raises_stale_index_as_not_run() -> None:
    """`stale_index` never produces a `not_run` entry, only a warning."""
    payload = {"concept": None, "withheld": 0, "not_run": ()}
    rendered = gate.finish(payload, _consistency(stale_stores=("fts",)))
    assert _rendered_not_run(rendered) == []
    assert any(w["code"] == "stale_index" for w in _rendered_warnings(rendered))
