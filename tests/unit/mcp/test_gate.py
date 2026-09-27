"""Direct unit tests for `openkos.mcp.gate`'s `get`-facing surface
(mcp-read-surface slice 5, design Decisions 3-4): `disclose_get`'s
truth table, `withheld` counting per channel, and `finish`'s consistency
`warnings`/aggregated `not_run` rendering.
"""

from __future__ import annotations

from typing import cast

from openkos import read_outcome
from openkos.application import concept_read, list_service, next_action
from openkos.application import consistency as application_consistency
from openkos.application import query as query_service
from openkos.bundle import listing
from openkos.mcp import gate, tools
from openkos.model import okf
from openkos.retrieval.answer import AnswerResult, Citation


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


# ---------------------------------------------------------------------------
# 6.2: disclose_navigate's table (design Decision 5)
# ---------------------------------------------------------------------------


def _neighborhood(
    *,
    concept_id: str = "concepts/target",
    neighbors: tuple[concept_read.Neighbor, ...] = (),
    skipped_count: int = 0,
) -> concept_read.Neighborhood:
    return concept_read.Neighborhood(
        concept_id=concept_id, neighbors=neighbors, skipped_count=skipped_count
    )


def test_disclose_navigate_table_target_not_disclosable() -> None:
    """A target `concept_id` itself not disclosable returns `concept_id:
    null, withheld: 1` -- no neighbors listed, even if some were passed."""
    raw = _neighborhood(
        neighbors=(
            concept_read.Neighbor(
                concept_id="concepts/other", direction="out", relation_type=None
            ),
        )
    )
    result = gate.disclose_navigate(raw, _snapshot(frozenset()))
    assert result["concept_id"] is None
    assert result["withheld"] == 1
    assert result["neighbors"] == []


def test_disclose_navigate_removes_confidential_outbound_neighbor() -> None:
    """A confidential neighbor reachable via an OUTBOUND edge is removed
    and counted."""
    raw = _neighborhood(
        neighbors=(
            concept_read.Neighbor(
                concept_id="concepts/blocked-out", direction="out", relation_type=None
            ),
        )
    )
    result = gate.disclose_navigate(raw, _snapshot(frozenset({"concepts/target"})))
    assert result["concept_id"] == "concepts/target"
    assert result["neighbors"] == []
    assert result["withheld"] == 1


def test_disclose_navigate_removes_confidential_inbound_neighbor() -> None:
    """The same via an INBOUND edge is also removed and counted -- this is
    the case a mutation that filters only outbound edges must fail."""
    raw = _neighborhood(
        neighbors=(
            concept_read.Neighbor(
                concept_id="concepts/blocked-in", direction="in", relation_type=None
            ),
        )
    )
    result = gate.disclose_navigate(raw, _snapshot(frozenset({"concepts/target"})))
    assert result["concept_id"] == "concepts/target"
    assert result["neighbors"] == []
    assert result["withheld"] == 1


def test_disclose_navigate_keeps_disclosable_neighbors_both_directions() -> None:
    """A disclosable outbound and a disclosable inbound neighbor both
    survive, each rendered with `id`, `direction`, and `relation`."""
    raw = _neighborhood(
        neighbors=(
            concept_read.Neighbor(
                concept_id="concepts/allowed-in",
                direction="in",
                relation_type="related_to",
            ),
            concept_read.Neighbor(
                concept_id="concepts/allowed-out",
                direction="out",
                relation_type=None,
            ),
        )
    )
    result = gate.disclose_navigate(
        raw,
        _snapshot(
            frozenset(
                {"concepts/target", "concepts/allowed-in", "concepts/allowed-out"}
            )
        ),
    )
    assert result["concept_id"] == "concepts/target"
    assert result["withheld"] == 0
    assert result["neighbors"] == [
        {"id": "concepts/allowed-in", "direction": "in", "relation": "related_to"},
        {"id": "concepts/allowed-out", "direction": "out", "relation": None},
    ]


def test_disclose_navigate_reports_graph_build_not_run() -> None:
    """`store.skipped` non-empty produces a `not_run` entry labelled
    `graph_build` with a count-only reason, and the tool still returns
    whatever neighbors it did read."""
    raw = _neighborhood(
        neighbors=(
            concept_read.Neighbor(
                concept_id="concepts/allowed-out", direction="out", relation_type=None
            ),
        ),
        skipped_count=2,
    )
    result = gate.disclose_navigate(
        raw, _snapshot(frozenset({"concepts/target", "concepts/allowed-out"}))
    )
    assert result["concept_id"] == "concepts/target"
    assert result["neighbors"] == [
        {"id": "concepts/allowed-out", "direction": "out", "relation": None}
    ]
    not_run = _raw_not_run(result)
    assert len(not_run) == 1
    assert not_run[0].label == "graph_build"
    reason = not_run[0].reason
    assert "2" in reason


