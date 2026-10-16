"""The app factory: settings that hide the token, and the versioned prefix."""

from importlib import metadata

import pytest
from fastapi.testclient import TestClient

from openkos import api
from openkos.localapi import _routes
from openkos.localapi._app import ServerSettings, create_app
from openkos.localapi._guard import LocalApiGuard
from tests.unit.localapi.conftest import AUTH, PORT, TOKEN, TOKEN_DIGEST


def test_the_settings_repr_carries_neither_the_token_nor_its_digest(
    settings: ServerSettings,
) -> None:
    text = repr(settings)

    assert TOKEN not in text
    assert TOKEN_DIGEST.hex() not in text
    assert repr(TOKEN_DIGEST) not in text
    assert str(PORT) in text  # the other fields still show


def test_the_prefix_is_v1_today() -> None:
    assert api.API_VERSION == 1
    assert _routes.prefix() == "/v1"


def test_the_prefix_follows_the_api_version(
    settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "API_VERSION", 7)

    with TestClient(create_app(settings), base_url=f"http://127.0.0.1:{PORT}") as c:
        assert c.get("/v7/health", headers=AUTH).status_code == 200
        assert c.get("/v1/health", headers=AUTH).status_code == 404
        assert c.get("/v7/health", headers=AUTH).json()["api_version"] == 7


def test_health_reports_unknown_when_the_package_metadata_is_absent(
    settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def absent(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", absent)

    with TestClient(create_app(settings), base_url=f"http://127.0.0.1:{PORT}") as c:
        assert c.get("/v1/health", headers=AUTH).json()["engine_version"] == "unknown"


def test_the_docs_pages_are_not_mounted(settings: ServerSettings) -> None:
    app = create_app(settings)

    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)


def test_the_guard_is_the_only_middleware(settings: ServerSettings) -> None:
    app = create_app(settings)

    assert len(app.user_middleware) == 1
    installed: object = app.user_middleware[0].cls
    assert installed is LocalApiGuard
