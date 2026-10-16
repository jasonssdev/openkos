"""Preview, apply and refresh over HTTP: request validation, the echoed version,
the five-second lock wait, and the real two-decision workspace.

The stub tests patch `openkos.api.<fn>` and read the argument it received, so
each stub also proves it was called. The real tests run the whole service in a
git repository whose identity is pinned through `GIT_CONFIG_COUNT`.
"""

import json
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api, lock
from openkos.llm.base import BackendHostLocality, Message
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app, openapi_document
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.application.curation_support import write_concept
from tests.unit.localapi.conftest import AUTH, PORT

PAIR = ("concepts/a", "concepts/b")
KEY = "identity:abc123"
VERSION = api.DecisionVersion(KEY, "payload-digest-1", "content-digest-1")
VERSION_JSON = {
    "decision_key": KEY,
    "payload_digest": "payload-digest-1",
    "content_digest": "content-digest-1",
}


def _make_client(settings: ServerSettings) -> TestClient:
    return TestClient(
        create_app(settings),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    )


@pytest.fixture
def client(settings: ServerSettings) -> Iterator[TestClient]:
    with _make_client(settings) as c:
        yield c


class Recorder:
    """What a stubbed `openkos.api` operation was called with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    result: Any,
    *,
    warn: Sequence[str] = (),
) -> Recorder:
    recorder = Recorder()

    def stub(*args: Any, **kwargs: Any) -> Any:
        recorder.calls.append({"args": args, "kwargs": kwargs})
        callback = kwargs.get("on_warning")
        for message in warn:
            assert callback is not None
            callback(message)
        return result

    monkeypatch.setattr(api, name, stub)
    return recorder


def _applied(**kw: Any) -> api.Applied:
    return api.Applied(KEY, api.Action.MERGE, "abc1234", **kw)


def _preview_result() -> api.DecisionPreview:
    return api.DecisionPreview(
        KEY, api.Action.MERGE, api.KeepDistinctPlan(PAIR), VERSION
    )


# -- request validation (S3.1) --------------------------------------------------- #

_APPLY_OK = {"action": "merge", "version": VERSION_JSON}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (f"/v1/decisions/{KEY}/preview", {"action": "merge", "extra": 1}),
        (f"/v1/decisions/{KEY}/preview", {"action": "obliterate"}),
        (f"/v1/decisions/{KEY}/preview", {}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "extra": 1}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "action": "obliterate"}),
        (
            f"/v1/decisions/{KEY}/apply",
            {"action": "merge", "version": {"decision_key": KEY}},
        ),
        (f"/v1/decisions/{KEY}/apply", {"action": "merge", "version": "token"}),
        (f"/v1/decisions/{KEY}/apply", {"action": "merge"}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "wait_seconds": 5.5}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "wait_seconds": -0.5}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "wait_seconds": 3600}),
        (f"/v1/decisions/{KEY}/apply", {**_APPLY_OK, "wait_seconds": "soon"}),
        ("/v1/queue/refresh", {"extra": 1}),
        ("/v1/queue/refresh", {"wait_seconds": 5.5}),
        ("/v1/queue/refresh", {"wait_seconds": -1}),
        ("/v1/queue/refresh", {"wait_seconds": 3600}),
    ],
)
def test_an_invalid_body_is_422_and_the_api_is_never_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: Any
) -> None:
    calls = [
        _stub(monkeypatch, "preview_decision", _preview_result()),
        _stub(monkeypatch, "apply_decision", _applied()),
        _stub(monkeypatch, "refresh_decision_queue", None),
    ]

    response = client.post(path, json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert [c.calls for c in calls] == [[], [], []]


@pytest.mark.parametrize("wait", [0, 0.5, 5, 5.0])
def test_a_wait_inside_the_range_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, wait: float
) -> None:
    recorder = _stub(monkeypatch, "apply_decision", _applied())

    response = client.post(
        f"/v1/decisions/{KEY}/apply",
        json={**_APPLY_OK, "wait_seconds": wait},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert recorder.calls[0]["kwargs"]["wait_seconds"] == float(wait)


def test_the_refresh_body_may_be_omitted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _stub(monkeypatch, "refresh_decision_queue", _refresh_result())

    bare = client.post("/v1/queue/refresh", headers=AUTH)
    empty = client.post("/v1/queue/refresh", json={}, headers=AUTH)

    assert (bare.status_code, empty.status_code) == (200, 200)
    assert [c["kwargs"]["wait_seconds"] for c in recorder.calls] == [5.0, 5.0]


def test_the_ceiling_caps_the_wait_a_client_did_not_shorten(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    applied = _stub(monkeypatch, "apply_decision", _applied())
    refreshed = _stub(monkeypatch, "refresh_decision_queue", _refresh_result())
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 1.5)

    client.post(f"/v1/decisions/{KEY}/apply", json=_APPLY_OK, headers=AUTH)
    client.post("/v1/queue/refresh", headers=AUTH)
    client.post(
        f"/v1/decisions/{KEY}/apply",
        json={**_APPLY_OK, "wait_seconds": 0.25},
        headers=AUTH,
    )

    assert applied.calls[0]["kwargs"]["wait_seconds"] == 1.5
    assert refreshed.calls[0]["kwargs"]["wait_seconds"] == 1.5
    assert applied.calls[1]["kwargs"]["wait_seconds"] == 0.25


def _refresh_result() -> api.QueueRefresh:
    def kind(name: Any, inserted: int) -> api.KindRefresh:
        return api.KindRefresh(name, inserted, 1, 2, 3, 4, 5, True)

    return api.QueueRefresh(
        identity=kind("identity", 7),
        relation_type=kind("relation_type", 8),
        volatility=api.KindRefresh("volatility", 9, 1, 2, 3, 4, 5, False, 2),
        identity_truncation_notice="truncated",
    )


# -- the echo and the mapping (S3.2) ------------------------------------------- #


def test_a_preview_serves_the_plan_and_its_version_and_the_action_reaches_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, workspace: api.Workspace
) -> None:
    recorder = _stub(monkeypatch, "preview_decision", _preview_result())

    response = client.post(
        f"/v1/decisions/{KEY}/preview", json={"action": "merge"}, headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == VERSION_JSON
    assert body["plan"] == {"member_ids": list(PAIR)}
    assert body["action"] == "merge"
    args = recorder.calls[0]["args"]
    assert args[0].root == workspace.root
    assert args[1:] == (KEY, api.Action.MERGE)


def test_the_echoed_version_reaches_apply_unchanged_with_the_default_wait(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _stub(monkeypatch, "apply_decision", _applied())

    response = client.post(f"/v1/decisions/{KEY}/apply", json=_APPLY_OK, headers=AUTH)

    assert response.status_code == 200
    call = recorder.calls[0]
    assert call["args"][1] == VERSION
    assert call["args"][2] is api.Action.MERGE
    assert call["kwargs"]["wait_seconds"] == 5.0
    assert response.json() == {
        "applied": {
            "decision_key": KEY,
            "action": "merge",
            "commit_sha": "abc1234",
            "committed_confidential": False,
        },
        "warnings": [],
    }


def test_apply_warnings_reach_the_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(
        monkeypatch,
        "apply_decision",
        api.Applied(KEY, api.Action.MERGE, None),
        warn=("not committed: no git", "second"),
    )

    response = client.post(f"/v1/decisions/{KEY}/apply", json=_APPLY_OK, headers=AUTH)

    assert response.json()["warnings"] == ["not committed: no git", "second"]
    assert response.json()["applied"]["commit_sha"] is None


def test_committed_confidential_passes_through_the_apply_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "apply_decision", _applied(committed_confidential=True))

    response = client.post(f"/v1/decisions/{KEY}/apply", json=_APPLY_OK, headers=AUTH)

    assert response.json()["applied"]["committed_confidential"] is True


def test_a_path_key_that_differs_from_the_version_key_is_422_key_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _stub(monkeypatch, "apply_decision", _applied())

    response = client.post(
        "/v1/decisions/identity:other/apply", json=_APPLY_OK, headers=AUTH
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "key_mismatch",
        "retryable": False,
        "message": "the path key differs from the version's decision key",
    }
    assert recorder.calls == []


def test_a_refresh_passes_the_default_wait_and_serves_the_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _refresh_result()
    recorder = _stub(monkeypatch, "refresh_decision_queue", expected)

    response = client.post("/v1/queue/refresh", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert recorder.calls[0]["kwargs"] == {"wait_seconds": 5.0}
    assert body["identity"]["inserted"] == 7
    assert body["volatility"]["ambiguous"] == 2
    assert body["identity_truncation_notice"] == "truncated"
    assert TypeAdapter(api.QueueRefresh).validate_python(body) == expected


# -- a real two-decision workspace (S3.6) -------------------------------------- #


def _pin_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "OpenKOS Test")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "test@openkos.invalid")


@pytest.fixture
def root(
    settings: ServerSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Concepts a, b and c committed, an identity merge and a tier decision open."""
    _pin_identity(monkeypatch, tmp_path)
    root = settings.workspace.root
    write_concept(root, "concepts/a", title="A")
    write_concept(root, "concepts/b", title="B")
    write_concept(root, "concepts/c", title="C", body="See [B](/concepts/b.md).")
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    support.enqueue_identity(root, PAIR, verdict="same", fresh=True)
    support.enqueue_volatility(root, "Person", "static")
    support.forbid_model_clients(monkeypatch, "a preview or an apply")
    return root