def test_graph_build_reason_validated_before_forwarding() -> None:
    """`finish` must not forward a `graph_build` `NotRun`'s raw `reason`
    unless it matches the EXACT count-only shape `disclose_navigate`
    produces (design Decision 3: every `reason` is fixed and count-only,
    never exception text or a document path). The label alone is not a
    trust boundary -- `finish` also receives service-produced `NotRun`s
    under the same label vocabulary, so a reason carrying a document path
    or `str(exc)` must be treated as unrecognized (aggregated, count-only)
    exactly like any other untrusted entry, never forwarded verbatim."""
    document_path_payload = {
        "concept_id": None,
        "withheld": 0,
        "not_run": (
            read_outcome.NotRun(
                label="graph_build", reason="/concepts/secret.md: boom"
            ),
        ),
    }
    rendered = gate.finish(document_path_payload, _consistency())
    rendered_text = str(_rendered_not_run(rendered))
    assert "/concepts/secret.md" not in rendered_text
    assert "boom" not in rendered_text

    exception_text_payload = {
        "concept_id": None,
        "withheld": 0,
        "not_run": (
            read_outcome.NotRun(
                label="graph_build",
                reason="cannot read concepts/zq-canary-7f3a: ZQ-CANARY-BODY-7F3A",
            ),
        ),
    }
    rendered = gate.finish(exception_text_payload, _consistency())
    rendered_text = str(_rendered_not_run(rendered))
    assert "zq-canary-7f3a" not in rendered_text
    assert "ZQ-CANARY-BODY-7F3A" not in rendered_text

    legitimate_payload = {
        "concept_id": None,
        "withheld": 0,
        "not_run": (
            read_outcome.NotRun(
                label="graph_build", reason="2 edges could not be included"
            ),
        ),
    }
    rendered = gate.finish(legitimate_payload, _consistency())
    assert _rendered_not_run(rendered) == [
        {"label": "graph_build", "reason": "2 edges could not be included"}
    ]


# ---------------------------------------------------------------------------
# 7.7: disclose_pending's truth table (design Decision 6)
# ---------------------------------------------------------------------------


def _next_result(
    *,
    action: next_action.NextAction | None = None,
    declinations: tuple[str, ...] = (),
    declination_subjects: tuple[tuple[str, ...] | None, ...] = (),
    skip_notices: tuple[str, ...] = (),
) -> next_action.NextResult:
    return next_action.NextResult(
        action=action,
        declinations=declinations,
        declination_subjects=declination_subjects,
        skip_notices=skip_notices,
    )


def test_disclose_pending_undeclared_action_is_withheld() -> None:
    """An action whose `subjects` is `None` (undeclared) is withheld
    regardless of whether the underlying finding is itself harmless --
    fail-closed on the absence of a declaration, not on content."""
    action = next_action.NextAction(command="openkos reindex", reason="r")
    assert action.subjects is None
    raw = _next_result(action=action)
    result = gate.disclose_pending(raw, _snapshot(frozenset()))
    assert result["action"] is None
    assert result["withheld"] == 1


def test_disclose_pending_declared_subject_free_action_is_disclosed() -> None:
    """An action whose `subjects` is the explicit empty tuple `()` is
    disclosed normally -- an explicitly declared empty set trivially
    satisfies "every subject is disclosable"."""
    action = next_action.NextAction(command="openkos reindex", reason="r", subjects=())
    raw = _next_result(action=action)
    result = gate.disclose_pending(raw, _snapshot(frozenset()))
    assert result["action"] == {"command": "openkos reindex", "reason": "r"}
    assert result["withheld"] == 0


def test_disclose_pending_action_with_one_non_disclosable_subject_is_withheld() -> None:
    """An action whose `subjects` includes one non-disclosable concept id is
    withheld in full, not partially disclosed."""
    action = next_action.NextAction(
        command="openkos backfill-sensitivity",
        reason="r",
        subjects=("concepts/derived", "sources/a"),
    )
    raw = _next_result(action=action)
    result = gate.disclose_pending(
        raw, _snapshot(frozenset({"concepts/derived"}))
    )  # "sources/a" is missing from the allowed set
    assert result["action"] is None
    assert result["withheld"] == 1


