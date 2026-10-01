"""The commit phase of `adjudicate` (#1137, ADR-0036; tasks.md 1.6, second
group).

The model calls (judging, and the merged-body reconciliation pass of #645),
the per-item prompts and the typed-count prompt all run without the workspace
lock. A merge's drift re-validation, write and auto-commit, and the persist of
the verdicts into `findings.db`, are commit phases.
"""

import contextlib
from pathlib import Path

import pytest

from openkos import config as okf_config
from openkos.application import merge_service
from openkos.cli.main import app
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.state import adjudications as adjudications_store
from openkos.state import derived
from tests.unit.cli.commit_phase_support import (
    hold_the_lock_from,
    init_workspace,
    lock_is_free,
    runner,
    wrap,
)
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.vcs.conftest import isolate_git_identity

_SENTINEL_BODY = "SENTINEL-CONFIDENTIAL-BODY"


@pytest.fixture(autouse=True)
def _git_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity-config"),
        name="Isolated Tester",
        email="tester@example.invalid",
    )


def _write(
    path: Path, *, title: str, body: str, sensitivity: str | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = f"sensitivity: {sensitivity}\n" if sensitivity else ""
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n{extra}---\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def _seed_same_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    body_a: str = "Alpha states the shared subject at length. " * 8,
    body_b: str = "Beta restates the same subject in a second voice. " * 8,
    on_judge: object = None,
) -> CandidateGroup:
    """Two bodied concepts adjudicated SAME. `on_judge`, when given, runs inside
    the (stubbed) judging call -- the compute phase's longest wait."""
    _write(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A", body=body_a)
    _write(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B", body=body_b)
    commit_pending_fixture_docs()
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )

    def _judge(candidates: list[CandidateGroup], **kwargs: object) -> AdjudicationBatch:
        if callable(on_judge):
            on_judge()
        return AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=candidates[0],
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _judge)
    return group


def _stored_adjudications(
    tmp_path: Path,
) -> tuple[adjudications_store.Adjudication, ...]:
    layout = okf_config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return adjudications_store.open_adjudications(conn)
    finally:
        conn.close()


def _stub_reconcile(
    monkeypatch: pytest.MonkeyPatch, at_reconcile: list[bool], root: Path
) -> None:
    def _reconcile(root_arg: Path, prepared: object) -> tuple[object, None]:
        at_reconcile.append(lock_is_free(root))
        return prepared, None

    monkeypatch.setattr("openkos.cli.main._reconcile_merged_survivor", _reconcile)


def test_apply_judges_asks_and_reconciles_without_the_lock_then_merges_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    at_judge: list[bool] = []
    at_prompt: list[bool] = []
    at_reconcile: list[bool] = []
    at_commit: list[bool] = []
    _seed_same_group(
        tmp_path, monkeypatch, on_judge=lambda: at_judge.append(lock_is_free(tmp_path))
    )
    _stub_reconcile(monkeypatch, at_reconcile, tmp_path)

    def _answer_yes(*args: object, **kwargs: object) -> str:
        at_prompt.append(lock_is_free(tmp_path))
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_yes)
    wrap(
        monkeypatch,
        merge_service,
        "commit_merge",
        before=lambda: at_commit.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 0, result.stderr
    assert at_judge == [True]
    assert at_prompt == [True]
    assert at_reconcile == [True]
    assert at_commit == [False]
    assert (
        not (tmp_path / "bundle" / "concepts" / "a.md").exists()
        or not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    )


def test_apply_same_reconciles_without_the_lock_then_merges_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    at_reconcile: list[bool] = []
    at_commit: list[bool] = []
    _seed_same_group(tmp_path, monkeypatch)
    _stub_reconcile(monkeypatch, at_reconcile, tmp_path)
    wrap(
        monkeypatch,
        merge_service,
        "commit_merge",
        before=lambda: at_commit.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "1"])

    assert result.exit_code == 0, result.stderr
    assert at_reconcile == [True]
    assert at_commit == [False]


def test_apply_refuses_with_exit_3_when_the_lock_is_busy_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _seed_same_group(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        take = hold_the_lock_from(stack, tmp_path, acquired)

        def _answer_yes_and_let_another_process_in(
            *args: object, **kwargs: object
        ) -> str:
            take()
            return "y"

        monkeypatch.setattr("typer.prompt", _answer_yes_and_let_another_process_in)

        result = runner.invoke(app, ["adjudicate", "--apply"])

    assert acquired == [True]
    assert result.exit_code == 3
    assert "refusing to run" in result.stderr
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    # Judged verdicts are persisted under `.openkos/`; the bundle is untouched.
    after = _snapshot(tmp_path)
    assert {k: v for k, v in after.items() if str(k).startswith("bundle")} == {
        k: v for k, v in before.items() if str(k).startswith("bundle")
    }


def test_a_member_raised_to_confidential_while_reconciling_is_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel: the merged survivor's sensitivity was computed from both
    members' bytes before the reconciliation call. If the absorbed member is
    raised to `confidential` while that call runs (no lock is held), the merge
    must refuse -- writing it would stack the confidential body into a
    survivor that is still `private`."""
    init_workspace(tmp_path, monkeypatch)
    _seed_same_group(
        tmp_path, monkeypatch, body_b=f"{_SENTINEL_BODY} " + "Beta body. " * 80
    )
    survivor = tmp_path / "bundle" / "concepts" / "a.md"
    absorbed = tmp_path / "bundle" / "concepts" / "b.md"
    # The richer body survives (#776), so `a` is absorbed into `b`.
    survivor, absorbed = absorbed, survivor
    survivor_before = survivor.read_bytes()
    absorbed_path = absorbed

    def _raise_while_reconciling(
        root_arg: Path, prepared: object
    ) -> tuple[object, None]:
        _write(
            absorbed_path,
            title="Concept A",
            body="Alpha states the shared subject at length. " * 8,
            sensitivity="confidential",
        )
        return prepared, None

    monkeypatch.setattr(
        "openkos.cli.main._reconcile_merged_survivor", _raise_while_reconciling
    )
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "y")

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 3, result.stderr
    assert "refusing to write --" in result.stderr
    assert survivor.read_bytes() == survivor_before
    assert absorbed.exists()
    assert "confidential" in absorbed.read_text(encoding="utf-8")


@pytest.mark.parametrize("edited_while_judging", [False, True])
def test_a_verdict_is_persisted_only_for_the_content_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited_while_judging: bool
) -> None:
    """The persisted row's digests used to be read at persist time. With the
    judging call lock-free, a member edited meanwhile would be stored under its
    NEW digest and served later as a fresh verdict about content nobody judged."""
    init_workspace(tmp_path, monkeypatch)
    at_persist: list[bool] = []
    member = tmp_path / "bundle" / "concepts" / "a.md"

    def _edit() -> None:
        if edited_while_judging:
            _write(member, title="Concept A", body="Rewritten while judging.")

    _seed_same_group(tmp_path, monkeypatch, on_judge=_edit)
    wrap(
        monkeypatch,
        adjudications_store,
        "record_adjudications",
        before=lambda: at_persist.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0, result.stderr
    if edited_while_judging:
        assert _stored_adjudications(tmp_path) == ()
    else:
        assert at_persist == [False]
        assert [a.member_ids for a in _stored_adjudications(tmp_path)] == [
            ("concepts/a", "concepts/b")
        ]
