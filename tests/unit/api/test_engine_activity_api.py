"""`openkos.api.engine_activity`: the running task, the recent ones, what waits
and today's budget, read without a lock, a model or a write."""

from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    DailyBudget,
    EngineActivity,
    EngineJob,
    Workspace,
    engine_activity,
)
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    support.forbid_model_clients(monkeypatch, "the engine activity read")
    return root


def test_a_workspace_that_never_ran_the_daemon_has_an_empty_activity(
    root: Path,
) -> None:
    before = support.snapshot(root)

    result = engine_activity(Workspace.open(root))

    assert result.current is None
    assert result.recent == ()
    assert result.waiting_files == 0
    assert result.uncommitted_paths == 0
    assert result.jobs_unreadable is False
    assert result.budget == DailyBudget(calls_used=0, calls_limit=500, exhausted=False)
    assert support.snapshot(root) == before


def test_a_finished_job_is_listed_with_its_counts(root: Path) -> None:
    support.record_job(root, "watch", "completed")

    result = engine_activity(Workspace.open(root))

    assert result.recent == (
        EngineJob(
            kind="watch",
            started_at="2026-10-14T10:00:00Z",
            ended_at="2026-10-14T10:01:00Z",
            outcome="completed",
            chat_calls=0,
            units_done=0,
            units_deferred=3,
            needs_attention=False,
        ),
    )


def test_a_spent_budget_is_reported(root: Path) -> None:
    support.record_job(root, "watch", "budget_exhausted")

    result = engine_activity(Workspace.open(root))

    assert result.budget.exhausted is True
    assert result.recent[0].needs_attention is True


def test_it_takes_no_lock(root: Path) -> None:
    support.record_job(root, "maintenance", "completed")

    with lock.workspace_lock(root):
        result = engine_activity(Workspace.open(root))

    assert len(result.recent) == 1


def test_an_unreadable_record_is_reported(root: Path) -> None:
    (root / ".openkos").mkdir(exist_ok=True)
    (root / ".openkos" / "jobs.db").write_bytes(b"not a database" * 50)

    result: EngineActivity = engine_activity(Workspace.open(root))

    assert result.jobs_unreadable is True