def test_disclose_pending_misaligned_declination_subjects_withholds_all() -> None:
    """`declination_subjects` a different length from `declinations` (a
    defect condition) withholds every declination, fail-closed, rather than
    pairing any declination with the wrong subjects."""
    raw = _next_result(
        declinations=("a: declined", "b: declined"),
        declination_subjects=(("concepts/a",),),  # one entry short
    )
    result = gate.disclose_pending(raw, _snapshot(frozenset({"concepts/a"})))
    assert result["declinations"] == []
    assert result["withheld"] == 2


def test_disclose_pending_aligned_declination_subjects_filter_individually() -> None:
    """With aligned lengths, each declination is kept when its own subjects
    are declared and all disclosable, and withheld individually otherwise."""
    raw = _next_result(
        declinations=("a: declined", "b: declined"),
        declination_subjects=(("concepts/a",), ("concepts/b",)),
    )
    result = gate.disclose_pending(raw, _snapshot(frozenset({"concepts/a"})))
    assert result["declinations"] == ["a: declined"]
    assert result["withheld"] == 1


def test_disclose_pending_skip_notices_become_skipped_documents_not_withheld() -> None:
    """`skip_notices` become `skipped_documents: len(...)`, never counted
    toward `withheld`."""
    raw = _next_result(skip_notices=("concepts/broken.md: skipped (unparseable)",))
    result = gate.disclose_pending(raw, _snapshot(frozenset()))
    assert result["skipped_documents"] == 1
    assert result["withheld"] == 0


# ---------------------------------------------------------------------------
# 9.2: disclose_query's table (design Decision 9)
# ---------------------------------------------------------------------------


def _answer_result(
    *,
    answer: str = "the reply",
    citations: list[Citation] = [],  # noqa: B006 -- never mutated by callers below
    excerpted_titles: list[str] | None = None,
    excerpted_ids: list[str] | None = None,
    omitted_titles: list[str] | None = None,
    omitted_ids: list[str] | None = None,
    history_truncated_titles: list[str] | None = None,
    history_truncated_ids: list[str] | None = None,
    skip_notices: tuple[str, ...] = (),
) -> AnswerResult:
    return AnswerResult(
        answer=answer,
        citations=citations,
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=list(skip_notices),
        excerpted_titles=excerpted_titles if excerpted_titles is not None else [],
        excerpted_ids=excerpted_ids if excerpted_ids is not None else [],
        omitted_titles=omitted_titles if omitted_titles is not None else [],
        omitted_ids=omitted_ids if omitted_ids is not None else [],
        history_truncated_titles=(
            history_truncated_titles if history_truncated_titles is not None else []
        ),
        history_truncated_ids=(
            history_truncated_ids if history_truncated_ids is not None else []
        ),
    )


def _outcome(
    result: AnswerResult,
    *,
    vector_store_unavailable: bool = False,
    fts_unavailable: bool = False,
) -> query_service.QueryOutcome:
    return query_service.QueryOutcome(
        result=result,
        vector_store_unavailable=vector_store_unavailable,
        fts_unavailable=fts_unavailable,
    )


def test_disclose_query_citations_filtered_per_snapshot() -> None:
    """Citations are kept when disclosable, else counted -- and every
    citation disclosable means `answer_withheld` is `false`, the answer
    text passes through, and every count/flag/attribution/no_match_cause
    passes through unchanged. Covers "No withheld citation discloses the
    answer normally"."""
    result = _answer_result(
        answer="the reply",
        citations=[
            Citation(concept_id="concepts/allowed", title="Allowed"),
            Citation(concept_id="concepts/allowed2", title="Allowed Two"),
        ],
    )
    outcome = _outcome(result)
    snapshot = _snapshot(frozenset({"concepts/allowed", "concepts/allowed2"}))

    rendered = gate.disclose_query(outcome, snapshot)

    assert rendered["answer"] == "the reply"
    assert rendered["answer_withheld"] is False
    assert rendered["withheld"] == 0
    assert rendered["citations"] == [
        {
            "id": "concepts/allowed",
            "title": "Allowed",
            "excerpted": False,
            "confidential": False,
            "history": None,
        },
        {
            "id": "concepts/allowed2",
            "title": "Allowed Two",
            "excerpted": False,
            "confidential": False,
            "history": None,
        },
    ]
    assert rendered["llm_invoked"] is True
    assert rendered["no_match_cause"] == "none"


