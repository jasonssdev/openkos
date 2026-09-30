"""Direct tests for `openkos.application.unmerge_service` (issue #1168): the
`unmerge` use cases called WITHOUT the CLI -- an explicit workspace root,
injected effects, typed outcomes and typed refusals.

`tests/unit/cli/test_unmerge.py` and `test_merge_reconcile_characterization.py`
stay the black-box contract for what the verb prints and exits with; this
file pins what only a non-CLI caller can see.
"""

from pathlib import Path

import pytest

from openkos.application import merge_service
from openkos.application import unmerge_service as svc
from openkos.application.lifecycle import PreparedMerge
from openkos.application.write_gate import (
    ConfirmationAnswer,
    ConfirmationDeclined,
    ConfirmationUnavailable,
    DriftDetected,
    Refused,
)
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_SURVIVOR = "concepts/survivor"
_ABSORBED = "concepts/absorbed"
_THIRD = "concepts/third"


class _Recorder(svc.UnmergeObserver):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.plans: list[svc.UnwindPlan] = []

    def proposed(self, preview: svc.UnmergePreview) -> None:
        self.events.append(f"proposed:{preview.absorbed_canonical}")

    def restored(self, summary: svc.UnmergeSummary) -> None:
        self.events.append(f"restored:{summary.absorbed_canonical}")

    def unwind_planned(self, plan: svc.UnwindPlan) -> None:
        self.plans.append(plan)
        self.events.append("planned")

    def step_starting(self, step_number: int, total: int, absorbed_id: str) -> None:
        self.events.append(f"step:{step_number}/{total}:{absorbed_id}")


def _merge(root: Path, absorbed: str) -> None:
    def _keep(root: Path, prepared: PreparedMerge, policy: object) -> PreparedMerge:
        return prepared

    merge_service.merge_concepts(
        root,
        _SURVIVOR,
        absorbed,
        merge_service.MergePolicy(auto=True),
        ports=merge_service.MergePorts(
            autocommit=CommitRecorder(),
            has_reset_point=lambda root: True,
            apply_reconciliation=_keep,
        ),
    )


@pytest.fixture
def merged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _ABSORBED, title="Absorbed", body="Absorbed body.")
    _merge(root, _ABSORBED)
    return root


@pytest.fixture
def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _ABSORBED, title="Absorbed", body="Absorbed body.")
    write_concept(root, _THIRD, title="Third", body="Third body.")
    _merge(root, _ABSORBED)
    _merge(root, _THIRD)
    return root


def _unmerge(
    root: Path,
    *,
    commits: CommitRecorder | None = None,
    policy: svc.UnmergePolicy | None = None,
    confirm: "object | None" = None,
    observer: svc.UnmergeObserver | None = None,
    absorbed_id: str = _ABSORBED,
) -> svc.UnmergeOutcome:
    return svc.unmerge_concept(
        root,
        _SURVIVOR,
        absorbed_id,
        policy if policy is not None else svc.UnmergePolicy(auto=True),
        ports=svc.UnmergePorts(
            autocommit=commits if commits is not None else CommitRecorder()
        ),
        observer=observer,
        confirm=confirm,  # type: ignore[arg-type]
    )


