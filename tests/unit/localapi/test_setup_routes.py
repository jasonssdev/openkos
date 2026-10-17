"""The setup routes: readiness round-trips, and a failure maps to the table."""

import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.localapi.conftest import AUTH, PORT


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _readiness() -> api.SetupReadiness:
    return api.SetupReadiness(
        runtime=api.RuntimeReadiness(
            backend="ollama",
            host="localhost:11434",
            is_local=True,
            reachable=True,
            version="0.32.9",
            download_url="https://ollama.com/download",
        ),
        models=(
            api.ModelReadiness("qwen3:8b", ("chat",), True, True, True),
            api.ModelReadiness(
                "gemma4:26b-a4b", ("adjudication", "contradiction"), False, None, True
            ),
        ),
        memory=api.MemoryReadiness(None, 32 * 1024**3, None),
        ready=True,
    )


def test_readiness_round_trips(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[api.Workspace] = []

    def stub(workspace: api.Workspace) -> api.SetupReadiness:
        seen.append(workspace)
        return _readiness()

    monkeypatch.setattr(api, "setup_readiness", stub)

    response = client.get("/v1/setup/readiness", headers=AUTH)

    assert response.status_code == 200
    assert TypeAdapter(api.SetupReadiness).validate_python(response.json()) == (
        _readiness()
    )
    assert len(seen) == 1


def test_readiness_needs_the_token(client: TestClient) -> None:
    assert client.get("/v1/setup/readiness").status_code == 401


def test_an_unreadable_workspace_maps_to_its_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, settings: ServerSettings
) -> None:
    def boom(workspace: api.Workspace) -> api.SetupReadiness:
        raise api.WorkspaceUnreadable(workspace.root, "secret detail")

    monkeypatch.setattr(api, "setup_readiness", boom)

    response = client.get("/v1/setup/readiness", headers=AUTH)

    assert response.status_code == 500
    assert response.json()["code"] == "workspace_unreadable"
    assert "secret detail" not in response.text


# -- the pull job ----------------------------------------------------------------- #


class _Gate:
    """A stand-in for `api.pull_model`: admitted, then held until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.models: list[str] = []

    def __call__(
        self,
        workspace: api.Workspace,
        model: str,
        *,
        stop: api.StopToken | None = None,
        observer: api.PullObserver | None = None,
    ) -> api.PullOutcome | api.PullRefused:
        self.models.append(model)
        if model == "unknown:1":
            return api.PullRefused(model, "not_configured")
        assert stop is not None
        assert observer is not None
        stop.is_set()
        observer.progress(api.PullProgress(model, "downloading", 3, 9))
        assert self.release.wait(10)
        if stop.is_set():
            return api.PullOutcome(model, "stopped", 9, 9)
        return api.PullOutcome(model, "completed", 9, 9)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Gate]:
    fake = _Gate()
    monkeypatch.setattr(api, "pull_model", fake)
    yield fake
    fake.release.set()


def _until_settled(client: TestClient, pull_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while True:
        body: dict[str, object] = client.get(
            f"/v1/setup/pulls/{pull_id}", headers=AUTH
        ).json()
        if body["state"] in ("finished", "failed"):
            return body
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_a_pull_is_started_polled_and_finished(client: TestClient, gate: _Gate) -> None:
    started = client.post("/v1/setup/pulls", json={"model": "bge-m3"}, headers=AUTH)

    assert started.status_code == 202
    body = started.json()
    assert started.headers["location"] == f"/v1/setup/pulls/{body['pull_id']}"
    assert body["state"] == "running"
    assert gate.models == ["bge-m3"]
    polled = client.get(f"/v1/setup/pulls/{body['pull_id']}", headers=AUTH).json()
    assert (polled["phase"], polled["completed_bytes"], polled["total_bytes"]) == (
        "downloading",
        3,
        9,
    )

    gate.release.set()
    done = _until_settled(client, body["pull_id"])

    assert done["outcome"] == {
        "model": "bge-m3",
        "status": "completed",
        "completed_bytes": 9,
        "total_bytes": 9,
    }


def test_a_pull_is_stopped(client: TestClient, gate: _Gate) -> None:
    pull_id = client.post(
        "/v1/setup/pulls", json={"model": "bge-m3"}, headers=AUTH
    ).json()["pull_id"]

    stopping = client.post(f"/v1/setup/pulls/{pull_id}/stop", headers=AUTH)
    gate.release.set()
    done = _until_settled(client, pull_id)

    assert stopping.json()["state"] == "stopping"
    assert isinstance(done["outcome"], dict)
    assert done["outcome"]["status"] == "stopped"


def test_a_second_pull_is_409_pull_active(client: TestClient, gate: _Gate) -> None:
    client.post("/v1/setup/pulls", json={"model": "bge-m3"}, headers=AUTH)

    again = client.post("/v1/setup/pulls", json={"model": "qwen3:8b"}, headers=AUTH)

    assert again.status_code == 409
    assert again.json() == {
        "code": "pull_active",
        "retryable": True,
        "message": "a model download is already active",
    }


def test_an_unconfigured_model_is_422(client: TestClient, gate: _Gate) -> None:
    response = client.post("/v1/setup/pulls", json={"model": "unknown:1"}, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "model_not_configured"


@pytest.mark.parametrize("body", [{}, {"model": ""}, {"model": "m", "extra": 1}])
def test_an_invalid_pull_body_is_rejected_before_the_operation(
    client: TestClient, gate: _Gate, body: dict[str, object]
) -> None:
    response = client.post("/v1/setup/pulls", json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert gate.models == []


def test_an_unknown_pull_is_404(client: TestClient) -> None:
    assert client.get("/v1/setup/pulls/nope", headers=AUTH).status_code == 404
    assert client.post("/v1/setup/pulls/nope/stop", headers=AUTH).status_code == 404
