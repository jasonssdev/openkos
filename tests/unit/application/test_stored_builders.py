"""The store-only builders (#1391): persisted edge-typing suggestions and cached
volatility answers become the SAME queue proposals the daemon's producers
publish, with no client, no backend and no walk of the candidate graph."""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import pending as application_pending
from openkos.application import queue_producers as qp
from openkos.application import suggest_relations_service as relations
from openkos.application import suggest_volatility_service as volatility
from openkos.application.suggest_relations_service import SuggestRelationsOutcome
from openkos.graph.base import Edge
from openkos.resolution import volatility_typing
from openkos.resolution.edge_typing import DIRECTION_CORRECTED_NOTE, EdgeSuggestion
from openkos.resolution.volatility_typing import TierSuggestion
from openkos.state import derived
from openkos.state import edge_suggestions as store
from openkos.state import volatility_suggestions as volatility_store
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

SRC, DST = "concepts/a", "concepts/b"


def _digest(ref: str) -> str | None:
    return f"h:{ref}"


def _row(
    *,
    suggested_type: str = "works_at",
    include_confidential: bool = False,
    digests: tuple[tuple[str, str], ...] = ((SRC, f"h:{SRC}"), (DST, f"h:{DST}")),
) -> store.PersistedEdgeSuggestion:
    return store.PersistedEdgeSuggestion(
        source_id=SRC,
        target_id=DST,
        suggested_type=suggested_type,
        rationale="they work together",
        include_confidential=include_confidential,
        input_digests=tuple(
            store.InputDigest(input_ref=ref, digest=digest) for ref, digest in digests
        ),
    )


def _serve(
    row: store.PersistedEdgeSuggestion, *, include_confidential: bool | None = False
) -> EdgeSuggestion | None:
    return relations.servable_edge_suggestion(
        row, Edge(SRC, DST), _digest, include_confidential=include_confidential
    )


# -- S2.1 the per-row predicate ------------------------------------------------


def test_a_row_matching_everything_is_served_as_the_typed_suggestion() -> None:
    served = _serve(_row())

    assert served is not None
    assert (served.suggested_type, served.rationale) == (
        "works_at",
        "they work together",
    )
    assert served.edge == Edge(SRC, DST)


@pytest.mark.parametrize(
    "row",
    [
        _row(digests=((SRC, f"h:{SRC}"),)),  # a missing endpoint digest
        _row(
            digests=(
                (SRC, f"h:{SRC}"),
                (DST, f"h:{DST}"),
                ("concepts/c", "h:concepts/c"),
            )
        ),  # an extra digest
        _row(digests=((SRC, f"h:{SRC}"), (DST, "stale"))),  # a changed digest
        _row(suggested_type="   "),  # a type that no longer validates (blank)
    ],
    ids=["missing", "extra", "changed", "invalid-type"],
)
def test_a_row_failing_any_check_is_refused(row: store.PersistedEdgeSuggestion) -> None:
    assert _serve(row) is None


def test_the_confidential_bit_is_compared_when_given() -> None:
    assert _serve(_row(include_confidential=True), include_confidential=False) is None
    assert _serve(_row(include_confidential=True), include_confidential=True)


def test_the_confidential_bit_is_not_compared_when_none() -> None:
    assert _serve(_row(include_confidential=True), include_confidential=None)
    assert _serve(_row(include_confidential=False), include_confidential=None)


# -- S2.3 the stored edge suggestions ------------------------------------------


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> config.WorkspaceLayout:
    root = make_workspace(tmp_path, monkeypatch)
    for cid in (SRC, DST, "concepts/c"):
        write_doc(root, cid, {"type": "Concept", "title": cid}, f"Body of {cid}.\n")
    return config.WorkspaceLayout(root)


def _persist(
    layout: config.WorkspaceLayout, *results: EdgeSuggestion, confidential: bool = False
) -> None:
    relations.persist_edge_suggestions(
        layout, results, include_confidential=confidential
    )


def _daemon_payloads(
    layout: config.WorkspaceLayout, *results: EdgeSuggestion
) -> list[str]:
    outcome = SuggestRelationsOutcome(
        status="completed", results=tuple(results), total=len(results)
    )
    return [
        proposal.payload
        for proposal in qp.relation_proposals(
            outcome,
            current_digest=application_pending.current_finding_digest(
                layout.bundle_dir
            ),
        )
    ]


