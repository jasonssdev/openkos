"""Rebuilding search over HTTP (`sensitivity-and-rebuild` change, #1518): the
read, the pollable rebuild and its refusals, against a fake embedding client."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.api.test_search_api import Embedder, _remote, install
from tests.unit.cli.commit_phase_support import write_doc
from tests.unit.localapi.conftest import AUTH, PORT

WAIT = 10.0


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


@pytest.fixture
def root(settings: ServerSettings, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    ws = settings.workspace.root
    write_doc(ws, "concepts/open", {"type": "Concept", "title": "Open"}, "Open.\n")
    return ws


def _poll(client: TestClient, location: str) -> Any:
    deadline = time.monotonic() + WAIT
    while True:
        body = client.get(location, headers=AUTH).json()
        if body["state"] not in ("running", "stopping"):
            return body
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_the_read_builds_no_client_and_writes_nothing(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support.forbid_model_clients(monkeypatch, "the search read over HTTP")
    before = support.snapshot(root)
    response = client.get("/v1/search", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["model_changed"] is False
    assert response.json()["sends_to"] is None
    assert support.snapshot(root) == before


def test_a_rebuild_is_started_polled_and_finished(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    response = client.post("/v1/search/rebuilds", json={}, headers=AUTH)
    assert response.status_code == 202, response.text
    location = response.headers["Location"]
    assert location == f"/v1/search/rebuilds/{response.json()['rebuild_id']}"

    done = _poll(client, location)

    assert done["state"] == "finished", done
    assert done["outcome"]["embedded"] == 1
    assert done["outcome"]["complete"] is True
    assert client.get("/v1/search", headers=AUTH).json()["needs_rebuild"] is False


def test_a_remote_model_without_consent_answers_the_start_with_422(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = install(monkeypatch, Embedder(local=False))
    _remote(root, monkeypatch)
    assert client.get("/v1/search", headers=AUTH).json()["sends_to"] == (
        "llm.example.test"
    )

    response = client.post(
        "/v1/search/rebuilds", json={"allow_remote": False}, headers=AUTH
    )

    assert response.status_code == 422
    assert response.json()["code"] == "remote_not_allowed"
    assert embedder.texts == []


def test_with_consent_a_remote_rebuild_runs(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder(local=False))
    _remote(root, monkeypatch)
    response = client.post(
        "/v1/search/rebuilds", json={"allow_remote": True}, headers=AUTH
    )
    assert response.status_code == 202, response.text
    assert _poll(client, response.headers["Location"])["state"] == "finished"


def test_a_failure_is_reported_on_the_poll(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.llm.base import BackendUnavailable

    install(monkeypatch, Embedder(error=BackendUnavailable("down")))
    response = client.post("/v1/search/rebuilds", json={}, headers=AUTH)
    assert response.status_code == 202, response.text
    done = _poll(client, response.headers["Location"])
    assert done["state"] == "failed"
    assert done["error"]["code"] == "backend_unavailable"


def test_an_unknown_rebuild_is_404(client: TestClient, root: Path) -> None:
    assert client.get("/v1/search/rebuilds/nope", headers=AUTH).status_code == 404
    assert client.post("/v1/search/rebuilds/nope/stop", headers=AUTH).status_code == 404


def test_an_unknown_field_is_422(client: TestClient, root: Path) -> None:
    response = client.post("/v1/search/rebuilds", json={"force": True}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
