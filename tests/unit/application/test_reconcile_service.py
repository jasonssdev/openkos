"""Direct tests for `openkos.application.reconcile_service` (issue #1168): the
`reconcile` use case called WITHOUT the CLI -- an explicit workspace root,
injected effects, typed outcomes and typed refusals.

`tests/unit/cli/test_reconcile.py` and `test_merge_reconcile_characterization.py`
stay the black-box contract for what the verb prints and exits with; this
file pins what only a non-CLI caller can see, plus the pure pair machinery
that moved here with the transaction.
"""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import reconcile_service as svc
from openkos.application.write_gate import (
    ConfirmationAnswer,
    ConfirmationDeclined,
    ConfirmationUnavailable,
    DriftDetected,
    Refused,
)
from openkos.model import okf
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_A = "concepts/alpha"
_B = "concepts/beta"


class _Recorder(svc.ReconcileObserver):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.previews: list[svc.ReconcilePreview] = []

    def proposed(self, preview: svc.ReconcilePreview) -> None:
        self.previews.append(preview)
        self.events.append("proposed")

    def written(self, written: svc.ReconcileWritten) -> None:
        self.events.append("written")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _A, title="Alpha", body="Alpha claims X.")
    write_concept(root, _B, title="Beta", body="Beta claims Y.")
    return root


def _reconcile(
    root: Path,
    *,
    commits: CommitRecorder | None = None,
    observer: svc.ReconcileObserver | None = None,
    confirm: "object | None" = None,
    auto: bool = True,
    id_a: str | None = _A,
    id_b: str | None = _B,
    winner: str | None = None,
    revision: str | None = None,
) -> svc.ReconcileOutcome:
    return svc.reconcile_concepts(
        root,
        id_a,
        id_b,
        winner=winner,
        revision=revision,
        auto=auto,
        ports=svc.ReconcilePorts(
            autocommit=commits if commits is not None else CommitRecorder()
        ),
        observer=observer,
        confirm=confirm,  # type: ignore[arg-type]
    )


def _relations(root: Path, concept_id: str) -> list[okf.Relation]:
    text = (root / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return okf.decode_relations(metadata)


def test_a_symmetric_reconcile_against_an_explicit_root_prints_nothing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observer = _Recorder()
    commits = CommitRecorder()

    outcome = _reconcile(workspace, commits=commits, observer=observer)

    assert outcome == svc.ReconcileOutcome(changed=True)
    assert _relations(workspace, _A) == [
        okf.Relation(target=_B, type="reconciled_with")
    ]
    assert _relations(workspace, _B) == [
        okf.Relation(target=_A, type="reconciled_with")
    ]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert observer.events == ["proposed", "written"]
    [(root, paths, message)] = commits.calls
    assert root == workspace
    assert message == f"openkos: reconcile {_A} <-> {_B}"
    assert paths == [f"bundle/{_A}.md", f"bundle/{_B}.md", "bundle/log.md"]


def test_a_winner_records_one_directed_supersedes_edge(workspace: Path) -> None:
    commits = CommitRecorder()

    _reconcile(workspace, commits=commits, winner=_B)

    assert _relations(workspace, _A) == []
    assert _relations(workspace, _B) == [okf.Relation(target=_A, type="supersedes")]
    assert commits.calls[0][2] == f"openkos: reconcile {_B} supersedes {_A}"


def test_a_revision_records_one_directed_revises_edge(workspace: Path) -> None:
    commits = CommitRecorder()

    _reconcile(workspace, commits=commits, revision=_A)

    assert _relations(workspace, _A) == [okf.Relation(target=_B, type="revises")]
    assert _relations(workspace, _B) == []
    assert commits.calls[0][2] == f"openkos: reconcile {_A} revises {_B}"


def test_an_idempotent_rerun_is_reported_as_unchanged(workspace: Path) -> None:
    _reconcile(workspace)
    before = {
        rel: data for rel, data in tree(workspace).items() if rel != "bundle/log.md"
    }

    outcome = _reconcile(workspace)

    assert outcome == svc.ReconcileOutcome(changed=False)
    assert {
        rel: data for rel, data in tree(workspace).items() if rel != "bundle/log.md"
    } == before


def test_a_pair_already_resolved_differently_is_refused_with_its_text(
    workspace: Path,
) -> None:
    _reconcile(workspace)
    before = tree(workspace)

    with pytest.raises(Refused) as caught:
        _reconcile(workspace, winner=_A)

    assert caught.value.message == (
        "openkos reconcile: failed while preparing the reconcile -- concepts "
        f"{_A!r} and {_B!r} are already reconciled as a symmetric "
        "reconciliation ('reconciled_with'); reconcile will not overwrite an "
        "existing resolution. To change it, edit the concepts manually or "
        "revert with git, then re-run."
    )
    assert tree(workspace) == before


def test_outside_a_workspace_the_refusal_carries_the_exact_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(Refused) as caught:
        _reconcile(tmp_path)

    assert caught.value.message == (
        "openkos reconcile: refusing to reconcile -- no OpenKOS workspace "
        "found in this directory (run 'openkos init' first)."
    )


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"id_b": None}, "two concept ids are required"),
        ({"winner": _A, "revision": _A}, "--winner and --revision are mutually"),
        ({"id_b": _A}, "id_a and id_b must be distinct"),
        ({"winner": "concepts/gamma"}, "concepts/gamma"),
    ],
)
def test_request_shape_refusals_write_nothing(
    workspace: Path, kwargs: dict[str, str | None], fragment: str
) -> None:
    write_concept(workspace, "concepts/gamma", title="Gamma")
    before = tree(workspace)

    with pytest.raises(Refused) as caught:
        _reconcile(workspace, **kwargs)  # type: ignore[arg-type]

    assert fragment in caught.value.message
    assert caught.value.message.startswith(
        "openkos reconcile: refusing to reconcile -- "
    )
    assert tree(workspace) == before


