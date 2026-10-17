"""`openkos.api.refresh_decision_queue`: the decision queue refreshed from
persisted verdicts with no model, a lock only inside commit sections, and an
API-owned result.

The identity verdicts here are real: each test judges through the API itself
(quote, then run) and then refreshes, so the queue is filled from what the API
persisted.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    KindRefresh,
    QueueRefresh,
    QueueRefreshRefused,
    Workspace,
    WorkspaceBusyError,
    quote_judging,
    refresh_decision_queue,
    run_judging,
)
from openkos.llm.base import BackendHostLocality, Message
from openkos.resolution import candidates
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


class _Judge:
    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        return json.dumps({"verdict": "same", "confidence": 0.9, "rationale": "Same."})


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    support.write_identity_groups(ws)
    return ws


@pytest.fixture(autouse=True)
def no_client(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support.forbid_model_clients(monkeypatch, "a refresh")


def _judge_everything(root: Path) -> None:
    """Persist an identity verdict for every group, through the API."""
    workspace = Workspace.open(root)
    quote = quote_judging(workspace, "identity", chat_client=lambda task: _Judge())
    run_judging(workspace, quote.token, chat_client=lambda task: _Judge())


def test_a_refresh_resolves_no_client_and_reports_three_kinds(root: Path) -> None:
    _judge_everything(root)

    result = refresh_decision_queue(Workspace.open(root))

    assert isinstance(result, QueueRefresh)
    assert [
        r.kind for r in (result.identity, result.relation_type, result.volatility)
    ] == [
        "identity",
        "relation_type",
        "volatility",
    ]
    assert all(
        isinstance(r, KindRefresh) for r in (result.identity, result.relation_type)
    )
    assert result.identity.inserted == 3
    assert result.identity.retired == 0
    assert result.identity_truncation_notice is None


def test_only_identity_can_report_a_complete_pass(root: Path) -> None:
    _judge_everything(root)

    result = refresh_decision_queue(Workspace.open(root))

    assert result.identity.complete is True
    for other in (result.relation_type, result.volatility):
        assert other.complete is False
        assert other.retired == 0
    assert result.relation_type.ambiguous == 0
    assert result.volatility.ambiguous == 0


def test_a_second_refresh_changes_nothing(root: Path) -> None:
    _judge_everything(root)
    workspace = Workspace.open(root)
    refresh_decision_queue(workspace)

    again = refresh_decision_queue(workspace)

    assert (again.identity.inserted, again.identity.unchanged) == (0, 3)


def test_a_truncated_identity_report_carries_its_notice_and_is_not_complete(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = candidates.find_candidates_report

    def truncated(*args: object, **kwargs: object) -> candidates.CandidateGroupReport:
        report = real(*args, **kwargs)  # type: ignore[arg-type]
        return candidates.CandidateGroupReport(
            groups=report.groups, produced=report.produced + 4, retained=report.retained
        )

    monkeypatch.setattr("openkos.resolution.find_candidates_report", truncated)

    result = refresh_decision_queue(Workspace.open(root))

    assert (
        result.identity_truncation_notice
        == "3 of 7 candidate group(s) shown (cap reached)"
    )
    assert result.identity.complete is False


def test_an_unreadable_workspace_is_refused_and_nothing_is_written(root: Path) -> None:
    workspace = Workspace.open(root)
    (root / "openkos.yaml").unlink()
    before = support.snapshot(root)

    with pytest.raises(QueueRefreshRefused):
        refresh_decision_queue(workspace)

    assert support.snapshot(root) == before


def test_a_held_lock_raises_the_busy_error_with_no_wait(root: Path) -> None:
    _judge_everything(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        refresh_decision_queue(Workspace.open(root), wait_seconds=0)


@pytest.mark.parametrize("wait", [-1.0, float("nan"), 3601.0])
def test_a_wait_out_of_range_is_refused_before_anything_is_written(
    root: Path, wait: float
) -> None:
    _judge_everything(root)
    before = support.snapshot(root)

    with pytest.raises(ValueError, match="wait_seconds"):
        refresh_decision_queue(Workspace.open(root), wait_seconds=wait)

    assert support.snapshot(root) == before


def test_rows_naming_a_missing_page_are_counted_apart(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/g0-a", "concepts/gone"))
    support.enqueue_relation(root, "concepts/g0-a", "concepts/gone")

    result = refresh_decision_queue(Workspace.open(root))

    assert result.identity.retired_missing == 1
    assert result.relation_type.retired_missing == 1
    assert result.volatility.retired_missing == 0
