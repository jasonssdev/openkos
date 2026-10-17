"""The setup routes: readiness round-trips, and a failure maps to the table."""

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
