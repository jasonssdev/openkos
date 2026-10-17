"""`openkos.api` over contradiction, revision and refused-file rows
(settle-other-kinds, ADR-0072): their details on the list, `preview_finding`,
and `apply_decision` committing the version it returned, with the API's own
local git commit, in a real repository whose identity is pinned."""

import dataclasses
import typing
from pathlib import Path

import pytest

import openkos.api
from openkos import config
from openkos.api import (
    Action,
    Applied,
    ContradictionDetail,
    DeclinePlan,
    FindingPreview,
    NotApplied,
    ReconcilePlan,
    RefusedFileDetail,
    RevisionDetail,
    Workspace,
    apply_decision,
    list_decisions,
    preview_decision,
    preview_finding,
)
from openkos.application import queue_producers
from openkos.state import derived
from openkos.state import pending_queue as pq
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace, write_concept

PAIR = ("concepts/a", "concepts/b")
CONTRADICTION_KEY = pq.stored_decision_key(
    "contradiction", pq.contradiction_key(PAIR, None)
)
REVISION_KEY = pq.stored_decision_key("revision", pq.revision_key(PAIR))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    ws = make_workspace(tmp_path, monkeypatch)
    write_concept(ws, "concepts/a", title="A", body="The launch is in May.")
    write_concept(ws, "concepts/b", title="B", body="The launch is in June.")
    vcs_git.commit_paths(ws, ["bundle"], "seed concepts")
    support.forbid_model_clients(monkeypatch, "a finding preview or apply")
    return ws


def _contradiction(root: Path, merged_absorbed_id: str | None = None) -> None:
    support.enqueue(
        root,
        kind="contradiction",
        key_body=pq.contradiction_key(PAIR, merged_absorbed_id),
        payload={
            "pair_ids": list(PAIR),
            "merged_absorbed_id": merged_absorbed_id,
            "confidence": 0.95,
            "rationale": "May against June.",
            "conflicting_claims": ["in May", "in June"],
        },
        targets=PAIR,
        producer="contradictions/1",
        fresh=True,
    )


def _status(root: Path, key: str) -> tuple[str, str]:
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        row = conn.execute(
            "SELECT status, resolution FROM pending_items WHERE decision_key = ?",
            (key,),
        ).fetchone()
        return row[0], row[1]
    finally:
        conn.close()


def test_a_contradiction_lists_its_detail_and_its_actions(root: Path) -> None:
    _contradiction(root)

    [decision] = list_decisions(Workspace.open(root))

    assert decision.detail is None
    assert decision.finding == ContradictionDetail(
        first_id="concepts/a",
        second_id="concepts/b",
        merged_absorbed_id=None,
        rationale="May against June.",
        conflicting_claims=("in May", "in June"),
    )
    assert decision.actions == (
        Action.DECLINE,
        Action.RECONCILE,
        Action.SUPERSEDE,
        Action.SUPERSEDE_REVERSED,
        Action.SKIP,
    )
    assert decision.fresh is True


def test_a_revision_lists_its_detail_without_a_confidence(root: Path) -> None:
    support.enqueue(
        root,
        kind="revision",
        key_body=pq.revision_key(PAIR),
        payload={
            "pair_ids": list(PAIR),
            "verdict": "refines",
            "confidence": 0.7,
            "rationale": "June narrows May.",
            "quotes": ["in May", "in June"],
            "dates": ["2026-01-01", "2026-02-01"],
            "date_states": ["dated", "dated"],
        },
        targets=PAIR,
        producer="revisions/1",
    )

    [decision] = list_decisions(Workspace.open(root))

    assert decision.finding == RevisionDetail(
        first_id="concepts/a",
        second_id="concepts/b",
        change="refines",
        later_id="concepts/b",
        rationale="June narrows May.",
        first_quote="in May",
        second_quote="in June",
        first_date="2026-01-01",
        second_date="2026-02-01",
    )
    assert "confidence" not in {f.name for f in dataclasses.fields(RevisionDetail)}
    assert Action.REVISE_REVERSED in decision.actions


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (queue_producers.REASON_SOURCE_CHANGED, "changed_after_import"),
        (queue_producers.REASON_EXCEEDS_BUDGET, "exceeds_pass_budget"),
    ],
)
def test_a_refused_file_says_why_and_offers_nothing(
    root: Path, reason: str, code: str
) -> None:
    support.enqueue(
        root,
        kind="watch_refusal",
        key_body=pq.watch_refusal_key("sources/notes"),
        payload={
            "reason": reason,
            "inbox_path": "notes.md",
            "source_id": "sources/notes",
            "remedy": "x",
        },
        targets=("sources/notes",),
        producer="watch/1",
    )

    [decision] = list_decisions(Workspace.open(root))

    assert decision.finding == RefusedFileDetail(file_name="notes.md", reason=code)  # type: ignore[arg-type]
    assert decision.actions == ()
    assert decision.fresh is None


