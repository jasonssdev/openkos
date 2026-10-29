"""`GET /v1/activity`: the engine's activity over HTTP, read-only, for the
desktop Activity view."""

from collections.abc import Iterator

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
    support.forbid_model_clients(monkeypatch, "the activity route")
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def test_no_history_is_an_empty_activity_and_writes_nothing(
    client: TestClient, workspace: api.Workspace
) -> None:
    before = support.snapshot(workspace.root)

    response = client.get("/v1/activity", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "current": None,
        "recent": [],
        "waiting_files": 0,
        "uncommitted_paths": 0,
        "budget": {"calls_used": 0, "calls_limit": 500, "exhausted": False},
        "jobs_unreadable": False,
    }
    assert support.snapshot(workspace.root) == before


def test_a_finished_job_is_served_while_the_lock_is_held(
    client: TestClient, workspace: api.Workspace
) -> None:
    support.record_job(workspace.root, "watch", "budget_exhausted")

    with lock.workspace_lock(workspace.root):
        response = client.get("/v1/activity", headers=AUTH)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["budget"]["exhausted"] is True
    assert body["recent"][0]["kind"] == "watch"
    assert body["recent"][0]["needs_attention"] is True


def test_it_needs_the_token(client: TestClient) -> None:
    assert client.get("/v1/activity").status_code == 401


def test_the_read_is_not_a_write_route(client: TestClient) -> None:
    assert client.post("/v1/activity", headers=AUTH).status_code == 405
