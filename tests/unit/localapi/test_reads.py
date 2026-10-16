"""The read endpoints: populated models round-trip through the wire form, and a
real workspace is read without a lock, a model call or a write."""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api, lock
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.cli.commit_phase_support import write_doc
from tests.unit.localapi.conftest import AUTH, PORT

PAIR = ("concepts/a", "concepts/b")


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _status() -> api.WorkspaceStatus:
    return api.WorkspaceStatus(
        root=Path("/ws"),
        sources=2,
        concepts=5,
        concepts_by_type=MappingProxyType({"Concept": 4, "Person": 1}),
        conformance_findings=("bad.md: no type",),
        recent_activity=(api.ActivityEntry("2026-10-14", "ingested a note"),),
        health=api.HealthCounts(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
        vectors_missing=True,
        stale_indexes=("vectors",),
        edges=(3, 1),
        queue="present",
        open_decisions_by_kind=MappingProxyType({"identity": 2, "volatility": 1}),
        last_job=api.JobSummary(
            "maintenance", "2026-10-14T10:00:00Z", None, "failed", "boom", 3, True
        ),
        jobs_unreadable=False,
        warnings=("w1",),
    )


def _pending(*, computed: bool) -> api.PendingReport:
    if not computed:
        return api.PendingReport(False, (), (), (), False)
    return api.PendingReport(
        queue_computed=True,
        open_rows=(
            api.PendingRow("identity:k", "identity", PAIR, "a vs b", "openkos x"),
        ),
        stats=(api.KindCounts("identity", 4, 1, 1, 1, 0, 1),),
        attention=(api.JobSummary("maintenance", "t0", "t1", "failed", None, 0, True),),
        jobs_unreadable=True,
    )


def _decisions() -> tuple[api.Decision, ...]:
    def d(kind: Any, detail: Any, **kw: Any) -> api.Decision:
        return api.Decision(
            decision_key=f"{kind}:k",
            kind=kind,
            targets=PAIR,
            fresh=kw.pop("fresh", True),
            evaluation=kw.pop("evaluation", "not_applicable"),
            actions=kw.pop("actions", (api.Action.SKIP,)),
            withheld=kw.pop("withheld", ()),
            resolves_via="openkos adjudicate --apply",
            detail=detail,
        )

    return (
        d(
            "identity",
            api.IdentityDetail(PAIR, "Concept", "high", "same", "Same thing."),
            evaluation="judged_same",
            actions=(api.Action.MERGE, api.Action.SKIP),
        ),
        d(
            "identity",
            api.IdentityDetail(PAIR, "Concept", "low", None, None),
            fresh=None,
            evaluation="pending_evaluation",
            withheld=(api.WithheldAction(api.Action.MERGE, "pending_evaluation"),),
        ),
        d(
            "relation_type",
            api.RelationDetail("a", "b", "b", "a", "member_of", "Because."),
            actions=(api.Action.RELATE, api.Action.RELATE_REVERSED),
        ),
        d("volatility", api.TierDetail("Person", "slow", "static", "Names.")),
        d("contradiction", None, fresh=False),
    )


def _patch(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> list[Any]:
    calls: list[Any] = []

    def stub(workspace: api.Workspace) -> Any:
        calls.append(workspace)
        return value

    monkeypatch.setattr(api, name, stub)
    return calls


def test_status_serves_the_populated_model_and_it_round_trips(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, workspace: api.Workspace
) -> None:
    expected = _status()
    calls = _patch(monkeypatch, "status", expected)

    response = client.get("/v1/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["concepts_by_type"] == {"Concept": 4, "Person": 1}
    assert body["open_decisions_by_kind"] == {"identity": 2, "volatility": 1}
    assert body["edges"] == [3, 1]
    assert body["root"] == "/ws"
    assert body["recent_activity"] == [
        {"date": "2026-10-14", "text": "ingested a note"}
    ]
    assert body["last_job"]["units_deferred"] == 3
    assert body["health"]["open_contradictions"] == 10
    assert body["queue"] == "present"
    assert TypeAdapter(api.WorkspaceStatus).validate_python(body) == expected
    assert calls == [calls[0]]
    assert calls[0].root == workspace.root


@pytest.mark.parametrize("computed", [True, False])
def test_pending_serves_the_report_and_it_round_trips(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, computed: bool
) -> None:
    expected = _pending(computed=computed)
    _patch(monkeypatch, "pending_report", expected)

    response = client.get("/v1/pending", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["queue_computed"] is computed
    assert len(body["open_rows"]) == (1 if computed else 0)
    if computed:
        assert body["open_rows"][0]["targets"] == list(PAIR)
        assert body["stats"][0]["enqueued"] == 4
    assert TypeAdapter(api.PendingReport).validate_python(body) == expected


def test_decisions_serve_an_envelope_of_every_detail_and_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _decisions()
    _patch(monkeypatch, "list_decisions", expected)

    response = client.get("/v1/decisions", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert list(body) == ["decisions"]
    assert [d["kind"] for d in body["decisions"]] == [
        "identity",
        "identity",
        "relation_type",
        "volatility",
        "contradiction",
    ]
    assert body["decisions"][0]["detail"]["verdict"] == "same"
    assert body["decisions"][1]["withheld"] == [
        {"action": "merge", "reason": "pending_evaluation"}
    ]
    assert body["decisions"][4]["detail"] is None
    got = TypeAdapter(tuple[api.Decision, ...]).validate_python(body["decisions"])
    assert got == expected


@pytest.mark.parametrize(
    ("exc", "status", "code", "retryable"),
    [
        (api.QueueUnavailableError("SENTINEL"), 503, "queue_unavailable", False),
        (
            api.WorkspaceNotFound(Path("/SENTINEL"), "SENTINEL"),
            500,
            "workspace_not_found",
            False,
        ),
        (
            api.WorkspaceUnreadable(Path("/SENTINEL"), "SENTINEL"),
            500,
            "workspace_unreadable",
            False,
        ),
        (OSError("SENTINEL"), 500, "internal_error", False),
        (ValueError("SENTINEL"), 500, "internal_error", False),
    ],
)
def test_read_failures_map_to_the_error_rows_without_echoing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    def boom(_workspace: api.Workspace) -> None:
        raise exc

    monkeypatch.setattr(api, "pending_report", boom)

    response = client.get("/v1/pending", headers=AUTH)

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["retryable"] is retryable
    assert "SENTINEL" not in response.text


@pytest.fixture
def root(settings: ServerSettings) -> Path:
    root = settings.workspace.root
    for concept_id in (*PAIR, "concepts/c"):
        write_doc(root, concept_id, {"type": "Concept", "title": concept_id}, "Body.\n")
    return root


def test_an_absent_queue_reports_not_computed_and_nothing_is_written(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support.forbid_model_clients(monkeypatch, "a read")
    before = support.snapshot(root)

    pending = client.get("/v1/pending", headers=AUTH)
    decisions = client.get("/v1/decisions", headers=AUTH)
    status = client.get("/v1/status", headers=AUTH)

    assert pending.json()["queue_computed"] is False
    assert decisions.json() == {"decisions": []}
    assert status.status_code == 200
    assert status.json()["concepts"] == 3
    assert support.snapshot(root) == before


def test_two_open_decisions_are_listed_and_reads_ignore_a_held_lock(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support.enqueue_identity(root, PAIR, verdict="same", rationale="Same.")
    support.enqueue_volatility(root, "Person", "static")
    support.forbid_model_clients(monkeypatch, "a read")
    before = support.snapshot(root)

    with lock.workspace_lock(root):
        decisions = client.get("/v1/decisions", headers=AUTH)
        pending = client.get("/v1/pending", headers=AUTH)
        status = client.get("/v1/status", headers=AUTH)

    assert {d["kind"] for d in decisions.json()["decisions"]} == {
        "identity",
        "volatility",
    }
    assert len(pending.json()["open_rows"]) == 2
    assert status.json()["open_decisions_by_kind"] == {"identity": 1, "volatility": 1}
    assert support.snapshot(root) == before


def test_owner_local_content_is_unredacted_with_a_token_and_absent_without(
    client: TestClient, root: Path
) -> None:
    marker = "confidential-rationale-7c1e"
    write_doc(
        root,
        "concepts/marker",
        {"type": "Concept", "title": "s", "sensitivity": "confidential"},
        "Body.\n",
    )
    support.enqueue_identity(root, PAIR, verdict="same", rationale=marker)

    ok = client.get("/v1/decisions", headers=AUTH)
    none = client.get("/v1/decisions")
    wrong = client.get("/v1/decisions", headers={"Authorization": "Bearer nope"})

    assert marker in ok.text
    assert (none.status_code, wrong.status_code) == (401, 403)
    assert marker not in none.text + wrong.text


def test_importing_the_local_api_never_loads_the_disclosure_gate() -> None:
    code = (
        "import sys, openkos.localapi._app; "
        "print(json.dumps('openkos.mcp.gate' in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-c", "import json; " + code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) is False
