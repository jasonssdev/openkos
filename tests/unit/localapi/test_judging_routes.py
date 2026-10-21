"""The five judging routes over HTTP: quote, start, poll, stop and the backend
rows of the error table.

The run tests patch `openkos.api.run_judging` with a stub gated on events, so
every interleaving is forced by an event and no test sleeps to win a race. The
quote tests run the real service in a real workspace with a model client whose
`chat` fails the test.
"""

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from openkos import api, config
from openkos.api import _wiring
from openkos.application import backends
from openkos.llm.base import BackendHostLocality
from openkos.localapi import _runs
from openkos.localapi._app import ServerSettings, create_app
from openkos.localapi._runs import RunRegistry
from tests.unit.api import support
from tests.unit.application.test_finding_recheck_contradiction import (
    _A,
    _B,
    Judge,
    _enqueue,
    _write,
)
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_runs_registry import WAIT, Stub, make_outcome

SENTINEL = "sentinel-value-9f3a"

TOKEN = api.QuoteToken(
    binding=api.QuoteBinding(
        kind="identity",
        model="m",
        backend="ollama",
        is_local=True,
        effective_confidential=False,
        rubric_digest="r1",
    ),
    include_confidential=False,
    include_uncertain=True,
    work=(
        api.WorkItem("k1", (("concepts/a", "d1"), ("concepts/b", "d2"))),
        api.WorkItem("k2", (("concepts/c", ""), ("concepts/d", "d4"))),
    ),
    calls=2,
)
TOKEN_JSON = TypeAdapter(api.QuoteToken).dump_python(TOKEN, mode="json")
START = {"token": TOKEN_JSON}


class RecordingStub(Stub):
    """The registry tests' stub, also keeping the token it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[api.QuoteToken | None] = []

    def __call__(
        self, workspace: api.Workspace, token: api.QuoteToken | None, **kwargs: Any
    ) -> Any:
        self.tokens.append(token)
        return super().__call__(workspace, token, **kwargs)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingStub]:
    fake = RecordingStub()
    monkeypatch.setattr(api, "run_judging", fake)
    yield fake
    fake.release.set()  # never leave a run thread parked


@pytest.fixture
def app(settings: ServerSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def registry(app: FastAPI) -> RunRegistry:
    registry: RunRegistry = app.state.registry
    return registry


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False
    ) as c:
        yield c


def start(client: TestClient, body: Any = START) -> Any:
    return client.post("/v1/judging/runs", json=body, headers=AUTH)


def poll(client: TestClient, run_id: str) -> Any:
    return client.get(f"/v1/judging/runs/{run_id}", headers=AUTH)


def settle(client: TestClient, stub: Stub, run_id: str) -> dict[str, Any]:
    """Let the stub return and wait until the run is no longer active."""
    stub.release.set()
    deadline = time.monotonic() + WAIT
    while True:
        body: dict[str, Any] = poll(client, run_id).json()
        if body["state"] not in ("running", "stopping"):
            return body
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)


# -- quote ------------------------------------------------------------------------ #


class _FailingChat:
    """A model client whose `chat` fails the test: a quote must never call it."""

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: object) -> str:
        pytest.fail("a quote called the model")


class _ClientFactory:
    """Stands in for the concrete client class the API's wiring binds."""

    def __init__(self) -> None:
        self.built = 0

    def __call__(self, **kwargs: object) -> _FailingChat:
        self.built += 1
        return _FailingChat()


@pytest.fixture
def real_workspace(
    workspace: api.Workspace, monkeypatch: pytest.MonkeyPatch
) -> api.Workspace:
    support.write_identity_groups(workspace.root)
    monkeypatch.setattr(_wiring, "OllamaClient", _ClientFactory())
    return workspace


def quote(client: TestClient, body: Any) -> Any:
    return client.post("/v1/judging/quote", json=body, headers=AUTH)


