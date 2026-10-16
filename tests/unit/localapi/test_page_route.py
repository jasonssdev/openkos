"""`GET /v1/pages` (`review-screen` change): a page read over the wire, an
unknown page answered `not_found`, and nothing written."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api import support
from tests.unit.cli.commit_phase_support import write_doc
from tests.unit.localapi.conftest import AUTH, PORT

PAGE = "people/marguerite-osei"
SOURCE = "sources/notes"


@pytest.fixture
def root(workspace: api.Workspace) -> Path:
    root = workspace.root
    write_doc(
        root,
        SOURCE,
        {
            "type": "Source",
            "title": "Notes",
            "resource": "raw/notes.md",
            "generated": {"by": "openkos/0.3", "at": "2026-10-12T09:00:00Z"},
        },
    )
    write_doc(
        root,
        PAGE,
        {"type": "Person", "title": "Marguerite Osei", "provenance": [SOURCE]},
        "Runs the allotment rota.\n",
    )
    return root


@pytest.fixture
def client(settings: ServerSettings, root: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def test_a_page_is_read(client: TestClient, root: Path) -> None:
    before = support.snapshot(root)

    response = client.get("/v1/pages", params={"concept_id": PAGE}, headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "concept_id": PAGE,
        "title": "Marguerite Osei",
        "okf_type": "Person",
        "excerpt": "Runs the allotment rota.",
        "sensitivity": "private",
        "sources": [
            {
                "source_id": SOURCE,
                "file_name": "notes.md",
                "added_at": "2026-10-12T09:00:00Z",
            }
        ],
    }
    assert TypeAdapter(api.Page).validate_python(body) == api.read_page(
        api.Workspace.open(root), PAGE
    )
    assert support.snapshot(root) == before


@pytest.mark.parametrize("concept_id", ["people/nobody", "../outside"])
def test_an_unknown_page_is_not_found(client: TestClient, concept_id: str) -> None:
    response = client.get("/v1/pages", params={"concept_id": concept_id}, headers=AUTH)

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "retryable": False,
        "message": "no such page",
    }
    assert concept_id not in response.text


def test_a_read_without_a_concept_id_is_invalid(client: TestClient) -> None:
    response = client.get("/v1/pages", headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_a_read_needs_the_token(client: TestClient) -> None:
    response = client.get("/v1/pages", params={"concept_id": PAGE})

    assert response.status_code == 401
