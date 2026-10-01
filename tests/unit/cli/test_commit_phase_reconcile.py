"""The commit phase of `reconcile`, in both its forms (#1137, ADR-0036;
tasks.md 1.6, second group).

`reconcile` calls no model. Its only compute-phase waits are the confirmation
prompt and the `--from-findings` per-item prompts, and none of them may hold
the workspace lock. The write, the drift re-check and the auto-commit are the
commit phase.
"""

import contextlib
from pathlib import Path

import pytest

from openkos import config as config_module
from openkos import fsio
from openkos.cli.main import app
from openkos.model import okf
from openkos.state import derived, findings
from tests.unit.cli.commit_phase_support import (
    hold_the_lock_from,
    init_workspace,
    lock_is_free,
    metadata_of,
    runner,
    simulate_tty,
    wrap,
    write_doc,
)
from tests.unit.cli.conftest import commit_pending_fixture_docs, confirm_after
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.vcs.conftest import isolate_git_identity


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


def _seed_pair(tmp_path: Path) -> None:
    write_doc(tmp_path, "concepts/a", {"type": "Concept", "title": "A"})
    write_doc(tmp_path, "concepts/b", {"type": "Concept", "title": "B"})
    commit_pending_fixture_docs()


def test_prompt_does_not_hold_the_lock_but_the_write_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _seed_pair(tmp_path)
    simulate_tty(monkeypatch)
    at_prompt: list[bool] = []
    at_write: list[bool] = []
    confirm_after(monkeypatch, lambda: at_prompt.append(lock_is_free(tmp_path)))
    wrap(
        monkeypatch,
        fsio,
        "write_atomic",
        before=lambda: at_write.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["reconcile", "concepts/a", "concepts/b"], input="y\n")

    assert result.exit_code == 0, result.stderr
    assert at_prompt == [True]
    assert at_write == [False, False, False]
    relations = okf.decode_relations(metadata_of(tmp_path, "concepts/a"))
    assert [(r.target, r.type) for r in relations] == [
        ("concepts/b", "reconciled_with")
    ]


def test_a_busy_lock_at_commit_is_exit_3_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _seed_pair(tmp_path)
    simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        confirm_after(monkeypatch, hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(
            app, ["reconcile", "concepts/a", "concepts/b"], input="y\n"
        )

    assert acquired == [True]
    assert result.exit_code == 3
    assert "refusing to run" in result.stderr
    assert _snapshot(tmp_path) == before


def test_from_findings_prompt_does_not_hold_the_lock_but_the_write_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _seed_pair(tmp_path)
    simulate_tty(monkeypatch)
    conn = derived.open_derived_connection(
        config_module.WorkspaceLayout(tmp_path).findings_db_path
    )
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concepts/a", "concepts/b"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="they disagree",
                    input_digests=(),
                )
            ],
        )
    finally:
        conn.close()
    at_prompt: list[bool] = []
    at_write: list[bool] = []

    def _answer_yes(*args: object, **kwargs: object) -> str:
        at_prompt.append(lock_is_free(tmp_path))
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_yes)
    wrap(
        monkeypatch,
        fsio,
        "write_atomic",
        before=lambda: at_write.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["reconcile", "--from-findings"])

    assert result.exit_code == 0, result.stderr
    assert at_prompt == [True]
    assert at_write == [False, False, False]