def _identity_key(client: TestClient) -> str:
    listed = client.get("/v1/decisions", headers=AUTH).json()["decisions"]
    return str(next(d["decision_key"] for d in listed if d["kind"] == "identity"))


def _preview(client: TestClient, key: str, action: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/decisions/{key}/preview", json={"action": action}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _apply(client: TestClient, key: str, body: dict[str, Any]) -> Any:
    return client.post(f"/v1/decisions/{key}/apply", json=body, headers=AUTH)


def test_preview_then_apply_one_of_two_decisions_leaves_the_other_open(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)
    before = support.snapshot(root)

    preview = _preview(client, key, "merge")

    assert support.snapshot(root) == before  # a preview writes nothing
    assert preview["plan"]["survivor_id"] == "concepts/a"
    response = _apply(client, key, {"action": "merge", "version": preview["version"]})

    assert response.status_code == 200, response.text
    applied = response.json()["applied"]
    assert applied["decision_key"] == key
    assert applied["commit_sha"] is not None
    assert applied["committed_confidential"] is False
    remaining = client.get("/v1/decisions", headers=AUTH).json()["decisions"]
    assert [d["kind"] for d in remaining] == ["volatility"]
    assert not (root / "bundle" / "concepts" / "b.md").exists()


def test_a_merge_that_commits_a_confidential_concept_says_so(
    client: TestClient, root: Path
) -> None:
    path = root / "bundle" / "concepts" / "a.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "type: Concept\n", "type: Concept\nsensitivity: confidential\n", 1
        ),
        encoding="utf-8",
    )
    vcs_git.commit_paths(root, ["bundle/concepts/a.md"], "mark confidential")
    support.enqueue_identity(root, PAIR, verdict="same", fresh=True)  # re-record inputs
    key = _identity_key(client)
    version = _preview(client, key, "merge")["version"]

    response = _apply(client, key, {"action": "merge", "version": version})

    assert response.status_code == 200, response.text
    assert response.json()["applied"]["commit_sha"] is not None
    assert response.json()["applied"]["committed_confidential"] is True


