"""The refresh through the public API only: a persisted verdict of each kind
becomes a listed decision carrying its values, every counter of the service's
outcome reaches the API result under its own name, and the compute phase holds
no workspace lock."""

from pathlib import Path
from typing import Any

import pytest

from openkos import config, lock
from openkos.api import (
    IdentityDetail,
    KindRefresh,
    RelationDetail,
    TierDetail,
    Workspace,
    list_decisions,
    refresh_decision_queue,
)
from openkos.application import queue_producers, queue_refresh
from openkos.resolution import candidates as cand
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.application.test_queue_refresh import (
    DST,
    MEMBERS,
    MODEL,
    SRC,
    _group,
    _record_all,
)
from tests.unit.cli.commit_phase_support import write_doc


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    for cid in (*MEMBERS, SRC, DST):
        write_doc(ws, cid, {"type": "Concept", "title": cid}, f"Body of {cid}.\n")
    write_doc(
        ws,
        "people/ada",
        {"type": "Person", "title": "Ada", "sensitivity": "private"},
        "Body of Ada.\n",
    )
    return ws


@pytest.fixture(autouse=True)
def pinned(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support.forbid_model_clients(monkeypatch, "a refresh")
    monkeypatch.setattr(config, "resolve_task_model", lambda cfg, task: MODEL)


def _finder(monkeypatch: pytest.MonkeyPatch, on_call: Any = None) -> None:
    def find(bundle_dir: Path, **kwargs: Any) -> cand.CandidateGroupReport:
        if on_call is not None:
            on_call()
        return cand.CandidateGroupReport(
            groups=(_group(*MEMBERS),), produced=1, retained=1
        )

    monkeypatch.setattr("openkos.resolution.find_candidates_report", find)


def test_one_verdict_per_kind_is_listed_with_its_values(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_all(root)
    _finder(monkeypatch)
    workspace = Workspace.open(root)
    assert list_decisions(workspace) == ()

    refresh = refresh_decision_queue(workspace)

    decisions = {d.kind: d for d in list_decisions(workspace)}
    assert sorted(decisions) == ["identity", "relation_type", "volatility"]
    assert len(list_decisions(workspace)) == 3
    assert (
        refresh.identity.inserted,
        refresh.relation_type.inserted,
        refresh.volatility.inserted,
    ) == (1, 1, 1)

    identity = decisions["identity"]
    assert identity.targets == MEMBERS
    assert identity.evaluation == "judged_same"
    assert isinstance(identity.detail, IdentityDetail)
    assert identity.detail.member_ids == MEMBERS
    assert identity.detail.verdict == "same"
    assert identity.detail.rationale == "Judged."

    relation = decisions["relation_type"]
    assert relation.targets == (SRC, DST)
    assert isinstance(relation.detail, RelationDetail)
    assert (relation.detail.source_id, relation.detail.target_id) == (SRC, DST)
    assert relation.detail.suggested_type == "works_at"
    assert relation.detail.rationale == "they work together"

    volatility = decisions["volatility"]
    assert isinstance(volatility.detail, TierDetail)
    assert volatility.detail.type_name == "Person"
    assert volatility.detail.suggested_tier == "static"


def _result(kind: str, base: int, *, complete: bool) -> queue_producers.ProducerResult:
    return queue_producers.ProducerResult(
        kind=kind,
        inserted=base + 1,
        unchanged=base + 2,
        replaced=base + 3,
        suppressed=base + 4,
        retired=base + 5,
        complete=complete,
        amended=base + 6,
    )


def test_every_counter_of_the_service_outcome_reaches_its_own_field(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = queue_refresh.RefreshOutcome(
        identity=_result("identity", 10, complete=True),
        relation_type=_result("relation_type", 20, complete=False),
        volatility=_result("volatility", 30, complete=True),
        volatility_ambiguous=7,
        identity_truncation_notice="notice",
    )
    monkeypatch.setattr(
        queue_refresh, "refresh_decision_queue", lambda *a, **k: outcome
    )

    result = refresh_decision_queue(Workspace.open(root))

    for kind, base, ambiguous, complete in (
        ("identity", 10, 0, True),
        ("relation_type", 20, 0, False),
        ("volatility", 30, 7, True),
    ):
        assert getattr(result, kind) == KindRefresh(
            kind=kind,  # type: ignore[arg-type]
            inserted=base + 1,
            unchanged=base + 2,
            replaced=base + 3,
            suppressed=base + 4,
            retired=base + 5,
            complete=complete,
            amended=base + 6,
            ambiguous=ambiguous,
        )
    assert result.identity_truncation_notice == "notice"


def test_the_compute_phase_holds_no_workspace_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_all(root)
    acquired: list[bool] = []

    def take_the_lock() -> None:
        with lock.workspace_lock(root):
            acquired.append(True)

    _finder(monkeypatch, take_the_lock)

    refresh_decision_queue(Workspace.open(root))

    assert acquired == [True]
