"""Undoing a settled finding over HTTP (`undo-findings` change, ADR-0074): the
routes, the echoed versions, the error rows, and real round trips over findings
settled through the decision routes themselves."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api
from openkos.localapi._app import ServerSettings, create_app
from openkos.state import pending_queue as pq
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _pin_identity
from tests.unit.application.curation_support import write_concept
from tests.unit.localapi.conftest import AUTH, PORT

A, B = PAIR = ("concepts/a", "concepts/b")
KEY = pq.stored_decision_key("contradiction", pq.contradiction_key(PAIR, None))


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


@pytest.fixture
def root(
    settings: ServerSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Two committed pages and an open contradiction between them."""
    _pin_identity(monkeypatch, tmp_path)
    root = settings.workspace.root
    write_concept(root, A, title="A", body="The launch is in May.")
    write_concept(root, B, title="B", body="The launch is in June.")
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
    support.forbid_model_clients(monkeypatch, "an undo over HTTP")
    return root


def _post(client: TestClient, path: str, body: Any) -> Any:
    return client.post(path, json=body, headers=AUTH)


def _settle(client: TestClient, action: str) -> None:
    preview = _post(client, f"/v1/decisions/{KEY}/preview-finding", {"action": action})
    assert preview.status_code == 200, preview.text
    applied = _post(
        client,
        f"/v1/decisions/{KEY}/apply",
        {"action": action, "version": preview.json()["version"]},
    )
    assert applied.status_code == 200, applied.text


@pytest.fixture
def declined(root: Path, client: TestClient) -> Path:
    _settle(client, "decline")
    return root


# -- a declined contradiction ---------------------------------------------------- #

_REOPEN = "/v1/contradictions/reopen"


def test_a_declined_contradiction_is_listed_and_reopened_over_http(
    client: TestClient, declined: Path
) -> None:
    before = support.snapshot(declined)
    listed = client.get("/v1/declined-contradictions", headers=AUTH)
    preview = _post(
        client,
        f"{_REOPEN}/preview",
        {"first_id": B, "second_id": A, "merged_absorbed_id": None},
    )
    assert support.snapshot(declined) == before

    assert listed.status_code == 200, listed.text
    (entry,) = listed.json()["contradictions"]
    assert (entry["first_id"], entry["second_id"]) == (A, B)
    assert entry["merged_absorbed_id"] is None
    assert entry["decided_at"]
    assert preview.status_code == 200, preview.text
    assert TypeAdapter(api.ContradictionReopenPreview).validate_python(
        preview.json()
    ) == api.preview_reopen_contradiction(api.Workspace.open(declined), A, B)

    applied = _post(client, f"{_REOPEN}/apply", {"version": preview.json()["version"]})

    assert applied.status_code == 200, applied.text
    reopened = TypeAdapter(api.ContradictionReopened).validate_python(
        applied.json()["reopened"]
    )
    assert (reopened.first_id, reopened.second_id) == (A, B)
    assert reopened.commit_sha is not None
    assert applied.json()["warnings"] == []
    assert client.get("/v1/declined-contradictions", headers=AUTH).json() == {
        "contradictions": []
    }


def test_the_discriminator_may_be_left_out_of_the_preview_body(
    client: TestClient, declined: Path
) -> None:
    response = _post(client, f"{_REOPEN}/preview", {"first_id": A, "second_id": B})

    assert response.status_code == 200, response.text


def test_a_contradiction_never_declined_is_409_not_declined(
    client: TestClient, root: Path
) -> None:
    response = _post(client, f"{_REOPEN}/preview", {"first_id": A, "second_id": B})

    assert response.status_code == 409
    assert response.json() == {
        "code": "not_declined",
        "retryable": False,
        "message": "this contradiction is not recorded as declined",
    }


def test_an_altered_reopen_version_is_409_stale(
    client: TestClient, declined: Path
) -> None:
    version = _post(
        client, f"{_REOPEN}/preview", {"first_id": A, "second_id": B}
    ).json()["version"]
    before = support.snapshot(declined)

    response = _post(
        client, f"{_REOPEN}/apply", {"version": {**version, "content_digest": "0" * 64}}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert support.snapshot(declined) == before


@pytest.mark.parametrize(
    "body",
    [
        {"first_id": A},
        {"first_id": A, "second_id": B, "extra": 1},
        {"first_id": A, "second_id": 3},
    ],
)
def test_an_invalid_reopen_preview_body_is_422(
    client: TestClient, root: Path, body: dict[str, Any]
) -> None:
    assert _post(client, f"{_REOPEN}/preview", body).status_code == 422


def test_an_unknown_field_beside_the_echoed_version_is_422(
    client: TestClient, declined: Path
) -> None:
    version = _post(
        client, f"{_REOPEN}/preview", {"first_id": A, "second_id": B}
    ).json()["version"]

    response = _post(client, f"{_REOPEN}/apply", {"version": version, "x": 1})

    assert response.status_code == 422