def test_a_quote_carries_the_full_token_and_makes_no_model_call(
    client: TestClient, real_workspace: api.Workspace
) -> None:
    response = quote(client, {"kind": "identity"})

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "identity"
    assert body["to_judge"] == 3
    token = body["token"]
    assert set(token) == {
        "binding",
        "include_confidential",
        "include_uncertain",
        "work",
        "calls",
    }
    assert token["calls"] == 3
    assert len(token["work"]) == 3
    digests = token["work"][0]["member_digests"]
    assert len(digests) == 2
    assert all(
        isinstance(pair, list)
        and len(pair) == 2
        and all(isinstance(part, str) for part in pair)
        for pair in digests
    )
    # `_FailingChat.chat` fails the test if it was reached; the client was built.
    assert _wiring.OllamaClient.built == 1  # type: ignore[attr-defined]


def test_a_quote_round_trips_through_its_python_type(
    client: TestClient, real_workspace: api.Workspace
) -> None:
    body = quote(client, {"kind": "identity"}).json()

    rebuilt = TypeAdapter(api.JudgingQuote).validate_python(body)

    assert rebuilt == api.quote_judging(real_workspace, "identity")
    assert rebuilt.token.work[0].member_digests[0][0].startswith("concepts/")


def test_the_quote_token_is_the_one_a_run_accepts(
    client: TestClient, real_workspace: api.Workspace, stub: RecordingStub
) -> None:
    issued = api.quote_judging(real_workspace, "identity").token
    body = quote(client, {"kind": "identity"}).json()

    assert start(client, {"token": body["token"]}).status_code == 202
    assert stub.tokens == [issued]


def _canned(**changes: Any) -> api.JudgingQuote:
    fields: dict[str, Any] = {
        "kind": "identity",
        "total": 1,
        "served": 0,
        "uncertain": 0,
        "to_judge": 1,
        "calls": 1,
        "binding": TOKEN.binding,
        "notices": (),
        "token": TOKEN,
    }
    fields.update(changes)
    return api.JudgingQuote(**fields)


@pytest.mark.parametrize("kind", ["identity", "relation_type", "volatility"])
def test_the_booleans_default_to_false_and_the_kind_is_forwarded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(workspace: api.Workspace, kind: str, **kwargs: Any) -> api.JudgingQuote:
        seen.append({"kind": kind, **kwargs})
        return _canned()

    monkeypatch.setattr(api, "quote_judging", fake)

    assert quote(client, {"kind": kind}).status_code == 200
    assert seen == [
        {"kind": kind, "include_confidential": False, "include_uncertain": False}
    ]


def test_the_booleans_are_forwarded_when_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(workspace: api.Workspace, kind: str, **kwargs: Any) -> api.JudgingQuote:
        seen.append(kwargs)
        return _canned()

    monkeypatch.setattr(api, "quote_judging", fake)

    body = {"kind": "identity", "include_confidential": True, "include_uncertain": True}
    assert quote(client, body).status_code == 200
    assert seen == [{"include_confidential": True, "include_uncertain": True}]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "everything"},
        {},
        {"kind": "identity", "include_confidential": "perhaps"},
        {"kind": "identity", "extra": 1},
        # A changed finding is re-checked by its decision: the key is required for
        # those kinds, must name a decision of the kind, and means nothing else.
        {"kind": "contradiction"},
        {"kind": "revision", "decision_key": ""},
        {"kind": "contradiction", "decision_key": "revision:abc"},
        {"kind": "revision", "decision_key": "identity:abc"},
        {"kind": "identity", "decision_key": "identity:abc"},
    ],
)
def test_an_invalid_quote_request_is_422_and_makes_no_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    called: list[object] = []
    monkeypatch.setattr(api, "quote_judging", lambda *a, **k: called.append(a))

    response = quote(client, body)

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "retryable": False,
        "message": "the request is not valid",
    }
    assert called == []


