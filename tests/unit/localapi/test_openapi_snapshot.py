"""The HTTP contract is pinned by a committed OpenAPI document."""

import json
import re
import socket
from pathlib import Path

import pytest

from openkos import api
from openkos.localapi._app import ServerSettings, create_app, openapi_document
from tests.unit.localapi import openapi_snapshot

_REGENERATE = "run: uv run python -m tests.unit.localapi.openapi_snapshot --write"


def _committed() -> str:
    assert openapi_snapshot.SNAPSHOT_PATH.exists(), _REGENERATE
    return openapi_snapshot.SNAPSHOT_PATH.read_text(encoding="utf-8")


def test_the_live_document_matches_the_committed_snapshot() -> None:
    assert _committed() == openapi_snapshot.dumps(openapi_document()), _REGENERATE


def test_the_running_app_serves_the_same_document(settings: ServerSettings) -> None:
    assert json.loads(_committed()) == create_app(settings).openapi()


def test_the_document_needs_no_workspace_and_opens_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("generating the contract opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.chdir(tmp_path)  # an empty directory: no workspace anywhere

    assert openapi_document()["openapi"]


def test_every_path_is_versioned() -> None:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)

    assert paths  # not vacuous
    assert all(path.startswith("/v1/") for path in paths)


def test_no_component_is_split_or_module_qualified() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)
    names = list(components["schemas"])

    assert names  # not vacuous
    assert not [n for n in names if n.endswith(("-Input", "-Output"))]
    assert not [n for n in names if "." in n or re.search(r"[a-z]__[A-Za-z]", n)]


def test_the_version_is_the_api_version_not_the_package_version() -> None:
    info = openapi_document()["info"]
    assert isinstance(info, dict)

    assert info["version"] == str(api.API_VERSION)


def test_a_bearer_security_scheme_is_published() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)

    assert components["securitySchemes"] == {
        "BearerToken": {"type": "http", "scheme": "bearer"}
    }


def test_the_uniform_error_body_is_described() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)

    assert set(components["schemas"]["ErrorBody"]["properties"]) == {
        "code",
        "retryable",
        "message",
    }
