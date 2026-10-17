"""Undoing a review choice over HTTP (`review-undo` change, ADR-0067): the
routes, the echoed versions, the error rows, and real round trips over choices
applied through the decision routes themselves."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
from openkos.state import pending_queue as pq
from tests.unit.api import support
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_decisions_write import _Holder
from tests.unit.localapi.test_unmerge_routes import A, B, _identity_key, _seed


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    ) as c:
        yield c


def _decide(client: TestClient, action: str) -> None:
    key = _identity_key(client)
    preview = client.post(
        f"/v1/decisions/{key}/preview", json={"action": action}, headers=AUTH
    )
    assert preview.status_code == 200, preview.text
    applied = client.post(
        f"/v1/decisions/{key}/apply",
        json={"action": action, "version": preview.json()["version"]},
        headers=AUTH,
    )
    assert applied.status_code == 200, applied.text


@pytest.fixture
def kept(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> Path:
    """`a` and `b` kept separate through the decision routes."""
    root = _seed(settings, tmp_path, monkeypatch, (A, B))
    _decide(client, "keep_distinct")
    return root


def _post(client: TestClient, path: str, body: Any) -> Any:
    return client.post(path, json=body, headers=AUTH)


# -- keep separate ---------------------------------------------------------------- #


def test_kept_separate_pairs_are_listed_and_nothing_is_written(
    client: TestClient, kept: Path
) -> None:
    before = support.snapshot(kept)

    response = client.get("/v1/kept-distinct", headers=AUTH)

    assert response.status_code == 200, response.text
    (group,) = response.json()["groups"]
    assert group["member_ids"] == [A, B]
    assert group["decided_at"]
    assert support.snapshot(kept) == before


def test_a_kept_pair_is_reopened_over_http(client: TestClient, kept: Path) -> None:
    before = support.snapshot(kept)
    preview = _post(client, "/v1/reopen/preview", {"member_ids": [B, A]})
    assert preview.status_code == 200, preview.text
    assert support.snapshot(kept) == before
    assert TypeAdapter(api.ReopenPreview).validate_python(preview.json()) == (
        api.preview_reopen(api.Workspace.open(kept), (A, B))
    )

    applied = _post(client, "/v1/reopen/apply", {"version": preview.json()["version"]})

    assert applied.status_code == 200, applied.text
    reopened = TypeAdapter(api.Reopened).validate_python(applied.json()["reopened"])
    assert reopened.member_ids == (A, B)
    assert reopened.commit_sha is not None
    assert applied.json()["warnings"] == []
    assert client.get("/v1/kept-distinct", headers=AUTH).json() == {"groups": []}


def test_a_pair_never_kept_separate_is_409_not_kept_distinct(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    _seed(settings, tmp_path, monkeypatch, (A, B))

    response = _post(client, "/v1/reopen/preview", {"member_ids": [A, B]})

    assert response.status_code == 409
    assert response.json() == {
        "code": "not_kept_distinct",
        "retryable": False,
        "message": "these concepts are not recorded as kept distinct",
    }


def test_an_altered_version_is_409_stale(client: TestClient, kept: Path) -> None:
    version = _post(client, "/v1/reopen/preview", {"member_ids": [A, B]}).json()[
        "version"
    ]
    before = support.snapshot(kept)

    response = _post(
        client, "/v1/reopen/apply", {"version": {**version, "content_digest": "0" * 64}}
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "stale",
        "retryable": False,
        "message": "the concepts changed since the preview; preview it again",
    }
    assert support.snapshot(kept) == before


def test_a_held_lock_is_503_busy_and_writes_nothing(
    client: TestClient, kept: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _post(client, "/v1/reopen/preview", {"member_ids": [A, B]}).json()[
        "version"
    ]
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 0.1)
    before = support.snapshot(kept)
    held = _Holder(kept)
    try:
        response = _post(client, "/v1/reopen/apply", {"version": version})
    finally:
        held.release()

    assert response.status_code == 503
    assert response.json()["code"] == "workspace_busy"
    assert support.snapshot(kept) == before


# -- request validation and the wait ------------------------------------------- #

_REOPEN_VERSION = {"member_ids": [A, B], "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/reopen/preview", {}),
        ("/v1/reopen/preview", {"member_ids": [A, B], "extra": 1}),
        ("/v1/reopen/preview", {"member_ids": A}),
        ("/v1/reopen/apply", {"version": _REOPEN_VERSION, "extra": 1}),
        ("/v1/reopen/apply", {"version": {"member_ids": [A, B]}}),
        ("/v1/reopen/apply", {}),
        ("/v1/reopen/apply", {"version": _REOPEN_VERSION, "wait_seconds": 5.5}),
    ],
)
def test_an_invalid_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_reopen", "apply_reopen"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = _post(client, path, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_the_reopen_version_reaches_the_api_as_the_dataclass_and_the_wait_is_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, float]] = []

    def stub(
        workspace: api.Workspace, version: api.ReopenVersion, **kw: Any
    ) -> api.Reopened:
        seen.append((version, kw["wait_seconds"]))
        kw["on_warning"]("the undo was applied but not committed: x")
        return api.Reopened((A, B), None)

    monkeypatch.setattr(api, "apply_reopen", stub)

    default = _post(client, "/v1/reopen/apply", {"version": _REOPEN_VERSION})
    shorter = _post(
        client,
        "/v1/reopen/apply",
        {"version": _REOPEN_VERSION, "wait_seconds": 0.25},
    )

    assert default.status_code == 200, default.text
    assert default.json()["warnings"] == ["the undo was applied but not committed: x"]
    assert shorter.status_code == 200, shorter.text
    version = api.ReopenVersion((A, B), "d" * 64)
    assert seen == [(version, _errors.WRITE_WAIT_SECONDS), (version, 0.25)]


# -- a connection ------------------------------------------------------------------ #


@pytest.fixture
def related(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> Path:
    """`a` is a member of `b`, written through the decision routes."""
    root = _seed(settings, tmp_path, monkeypatch, (A, B))
    support.enqueue_relation(root, A, B, fresh=True)
    key = pq.stored_decision_key("relation_type", pq.relation_type_key(A, B))
    preview = _post(client, f"/v1/decisions/{key}/preview", {"action": "relate"})
    assert preview.status_code == 200, preview.text
    applied = _post(
        client,
        f"/v1/decisions/{key}/apply",
        {"action": "relate", "version": preview.json()["version"]},
    )
    assert applied.status_code == 200, applied.text
    return root


_EDGE = {"source_id": A, "relation_type": "member_of", "target_id": B}


def test_a_connection_is_removed_over_http(client: TestClient, related: Path) -> None:
    before = support.snapshot(related)
    preview = _post(client, "/v1/unrelate/preview", _EDGE)
    assert preview.status_code == 200, preview.text
    assert support.snapshot(related) == before
    assert TypeAdapter(api.UnrelatePreview).validate_python(preview.json()) == (
        api.preview_unrelate(api.Workspace.open(related), A, "member_of", B)
    )

    applied = _post(
        client, "/v1/unrelate/apply", {"version": preview.json()["version"]}
    )

    assert applied.status_code == 200, applied.text
    unrelated = TypeAdapter(api.Unrelated).validate_python(applied.json()["unrelated"])
    assert (unrelated.source_id, unrelated.relation_type, unrelated.target_id) == (
        A,
        "member_of",
        B,
    )
    assert unrelated.commit_sha is not None
    assert applied.json()["warnings"] == []


def test_an_absent_connection_is_409_not_related(
    client: TestClient, related: Path
) -> None:
    response = _post(client, "/v1/unrelate/preview", {**_EDGE, "source_id": B})

    assert response.status_code == 409
    assert response.json() == {
        "code": "not_related",
        "retryable": False,
        "message": (
            "the source holds no such typed relation, or a concept no longer exists"
        ),
    }


def test_an_altered_unrelate_version_is_409_stale(
    client: TestClient, related: Path
) -> None:
    version = _post(client, "/v1/unrelate/preview", _EDGE).json()["version"]
    before = support.snapshot(related)

    response = _post(
        client, "/v1/unrelate/apply", {"version": {**version, "content_digest": "0"}}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert support.snapshot(related) == before


_UNRELATE_VERSION = {**_EDGE, "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/unrelate/preview", {"source_id": A, "target_id": B}),
        ("/v1/unrelate/preview", {**_EDGE, "extra": 1}),
        ("/v1/unrelate/apply", {"version": _UNRELATE_VERSION, "extra": 1}),
        ("/v1/unrelate/apply", {"version": _EDGE}),
        ("/v1/unrelate/apply", {"version": _UNRELATE_VERSION, "wait_seconds": -1}),
    ],
)
def test_an_invalid_unrelate_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_unrelate", "apply_unrelate"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = _post(client, path, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_the_unrelate_version_reaches_the_api_and_the_wait_is_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, float]] = []

    def stub(
        workspace: api.Workspace, version: api.UnrelateVersion, **kw: Any
    ) -> api.Unrelated:
        seen.append((version, kw["wait_seconds"]))
        return api.Unrelated(A, "member_of", B, None)

    monkeypatch.setattr(api, "apply_unrelate", stub)

    response = _post(client, "/v1/unrelate/apply", {"version": _UNRELATE_VERSION})

    assert response.status_code == 200, response.text
    assert seen == [
        (api.UnrelateVersion(A, "member_of", B, "d" * 64), _errors.WRITE_WAIT_SECONDS)
    ]


# -- how quickly a type goes out of date ------------------------------------------- #


def test_a_tier_is_set_and_restored_over_http(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    root = _seed(settings, tmp_path, monkeypatch, (A, B))
    before = support.snapshot(root)
    preview = _post(
        client, "/v1/tiers/preview", {"type_name": "Person", "tier": "static"}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["configured_tier"] is None
    assert support.snapshot(root) == before

    applied = _post(client, "/v1/tiers/apply", {"version": preview.json()["version"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"]["tier"] == "static"

    restore = _post(client, "/v1/tiers/preview", {"type_name": "Person", "tier": None})
    assert restore.status_code == 200, restore.text
    assert restore.json()["configured_tier"] == "static"
    undone = _post(client, "/v1/tiers/apply", {"version": restore.json()["version"]})
    assert undone.status_code == 200, undone.text
    assert (
        TypeAdapter(api.TierChanged).validate_python(undone.json()["changed"]).tier
        is None
    )


def test_tier_refusals_are_answered_by_their_reason(
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    _seed(settings, tmp_path, monkeypatch, (A, B))

    unchanged = _post(
        client, "/v1/tiers/preview", {"type_name": "Person", "tier": None}
    )
    unknown = _post(client, "/v1/tiers/preview", {"type_name": "Ship", "tier": "slow"})

    assert (unchanged.status_code, unchanged.json()["code"]) == (409, "tier_unchanged")
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_type")


_TIER_VERSION = {"type_name": "Person", "tier": "slow", "content_digest": "d" * 64}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/tiers/preview", {"type_name": "Person", "tier": "sometimes"}),
        ("/v1/tiers/preview", {"type_name": "Person"}),
        ("/v1/tiers/preview", {"type_name": "Person", "tier": "slow", "extra": 1}),
        ("/v1/tiers/apply", {"version": {**_TIER_VERSION, "tier": "sometimes"}}),
        ("/v1/tiers/apply", {"version": _TIER_VERSION, "extra": 1}),
        ("/v1/tiers/apply", {"version": _TIER_VERSION, "wait_seconds": 6}),
    ],
)
def test_an_invalid_tier_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls: list[str] = []
    for name in ("preview_tier_change", "apply_tier_change"):
        monkeypatch.setattr(api, name, lambda *a, _n=name, **k: calls.append(_n))

    response = _post(client, path, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert calls == []


def test_the_tier_version_reaches_the_api_and_the_wait_is_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, float]] = []

    def stub(
        workspace: api.Workspace, version: api.TierChangeVersion, **kw: Any
    ) -> api.TierChanged:
        seen.append((version, kw["wait_seconds"]))
        return api.TierChanged("Person", "slow", None)

    monkeypatch.setattr(api, "apply_tier_change", stub)

    response = _post(client, "/v1/tiers/apply", {"version": _TIER_VERSION})

    assert response.status_code == 200, response.text
    assert seen == [
        (api.TierChangeVersion("Person", "slow", "d" * 64), _errors.WRITE_WAIT_SECONDS)
    ]