def test_a_stored_row_is_served_with_the_byte_identical_daemon_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path, monkeypatch)
    plain = EdgeSuggestion(Edge(SRC, DST), "works_at", "they work together")
    # A direction-corrected suggestion: the row is keyed on the CANDIDATE pair
    # and the correction rides in the rationale (#991).
    swapped = EdgeSuggestion(
        Edge(DST, "concepts/c"),
        "employs",
        "reads backwards" + DIRECTION_CORRECTED_NOTE,
        corrected_edge=Edge("concepts/c", DST),
    )
    _persist(layout, plain, swapped)

    served = relations.stored_edge_suggestions(layout)

    assert [(s.edge, s.suggested_type) for s in served] == [
        (Edge(SRC, DST), "works_at"),
        (Edge(DST, "concepts/c"), "employs"),
    ]
    assert served[1].corrected_edge == Edge("concepts/c", DST)
    assert [
        proposal.payload
        for proposal in (
            qp.relation_suggestion_proposal(
                s,
                current_digest=application_pending.current_finding_digest(
                    layout.bundle_dir
                ),
            )
            for s in served
        )
        if proposal is not None
    ] == _daemon_payloads(layout, plain, swapped)


def test_the_latest_row_per_directed_pair_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path, monkeypatch)
    _persist(layout, EdgeSuggestion(Edge(SRC, DST), "works_at", "first"))
    _persist(layout, EdgeSuggestion(Edge(SRC, DST), "founded", "second"))

    served = relations.stored_edge_suggestions(layout)

    assert [(s.suggested_type, s.rationale) for s in served] == [("founded", "second")]


def test_a_row_whose_endpoint_changed_since_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path, monkeypatch)
    _persist(
        layout,
        EdgeSuggestion(Edge(SRC, DST), "works_at", "stays"),
        EdgeSuggestion(Edge(DST, "concepts/c"), "employs", "goes stale"),
    )
    # The edge was typed, unlinked or forgotten since: an endpoint's bytes moved.
    write_doc(layout.root, "concepts/c", {"type": "Concept"}, "Rewritten.\n")

    served = relations.stored_edge_suggestions(layout)

    assert [(s.edge, s.rationale) for s in served] == [(Edge(SRC, DST), "stays")]


def test_a_forgotten_endpoint_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path, monkeypatch)
    _persist(layout, EdgeSuggestion(Edge(SRC, DST), "works_at", "gone"))
    (layout.bundle_dir / f"{DST}.md").unlink()

    assert relations.stored_edge_suggestions(layout) == []


def test_no_findings_store_serves_nothing_and_a_corrupt_one_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path, monkeypatch)
    assert relations.stored_edge_suggestions(layout) == []
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a database")

    assert relations.stored_edge_suggestions(layout) == []


# -- S2.4 the disjunction pin --------------------------------------------------


def test_the_two_confidentiality_bits_plan_as_a_disjunction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The volatility builder plans two regimes, `(include=False, exempt=False)`
    and `(include=True, exempt=False)`, and treats the second as standing for
    every regime where EITHER bit is set. That holds only while `sensitivity`
    keeps the disjunction; pin it before the builder relies on it."""
    layout = _workspace(tmp_path, monkeypatch)
    write_doc(
        layout.root,
        "concepts/secret",
        {"type": "Concept", "title": "secret", "sensitivity": "confidential"},
        "Secret body.\n",
    )
    write_doc(
        layout.root,
        "concepts/open",
        {"type": "Concept", "title": "open", "sensitivity": "private"},
        "Open body.\n",
    )

    def plan(
        *, include: bool, exempt: bool
    ) -> tuple[volatility_typing.PlannedType, ...]:
        return volatility_typing.plan_types(
            layout.bundle_dir, include_confidential=include, local_exemption=exempt
        )

    excluded = plan(include=False, exempt=False)
    assert plan(include=True, exempt=False) == plan(include=False, exempt=True)
    assert plan(include=True, exempt=False) == plan(include=True, exempt=True)
    # Non-vacuous: the regimes genuinely differ, so equality above is a finding.
    assert plan(include=True, exempt=False) != excluded
    assert [p.input_refs for p in excluded] == [("concepts/open",)]
    assert [p.input_refs for p in plan(include=True, exempt=False)] == [
        ("concepts/a", "concepts/b", "concepts/c", "concepts/open", "concepts/secret")
    ]


# -- S2.5 the stored volatility answers ----------------------------------------

MODEL = "the-task-model"


def _volatility_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[config.WorkspaceLayout, config.Config]:
    root = make_workspace(tmp_path, monkeypatch)
    for cid, kind, level in (
        ("people/ada", "Person", "private"),
        ("people/hidden", "Person", "confidential"),
        ("events/launch", "Event", "private"),
    ):
        write_doc(
            root,
            cid,
            {"type": kind, "title": cid, "sensitivity": level},
            f"Body of {cid}.\n",
        )
    monkeypatch.setattr(config, "resolve_task_model", lambda cfg, task: MODEL)
    return config.WorkspaceLayout(root), config.read_config(root)


def _digests(
    layout: config.WorkspaceLayout, cfg: config.Config, type_name: str
) -> tuple[str, str]:
    """The type's prompt digest in the excluded and in the included regime."""
    by_regime = [
        {
            p.type_name: p.prompt_digest
            for p in volatility_typing.plan_types(
                layout.bundle_dir,
                include_confidential=include,
                local_exemption=False,
                rationale_language=cfg.rationale_language,
                type_tiers=cfg.type_tiers,
            )
        }
        for include in (False, True)
    ]
    return by_regime[0][type_name], by_regime[1][type_name]


