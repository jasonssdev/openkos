"""The model settings over HTTP (`model-settings` change): the read, the
preview, the echoed version and the error rows."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openkos import api, config
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.localapi.conftest import AUTH, PORT

_REMOTE = {
    "backend": "openai-compatible",
    "base_url": "https://api.example.com/v1",
    "embedding_base_url": None,
    "model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small",
}


@pytest.fixture
def client(
    settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    support.forbid_model_clients(monkeypatch, "the settings routes")
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _post(client: TestClient, path: str, body: Any) -> Any:
    return client.post(path, json=body, headers=AUTH)


def test_the_read_answers_the_settings_and_writes_nothing(
    client: TestClient, workspace: api.Workspace
) -> None:
    before = support.snapshot(workspace.root)

    response = client.get("/v1/settings/model", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "ollama"
    assert (body["is_local"], body["remote_hosts"], body["takes_api_key"]) == (
        True,
        [],
        False,
    )
    assert support.snapshot(workspace.root) == before


def test_a_preview_and_its_version_apply_the_change(
    client: TestClient, workspace: api.Workspace
) -> None:
    before = support.snapshot(workspace.root)
    preview = _post(client, "/v1/settings/model/preview", _REMOTE)
    assert preview.status_code == 200, preview.text
    assert preview.json()["proposed"]["remote_hosts"] == ["api.example.com"]
    assert preview.json()["search_rebuild"] is True
    assert support.snapshot(workspace.root) == before

    applied = _post(
        client, "/v1/settings/model/apply", {"version": preview.json()["version"]}
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"]["settings"]["backend"] == "openai-compatible"
    assert isinstance(applied.json()["warnings"], list)
    cfg = config.read_config(workspace.root)
    assert (cfg.backend, cfg.base_url) == ("openai-compatible", _REMOTE["base_url"])


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({**_REMOTE, "base_url": "api.example.com"}, 422, "invalid_base_url"),
        (
            {**_REMOTE, "embedding_base_url": "ftp://x"},
            422,
            "invalid_embedding_base_url",
        ),
        ({**_REMOTE, "model": "a b"}, 422, "invalid_model"),
        ({**_REMOTE, "embedding_model": "yes"}, 422, "invalid_embedding_model"),
        ({**_REMOTE, "base_url": None}, 422, "base_url_required"),
    ],
)
def test_refusals_are_answered_by_their_reason(
    client: TestClient, body: Any, status: int, code: str
) -> None:
    response = _post(client, "/v1/settings/model/preview", body)

    assert (response.status_code, response.json()["code"]) == (status, code)


def test_unchanged_settings_are_a_conflict(
    client: TestClient, workspace: api.Workspace
) -> None:
    cfg = config.read_config(workspace.root)
    body = {
        "backend": "ollama",
        "base_url": None,
        "embedding_base_url": None,
        "model": cfg.model,
        "embedding_model": cfg.embedding_model,
    }

    response = _post(client, "/v1/settings/model/preview", body)

    assert (response.status_code, response.json()["code"]) == (
        409,
        _errors.SETTINGS_UNCHANGED,
    )


def test_a_stale_version_is_a_conflict(
    client: TestClient, workspace: api.Workspace
) -> None:
    preview = _post(client, "/v1/settings/model/preview", _REMOTE).json()
    yaml = workspace.root / "openkos.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8") + "# note\n", encoding="utf-8")

    response = _post(
        client, "/v1/settings/model/apply", {"version": preview["version"]}
    )

    assert (response.status_code, response.json()["code"]) == (409, _errors.STALE)


def test_the_apply_passes_the_echoed_version_and_the_capped_wait(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[api.ModelSettingsVersion, float]] = []
    settings = api.ModelSettings("ollama", None, None, "m", "e", (), True, False)

    def fake(
        workspace: api.Workspace,
        version: api.ModelSettingsVersion,
        *,
        wait_seconds: float,
        on_warning: Any,
    ) -> api.ModelSettingsChanged:
        seen.append((version, wait_seconds))
        on_warning("not committed")
        return api.ModelSettingsChanged(settings=settings, commit_sha=None)

    monkeypatch.setattr(api, "apply_model_settings", fake)
    version = {"change": {**_REMOTE}, "content_digest": "d" * 64}

    response = _post(client, "/v1/settings/model/apply", {"version": version})

    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == ["not committed"]
    assert seen == [
        (
            api.ModelSettingsVersion(
                api.ModelSettingsChange(
                    "openai-compatible",
                    "https://api.example.com/v1",
                    None,
                    "gpt-4o-mini",
                    "text-embedding-3-small",
                ),
                "d" * 64,
            ),
            _errors.WRITE_WAIT_SECONDS,
        )
    ]


_VERSION = {"change": _REMOTE, "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/settings/model/preview", {**_REMOTE, "backend": "cloud"}),
        ("/v1/settings/model/preview", {**_REMOTE, "api_key": "sk-x"}),
        ("/v1/settings/model/preview", {"backend": "ollama"}),
        ("/v1/settings/model/apply", {"version": _VERSION, "extra": 1}),
        ("/v1/settings/model/apply", {"version": _VERSION, "wait_seconds": 6}),
        (
            "/v1/settings/model/apply",
            {"version": {**_VERSION, "change": {**_REMOTE, "backend": "x"}}},
        ),
    ],
)
def test_an_invalid_settings_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_model_settings", "apply_model_settings"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = _post(client, path, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_every_refusal_reason_has_its_own_row() -> None:
    from typing import get_args

    from openkos.application.model_settings import ModelSettingsRefusalReason

    for reason in get_args(ModelSettingsRefusalReason):
        problem = _errors.outcome_problem(api.ModelSettingsRefused(reason))

        assert problem.body.code != _errors.INTERNAL_ERROR, reason
        assert problem.body.code in (reason, _errors.SETTINGS_UNCHANGED)
