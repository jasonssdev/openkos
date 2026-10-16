"""`openkos.api` and a group decision (`identity-groups-decision`, ADR-0063):
`merge_group` is listed, previewed with a selection and applied against the
version the preview returned, through the public facade only.

Every test runs in a real git-backed workspace whose identity is pinned; the
commit assertions read what the commit carried.
"""

from pathlib import Path

import pytest

from openkos import api
from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    GroupSelection,
    MergeGroupInterrupted,
    MergePlan,
    MergeStep,
    NotApplied,
    Stale,
    Workspace,
    WriteFailed,
    apply_decision,
    list_decisions,
    preview_decision,
)
from openkos.application import curate_decisions
from openkos.application import lifecycle as application_lifecycle
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

A, B, C, D = (f"concepts/{n}" for n in "abcd")
THREE = (A, B, C)
KEY = curate_decisions.identity_decision_key(THREE)


def _doc(
    root: Path, concept_id: str, okf_type: str = "Concept", **extra: object
) -> None:
    write_doc(
        root,
        concept_id,
        {"type": okf_type, "title": concept_id, **extra},
        f"Body of {concept_id}.\n",
    )


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    types: dict[str, str],
    *,
    verdict: str | None = "same",
    sensitivity: dict[str, str] | None = None,
) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id, okf_type in types.items():
        extra = (
            {"sensitivity": sensitivity[concept_id]}
            if sensitivity and (concept_id in sensitivity)
            else {}
        )
        _doc(root, concept_id, okf_type, **extra)
    _doc(root, D)  # a bystander that is not part of any group
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    members = tuple(types)
    support.enqueue_identity(root, members, verdict=verdict, fresh=True)
    # After the workspace exists: `init` itself builds a diagnostics client.
    support.forbid_model_clients(monkeypatch, "a group preview or apply")
    return root


def _select(survivor: str, *marked: str) -> GroupSelection:
    return GroupSelection(survivor_id=survivor, member_ids=marked)


def _preview(root: Path, key: str, selection: GroupSelection | None) -> DecisionPreview:
    result = preview_decision(
        Workspace.open(root), key, Action.MERGE_GROUP, selection=selection
    )
    assert isinstance(result, DecisionPreview), result
    return result


# -- listing ----------------------------------------------------------------- #


def test_a_judged_same_group_lists_merge_group_and_withholds_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))

    [decision] = list_decisions(Workspace.open(root))

    assert decision.actions == (
        Action.MERGE_GROUP,
        Action.KEEP_DISTINCT,
        Action.SKIP,
    )
    assert [(w.action, w.reason) for w in decision.withheld] == [
        (Action.MERGE, "more_than_two_members")
    ]


# -- the plan ---------------------------------------------------------------- #


def test_a_group_preview_plans_a_merge_with_its_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path, monkeypatch, {A: "Person", B: "Organization", C: "Person"}
    )
    before = support.snapshot(root)

    preview = _preview(root, KEY, _select(A, A, B))

    plan = preview.plan
    assert isinstance(plan, MergePlan)
    assert plan.kind == "merge"
    assert plan.survivor_criterion == "chosen"
    assert (plan.survivor_id, plan.absorbed_id) == (A, B)
    assert plan.steps == (
        MergeStep(
            survivor_id=A,
            absorbed_id=B,
            absorbed_type="Organization",
            type_outcome="recorded",
            files_rewritten=0,
        ),
    )
    assert plan.kept_distinct_ids == (C,)
    assert plan.survivor_type == "Person"
    assert plan.cross_type_concern is not None
    assert "Organization" in plan.cross_type_concern
    assert preview.version.selection == _select(A, A, B)
    assert support.snapshot(root) == before


def test_a_group_plan_counts_the_documents_the_steps_rewrite_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))
    _doc(root, "concepts/linker")
    (root / "bundle" / "concepts" / "linker.md").write_text(
        "---\ntype: Concept\ntitle: linker\n---\n\n"
        "See [A](/concepts/a.md) and [C](/concepts/c.md).\n",
        encoding="utf-8",
    )
    vcs_git.commit_paths(root, ["bundle"], "add a linker")
    support.enqueue_identity(root, THREE, verdict="same", fresh=True)

    plan = _preview(root, KEY, _select(B, A, B, C)).plan

    assert isinstance(plan, MergePlan)
    assert [s.files_rewritten for s in plan.steps] == [1, 1]
    assert plan.files_rewritten == 1  # one document, rewritten by both steps


