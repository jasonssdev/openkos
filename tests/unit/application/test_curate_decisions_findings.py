"""Contradiction and revision rows on the curation decision surface
(settle-other-kinds, ADR-0072): their actions, freshness, previews and
token-path commits, written through the CLI's own write cores.

Every test drives the real surface over a real workspace and reads the bundle
and the queue back, asserting what was carried rather than a verdict.
"""

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import curate_decisions, revisions
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionObserver,
    DecisionPorts,
    DecisionVersion,
    NotApplied,
    Stale,
    WithheldAction,
)
from openkos.bundle import decisions as bundle_decisions
from openkos.model import okf
from openkos.state import derived
from openkos.state import pending_queue as pq
from openkos.state.vectorstore import content_hash
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_A, _B = "concepts/alpha", "concepts/beta"
_PAIR = (_A, _B)
_NOW = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = make_workspace(tmp_path, monkeypatch)
    write_concept(workspace, _A, title="Alpha", body="Alpha says the date is May.")
    write_concept(workspace, _B, title="Beta", body="Beta says the date is June.")
    return workspace


def _layout(root: Path) -> config.WorkspaceLayout:
    return config.WorkspaceLayout(root)


def _digest(root: Path, concept_id: str) -> str:
    return content_hash((root / "bundle" / f"{concept_id}.md").read_bytes())


