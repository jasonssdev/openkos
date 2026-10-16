"""Merge history and unmerge over HTTP (`unmerge-api` change, ADR-0064): the
three routes, the echoed version, the error rows, and real round trips over
merges made through the decision routes themselves.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app, openapi_document
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.application.curation_support import write_concept
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_decisions_write import _Holder, _pin_identity

A, B, C = "concepts/a", "concepts/b", "concepts/c"


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _identity_key(client: TestClient) -> str:
    listed = client.get("/v1/decisions", headers=AUTH).json()["decisions"]
    return str(listed[0]["decision_key"])


def _merge(client: TestClient, body: dict[str, Any]) -> None:
    key = _identity_key(client)
    preview = client.post(f"/v1/decisions/{key}/preview", json=body, headers=AUTH)
    assert preview.status_code == 200, preview.text
    applied = client.post(
        f"/v1/decisions/{key}/apply",
        json={"action": body["action"], "version": preview.json()["version"]},
        headers=AUTH,
    )
    assert applied.status_code == 200, applied.text


def _seed(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    members: tuple[str, ...],
) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    root = settings.workspace.root
    for concept_id in members:
        write_concept(root, concept_id, title=f"Title {concept_id}")
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    support.enqueue_identity(root, members, verdict="same", fresh=True)
    support.forbid_model_clients(monkeypatch, "an unmerge over HTTP")
    return root


@pytest.fixture
def pair(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> Path:
    """`a` absorbed `b` through the decision routes."""
    root = _seed(settings, tmp_path, monkeypatch, (A, B))
    _merge(client, {"action": "merge"})
    return root


@pytest.fixture
def group(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> Path:
    """`a` absorbed `b`, then `c`, through one group decision."""
    root = _seed(settings, tmp_path, monkeypatch, (A, B, C))
    _merge(
        client,
        {
            "action": "merge_group",
            "selection": {"survivor_id": A, "member_ids": [A, B, C]},
        },
    )
    return root


def _preview(client: TestClient, survivor: str, absorbed: str, **extra: Any) -> Any:
    return client.post(
        "/v1/unmerge/preview",
        json={"survivor_id": survivor, "absorbed_id": absorbed, **extra},
        headers=AUTH,
    )


def _apply(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post("/v1/unmerge/apply", json=body, headers=AUTH)


# -- history ------------------------------------------------------------------ #


def test_history_lists_a_groups_merges_newest_first(
    client: TestClient, group: Path
) -> None:
    before = support.snapshot(group)

    response = client.get("/v1/merges", params={"concept_id": A}, headers=AUTH)

    assert response.status_code == 200, response.text
    merges = response.json()["merges"]
    assert [(m["absorbed_id"], m["absorbed_title"]) for m in merges] == [
        (C, f"Title {C}"),
        (B, f"Title {B}"),
    ]
    assert TypeAdapter(list[api.MergeRecord]).validate_python(merges) == list(
        api.merge_history(api.Workspace.open(group), A)
    )
    assert support.snapshot(group) == before


def test_history_without_a_concept_id_is_invalid(client: TestClient) -> None:
    response = client.get("/v1/merges", headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


# -- preview and apply -------------------------------------------------------- #


def test_a_pair_is_split_again_over_http(client: TestClient, pair: Path) -> None:
    before = support.snapshot(pair)
    preview = _preview(client, A, B)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert support.snapshot(pair) == before
    assert [s["absorbed_id"] for s in body["steps"]] == [B]
    assert TypeAdapter(api.UnmergePreview).validate_python(body) == (
        api.preview_unmerge(api.Workspace.open(pair), A, B)
    )

    applied = _apply(client, {"version": body["version"]})

    assert applied.status_code == 200, applied.text
    unmerged = TypeAdapter(api.Unmerged).validate_python(applied.json()["unmerged"])
    assert unmerged.survivor_id == A
    assert unmerged.restored_ids == (B,)
    [sha] = unmerged.commit_shas
    assert sha is not None
    assert unmerged.committed_confidential is False
    assert applied.json()["warnings"] == []
    assert (pair / "bundle" / f"{B}.md").is_file()


def test_a_group_is_split_again_in_one_apply(client: TestClient, group: Path) -> None:
    body = _preview(client, A, B).json()
    assert [s["absorbed_id"] for s in body["steps"]] == [C, B]

    applied = _apply(client, {"version": body["version"]})

    assert applied.status_code == 200, applied.text
    assert applied.json()["unmerged"]["restored_ids"] == [C, B]


def test_a_flipped_echoed_flag_is_409_stale(client: TestClient, pair: Path) -> None:
    version = _preview(client, A, B).json()["version"]
    before = support.snapshot(pair)

    response = _apply(client, {"version": {**version, "discard_survivor_edits": True}})

    assert response.status_code == 409
    assert response.json() == {
        "code": "stale",
        "retryable": False,
        "message": "the concepts changed since the preview; preview it again",
    }
    assert support.snapshot(pair) == before


def test_an_absorbed_survivor_is_409_survivor_missing(
    client: TestClient, group: Path
) -> None:
    response = _preview(client, B, C)

    assert response.status_code == 409
    assert response.json()["code"] == "survivor_missing"
    assert response.json()["retryable"] is False


def test_an_id_never_merged_in_is_409_not_merged(
    client: TestClient, pair: Path
) -> None:
    response = _preview(client, A, C)

    assert response.status_code == 409
    assert response.json() == {
        "code": "not_merged",
        "retryable": False,
        "message": "that concept is not recorded as merged into this one",
    }


def test_an_edited_survivor_is_409_unless_the_preview_opts_in(
    client: TestClient, pair: Path
) -> None:
    path = pair / "bundle" / f"{A}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")

    refused = _preview(client, A, B)
    allowed = _preview(client, A, B, discard_survivor_edits=True)

    assert refused.status_code == 409
    assert refused.json() == {
        "code": "survivor_edited",
        "retryable": False,
        "message": (
            "the concept was edited since the merge; undoing it would discard the edit"
        ),
    }
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["survivor_edits_discarded"] is True
    assert allowed.json()["version"]["discard_survivor_edits"] is True


def test_a_replayed_version_is_409_not_merged(client: TestClient, pair: Path) -> None:
    version = _preview(client, A, B).json()["version"]
    assert _apply(client, {"version": version}).status_code == 200

    response = _apply(client, {"version": version})

    assert response.status_code == 409
    assert response.json()["code"] == "not_merged"


def test_an_invalid_id_passes_the_refusal_message(
    client: TestClient, pair: Path
) -> None:
    response = _preview(client, "../escape", B)

    assert response.status_code == 422
    assert response.json()["code"] == "write_refused"
    assert "openkos unmerge" in response.json()["message"]


def test_a_held_lock_is_503_busy_and_writes_nothing(
    client: TestClient, pair: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _preview(client, A, B).json()["version"]
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 0.1)
    before = support.snapshot(pair)
    held = _Holder(pair)
    try:
        response = _apply(client, {"version": version})
    finally:
        held.release()

    assert response.status_code == 503
    assert response.json()["code"] == "workspace_busy"
    assert response.headers["retry-after"] == "2"
    assert support.snapshot(pair) == before


# -- request validation and the wait ------------------------------------------ #

_VERSION = {"survivor_id": A, "absorbed_id": B, "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/unmerge/preview", {"survivor_id": A}),
        ("/v1/unmerge/preview", {"survivor_id": A, "absorbed_id": B, "extra": 1}),
        ("/v1/unmerge/preview", {"survivor_id": A, "absorbed_id": 3}),
        ("/v1/unmerge/apply", {"version": _VERSION, "extra": 1}),
        ("/v1/unmerge/apply", {"version": {"survivor_id": A}}),
        ("/v1/unmerge/apply", {}),
        ("/v1/unmerge/apply", {"version": _VERSION, "wait_seconds": 5.5}),
        ("/v1/unmerge/apply", {"version": _VERSION, "wait_seconds": -1}),
    ],
)
def test_an_invalid_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_unmerge", "apply_unmerge"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = client.post(path, json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_the_version_reaches_the_api_as_the_dataclass_and_the_wait_is_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, float]] = []

    def stub(
        workspace: api.Workspace, version: api.UnmergeVersion, **kw: Any
    ) -> api.Unmerged:
        seen.append((version, kw["wait_seconds"]))
        kw["on_warning"]("the unmerge was applied but not committed: x")
        return api.Unmerged(A, (B,), (None,))

    monkeypatch.setattr(api, "apply_unmerge", stub)

    default = _apply(client, {"version": _VERSION})
    shorter = _apply(client, {"version": _VERSION, "wait_seconds": 0.25})

    assert default.status_code == 200, default.text
    assert default.json()["warnings"] == [
        "the unmerge was applied but not committed: x"
    ]
    assert seen == [
        (api.UnmergeVersion(A, B, "d" * 64), _errors.WRITE_WAIT_SECONDS),
        (api.UnmergeVersion(A, B, "d" * 64), 0.25),
    ]
    assert shorter.status_code == 200


def test_an_interrupted_unwind_is_500_unmerge_interrupted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stub(*args: Any, **kwargs: Any) -> None:
        raise api.UnmergeInterrupted(A, (C,), ("abc1234",), B, "boom /secret/path")

    monkeypatch.setattr(api, "apply_unmerge", stub)

    response = _apply(client, {"version": _VERSION})

    assert response.status_code == 500
    assert response.json() == {
        "code": "unmerge_interrupted",
        "retryable": False,
        "message": "the unmerge stopped part way; the completed steps are kept",
    }


def test_the_contract_has_one_unmerge_version_component() -> None:
    schemas = openapi_document()["components"]["schemas"]  # type: ignore[index]

    assert "UnmergeVersion" in schemas
    assert not any(name.startswith("UnmergeVersion-") for name in schemas)


def test_the_ceiling_caps_the_wait_a_client_did_not_shorten(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    def stub(*args: Any, **kwargs: Any) -> api.Unmerged:
        waits.append(kwargs["wait_seconds"])
        return api.Unmerged(A, (B,), (None,))

    monkeypatch.setattr(api, "apply_unmerge", stub)
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 1.5)

    _apply(client, {"version": _VERSION})

    assert waits == [1.5]