def test_a_pairwise_merge_plan_lists_its_one_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, {A: "Concept", B: "Concept"})
    key = curate_decisions.identity_decision_key((A, B))

    result = preview_decision(Workspace.open(root), key, Action.MERGE)

    assert isinstance(result, DecisionPreview)
    plan = result.plan
    assert isinstance(plan, MergePlan)
    [step] = plan.steps
    assert (step.survivor_id, step.absorbed_id) == (plan.survivor_id, plan.absorbed_id)
    assert plan.survivor_type == "Concept"
    assert plan.kept_distinct_ids == ()


def test_a_missing_selection_is_returned_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))

    result = preview_decision(Workspace.open(root), KEY, Action.MERGE_GROUP)

    assert result == NotApplied(Action.MERGE_GROUP, "invalid_selection")


# -- apply ------------------------------------------------------------------- #


def test_apply_merges_the_marked_members_and_makes_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))
    version = _preview(root, KEY, _select(B, A, B)).version
    count_before = _git(root, "rev-list", "--count", "HEAD")

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE_GROUP)

    assert isinstance(outcome, Applied)
    assert outcome.merged == ((B, A),)
    assert outcome.commit_sha is not None
    assert _git(root, "rev-list", "--count", "HEAD") == str(int(count_before) + 1)
    assert _git(root, "log", "-1", "--format=%s").startswith(
        "openkos: merge group into concepts/b (concepts/a); keep distinct "
    )
    files = _head_files(root)
    assert "bundle/concepts/a.md" in files
    assert "bundle/concepts/b.md" in files
    assert "bundle/concepts/c.md" not in files
    assert outcome.kept_distinct == ((B, C),)


def test_a_confidential_marked_member_sets_the_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(
        tmp_path,
        monkeypatch,
        dict.fromkeys(THREE, "Concept"),
        sensitivity={B: "confidential"},
    )
    version = _preview(root, KEY, _select(A, A, B)).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE_GROUP)

    assert isinstance(outcome, Applied)
    assert outcome.committed_confidential is True


def test_an_altered_selection_is_stale_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))
    version = _preview(root, KEY, _select(A, A, B)).version
    altered = type(version)(
        version.decision_key,
        version.payload_digest,
        version.content_digest,
        _select(A, A, B, C),
    )
    before = support.snapshot(root)

    outcome = apply_decision(Workspace.open(root), altered, Action.MERGE_GROUP)

    assert outcome == Stale(KEY, "content_changed")
    assert support.snapshot(root) == before


def test_an_interrupted_group_is_caught_as_a_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, dict.fromkeys(THREE, "Concept"))
    version = _preview(root, KEY, _select(A, A, B, C)).version
    real = application_lifecycle.merge_core
    calls: list[int] = []

    def merge_core(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application_lifecycle, "merge_core", merge_core)

    with pytest.raises(WriteFailed) as caught:
        apply_decision(Workspace.open(root), version, Action.MERGE_GROUP)

    failure = caught.value
    assert isinstance(failure, MergeGroupInterrupted)
    assert failure.completed == ((A, B),)
    assert failure.failed_absorbed_id == C
    assert failure.commit_sha is not None
    assert api.MergeGroupInterrupted is MergeGroupInterrupted


# -- judging makes the group mergeable --------------------------------------- #


def test_a_group_judged_same_through_the_api_offers_merge_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.api import test_judging_api as judging

    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    for suffix in "abc":
        write_doc(
            root,
            f"concepts/g-{suffix}",
            {"type": "Concept", "title": "Acme Corporation", "sensitivity": "private"},
            f"Member {suffix}.\n",
        )
    workspace = Workspace.open(root)
    factory = judging.Factory()
    quote = api.quote_judging(workspace, "identity", chat_client=factory)

    outcome = api.run_judging(workspace, quote.token, chat_client=factory)
    api.refresh_decision_queue(workspace)

    assert getattr(outcome, "status", None) == "completed"
    [decision] = list_decisions(workspace)
    members = tuple(f"concepts/g-{s}" for s in "abc")
    assert decision.decision_key == curate_decisions.identity_decision_key(members)
    assert decision.evaluation == "judged_same"
    assert decision.actions == (
        Action.MERGE_GROUP,
        Action.KEEP_DISTINCT,
        Action.SKIP,
    )