def _enqueue(root: Path, proposal: pq.Proposal) -> pq.Proposal:
    layout = _layout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.upsert_proposal(
            conn,
            proposal,
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()
    return proposal


def _contradiction(
    root: Path, merged_absorbed_id: str | None = None, payload: object = None
) -> pq.Proposal:
    digests: tuple[pq.InputDigest, ...]
    if merged_absorbed_id is None:
        digests = (
            pq.InputDigest(_A, _digest(root, _A)),
            pq.InputDigest(_B, _digest(root, _B)),
            pq.InputDigest("relation_type", content_hash(b"related_to")),
        )
    else:
        digests = (
            pq.InputDigest(f"{_A}:survivor_before", content_hash(b"before")),
            pq.InputDigest(
                f"{merged_absorbed_id}:absorbed_snapshot", content_hash(b"snap")
            ),
        )
    # A merged-body finding is stored as the producer stores it: the survivor
    # twice, the absorbed page as the discriminator.
    pair = _PAIR if merged_absorbed_id is None else (_A, _A)
    stored = (
        {
            "pair_ids": list(pair),
            "merged_absorbed_id": merged_absorbed_id,
            "confidence": 0.9,
            "rationale": "May against June.",
            "conflicting_claims": ["the date is May", "the date is June"],
        }
        if payload is None
        else payload
    )
    return _enqueue(
        root,
        pq.Proposal(
            kind="contradiction",
            key_body=pq.contradiction_key(pair, merged_absorbed_id),
            producer="contradictions/1",
            payload=json.dumps(stored),
            targets=_PAIR if merged_absorbed_id is None else (_A, merged_absorbed_id),
            input_digests=digests,
            merged_absorbed_id=merged_absorbed_id,
        ),
    )


def _revision(
    root: Path,
    verdict: str = "reverses",
    dates: tuple[str | None, str | None] = ("2026-01-01", "2026-03-01"),
    payload: object = None,
) -> pq.Proposal:
    layout = _layout(root)
    recorded = revisions.revision_input_digests(
        layout, revisions._bundle_text_snapshot(layout), _PAIR
    )
    stored = (
        {
            "pair_ids": list(_PAIR),
            "verdict": verdict,
            "confidence": 0.8,
            "rationale": "June overturns May.",
            "quotes": ["May.", "June."],
            "dates": list(dates),
            "date_states": ["dated" if d else "missing" for d in dates],
        }
        if payload is None
        else payload
    )
    return _enqueue(
        root,
        pq.Proposal(
            kind="revision",
            key_body=pq.revision_key(_PAIR),
            producer="revisions/1",
            payload=json.dumps(stored),
            targets=_PAIR,
            input_digests=tuple(
                pq.InputDigest(d.input_ref, d.digest) for d in recorded
            ),
        ),
    )


def _items(root: Path) -> list[pq.PendingItem]:
    conn = derived.open_derived_connection(_layout(root).findings_db_path)
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def _row(root: Path, proposal: pq.Proposal) -> pq.PendingItem:
    [row] = [i for i in _items(root) if i.decision_key == proposal.decision_key]
    return row


def _listed(root: Path) -> curate_decisions.DecisionItem:
    [item] = curate_decisions.list_open_decisions(_layout(root))
    return item


def _relations(root: Path, concept_id: str) -> list[okf.Relation]:
    text = (root / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    return okf.decode_relations(okf.load_frontmatter(text)[0])


def _status(root: Path, concept_id: str) -> object:
    text = (root / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    return okf.load_frontmatter(text)[0].get("status")


def _preview(
    root: Path, proposal: pq.Proposal, action: Action
) -> curate_decisions.FindingDecisionPreview | NotApplied | Stale:
    return curate_decisions.preview_finding(
        root, _layout(root), proposal.decision_key, action, now=_NOW
    )


def _must(
    result: curate_decisions.FindingDecisionPreview | NotApplied | Stale,
) -> curate_decisions.FindingDecisionPreview:
    assert isinstance(result, curate_decisions.FindingDecisionPreview), result
    return result


def _apply(
    root: Path,
    version: DecisionVersion,
    action: Action,
    commits: CommitRecorder | None = None,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.apply_decision(
        root,
        _layout(root),
        version,
        action,
        policy=curate_decisions.DecisionPolicy(now=_NOW),
        ports=DecisionPorts(autocommit=commits or CommitRecorder()),
        observer=DecisionObserver(),
        surface="api",
    )


# -- the list ------------------------------------------------------------------ #


def test_a_typed_edge_contradiction_offers_decline_and_the_three_resolutions(
    root: Path,
) -> None:
    _contradiction(root)

    item = _listed(root)

    assert item.actions == (
        Action.DECLINE,
        Action.RECONCILE,
        Action.SUPERSEDE,
        Action.SUPERSEDE_REVERSED,
        Action.SKIP,
    )
    assert item.withheld == ()
    assert item.fresh is True


def test_a_merged_body_contradiction_offers_decline_and_withholds_reconcile(
    root: Path,
) -> None:
    _contradiction(root, merged_absorbed_id="concepts/gone")

    item = _listed(root)

    assert item.actions == (Action.DECLINE, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.RECONCILE, "merged_content"),)
    assert item.fresh is True  # snapshot labels are not files: unchanged


def test_a_typed_edge_naming_one_page_twice_is_unreadable(root: Path) -> None:
    _contradiction(root, payload={"pair_ids": [_A, _A], "merged_absorbed_id": None})

    assert _listed(root).actions == ()


def test_an_unreadable_contradiction_offers_nothing(root: Path) -> None:
    _contradiction(root, payload={"confidence": 0.9})

    item = _listed(root)

    assert item.actions == ()
    assert item.withheld == (WithheldAction(Action.RECONCILE, "no_valid_suggestion"),)


@pytest.mark.parametrize(
    ("verdict", "dates", "expected"),
    [
        # the second Decision is later: it holds the edge
        ("reverses", ("2026-01-01", "2026-03-01"), Action.SUPERSEDE_REVERSED),
        ("refines", ("2026-01-01", "2026-03-01"), Action.REVISE_REVERSED),
        # the first is later
        ("reverses", ("2026-05-01", "2026-03-01"), Action.SUPERSEDE),
        ("refines", ("2026-05-01", "2026-03-01"), Action.REVISE),
    ],
)
def test_a_dated_revision_offers_the_one_write_its_dates_name(
    root: Path, verdict: str, dates: tuple[str, str], expected: Action
) -> None:
    _revision(root, verdict, dates)

    item = _listed(root)

    assert item.actions == (expected, Action.RECONCILE, Action.SKIP)
    assert item.fresh is True


def test_an_undated_revision_offers_every_direction(root: Path) -> None:
    _revision(root, dates=(None, None))

    assert _listed(root).actions == (
        Action.SUPERSEDE,
        Action.SUPERSEDE_REVERSED,
        Action.REVISE,
        Action.REVISE_REVERSED,
        Action.RECONCILE,
        Action.SKIP,
    )


def test_an_unreadable_revision_offers_nothing(root: Path) -> None:
    _revision(root, payload={"pair_ids": list(_PAIR), "verdict": "reaffirms"})

    item = _listed(root)

    assert item.actions == ()
    assert item.withheld == (WithheldAction(Action.RECONCILE, "no_valid_suggestion"),)


def test_an_edited_page_makes_its_contradiction_stale(root: Path) -> None:
    proposal = _contradiction(root)
    write_concept(root, _B, title="Beta", body="Beta now says July.")

    assert _listed(root).fresh is False
    assert _preview(root, proposal, Action.RECONCILE) == Stale(
        proposal.decision_key, "row_inputs_changed"
    )


def test_a_missing_page_makes_its_contradiction_stale(root: Path) -> None:
    _contradiction(root)
    (root / "bundle" / f"{_B}.md").unlink()

    assert _listed(root).fresh is False


def test_an_edited_decision_makes_its_revision_stale(root: Path) -> None:
    _revision(root)
    write_concept(root, _A, title="Alpha", body="Alpha moved.")

    assert _listed(root).fresh is False


# -- previews ------------------------------------------------------------------ #


def test_a_reconcile_preview_plans_without_writing(root: Path) -> None:
    proposal = _contradiction(root)
    before = tree(root)

    preview = _must(_preview(root, proposal, Action.SUPERSEDE))

    assert tree(root) == before
    body = preview.preview
    assert isinstance(body, curate_decisions.ReconcileDecisionPreview)
    assert (body.relation_type, body.holder_id, body.target_id) == (
        "supersedes",
        _A,
        _B,
    )
    assert body.pages_changed == (_A, _B)
    assert preview.version.decision_key == proposal.decision_key
    assert preview.version.payload_digest == pq.payload_digest_for(proposal)


def test_the_version_binds_the_direction(root: Path) -> None:
    proposal = _contradiction(root)

    forward = _must(_preview(root, proposal, Action.SUPERSEDE)).version
    back = _must(_preview(root, proposal, Action.SUPERSEDE_REVERSED)).version
    symmetric = _must(_preview(root, proposal, Action.RECONCILE)).version

    assert (
        len({forward.content_digest, back.content_digest, symmetric.content_digest})
        == 3
    )


def test_a_decline_preview_names_the_pair_and_writes_nothing(root: Path) -> None:
    proposal = _contradiction(root, merged_absorbed_id="concepts/gone")
    before = tree(root)

    preview = _must(_preview(root, proposal, Action.DECLINE))

    assert tree(root) == before
    assert preview.preview == curate_decisions.DeclineDecisionPreview(
        proposal.decision_key, (_A, _A), "concepts/gone"
    )


def test_each_preview_refuses_the_others_actions(root: Path) -> None:
    proposal = _contradiction(root)

    assert curate_decisions.preview_decision(
        root, _layout(root), proposal.decision_key, Action.DECLINE
    ) == NotApplied(Action.DECLINE, "wrong_preview")
    assert _preview(root, proposal, Action.SKIP) == NotApplied(Action.SKIP, "skipped")
    assert _preview(root, proposal, Action.MERGE) == NotApplied(
        Action.MERGE, "not_offered"
    )


def test_preview_finding_refuses_a_decision_action(root: Path) -> None:
    write_concept(root, "concepts/c", title="C")
    relation = _enqueue(
        root,
        pq.Proposal(
            kind="relation_type",
            key_body=pq.relation_type_key(_A, "concepts/c"),
            producer="suggest-relations/1",
            payload=json.dumps(
                {
                    "source_id": _A,
                    "target_id": "concepts/c",
                    "effective_source_id": _A,
                    "effective_target_id": "concepts/c",
                    "suggested_type": "related_to",
                    "rationale": "r",
                }
            ),
            targets=(_A, "concepts/c"),
            input_digests=(
                pq.InputDigest(_A, _digest(root, _A)),
                pq.InputDigest("concepts/c", _digest(root, "concepts/c")),
            ),
        ),
    )

    assert _preview(root, relation, Action.RELATE) == NotApplied(
        Action.RELATE, "wrong_preview"
    )


def test_a_pair_reconciled_differently_is_refused_at_preview(root: Path) -> None:
    proposal = _contradiction(root)
    first = _must(_preview(root, proposal, Action.SUPERSEDE))
    assert isinstance(_apply(root, first.version, Action.SUPERSEDE), Applied)
    # a second row over the same pair, as a later pass could propose
    again = _revision(root, dates=(None, None))
    before = tree(root)

    assert _preview(root, again, Action.RECONCILE) == NotApplied(
        Action.RECONCILE, "already_reconciled"
    )
    assert tree(root) == before


def test_a_missing_page_is_stale_at_preview_and_member_missing_at_apply(
    root: Path,
) -> None:
    proposal = _contradiction(root, merged_absorbed_id="concepts/gone")
    version = _must(_preview(root, proposal, Action.DECLINE)).version
    (root / "bundle" / f"{_A}.md").unlink()
    before = tree(root)

    assert _preview(root, proposal, Action.DECLINE) == Stale(
        proposal.decision_key, "row_inputs_changed"
    )
    assert _apply(root, version, Action.DECLINE) == NotApplied(
        Action.DECLINE, "member_missing"
    )
    assert tree(root) == before


# -- applies ------------------------------------------------------------------- #


def test_supersede_deprecates_the_second_page_and_applies_the_row(root: Path) -> None:
    proposal = _contradiction(root)
    commits = CommitRecorder()
    version = _must(_preview(root, proposal, Action.SUPERSEDE)).version

    outcome = _apply(root, version, Action.SUPERSEDE, commits)

    assert outcome == Applied(proposal.decision_key, Action.SUPERSEDE, "abc1234")
    assert _relations(root, _A) == [okf.Relation(target=_B, type="supersedes")]
    assert _status(root, _B) == "deprecated"
    [(_root, paths, message)] = commits.calls
    assert paths == [f"bundle/{_A}.md", f"bundle/{_B}.md", "bundle/log.md"]
    assert message == f"openkos: reconcile {_A} supersedes {_B}"
    row = _row(root, proposal)
    assert (row.status, row.resolution) == ("applied", "as_proposed")


def test_reconcile_records_both_pages_as_reconciled(root: Path) -> None:
    proposal = _contradiction(root)
    version = _must(_preview(root, proposal, Action.RECONCILE)).version

    assert isinstance(_apply(root, version, Action.RECONCILE), Applied)

    assert _relations(root, _A) == [okf.Relation(target=_B, type="reconciled_with")]
    assert _relations(root, _B) == [okf.Relation(target=_A, type="reconciled_with")]


def test_a_version_for_one_direction_does_not_apply_as_the_other(root: Path) -> None:
    proposal = _contradiction(root)
    version = _must(_preview(root, proposal, Action.SUPERSEDE)).version
    before = tree(root)

    outcome = _apply(root, version, Action.SUPERSEDE_REVERSED)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert tree(root) == before


def test_an_edit_after_the_preview_is_stale(root: Path) -> None:
    proposal = _contradiction(root)
    version = _must(_preview(root, proposal, Action.RECONCILE)).version
    write_concept(root, _A, title="Alpha", body="Alpha says April.")
    before = tree(root)

    outcome = _apply(root, version, Action.RECONCILE)

    assert isinstance(outcome, Stale)
    assert tree(root) == before


def test_a_row_resolved_after_the_preview_is_stale_inside_the_section(
    root: Path,
) -> None:
    proposal = _contradiction(root)
    version = _must(_preview(root, proposal, Action.SUPERSEDE)).version
    entered: list[str] = []

    @contextlib.contextmanager
    def section() -> Iterator[None]:
        entered.append("section")
        # another writer settles the row while this apply waited for the lock
        conn = derived.open_derived_connection(_layout(root).findings_db_path)
        try:
            pq.resolve_open_by_key(
                conn,
                proposal.decision_key,
                resolution=lambda _item: "declined",
                resolved_by="decline",
                commit_section=contextlib.nullcontext,
            )
        finally:
            conn.close()
        yield

    before = tree(root)
    outcome = curate_decisions.apply_decision(
        root,
        _layout(root),
        version,
        Action.SUPERSEDE,
        policy=curate_decisions.DecisionPolicy(now=_NOW),
        ports=DecisionPorts(autocommit=CommitRecorder(), commit_section=section),
        observer=DecisionObserver(),
        surface="api",
    )

    assert entered == ["section"]
    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert tree(root) == before


def test_decline_records_the_decision_commits_its_sidecar_and_declines_the_row(
    root: Path,
) -> None:
    proposal = _contradiction(root, merged_absorbed_id="concepts/gone")
    commits = CommitRecorder()
    version = _must(_preview(root, proposal, Action.DECLINE)).version

    outcome = _apply(root, version, Action.DECLINE, commits)

    assert outcome == Applied(proposal.decision_key, Action.DECLINE, "abc1234")
    [record] = bundle_decisions.read_decisions(_A, _layout(root).bundle_dir)
    assert (record.pair_ids, record.merged_absorbed_id, record.state) == (
        (_A, _A),
        "concepts/gone",
        "declined",
    )
    [(_root, paths, message)] = commits.calls
    [path] = paths
    assert path.startswith("bundle/.state/decisions/")
    assert message == f"openkos: decline contradiction {_A}/{_A}"
    row = _row(root, proposal)
    assert (row.status, row.resolution) == ("declined", "declined")


def test_an_undated_revision_applied_reversed_is_as_proposed(root: Path) -> None:
    proposal = _revision(root, dates=(None, None))
    version = _must(_preview(root, proposal, Action.REVISE_REVERSED)).version

    assert isinstance(_apply(root, version, Action.REVISE_REVERSED), Applied)

    assert _relations(root, _B) == [okf.Relation(target=_A, type="revises")]
    assert _status(root, _A) is None  # a refinement hides nothing
    row = _row(root, proposal)
    assert (row.status, row.resolution) == ("applied", "as_proposed")


def test_reconcile_on_a_revision_applies_the_row_modified(root: Path) -> None:
    proposal = _revision(root)
    version = _must(_preview(root, proposal, Action.RECONCILE)).version

    assert isinstance(_apply(root, version, Action.RECONCILE), Applied)

    row = _row(root, proposal)
    assert (row.status, row.resolution) == ("applied", "modified")
