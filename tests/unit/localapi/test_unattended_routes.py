"""`GET /v1/unattended` (ADR-0078): the latest unattended outcome over HTTP,
read-only, so the desktop app can tell a daemon idling on a spent budget."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openkos import api, lock
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.localapi.conftest import AUTH, PORT


@pytest.fixture
def client(
    settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    support.forbid_model_clients(monkeypatch, "the unattended route")
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def test_no_history_is_served_as_such_and_writes_nothing(
    client: TestClient, workspace: api.Workspace
) -> None:
    before = support.snapshot(workspace.root)

    response = client.get("/v1/unattended", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "last_job": None,
        "budget_exhausted": False,
        "jobs_unreadable": False,
    }
    assert support.snapshot(workspace.root) == before


def test_a_spent_budget_is_served_with_its_job(
    client: TestClient, workspace: api.Workspace
) -> None:
    support.record_job(workspace.root, "watch", "budget_exhausted")

    with lock.workspace_lock(workspace.root):  # reads ignore a held lock
        response = client.get("/v1/unattended", headers=AUTH)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["budget_exhausted"] is True
    assert body["last_job"]["kind"] == "watch"
    assert body["last_job"]["outcome"] == "budget_exhausted"


def test_it_needs_the_token(client: TestClient) -> None:
    assert client.get("/v1/unattended").status_code == 401


def test_the_read_is_not_a_write_route(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/v1/unattended", headers=AUTH).status_code == 405
