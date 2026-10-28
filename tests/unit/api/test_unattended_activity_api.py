"""`openkos.api.unattended_activity` (ADR-0078): the latest unattended job and
whether the budget is spent, read without a lock, a model or a write."""

from pathlib import Path

import pytest

from openkos import lock
from openkos.api import JobSummary, UnattendedActivity, Workspace, unattended_activity
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    support.forbid_model_clients(monkeypatch, "the unattended activity read")
    return root


def test_a_workspace_that_never_ran_the_daemon_has_no_history(root: Path) -> None:
    before = support.snapshot(root)

    result = unattended_activity(Workspace.open(root))

    assert result == UnattendedActivity(
        last_job=None, budget_exhausted=False, jobs_unreadable=False
    )
    assert support.snapshot(root) == before


def test_a_spent_budget_is_reported_with_the_job_that_hit_it(root: Path) -> None:
    support.record_job(root, "watch", "budget_exhausted")

    result = unattended_activity(Workspace.open(root))

    assert result.budget_exhausted is True
    assert result.last_job == JobSummary(
        kind="watch",
        started_at="2026-10-14T10:00:00Z",
        ended_at="2026-10-14T10:01:00Z",
        outcome="budget_exhausted",
        detail_code=None,
        units_deferred=3,
        needs_attention=True,
    )


def test_it_takes_no_lock(root: Path) -> None:
    support.record_job(root, "maintenance", "completed")

    with lock.workspace_lock(root):  # another writer holds the lock
        result = unattended_activity(Workspace.open(root))

    assert result.budget_exhausted is False
    assert result.last_job is not None
    assert result.last_job.needs_attention is False