def test_unmerge_restores_the_pair_against_an_explicit_root_without_printing(
    merged: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observer = _Recorder()
    commits = CommitRecorder()

    outcome = _unmerge(merged, commits=commits, observer=observer)

    assert outcome == svc.UnmergeOutcome(
        survivor_canonical=_SURVIVOR, absorbed_canonical=_ABSORBED
    )
    assert (merged / "bundle" / f"{_ABSORBED}.md").is_file()
    assert "Absorbed body." not in (merged / "bundle" / f"{_SURVIVOR}.md").read_text(
        encoding="utf-8"
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert observer.events == [f"proposed:{_ABSORBED}", f"restored:{_ABSORBED}"]
    [(root, paths, message)] = commits.calls
    assert root == merged
    assert message == f"openkos: unmerge {_ABSORBED}"
    assert f"bundle/{_ABSORBED}.md" in paths


def test_outside_a_workspace_the_refusal_carries_the_exact_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(Refused) as caught:
        _unmerge(tmp_path)

    assert caught.value.message == (
        "openkos unmerge: refusing to unmerge -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first)."
    )


def test_an_absorbed_survivor_names_its_absorber(merged: Path) -> None:
    with pytest.raises(Refused) as caught:
        svc.unmerge_concept(
            merged,
            _ABSORBED,
            _SURVIVOR,
            svc.UnmergePolicy(auto=True),
            ports=svc.UnmergePorts(autocommit=CommitRecorder()),
        )

    assert f"It was absorbed into '{_SURVIVOR}'; run `openkos unmerge" in (
        caught.value.message
    )


def test_a_required_confirmation_with_no_callback_is_unavailable(
    merged: Path,
) -> None:
    before = tree(merged)

    with pytest.raises(ConfirmationUnavailable) as caught:
        _unmerge(merged, policy=svc.UnmergePolicy(auto=False))

    assert caught.value.message == (
        "openkos unmerge: refusing to write without confirmation -- stdin is "
        "not a TTY; re-run with --auto."
    )
    assert tree(merged) == before


def test_a_declined_confirmation_writes_nothing(merged: Path) -> None:
    before = tree(merged)

    def _decline(prompt: str) -> ConfirmationAnswer:
        return "declined"

    with pytest.raises(ConfirmationDeclined):
        _unmerge(merged, policy=svc.UnmergePolicy(auto=False), confirm=_decline)

    assert tree(merged) == before


def test_a_survivor_edited_since_the_merge_is_refused_unless_discarded(
    merged: Path,
) -> None:
    survivor = merged / "bundle" / f"{_SURVIVOR}.md"
    survivor.write_text(
        survivor.read_text(encoding="utf-8") + "\nlater edit\n", encoding="utf-8"
    )
    before = tree(merged)

    with pytest.raises(Refused) as caught:
        _unmerge(merged)

    assert "does not match the bytes" in caught.value.message
    assert tree(merged) == before

    outcome = _unmerge(
        merged, policy=svc.UnmergePolicy(auto=True, discard_survivor_edits=True)
    )
    assert outcome.absorbed_canonical == _ABSORBED


def test_an_edit_during_the_confirmation_is_drift_with_the_copy_your_edit_remedy(
    merged: Path,
) -> None:
    index = merged / "bundle" / "index.md"

    def _edit_then_proceed(prompt: str) -> ConfirmationAnswer:
        index.write_text(
            index.read_text(encoding="utf-8") + "\nlate edit\n", encoding="utf-8"
        )
        return "proceed"

    commits = CommitRecorder()

    with pytest.raises(DriftDetected) as caught:
        _unmerge(
            merged,
            commits=commits,
            policy=svc.UnmergePolicy(auto=False),
            confirm=_edit_then_proceed,
        )

    assert "Copy your edit somewhere safe before re-running" in caught.value.message
    assert not (merged / "bundle" / f"{_ABSORBED}.md").exists()
    assert commits.calls == []


def test_a_write_failure_is_a_refusal_not_a_traceback(
    merged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import lifecycle as application_lifecycle

    def _boom(layout: object, prepared: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(application_lifecycle, "unmerge_core", _boom)

    with pytest.raises(Refused) as caught:
        _unmerge(merged)

    assert caught.value.message == (
        "openkos unmerge: failed while writing the unmerge -- disk full."
    )


def test_unwind_restores_every_entry_newest_first_with_one_plan(
    chain: Path,
) -> None:
    observer = _Recorder()
    commits = CommitRecorder()

    outcome = svc.unwind_merges(
        chain,
        _SURVIVOR,
        _ABSORBED,
        svc.UnmergePolicy(auto=True),
        ports=svc.UnmergePorts(autocommit=commits),
        observer=observer,
    )

    assert outcome == svc.UnwindOutcome(
        survivor_canonical=_SURVIVOR, absorbed_ids=(_THIRD, _ABSORBED)
    )
    [plan] = observer.plans
    assert [step.absorbed_id for step in plan.steps] == [_THIRD, _ABSORBED]
    assert observer.events == [
        "planned",
        f"step:1/2:{_THIRD}",
        f"proposed:{_THIRD}",
        f"restored:{_THIRD}",
        f"step:2/2:{_ABSORBED}",
        f"proposed:{_ABSORBED}",
        f"restored:{_ABSORBED}",
    ]
    assert [message for _, _, message in commits.calls] == [
        f"openkos: unmerge {_THIRD}",
        f"openkos: unmerge {_ABSORBED}",
    ]
    assert (chain / "bundle" / f"{_THIRD}.md").is_file()
    assert (chain / "bundle" / f"{_ABSORBED}.md").is_file()


def test_unwind_asks_once_for_the_whole_plan(chain: Path) -> None:
    asked: list[str] = []

    def _yes(prompt: str) -> ConfirmationAnswer:
        asked.append(prompt)
        return "proceed"

    svc.unwind_merges(
        chain,
        _SURVIVOR,
        _ABSORBED,
        svc.UnmergePolicy(auto=False),
        ports=svc.UnmergePorts(autocommit=CommitRecorder()),
        confirm=_yes,
    )

    assert asked == ["Proceed with these changes?"]


def test_unwind_with_no_callback_is_unavailable_before_any_step(
    chain: Path,
) -> None:
    before = tree(chain)
    observer = _Recorder()

    with pytest.raises(ConfirmationUnavailable):
        svc.unwind_merges(
            chain,
            _SURVIVOR,
            _ABSORBED,
            svc.UnmergePolicy(auto=False),
            ports=svc.UnmergePorts(autocommit=CommitRecorder()),
            observer=observer,
        )

    assert tree(chain) == before
    assert observer.events == ["planned"]


def test_unwind_to_an_unknown_entry_is_refused_with_no_write(chain: Path) -> None:
    before = tree(chain)

    with pytest.raises(Refused) as caught:
        svc.unwind_merges(
            chain,
            _SURVIVOR,
            "concepts/ghost",
            svc.UnmergePolicy(auto=True),
            ports=svc.UnmergePorts(autocommit=CommitRecorder()),
        )

    assert "was never merged into" in caught.value.message
    assert tree(chain) == before


def test_a_failing_step_stops_the_unwind_and_carries_its_own_refusal(
    chain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import lifecycle as application_lifecycle

    real = application_lifecycle.unmerge_core
    calls = {"n": 0}

    def _second_fails(layout: object, prepared: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated")
        return real(layout, prepared)  # type: ignore[arg-type]

    monkeypatch.setattr(application_lifecycle, "unmerge_core", _second_fails)
    commits = CommitRecorder()

    with pytest.raises(svc.UnwindStopped) as caught:
        svc.unwind_merges(
            chain,
            _SURVIVOR,
            _ABSORBED,
            svc.UnmergePolicy(auto=True),
            ports=svc.UnmergePorts(autocommit=commits),
        )

    stopped = caught.value
    assert isinstance(stopped.cause, Refused)
    assert stopped.cause.message == (
        "openkos unmerge: failed while writing the unmerge -- simulated."
    )
    assert (stopped.step_number, stopped.total, stopped.absorbed_id) == (
        2,
        2,
        _ABSORBED,
    )
    assert stopped.message == (
        "openkos unmerge: --to unwind stopped at step 2 of 2 (restore "
        "'concepts/absorbed') -- steps 1..1 completed and left a consistent "
        "bundle (git-recoverable); completed steps are not rolled back."
    )
    assert len(commits.calls) == 1, "the completed first step stays committed"


def test_a_drifting_step_keeps_its_drift_type_through_the_chain(
    chain: Path,
) -> None:
    index = chain / "bundle" / "index.md"

    def _edit_on_first_step(observer_calls: list[str]) -> svc.UnmergeObserver:
        class _Edit(svc.UnmergeObserver):
            def proposed(self, preview: svc.UnmergePreview) -> None:
                if not observer_calls:
                    observer_calls.append("edited")
                    index.write_text(
                        index.read_text(encoding="utf-8") + "\nlate\n",
                        encoding="utf-8",
                    )

        return _Edit()

    with pytest.raises(svc.UnwindStopped) as caught:
        svc.unwind_merges(
            chain,
            _SURVIVOR,
            _ABSORBED,
            svc.UnmergePolicy(auto=True),
            ports=svc.UnmergePorts(autocommit=CommitRecorder()),
            observer=_edit_on_first_step([]),
        )

    assert isinstance(caught.value.cause, DriftDetected)
    assert "no earlier steps had completed" in caught.value.message