def _record(
    layout: config.WorkspaceLayout,
    type_name: str,
    digest: str,
    tier: str,
    *,
    model: str = MODEL,
    rationale: str = "because",
) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        volatility_store.record_volatility_suggestions(
            conn,
            [
                volatility_store.PersistedVolatilitySuggestion(
                    type_name=type_name,
                    model=model,
                    prompt_digest=digest,
                    suggested_tier=tier,
                    rationale=rationale,
                    input_refs=(),
                )
            ],
        )
    finally:
        conn.close()


def test_a_type_answered_in_only_one_regime_is_served_from_that_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    excluded, included = _digests(layout, cfg, "Person")
    assert excluded != included  # the confidential body changes the prompt
    _record(layout, "Person", excluded, "slow")

    result = volatility.stored_volatility_answers(layout, cfg)

    assert [(a.type_name, a.suggested_tier, a.rationale) for a in result.answers] == [
        ("Person", "slow", "because")
    ]
    assert result.ambiguous == 0

    _record(layout, "Person", included, "volatile", rationale="seen it all")
    result = volatility.stored_volatility_answers(layout, cfg)

    assert [(a.suggested_tier, a.rationale) for a in result.answers] == [
        ("volatile", "seen it all")
    ]


def test_a_type_whose_two_regimes_share_a_prompt_is_served_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    excluded, included = _digests(layout, cfg, "Event")
    assert excluded == included  # no confidential Event: one question
    _record(layout, "Event", excluded, "static")

    result = volatility.stored_volatility_answers(layout, cfg)

    assert [(a.type_name, a.suggested_tier) for a in result.answers] == [
        ("Event", "static")
    ]
    assert result.ambiguous == 0


def _two_answers(
    monkeypatch: pytest.MonkeyPatch,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    *,
    excluded_answer: tuple[str, str],
    included_answer: tuple[str, str],
) -> None:
    """The store keeps ONE row per type, so no persisted state answers a type's
    two regime prompts differently today; stub the lookup to prove the builder
    still refuses to guess if the store ever does."""
    excluded, included = _digests(layout, cfg, "Person")
    held = {
        ("Person", excluded): excluded_answer,
        ("Person", included): included_answer,
    }

    def fake_cached_answers(
        layout: config.WorkspaceLayout,
        model: str,
        type_tiers: object = None,
    ) -> object:
        def lookup(type_name: str, digest: str) -> TierSuggestion | None:
            answer = held.get((type_name, digest))
            if answer is None:
                return None
            return TierSuggestion(type_name, "", answer[0], answer[1])

        return lookup

    monkeypatch.setattr(volatility, "cached_answers", fake_cached_answers)


def test_a_type_answered_identically_in_both_regimes_is_served_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    _two_answers(
        monkeypatch,
        layout,
        cfg,
        excluded_answer=("slow", "same"),
        included_answer=("slow", "same"),
    )

    result = volatility.stored_volatility_answers(layout, cfg)

    assert [(a.type_name, a.suggested_tier, a.rationale) for a in result.answers] == [
        ("Person", "slow", "same")
    ]
    assert result.ambiguous == 0


def test_a_type_answered_differently_in_both_regimes_is_skipped_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    _two_answers(
        monkeypatch,
        layout,
        cfg,
        excluded_answer=("slow", "same"),
        included_answer=("volatile", "same"),
    )

    result = volatility.stored_volatility_answers(layout, cfg)

    assert result.answers == ()
    assert result.ambiguous == 1


def test_another_models_rows_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    excluded, _included = _digests(layout, cfg, "Person")
    _record(layout, "Person", excluded, "slow", model="some-other-model")

    result = volatility.stored_volatility_answers(layout, cfg)

    assert (result.answers, result.ambiguous) == ((), 0)


def test_a_type_equal_to_its_current_effective_tier_yields_no_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, cfg = _volatility_workspace(tmp_path, monkeypatch)
    excluded, _included = _digests(layout, cfg, "Event")
    current = volatility_typing.effective_tier("Event", cfg.type_tiers)
    _record(layout, "Event", excluded, current)
    _record(layout, "Person", _digests(layout, cfg, "Person")[0], "static")

    result = volatility.stored_volatility_answers(layout, cfg)

    assert {a.type_name: a.current_default for a in result.answers} == {
        "Event": current,
        "Person": volatility_typing.effective_tier("Person", cfg.type_tiers),
    }
    proposals = {
        a.type_name: qp.volatility_suggestion_proposal(a) for a in result.answers
    }
    assert proposals["Event"] is None
    assert proposals["Person"] is not None