def test_a_finding_is_previewed_then_applied_and_committed(root: Path) -> None:
    _contradiction(root)
    workspace = Workspace.open(root)

    preview = preview_finding(workspace, CONTRADICTION_KEY, Action.SUPERSEDE)

    assert isinstance(preview, FindingPreview)
    assert preview.plan == ReconcilePlan(
        first_id="concepts/a",
        second_id="concepts/b",
        relation_type="supersedes",
        holder_id="concepts/a",
        target_id="concepts/b",
        deprecates_id="concepts/b",
        pages_changed=("concepts/a", "concepts/b"),
    )
    outcome = apply_decision(workspace, preview.version, Action.SUPERSEDE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    assert _head_files(root) == [
        "bundle/concepts/a.md",
        "bundle/concepts/b.md",
        "bundle/log.md",
    ]
    assert _git(root, "log", "-1", "--format=%s") == (
        "openkos: reconcile concepts/a supersedes concepts/b"
    )
    assert _status(root, CONTRADICTION_KEY) == ("applied", "as_proposed")


def test_a_decline_commits_only_its_sidecar(root: Path) -> None:
    _contradiction(root)
    workspace = Workspace.open(root)

    preview = preview_finding(workspace, CONTRADICTION_KEY, Action.DECLINE)

    assert isinstance(preview, FindingPreview)
    assert preview.plan == DeclinePlan("concepts/a", "concepts/b", None)
    outcome = apply_decision(workspace, preview.version, Action.DECLINE)

    assert isinstance(outcome, Applied)
    [path] = _head_files(root)
    assert path.startswith("bundle/.state/decisions/")
    assert _status(root, CONTRADICTION_KEY) == ("declined", "declined")


def test_the_two_previews_point_at_each_other(root: Path) -> None:
    _contradiction(root)
    support.enqueue_identity(root, PAIR, verdict="same", fresh=True)
    workspace = Workspace.open(root)
    identity_key = pq.stored_decision_key("identity", pq.identity_key(PAIR))
    before = support.snapshot(root)

    assert preview_decision(workspace, CONTRADICTION_KEY, Action.RECONCILE) == (
        NotApplied(Action.RECONCILE, "wrong_preview")
    )
    assert preview_finding(workspace, identity_key, Action.KEEP_DISTINCT) == (
        NotApplied(Action.KEEP_DISTINCT, "wrong_preview")
    )
    assert support.snapshot(root) == before


def test_the_decision_unions_are_unchanged_and_the_version_stays_one() -> None:
    hints = typing.get_type_hints(openkos.api.DecisionPreview)
    assert typing.get_args(hints["plan"]) == (
        openkos.api.MergePlan,
        openkos.api.RelatePlan,
        openkos.api.TierPlan,
        openkos.api.KeepDistinctPlan,
    )
    detail = typing.get_type_hints(openkos.api.Decision)["detail"]
    assert typing.get_args(detail) == (
        openkos.api.IdentityDetail,
        openkos.api.RelationDetail,
        openkos.api.TierDetail,
        type(None),
    )
    assert typing.get_args(typing.get_type_hints(FindingPreview)["plan"]) == (
        ReconcilePlan,
        DeclinePlan,
    )
    assert openkos.api.API_VERSION == 1
