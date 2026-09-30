"""Direct tests for `openkos.application.merge_service` (issue #1168): the
`merge` use case called WITHOUT the CLI -- an explicit workspace root,
injected effects, typed outcomes and typed refusals.

`tests/unit/cli/test_merge.py` and `test_merge_reconcile_characterization.py`
stay the black-box contract for what the verb prints and exits with; this
file pins what only a non-CLI caller can see: that the service reads no
current directory, prompts nothing, prints nothing, raises no `typer.Exit`,
and hands its results back as values.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import merge_service as svc
from openkos.application.lifecycle import PreparedMerge
from openkos.application.write_gate import (
    ConfirmationAnswer,
    ConfirmationDeclined,
    ConfirmationUnavailable,
    DriftDetected,
    Refused,
)
from openkos.bundle import ledger as bundle_ledger
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_SURVIVOR = "concepts/survivor"
_ABSORBED = "concepts/absorbed"


class _Recorder(svc.MergeObserver):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.previews: list[svc.MergePreview] = []
        self.summaries: list[svc.MergeSummary] = []
        self.shas: list[str] = []

    def proposed(self, preview: svc.MergePreview) -> None:
        self.previews.append(preview)
        self.events.append("proposed")

    def merged(self, summary: svc.MergeSummary) -> None:
        self.summaries.append(summary)
        self.events.append("merged")

    def committed(self, sha: str) -> None:
        self.shas.append(sha)
        self.events.append("committed")


def _ports(
    commits: CommitRecorder,
    events: list[str],
    *,
    reset_point: bool = True,
    reset_probes: list[Path] | None = None,
) -> svc.MergePorts:
    def _has_reset_point(root: Path) -> bool:
        if reset_probes is not None:
            reset_probes.append(root)
        return reset_point

    def _reconcile(
        root: Path, prepared: PreparedMerge, policy: svc.MergePolicy
    ) -> PreparedMerge:
        events.append("reconcile")
        return prepared

    return svc.MergePorts(
        autocommit=commits,
        has_reset_point=_has_reset_point,
        apply_reconciliation=_reconcile,
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor", body="Survivor body.")
    write_concept(root, _ABSORBED, title="Absorbed", body="Absorbed body.")
    return root


def _merge(
    root: Path,
    *,
    events: list[str],
    commits: CommitRecorder | None = None,
    policy: svc.MergePolicy | None = None,
    confirm: "object | None" = None,
    survivor_id: str = _SURVIVOR,
    absorbed_id: str = _ABSORBED,
    observer: svc.MergeObserver | None = None,
) -> svc.MergeOutcome:
    return svc.merge_concepts(
        root,
        survivor_id,
        absorbed_id,
        policy if policy is not None else svc.MergePolicy(auto=True),
        ports=_ports(commits if commits is not None else CommitRecorder(), events),
        observer=observer,
        confirm=confirm,  # type: ignore[arg-type]
    )


def test_merges_against_an_explicit_root_without_printing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    commits = CommitRecorder("deadbee")
    observer = _Recorder(events)

    outcome = _merge(workspace, events=events, commits=commits, observer=observer)

    assert outcome == svc.MergeOutcome(
        survivor_canonical=_SURVIVOR, absorbed_canonical=_ABSORBED, commit_sha="deadbee"
    )
    assert not (workspace / "bundle" / f"{_ABSORBED}.md").exists()
    assert "Absorbed body." in (workspace / "bundle" / f"{_SURVIVOR}.md").read_text(
        encoding="utf-8"
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert events == ["proposed", "reconcile", "merged", "committed"]
    assert observer.shas == ["deadbee"]
    assert observer.summaries == [
        svc.MergeSummary(
            survivor_canonical=_SURVIVOR,
            absorbed_canonical=_ABSORBED,
            index_name="index.md",
            log_name="log.md",
        )
    ]


def test_the_commit_holds_exactly_the_written_paths_and_names_the_merge(
    workspace: Path,
) -> None:
    commits = CommitRecorder()

    _merge(workspace, events=[], commits=commits)

    [(root, paths, message)] = commits.calls
    assert root == workspace
    assert message == f"openkos: merge {_ABSORBED} into {_SURVIVOR}"
    assert paths[:2] == ["bundle/index.md", "bundle/log.md"]
    assert f"bundle/{_SURVIVOR}.md" in paths
    assert f"bundle/{_ABSORBED}.md" in paths
    assert any(".state/" in path for path in paths), "the ledger sidecar is committed"


def test_a_degraded_commit_is_not_announced(workspace: Path) -> None:
    events: list[str] = []

    outcome = _merge(
        workspace,
        events=events,
        commits=CommitRecorder(None),
        observer=_Recorder(events),
    )

    assert outcome.commit_sha is None
    assert "committed" not in events


def test_the_preview_carries_the_plan_as_data(workspace: Path) -> None:
    observer = _Recorder([])

    _merge(workspace, events=[], observer=observer)

    [preview] = observer.previews
    assert preview.prepared.survivor_canonical == _SURVIVOR
    assert preview.prepared.absorbed_canonical == _ABSORBED
    assert preview.reconcile_planned is False
    assert preview.cross_source_same_pair is False
    assert preview.cross_type_concern is None
    assert (preview.index_name, preview.log_name) == ("index.md", "log.md")


def test_outside_a_workspace_the_refusal_carries_the_exact_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(Refused) as caught:
        _merge(tmp_path, events=[])

    assert caught.value.message == (
        "openkos merge: refusing to merge -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first)."
    )


def test_the_same_id_twice_is_refused_with_its_text(workspace: Path) -> None:
    before = tree(workspace)

    with pytest.raises(Refused) as caught:
        _merge(workspace, events=[], absorbed_id=_SURVIVOR)

    assert caught.value.message == (
        "openkos merge: refusing to merge -- survivor and absorbed concept-ids "
        f"must be distinct, both resolved to {_SURVIVOR!r}."
    )
    assert tree(workspace) == before


def test_an_unknown_id_is_refused_before_anything_is_written(
    workspace: Path,
) -> None:
    before = tree(workspace)

    with pytest.raises(Refused) as caught:
        _merge(workspace, events=[], absorbed_id="concepts/ghost")

    assert "concepts/ghost" in caught.value.message
    assert tree(workspace) == before


def test_a_required_confirmation_with_no_callback_is_unavailable_not_a_yes(
    workspace: Path,
) -> None:
    before = tree(workspace)
    events: list[str] = []

    with pytest.raises(ConfirmationUnavailable) as caught:
        _merge(workspace, events=events, policy=svc.MergePolicy(auto=False))

    assert caught.value.message == (
        "openkos merge: refusing to write without confirmation -- stdin is "
        "not a TTY; re-run with --auto."
    )
    assert tree(workspace) == before
    assert "reconcile" not in events, "the model call comes only after consent"


def test_a_declined_confirmation_writes_nothing(workspace: Path) -> None:
    before = tree(workspace)
    asked: list[str] = []

    def _decline(prompt: str) -> ConfirmationAnswer:
        asked.append(prompt)
        return "declined"

    with pytest.raises(ConfirmationDeclined):
        _merge(
            workspace,
            events=[],
            policy=svc.MergePolicy(auto=False),
            confirm=_decline,
        )

    assert asked == ["Proceed with these changes?"]
    assert tree(workspace) == before


def test_review_false_skips_the_question_like_auto(workspace: Path) -> None:
    cfg_path = workspace / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace("review: true", "review: false"),
        encoding="utf-8",
    )
    assert config.read_config(workspace).review is False

    outcome = _merge(workspace, events=[], policy=svc.MergePolicy(auto=False))

    assert outcome.survivor_canonical == _SURVIVOR


def test_an_edit_during_the_confirmation_is_drift_and_writes_nothing(
    workspace: Path,
) -> None:
    absorbed = workspace / "bundle" / f"{_ABSORBED}.md"

    def _edit_then_proceed(prompt: str) -> ConfirmationAnswer:
        absorbed.write_text(
            absorbed.read_text(encoding="utf-8") + "\nlate edit\n", encoding="utf-8"
        )
        return "proceed"

    before_survivor = (workspace / "bundle" / f"{_SURVIVOR}.md").read_bytes()
    commits = CommitRecorder()

    with pytest.raises(DriftDetected) as caught:
        _merge(
            workspace,
            events=[],
            commits=commits,
            policy=svc.MergePolicy(auto=False),
            confirm=_edit_then_proceed,
        )

    assert "delete target(s) changed on disk" in caught.value.message
    assert (workspace / "bundle" / f"{_SURVIVOR}.md").read_bytes() == before_survivor
    assert absorbed.exists(), "the drifted absorbed file is never deleted"
    assert commits.calls == []


def test_a_torn_ledger_write_refuses_with_no_override(workspace: Path) -> None:
    from openkos.bundle import ledger as bundle_ledger

    pending = bundle_ledger.pending_path_for(_SURVIVOR, workspace / "bundle")
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("stale", encoding="utf-8")

    with pytest.raises(Refused) as caught:
        _merge(workspace, events=[], policy=svc.MergePolicy(auto=True, force=True))

    assert "torn write pending" in caught.value.message


def test_a_flagged_ledger_refuses_unless_forced_and_probes_the_reset_point_lazily(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bundle_ledger,
        "scan_nesting_violations",
        lambda bundle_dir: [(_SURVIVOR, "tampered")],
    )
    probes: list[Path] = []
    ports = _ports(CommitRecorder(), [], reset_point=False, reset_probes=probes)

    with pytest.raises(Refused) as caught:
        svc.merge_concepts(
            workspace, _SURVIVOR, _ABSORBED, svc.MergePolicy(auto=True), ports=ports
        )

    assert "no git reset point is available" in caught.value.message
    assert probes == [workspace]

    probes.clear()
    svc.merge_concepts(
        workspace,
        _SURVIVOR,
        _ABSORBED,
        svc.MergePolicy(auto=True, force=True),
        ports=ports,
    )
    assert probes == [], "--force never asks, so the VCS is never probed"


def test_commit_merge_is_the_shared_write_for_curate_and_adjudicate(
    workspace: Path,
) -> None:
    from openkos.application import lifecycle as application_lifecycle

    layout = config.WorkspaceLayout(workspace)
    prepared = application_lifecycle.prepare_merge(
        layout.bundle_dir,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        layout.bundle_dir / f"{_SURVIVOR}.md",
        layout.bundle_dir / f"{_ABSORBED}.md",
        _SURVIVOR,
        _ABSORBED,
        workspace,
        now=datetime.now(UTC),
    )
    commits = CommitRecorder("c0ffee1")

    sha = svc.commit_merge(workspace, layout, prepared, autocommit=commits)

    assert sha == "c0ffee1"
    assert not (layout.bundle_dir / f"{_ABSORBED}.md").exists()
    [(_, paths, message)] = commits.calls
    assert message == f"openkos: merge {_ABSORBED} into {_SURVIVOR}"
    assert "bundle/index.md" in paths