def test_an_altered_version_is_409_stale_and_the_bundle_is_unchanged(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)
    version = _preview(client, key, "merge")["version"]
    altered = {**version, "content_digest": "0" * 64}
    before = support.snapshot(root)

    response = _apply(client, key, {"action": "merge", "version": altered})

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert response.json()["retryable"] is False
    assert response.json()["message"].endswith("preview it again")
    assert support.snapshot(root) == before


def test_a_decision_edited_after_the_preview_is_409_stale(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)
    version = _preview(client, key, "merge")["version"]
    member = root / "bundle" / "concepts" / "a.md"
    member.write_text(member.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")
    before = support.snapshot(root)

    response = _apply(client, key, {"action": "merge", "version": version})

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert support.snapshot(root) == before


def test_a_new_server_instance_applies_a_version_issued_by_another(
    settings: ServerSettings, root: Path
) -> None:
    with _make_client(settings) as first:
        key = _identity_key(first)
        version = _preview(first, key, "merge")["version"]
    with _make_client(settings) as second:
        response = _apply(second, key, {"action": "merge", "version": version})

    assert response.status_code == 200, response.text
    assert not (root / "bundle" / "concepts" / "b.md").exists()


def test_a_skip_posted_to_apply_is_422_skip_not_applicable(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)
    version = _preview(client, key, "merge")["version"]
    before = support.snapshot(root)

    response = _apply(client, key, {"action": "skip", "version": version})

    assert response.status_code == 422
    assert response.json()["code"] == "skip_not_applicable"
    assert support.snapshot(root) == before


def test_previewing_an_action_the_decision_does_not_offer_is_422(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)

    response = client.post(
        f"/v1/decisions/{key}/preview", json={"action": "set_tier"}, headers=AUTH
    )

    assert response.status_code == 422
    assert response.json()["code"] == "action_not_offered"


def test_previewing_a_key_that_never_existed_is_409_stale(
    client: TestClient, root: Path
) -> None:
    response = client.post(
        "/v1/decisions/identity:gone/preview", json={"action": "merge"}, headers=AUTH
    )

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert "no open decision" in response.json()["message"]


def test_an_unknown_action_is_rejected_before_any_operation_ran(
    client: TestClient, root: Path
) -> None:
    key = _identity_key(client)
    version = _preview(client, key, "merge")["version"]
    before = support.snapshot(root)

    response = _apply(client, key, {"action": "obliterate", "version": version})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert support.snapshot(root) == before


# -- the lock wait (S3.7) ------------------------------------------------------ #


class _Holder:
    """Holds the real workspace lock on its own thread until released."""

    def __init__(self, root: Path) -> None:
        self._release = threading.Event()
        self._held = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(root,), daemon=True)
        self._thread.start()
        assert self._held.wait(timeout=10)

    def _run(self, root: Path) -> None:
        with lock.workspace_lock(root):
            self._held.set()
            self._release.wait(timeout=30)

    def release(self) -> None:
        self._release.set()
        self._thread.join(timeout=10)