def test_a_finding_quote_goes_to_the_recheck_with_its_key_and_not_the_batch_quote(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def fake(
        workspace: api.Workspace, kind: str, decision_key: str, **kwargs: Any
    ) -> api.JudgingQuote:
        seen.append((kind, decision_key, kwargs))
        return _canned(kind=kind)

    def batch(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a finding quote took the batch quote")

    monkeypatch.setattr(api, "quote_finding_recheck", fake)
    monkeypatch.setattr(api, "quote_judging", batch)

    body = {"kind": "revision", "decision_key": "revision:abc"}
    assert quote(client, body).status_code == 200
    assert seen == [("revision", "revision:abc", {"include_confidential": False})]


def test_a_changed_contradiction_is_quoted_and_run_over_http(
    client: TestClient,
    workspace: api.Workspace,
    registry: RunRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = workspace.root
    _write(root, _A, "The date is May.", related=_B)
    _write(root, _B, "The date is May too.")
    key = _enqueue(root, (_A, _B))
    _write(root, _B, "The date is June.")  # the row's pages moved
    monkeypatch.setattr(_wiring, "OllamaClient", lambda **kw: Judge())

    response = quote(client, {"kind": "contradiction", "decision_key": key})

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["to_judge"], body["calls"]) == ("contradiction", 1, 1)
    assert body["token"]["work"][0]["work_key"] == key
    started = start(client, {"token": body["token"]})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + WAIT
    while poll(client, run_id).json()["state"] in ("running", "stopping"):
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)
    finished = poll(client, run_id).json()
    assert finished["kind"] == "contradiction"
    assert finished["state"] == "finished"
    assert finished["outcome"]["persisted"] == 1


_KEY = "sk-test-from-the-environment"


def test_the_insecure_key_advisory_reaches_the_client_without_the_key(
    client: TestClient,
    real_workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = real_workspace.root / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nbackend: openai-compatible\nbase_url: http://llm.example.test/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", _KEY)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", _ClientFactory())
    expected = backends.insecure_key_warning(config.read_config(real_workspace.root))

    response = quote(client, {"kind": "identity"})

    assert expected is not None
    notices = [n for n in response.json()["notices"] if n["code"] == "insecure_key"]
    assert notices == [
        {
            "code": "insecure_key",
            "count": None,
            "produced": None,
            "state": None,
            "model": None,
            "backend": None,
            "text": expected,
        }
    ]
    assert _KEY not in response.text


# -- start, poll and stop ---------------------------------------------------------- #


def test_start_answers_202_with_the_job_before_the_model_call_completes(
    client: TestClient, stub: RecordingStub
) -> None:
    response = start(client)

    assert response.status_code == 202
    body = response.json()
    assert response.headers["location"] == f"/v1/judging/runs/{body['run_id']}"
    assert body["state"] == "running"
    assert body["kind"] == "identity"
    assert set(body) == {
        "run_id",
        "kind",
        "state",
        "index",
        "total",
        "persisted",
        "outcome",
        "error",
    }
    assert not stub.release.is_set()  # the "model call" is still blocked
    assert stub.tokens == [TOKEN]  # the echoed token equals the issued value


def test_poll_is_200_for_a_running_run_and_follows_progress_to_the_outcome(
    client: TestClient, stub: RecordingStub
) -> None:
    run_id = start(client).json()["run_id"]
    assert stub.observer is not None
    stub.observer.progress(api.JudgingProgress(2, 2, "k2", "same"))
    stub.observer.chunk_persisted(2)

    running = poll(client, run_id)
    done = settle(client, stub, run_id)

    assert running.status_code == 200
    assert running.json()["state"] == "running"
    assert (running.json()["index"], running.json()["total"]) == (2, 2)
    assert done["state"] == "finished"
    assert (done["index"], done["total"], done["persisted"]) == (2, 2, 2)
    assert done["outcome"]["status"] == "completed"
    assert done["outcome"]["judged"] == 2
    assert done["error"] is None


def test_stop_returns_stopping_and_the_run_finishes_with_a_stopped_outcome(
    client: TestClient, stub: RecordingStub
) -> None:
    run_id = start(client).json()["run_id"]

    stopped = client.post(f"/v1/judging/runs/{run_id}/stop", headers=AUTH)
    after = settle(client, stub, run_id)

    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopping"
    assert poll(client, run_id).json()["state"] == "finished"
    assert after["outcome"]["status"] == "stopped"


def test_stopping_a_finished_run_returns_it_unchanged(
    client: TestClient, stub: RecordingStub
) -> None:
    run_id = start(client).json()["run_id"]
    finished = settle(client, stub, run_id)

    response = client.post(f"/v1/judging/runs/{run_id}/stop", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == finished


def test_a_run_that_raises_after_admission_is_a_failed_job_with_an_error(
    client: TestClient, stub: RecordingStub
) -> None:
    def explode() -> Any:
        raise api.BackendUnavailable(f"leaks {SENTINEL}")

    stub.after_admission = explode
    run_id = start(client).json()["run_id"]

    body = settle(client, stub, run_id)

    assert poll(client, run_id).status_code == 200
    assert body["state"] == "failed"
    assert body["outcome"] is None
    assert body["error"] == {
        "code": "backend_unavailable",
        "retryable": True,
        "message": "the configured model backend is unreachable",
    }


def test_a_second_start_while_active_is_409_run_active_and_the_first_is_unaffected(
    client: TestClient, stub: RecordingStub
) -> None:
    first = start(client).json()["run_id"]

    second = start(client)

    assert second.status_code == 409
    assert second.json() == {
        "code": "run_active",
        "retryable": True,
        "message": "a judging run is already active",
    }
    assert poll(client, first).json()["state"] == "running"
    assert stub.calls == 1


def test_a_new_run_can_start_after_the_first_finished(
    client: TestClient, stub: RecordingStub
) -> None:
    first = start(client).json()["run_id"]
    settle(client, stub, first)

    second = start(client)

    assert second.status_code == 202
    assert second.json()["run_id"] != first


@pytest.mark.parametrize("method", ["get", "post"])
def test_an_unknown_run_id_is_404_not_found(client: TestClient, method: str) -> None:
    path = "/v1/judging/runs/nope" + ("/stop" if method == "post" else "")

    response = client.request(method, path, headers=AUTH)

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "retryable": False,
        "message": "no such run",
    }


def test_the_seventeenth_finished_run_evicts_the_first(
    client: TestClient, stub: RecordingStub
) -> None:
    stub.release.set()
    ids = []
    for _ in range(17):
        run_id = start(client).json()["run_id"]
        settle(client, stub, run_id)
        ids.append(run_id)

    assert poll(client, ids[0]).status_code == 404
    assert [poll(client, run_id).status_code for run_id in ids[1:]] == [200] * 16


def test_status_and_decisions_are_served_while_a_run_is_blocked(
    client: TestClient, stub: RecordingStub
) -> None:
    run_id = start(client).json()["run_id"]

    status = client.get("/v1/status", headers=AUTH)
    decisions = client.get("/v1/decisions", headers=AUTH)

    assert status.status_code == 200
    assert decisions.status_code == 200
    assert decisions.json() == {"decisions": []}
    assert poll(client, run_id).json()["state"] == "running"


# -- token shapes ------------------------------------------------------------------ #


@pytest.mark.parametrize("body", [{}, {"token": None}])
def test_a_missing_or_null_token_is_422_run_refused_and_nothing_starts(
    client: TestClient, stub: RecordingStub, registry: RunRegistry, body: dict[str, Any]
) -> None:
    response = start(client, body)

    assert response.status_code == 422
    assert response.json() == {
        "code": "run_refused",
        "retryable": False,
        "message": "a run needs the token of a quote",
    }
    assert stub.calls == 0
    assert registry._runs == {}


@pytest.mark.parametrize(
    "body",
    [
        {"token": "not-a-token"},
        {"token": {"binding": {"kind": "identity"}}},
        {"token": {**TOKEN_JSON, "calls": "many"}},
        {**START, "extra": 1},
    ],
)
def test_a_malformed_token_is_422_invalid_request_and_nothing_starts(
    client: TestClient, stub: RecordingStub, registry: RunRegistry, body: dict[str, Any]
) -> None:
    response = start(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert stub.calls == 0
    assert registry._runs == {}


def test_a_stale_quote_is_409_quote_stale_and_no_job_exists(
    client: TestClient, stub: RecordingStub, registry: RunRegistry
) -> None:
    stub.before_admission = lambda: api.QuoteStale(
        kind="identity", changed=("model", "locality")
    )

    response = start(client)

    assert response.status_code == 409
    assert response.json() == {
        "code": "quote_stale",
        "retryable": False,
        "message": "the quote is stale (changed: model, locality); quote again",
    }
    assert stub.calls == 1
    assert registry._runs == {}


# -- the backend rows -------------------------------------------------------------- #

BACKEND_ROWS = [
    (api.BackendUnavailable, 503, "backend_unavailable", True),
    (api.BackendModelNotFound, 424, "backend_model_not_found", False),
    (api.BackendGenerationCapped, 502, "generation_capped", False),
    (api.BackendError, 502, "backend_error", False),
    (api.JudgingRefused, 503, "judging_refused", False),
]


@pytest.mark.parametrize(("kind", "status", "code", "retryable"), BACKEND_ROWS)
def test_a_raised_backend_error_before_admission_maps_to_its_row_and_never_echoes(
    client: TestClient,
    stub: RecordingStub,
    registry: RunRegistry,
    kind: type[Exception],
    status: int,
    code: str,
    retryable: bool,
) -> None:
    def explode() -> Any:
        raise kind(f"leaks {SENTINEL}")

    stub.before_admission = explode

    response = start(client)

    assert response.status_code == status
    body = response.json()
    assert (body["code"], body["retryable"]) == (code, retryable)
    assert SENTINEL not in response.text
    assert registry._runs == {}


@pytest.mark.parametrize(("kind", "status", "code", "retryable"), BACKEND_ROWS[:4])
def test_a_failure_in_a_completed_outcome_is_an_error_body_from_the_table(
    client: TestClient,
    stub: RecordingStub,
    kind: type[Exception],
    status: int,
    code: str,
    retryable: bool,
) -> None:
    stub.after_admission = lambda: make_outcome(
        status="failed", failure=kind(f"leaks {SENTINEL}"), failed_at=2
    )
    run_id = start(client).json()["run_id"]

    done = settle(client, stub, run_id)

    assert done["state"] == "finished"
    failure = done["outcome"]["failure"]
    assert (failure["code"], failure["retryable"]) == (code, retryable)
    assert set(failure) == {"code", "retryable", "message"}
    assert failure["message"]
    assert SENTINEL not in str(done)
    assert done["outcome"]["failed_at"] == 2


# -- shutdown ---------------------------------------------------------------------- #


def test_closing_the_app_with_a_blocked_run_returns_within_the_grace_and_sets_stop(
    app: FastAPI, stub: RecordingStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_runs, "SHUTDOWN_GRACE_SECONDS", 0.2)
    registry: RunRegistry = app.state.registry
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        run_id = start(c).json()["run_id"]
        thread = registry._runs[run_id].thread
        assert thread is not None
        began = time.monotonic()

    elapsed = time.monotonic() - began

    assert stub.stop is not None
    assert stub.stop.is_set()
    assert elapsed < WAIT / 2  # the grace, not the blocked "model call"
    assert thread.is_alive()  # abandoned, as a call in flight is
    stub.release.set()
    thread.join(WAIT)
    assert not thread.is_alive()
