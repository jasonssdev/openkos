"""Who can see a page over HTTP (`page-sensitivity` change, #1517): the two
routes, the echoed version, the error rows and a real round trip."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _pin_identity
from tests.unit.cli.commit_phase_support import metadata_of, write_doc
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_decisions_write import _Holder

PAGE = "concepts/garden"
SOURCE = "sources/notes"
CHILD = "concepts/from-notes"


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


@pytest.fixture
def root(
    settings: ServerSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    ws = settings.workspace.root
    write_doc(
        ws, PAGE, {"type": "Concept", "title": "The garden", "sensitivity": "private"}
    )
    write_doc(ws, SOURCE, {"type": "Source", "title": "Notes"})
    write_doc(
        ws,
        CHILD,
        {"type": "Concept", "title": "From the notes", "provenance": [SOURCE]},
    )
    vcs_git.commit_paths(ws, ["bundle"], "seed pages")
    support.forbid_model_clients(monkeypatch, "a sensitivity change over HTTP")
    return ws


def _post(client: TestClient, path: str, body: Any) -> Any:
    return client.post(path, json=body, headers=AUTH)


def _preview(client: TestClient, concept_id: str, level: str) -> Any:
    response = _post(
        client,
        "/v1/pages/sensitivity/preview",
        {"concept_id": concept_id, "level": level},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_preview_then_an_apply_sets_the_level(client: TestClient, root: Path) -> None:
    before = support.snapshot(root)
    preview = _preview(client, PAGE, "confidential")
    assert support.snapshot(root) == before
    assert preview["current"] == "private"
    assert preview["direction"] == "raise"
    assert preview["title"] == "The garden"
    assert preview["also_raised"] == []

    applied = _post(
        client, "/v1/pages/sensitivity/apply", {"version": preview["version"]}
    )

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["changed"]["level"] == "confidential"
    assert body["changed"]["previous"] == "private"
    assert body["changed"]["committed_confidential"] is True
    assert body["warnings"] == []
    assert metadata_of(root, PAGE)["sensitivity"] == "confidential"


def test_a_source_raise_names_the_pages_raised_with_it(
    client: TestClient, root: Path
) -> None:
    preview = _preview(client, SOURCE, "confidential")
    assert preview["also_raised"] == [
        {
            "concept_id": CHILD,
            "title": "From the notes",
            "before": "private",
            "after": "confidential",
        }
    ]
    applied = _post(
        client, "/v1/pages/sensitivity/apply", {"version": preview["version"]}
    )
    assert applied.json()["changed"]["also_raised"] == [CHILD]


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"concept_id": "concepts/missing", "level": "public"}, 404, "page_not_found"),
        ({"concept_id": PAGE, "level": "private"}, 409, "sensitivity_unchanged"),
        ({"concept_id": PAGE, "level": "secret"}, 422, "invalid_request"),
        (
            {"concept_id": PAGE, "level": "public", "extra": 1},
            422,
            "invalid_request",
        ),
    ],
)
def test_a_refused_preview_answers_its_row(
    client: TestClient, root: Path, body: dict[str, Any], status: int, code: str
) -> None:
    response = _post(client, "/v1/pages/sensitivity/preview", body)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_an_edited_page_is_stale(client: TestClient, root: Path) -> None:
    preview = _preview(client, PAGE, "confidential")
    page = root / "bundle" / f"{PAGE}.md"
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    response = _post(
        client, "/v1/pages/sensitivity/apply", {"version": preview["version"]}
    )
    assert response.status_code == 409
    assert response.json()["code"] == _errors.STALE
    assert metadata_of(root, PAGE)["sensitivity"] == "private"


def test_a_busy_workspace_answers_503_with_retry_after(
    client: TestClient, root: Path
) -> None:
    preview = _preview(client, PAGE, "confidential")
    held = _Holder(root)
    try:
        response = _post(
            client,
            "/v1/pages/sensitivity/apply",
            {"version": preview["version"], "wait_seconds": 0},
        )
    finally:
        held.release()
    assert response.status_code == 503
    assert response.json()["code"] == _errors.WORKSPACE_BUSY
    assert response.headers["Retry-After"]
    assert metadata_of(root, PAGE)["sensitivity"] == "private"


def _change_source(client: TestClient) -> None:
    preview = _preview(client, SOURCE, "confidential")
    applied = _post(
        client, "/v1/pages/sensitivity/apply", {"version": preview["version"]}
    )
    assert applied.status_code == 200, applied.text


def test_a_whole_change_is_undone_through_a_preview_then_an_apply(
    client: TestClient, root: Path
) -> None:
    _change_source(client)
    before = support.snapshot(root)
    preview = _post(
        client, "/v1/pages/sensitivity/undo/preview", {"concept_id": SOURCE}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["now"], body["restores"]) == ("confidential", "private")
    assert body["pages"] == [
        {
            "concept_id": CHILD,
            "title": "From the notes",
            "now": "confidential",
            "after": "private",
        }
    ]
    assert support.snapshot(root) == before

    applied = _post(
        client, "/v1/pages/sensitivity/undo/apply", {"version": body["version"]}
    )
    assert applied.status_code == 200, applied.text
    undone = applied.json()
    assert undone["undone"]["restored"] == [CHILD]
    assert undone["undone"]["kept"] == []
    assert undone["warnings"] == []
    assert metadata_of(root, SOURCE)["sensitivity"] == "private"
    assert metadata_of(root, CHILD)["sensitivity"] == "private"


@pytest.mark.parametrize(
    ("concept_id", "status", "code"),
    [
        ("concepts/missing", 404, "page_not_found"),
        (PAGE, 409, "sensitivity_no_record"),
    ],
)
def test_a_refused_undo_preview_answers_its_row(
    client: TestClient, root: Path, concept_id: str, status: int, code: str
) -> None:
    response = _post(
        client, "/v1/pages/sensitivity/undo/preview", {"concept_id": concept_id}
    )
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_a_page_changed_since_is_answered_in_plain_words(
    client: TestClient, root: Path
) -> None:
    _change_source(client)
    write_doc(
        root,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "public",
            "provenance": [SOURCE],
        },
    )
    response = _post(
        client, "/v1/pages/sensitivity/undo/preview", {"concept_id": SOURCE}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "sensitivity_changed_since"
    assert "changed" in response.json()["message"]


def test_an_edited_page_makes_the_undo_apply_stale(
    client: TestClient, root: Path
) -> None:
    _change_source(client)
    preview = _post(
        client, "/v1/pages/sensitivity/undo/preview", {"concept_id": SOURCE}
    ).json()
    page = root / "bundle" / f"{CHILD}.md"
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    response = _post(
        client, "/v1/pages/sensitivity/undo/apply", {"version": preview["version"]}
    )
    assert response.status_code == 409
    assert response.json()["code"] == _errors.STALE
    assert metadata_of(root, SOURCE)["sensitivity"] == "confidential"