@pytest.fixture
def holder(root: Path) -> Iterator[_Holder]:
    held = _Holder(root)
    yield held
    held.release()


def _version_for_merge(client: TestClient) -> tuple[str, dict[str, Any]]:
    key = _identity_key(client)
    return key, _preview(client, key, "merge")["version"]


def test_apply_on_a_held_lock_is_503_busy_with_retry_after_and_writes_nothing(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, version = _version_for_merge(client)
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 0.1)
    before = support.snapshot(root)
    held = _Holder(root)
    try:
        response = _apply(client, key, {"action": "merge", "version": version})
        reads = client.get("/v1/decisions", headers=AUTH)
    finally:
        held.release()

    assert response.status_code == 503
    assert response.json() == {
        "code": "workspace_busy",
        "retryable": True,
        "message": "the workspace is busy; try again",
    }
    assert response.headers["retry-after"] == "2"
    assert int(response.headers["retry-after"]) > 0
    assert reads.status_code == 200  # reads ignore the lock
    assert support.snapshot(root) == before


def test_a_lock_released_within_the_wait_lets_the_apply_through(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, version = _version_for_merge(client)
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 3.0)
    held = _Holder(root)
    timer = threading.Timer(0.3, held.release)
    timer.start()
    try:
        response = _apply(client, key, {"action": "merge", "version": version})
    finally:
        timer.join(timeout=10)
        held.release()

    assert response.status_code == 200, response.text
    assert not (root / "bundle" / "concepts" / "b.md").exists()


def test_wait_seconds_zero_answers_503_at_once(
    client: TestClient, root: Path, holder: _Holder
) -> None:
    key, version = _version_for_merge(client)

    response = _apply(
        client, key, {"action": "merge", "version": version, "wait_seconds": 0}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "workspace_busy"


class _Judge:
    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        return json.dumps({"verdict": "same", "confidence": 0.9, "rationale": "Same."})


def _judged_workspace(root: Path) -> None:
    """Persist identity verdicts the refresh can publish as rows."""
    support.write_identity_groups(root)
    workspace = api.Workspace.open(root)
    quote = api.quote_judging(workspace, "identity", chat_client=lambda task: _Judge())
    api.run_judging(workspace, quote.token, chat_client=lambda task: _Judge())


def test_refresh_serves_the_result_and_on_a_held_lock_is_503_busy(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _judged_workspace(root)
    monkeypatch.setattr(_errors, "WRITE_WAIT_SECONDS", 0.1)
    before = support.snapshot(root)
    held = _Holder(root)
    try:
        busy = client.post("/v1/queue/refresh", headers=AUTH)
    finally:
        held.release()

    assert busy.status_code == 503
    assert busy.json()["code"] == "workspace_busy"
    assert busy.headers["retry-after"] == "2"
    assert support.snapshot(root) == before

    ok = client.post("/v1/queue/refresh", headers=AUTH)

    assert ok.status_code == 200, ok.text
    assert ok.json()["identity"]["inserted"] == 3


# -- the published contract (S3.8) --------------------------------------------- #


def test_the_snapshot_publishes_the_write_types_and_the_wait_bounds() -> None:
    schemas = openapi_document()["components"]["schemas"]  # type: ignore[index]

    for name in ("ApplyRequest", "ApplyResponse", "DecisionVersion", "Action"):
        assert name in schemas
    wait = schemas["ApplyRequest"]["properties"]["wait_seconds"]
    assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 5, 5)
    refresh = schemas["RefreshRequest"]["properties"]["wait_seconds"]
    assert (refresh["minimum"], refresh["maximum"]) == (0, 5)
    applied = schemas["Applied"]
    assert "committed_confidential" in applied["properties"]
