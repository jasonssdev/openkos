"""The finding preview over HTTP (settle-other-kinds, ADR-0072):
`POST /v1/decisions/{key}/preview-finding`, its two new error rows, and a real
contradiction previewed and applied through the shared apply route."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openkos import api
from openkos.localapi._app import ServerSettings, create_app
from openkos.state import pending_queue as pq
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _pin_identity
from tests.unit.application.curation_support import write_concept
from tests.unit.localapi.conftest import AUTH, PORT

PAIR = ("concepts/a", "concepts/b")
KEY = "contradiction:abc123"
VERSION = api.DecisionVersion(KEY, "payload-digest-1", "content-digest-1")
PATH = f"/v1/decisions/{KEY}/preview-finding"


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _stub(monkeypatch: pytest.MonkeyPatch, name: str, result: Any) -> list[Any]:
    calls: list[Any] = []

    def stub(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(api, name, stub)
    return calls


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (
            api.ReconcilePlan(
                "concepts/a",
                "concepts/b",
                "supersedes",
                "concepts/a",
                "concepts/b",
                "concepts/b",
                ("concepts/a", "concepts/b"),
            ),
            {
                "kind": "reconcile",
                "first_id": "concepts/a",
                "second_id": "concepts/b",
                "relation_type": "supersedes",
                "holder_id": "concepts/a",
                "target_id": "concepts/b",
                "deprecates_id": "concepts/b",
                "pages_changed": ["concepts/a", "concepts/b"],
            },
        ),
        (
            api.DeclinePlan("concepts/a", "concepts/b", None),
            {
                "kind": "decline",
                "first_id": "concepts/a",
                "second_id": "concepts/b",
                "merged_absorbed_id": None,
            },
        ),
    ],
)
def test_the_finding_preview_serves_the_plan_and_the_version(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    plan: Any,
    expected: dict[str, Any],
) -> None:
    calls = _stub(
        monkeypatch,
        "preview_finding",
        api.FindingPreview(KEY, api.Action.SUPERSEDE, plan, VERSION),
    )

    response = client.post(PATH, json={"action": "supersede"}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == expected
    assert body["version"]["decision_key"] == KEY
    [(args, _kwargs)] = calls
    assert args[1:] == (KEY, api.Action.SUPERSEDE)


@pytest.mark.parametrize(
    "body", [{}, {"action": "obliterate"}, {"action": "decline", "selection": None}]
)
def test_an_invalid_finding_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    calls = _stub(monkeypatch, "preview_finding", None)

    response = client.post(PATH, json=body, headers=AUTH)

    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert calls == []


@pytest.mark.parametrize(
    ("route", "operation", "reason", "status", "code"),
    [
        ("preview-finding", "preview_finding", "wrong_preview", 422, "wrong_preview"),
        ("preview", "preview_decision", "wrong_preview", 422, "wrong_preview"),
        (
            "preview-finding",
            "preview_finding",
            "already_reconciled",
            409,
            "already_reconciled",
        ),
    ],
)
def test_the_new_refusals_map_to_their_rows(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    operation: str,
    reason: str,
    status: int,
    code: str,
) -> None:
    _stub(monkeypatch, operation, api.NotApplied(api.Action.RECONCILE, reason))  # type: ignore[arg-type]

    response = client.post(
        f"/v1/decisions/{KEY}/{route}", json={"action": "reconcile"}, headers=AUTH
    )

    assert (response.status_code, response.json()["code"]) == (status, code)
    assert response.json()["retryable"] is False


def test_already_reconciled_is_also_the_answer_of_an_apply(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(
        monkeypatch,
        "apply_decision",
        api.NotApplied(api.Action.RECONCILE, "already_reconciled"),
    )

    response = client.post(
        f"/v1/decisions/{KEY}/apply",
        json={
            "action": "reconcile",
            "version": {
                "decision_key": KEY,
                "payload_digest": "p",
                "content_digest": "c",
            },
        },
        headers=AUTH,
    )

    assert (response.status_code, response.json()["code"]) == (
        409,
        "already_reconciled",
    )


def test_a_real_contradiction_is_previewed_and_applied_over_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: ServerSettings
) -> None:
    _pin_identity(monkeypatch, tmp_path)
    root = settings.workspace.root
    write_concept(root, "concepts/a", title="A", body="In May.")
    write_concept(root, "concepts/b", title="B", body="In June.")
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    support.enqueue(
        root,
        kind="contradiction",
        key_body=pq.contradiction_key(PAIR, None),
        payload={
            "pair_ids": list(PAIR),
            "merged_absorbed_id": None,
            "confidence": 0.9,
            "rationale": "May against June.",
            "conflicting_claims": [],
        },
        targets=PAIR,
        producer="contradictions/1",
        fresh=True,
    )
    key = pq.stored_decision_key("contradiction", pq.contradiction_key(PAIR, None))

    with TestClient(create_app(settings), base_url=f"http://127.0.0.1:{PORT}") as http:
        listed = http.get("/v1/decisions", headers=AUTH).json()["decisions"]
        preview = http.post(
            f"/v1/decisions/{key}/preview-finding",
            json={"action": "supersede_reversed"},
            headers=AUTH,
        )
        applied = http.post(
            f"/v1/decisions/{key}/apply",
            json={"action": "supersede_reversed", "version": preview.json()["version"]},
            headers=AUTH,
        )

    assert listed[0]["finding"]["rationale"] == "May against June."
    assert "supersede_reversed" in listed[0]["actions"]
    assert preview.status_code == 200
    assert preview.json()["plan"]["holder_id"] == "concepts/b"
    assert applied.status_code == 200
    assert applied.json()["applied"]["action"] == "supersede_reversed"