def test_disclose_query_one_withheld_citation_withholds_the_whole_answer() -> None:
    """ONE withheld citation empties the answer text and sets
    `answer_withheld: true` -- covers "A withheld citation withholds the
    whole answer text". Kills keeping the answer text when any citation is
    withheld."""
    result = _answer_result(
        answer="the reply drew on a confidential source",
        citations=[
            Citation(concept_id="concepts/allowed", title="Allowed"),
            Citation(concept_id="concepts/blocked", title="Blocked"),
        ],
    )
    outcome = _outcome(result)
    snapshot = _snapshot(frozenset({"concepts/allowed"}))

    rendered = gate.disclose_query(outcome, snapshot)

    assert rendered["answer"] == ""
    assert rendered["answer_withheld"] is True
    assert rendered["withheld"] == 1
    assert rendered["citations"] == [
        {
            "id": "concepts/allowed",
            "title": "Allowed",
            "excerpted": False,
            "confidential": False,
            "history": None,
        }
    ]


def test_disclose_query_title_scrubbed_by_paired_id_not_title_text() -> None:
    """A title is scrubbed by its PAIRED id, not by title text -- two
    identical titles with different ids are treated independently."""
    result = _answer_result(
        excerpted_titles=["Same Title", "Same Title"],
        excerpted_ids=["concepts/allowed", "concepts/blocked"],
    )
    outcome = _outcome(result)
    snapshot = _snapshot(frozenset({"concepts/allowed"}))

    rendered = gate.disclose_query(outcome, snapshot)

    assert rendered["excerpted_titles"] == ["Same Title"]
    assert rendered["withheld"] == 1


def test_disclose_query_misaligned_title_id_pair_drops_every_title() -> None:
    """A misaligned title/id pair (different lengths) drops EVERY title in
    that list and counts them all -- fail closed rather than pairing a
    title with the wrong id."""
    result = _answer_result(
        omitted_titles=["Omitted One", "Omitted Two"],
        omitted_ids=["concepts/allowed"],  # one entry short
    )
    outcome = _outcome(result)
    snapshot = _snapshot(frozenset({"concepts/allowed"}))

    rendered = gate.disclose_query(outcome, snapshot)

    assert rendered["omitted_titles"] == []
    assert rendered["withheld"] == 2


def test_disclose_query_skip_notices_become_skipped_documents() -> None:
    """`skip_notices` become `skipped_documents`, separate from
    `withheld`."""
    result = _answer_result(skip_notices=("concepts/broken.md: skipped",))
    outcome = _outcome(result)

    rendered = gate.disclose_query(outcome, _snapshot(frozenset()))

    assert rendered["skipped_documents"] == 1
    assert rendered["withheld"] == 0


def test_disclose_query_counts_and_degraded_pass_through() -> None:
    """`counts`/`degraded` are built from `AnswerResult`'s own fields and
    `QueryOutcome`'s two store-unavailable flags, all passed through
    unchanged."""
    result = AnswerResult(
        answer="ok",
        citations=[],
        fts_hit_count=3,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=2,
        fused_count=4,
        context_block_count=1,
        dense_degraded=True,
        sufficiency_degraded=True,
    )
    outcome = _outcome(result, vector_store_unavailable=True, fts_unavailable=False)

    rendered = gate.disclose_query(outcome, _snapshot(frozenset()))

    assert rendered["counts"] == {
        "fts_hits": 3,
        "dense_hits": 2,
        "fused": 4,
        "context_blocks": 1,
    }
    assert rendered["degraded"] == {
        "dense": True,
        "sufficiency": True,
        "vector_store_unavailable": True,
        "fts_unavailable": False,
    }


# ---------------------------------------------------------------------------
# 9.6: stale_index only on query (design Decision 11)
# ---------------------------------------------------------------------------


def test_stale_index_only_on_query() -> None:
    """`query`'s `stale_reads` declares `("fts",)`; `get`/`navigate`/
    `pending` declare `()`, and `finish` only ever renders `stale_index`
    when `Consistency.stale_stores` is non-empty regardless of the
    declaring tool -- so a tool that never declares a stale read can never
    surface one. Covers "A stale derived store warns only on query" and
    "get, navigate, and pending do not report stale_index"."""
    assert tools.REGISTRY["query"].stale_reads == ("fts",)
    assert tools.REGISTRY["get"].stale_reads == ()
    assert tools.REGISTRY["navigate"].stale_reads == ()
    assert tools.REGISTRY["pending"].stale_reads == ()

    payload = {"concept": None, "withheld": 0, "not_run": ()}
    rendered = gate.finish(payload, _consistency(stale_stores=("fts",)))
    assert any(w["code"] == "stale_index" for w in _rendered_warnings(rendered))

    rendered_no_stale = gate.finish(payload, _consistency())
    assert not any(
        w["code"] == "stale_index" for w in _rendered_warnings(rendered_no_stale)
    )