def test_from_findings_takes_no_ids_no_winner_no_revision_no_auto() -> None:
    for kwargs in (
        {"id_a": _A},
        {"id_b": _B},
        {"winner": _A},
        {"revision": _A},
        {"auto": True},
    ):
        base: dict[str, object] = {
            "id_a": None,
            "id_b": None,
            "winner": None,
            "revision": None,
            "auto": False,
        }
        base.update(kwargs)
        with pytest.raises(Refused) as caught:
            svc.validate_request(from_findings=True, **base)  # type: ignore[arg-type]
        assert "--from-findings takes no concept ids" in caught.value.message

    svc.validate_request(
        id_a=None,
        id_b=None,
        winner=None,
        revision=None,
        from_findings=True,
        auto=False,
    )


def test_a_required_confirmation_with_no_callback_is_unavailable(
    workspace: Path,
) -> None:
    before = tree(workspace)

    with pytest.raises(ConfirmationUnavailable) as caught:
        _reconcile(workspace, auto=False)

    assert caught.value.message == svc.NOT_A_TTY_REFUSAL
    assert tree(workspace) == before


def test_a_declined_confirmation_writes_nothing(workspace: Path) -> None:
    before = tree(workspace)
    asked: list[str] = []

    def _decline(prompt: str) -> ConfirmationAnswer:
        asked.append(prompt)
        return "declined"

    with pytest.raises(ConfirmationDeclined):
        _reconcile(workspace, auto=False, confirm=_decline)

    assert asked == [svc.CONFIRM_PROMPT]
    assert tree(workspace) == before


def test_an_edit_during_the_confirmation_is_drift_and_writes_nothing(
    workspace: Path,
) -> None:
    beta = workspace / "bundle" / f"{_B}.md"

    def _edit_then_proceed(prompt: str) -> ConfirmationAnswer:
        beta.write_text(beta.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
        return "proceed"

    commits = CommitRecorder()

    with pytest.raises(DriftDetected) as caught:
        _reconcile(workspace, auto=False, confirm=_edit_then_proceed, commits=commits)

    assert "write target(s) changed on disk" in caught.value.message
    assert _relations(workspace, _A) == []
    assert commits.calls == []


def test_announce_preview_false_suppresses_only_the_proposal(
    workspace: Path,
) -> None:
    observer = _Recorder()
    layout = config.WorkspaceLayout(workspace)
    pair = svc.resolve_pair(layout, _A, _B)

    outcome = svc.reconcile_pair(
        workspace,
        config.read_config(workspace),
        pair,
        auto=True,
        announce_preview=False,
        ports=svc.ReconcilePorts(autocommit=CommitRecorder()),
        observer=observer,
    )

    assert outcome.changed is True
    assert observer.events == ["written"]


def test_the_preview_carries_the_status_export_for_the_superseded_side(
    workspace: Path,
) -> None:
    observer = _Recorder()

    _reconcile(workspace, winner=_A, observer=observer)

    [preview] = observer.previews
    assert preview.status_outcome is okf.ExportOutcome.EXPORT
    assert preview.target_is_a is False, "beta is the superseded side"
    assert (preview.edge_added_a, preview.edge_added_b) == (True, False)
    assert (preview.note_added_a, preview.note_added_b) == (True, True)


def test_resolve_pair_member_names_the_pair_on_a_stranger(workspace: Path) -> None:
    write_concept(workspace, "concepts/gamma", title="Gamma")
    layout = config.WorkspaceLayout(workspace)

    assert svc.resolve_pair_member(layout, "--winner", _A, _A, _B) == (_A, _B)
    assert svc.resolve_pair_member(layout, "--winner", _B, _A, _B) == (_B, _A)
    with pytest.raises(ValueError, match="must resolve to one of the pair"):
        svc.resolve_pair_member(layout, "--revision", "concepts/gamma", _A, _B)
