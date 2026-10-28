"""The inbox watch folder over HTTP (ADR-0078): the read, the two-phase change,
the echoed version and the error rows."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from openkos import api, config
from openkos.application.inbox_settings import InboxSettingsRefusalReason
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.localapi.conftest import AUTH, PORT


@pytest.fixture
def client(
    settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    support.forbid_model_clients(monkeypatch, "the inbox settings routes")
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    return inbox


def _post(client: TestClient, path: str, body: Any) -> Any:
    return client.post(path, json=body, headers=AUTH)


def test_the_read_is_off_by_default_and_writes_nothing(
    client: TestClient, workspace: api.Workspace
) -> None:
    before = support.snapshot(workspace.root)

    response = client.get("/v1/settings/inbox", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["inbox"] is None
    assert response.json()["quiet_seconds"] == 30
    assert support.snapshot(workspace.root) == before


def test_a_preview_and_its_version_apply_the_change(
    client: TestClient, workspace: api.Workspace, folder: Path
) -> None:
    before = support.snapshot(workspace.root)
    preview = _post(client, "/v1/settings/inbox/preview", {"inbox": str(folder)})
    assert preview.status_code == 200, preview.text
    assert preview.json()["proposed"]["inbox"] == str(folder.resolve())
    assert support.snapshot(workspace.root) == before

    applied = _post(
        client, "/v1/settings/inbox/apply", {"version": preview.json()["version"]}
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"]["settings"]["inbox"] == str(folder.resolve())
    assert isinstance(applied.json()["warnings"], list)
    assert config.read_config(workspace.root).unattended.inbox == folder.resolve()


def test_a_null_inbox_asks_to_turn_the_watch_off(
    client: TestClient, workspace: api.Workspace, folder: Path
) -> None:
    on = _post(client, "/v1/settings/inbox/preview", {"inbox": str(folder)}).json()
    _post(client, "/v1/settings/inbox/apply", {"version": on["version"]})

    off = _post(client, "/v1/settings/inbox/preview", {"inbox": None})

    assert off.status_code == 200, off.text
    assert off.json()["proposed"]["inbox"] is None


def test_refusals_are_answered_by_their_reason(
    client: TestClient, workspace: api.Workspace, tmp_path: Path
) -> None:
    def refuse(value: str) -> tuple[int, str]:
        response = _post(client, "/v1/settings/inbox/preview", {"inbox": value})
        return response.status_code, response.json()["code"]

    assert refuse("  ") == (422, "invalid_inbox")
    assert refuse(str(workspace.root / "raw")) == (422, "inbox_inside_workspace")
    assert refuse(str(tmp_path / "nope")) == (422, "inbox_not_a_directory")


def test_turning_off_what_is_off_is_a_conflict(client: TestClient) -> None:
    response = _post(client, "/v1/settings/inbox/preview", {"inbox": None})

    assert (response.status_code, response.json()["code"]) == (
        409,
        _errors.INBOX_UNCHANGED,
    )


def test_a_stale_version_is_a_conflict(
    client: TestClient, workspace: api.Workspace, folder: Path
) -> None:
    preview = _post(client, "/v1/settings/inbox/preview", {"inbox": str(folder)}).json()
    yaml = workspace.root / "openkos.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8") + "# note\n", encoding="utf-8")

    response = _post(
        client, "/v1/settings/inbox/apply", {"version": preview["version"]}
    )

    assert (response.status_code, response.json()["code"]) == (409, _errors.STALE)


def test_the_apply_passes_the_echoed_version_and_the_capped_wait(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[api.InboxSettingsVersion, float]] = []
    settings = api.InboxSettings("/in", 30, 500)

    def fake(
        workspace: api.Workspace,
        version: api.InboxSettingsVersion,
        *,
        wait_seconds: float,
        on_warning: Any,
    ) -> api.InboxSettingsChanged:
        seen.append((version, wait_seconds))
        on_warning("not committed")
        return api.InboxSettingsChanged(settings=settings, commit_sha=None)

    monkeypatch.setattr(api, "apply_inbox_settings", fake)
    version = {"change": {"inbox": "/in"}, "content_digest": "d" * 64}

    response = _post(client, "/v1/settings/inbox/apply", {"version": version})

    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == ["not committed"]
    assert seen == [
        (
            api.InboxSettingsVersion(api.InboxSettingsChange("/in"), "d" * 64),
            _errors.WRITE_WAIT_SECONDS,
        )
    ]


_VERSION = {"change": {"inbox": "/in"}, "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/settings/inbox/preview", {}),
        ("/v1/settings/inbox/preview", {"inbox": "/in", "extra": 1}),
        ("/v1/settings/inbox/preview", {"inbox": "x" * 5000}),
        ("/v1/settings/inbox/apply", {"version": _VERSION, "extra": 1}),
        ("/v1/settings/inbox/apply", {"version": _VERSION, "wait_seconds": 6}),
    ],
)
def test_an_invalid_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_inbox_settings", "apply_inbox_settings"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = _post(client, path, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_every_refusal_reason_has_its_own_row() -> None:
    for reason in get_args(InboxSettingsRefusalReason):
        problem = _errors.outcome_problem(api.InboxSettingsRefused(reason))

        assert problem.body.code != _errors.INTERNAL_ERROR, reason
