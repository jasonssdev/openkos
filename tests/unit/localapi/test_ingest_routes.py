"""The ingest routes over HTTP: the quote, the job's start, poll and stop, the
one-active rule, stale and missing tokens, and a real run end to end.

Most tests patch `openkos.api.run_ingest` with a stub steered by events, so
every interleaving is forced by an event and no test sleeps to win a race. The
end-to-end tests run the real API in a real workspace with a scripted model.
"""

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openkos import api, lock
from openkos.localapi import _ingests
from openkos.localapi._app import ServerSettings, create_app
from tests.unit.api.test_ingest_api import install
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_runs_registry import WAIT

_NOTES = "Notas sobre la plataforma.\nLa plataforma registra el origen de cada dato.\n"


def make_token() -> api.IngestQuoteToken:
    return api.IngestQuoteToken(
        binding=api.IngestBinding(
            model="m",
            backend="ollama",
            is_local=True,
            include_confidential=False,
            settings_digest="d",
        ),
        files=(api.IngestTokenFile("/x/a.md", "0" * 64, 3),),
        calls=3,
    )


def make_outcome(**changes: Any) -> api.IngestRunOutcome:
    fields: dict[str, Any] = {
        "status": "completed",
        "files": (
            api.IngestFileResult(
                path="/x/a.md",
                name="a.md",
                status="added",
                source_id="sources/a",
                created=2,
                revised=0,
                extraction_degraded=False,
                calls=3,
            ),
        ),
        "calls": 3,
        "indexes": "refreshed",
    }
    fields.update(changes)
    return api.IngestRunOutcome(**fields)


class Stub:
    """A replacement for `api.run_ingest`: admits at its first stop check, then
    waits for `release`."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.result: api.IngestRunResult | None = None
        self.error: Exception | None = None

    def __call__(
        self, workspace: api.Workspace, token: api.IngestQuoteToken, **kwargs: Any
    ) -> api.IngestRunResult:
        self.calls.append({"token": token, **kwargs})
        if self.result is not None and not isinstance(
            self.result, api.IngestRunOutcome
        ):
            return self.result
        stop: api.StopToken = kwargs["stop"]
        stop.is_set()  # admission
        kwargs["progress"](api.IngestProgress(1, 1, "/x/a.md", "extracting", 1))
        kwargs["on_warning"]("the ingest was applied but not committed: x")
        self.entered.set()
        assert self.release.wait(WAIT)
        if self.error is not None:
            raise self.error
        status = "stopped" if stop.is_set() else "completed"
        return make_outcome(status=status)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    fake = Stub()
    monkeypatch.setattr(api, "run_ingest", fake)
    yield fake
    fake.release.set()


@pytest.fixture
def app(settings: ServerSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False
    ) as c:
        yield c


def _token_json(token: api.IngestQuoteToken) -> dict[str, Any]:
    return {
        "binding": {
            "model": token.binding.model,
            "backend": token.binding.backend,
            "is_local": token.binding.is_local,
            "include_confidential": token.binding.include_confidential,
            "settings_digest": token.binding.settings_digest,
        },
        "files": [
            {"path": f.path, "digest": f.digest, "calls": f.calls} for f in token.files
        ],
        "calls": token.calls,
    }


def start(client: TestClient, body: Any) -> Any:
    return client.post("/v1/ingest/runs", json=body, headers=AUTH)


def poll(client: TestClient, run_id: str) -> Any:
    return client.get(f"/v1/ingest/runs/{run_id}", headers=AUTH)


def settle(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT
    while True:
        body: dict[str, Any] = poll(client, run_id).json()
        if body["state"] not in ("running", "stopping"):
            return body
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)


# --- the job --------------------------------------------------------------------


def test_start_returns_at_once_and_the_poll_reaches_the_outcome(
    client: TestClient, stub: Stub
) -> None:
    response = start(client, {"token": _token_json(make_token())})

    assert response.status_code == 202
    job = response.json()
    assert job["state"] == "running"
    assert response.headers["Location"] == f"/v1/ingest/runs/{job['run_id']}"
    assert stub.entered.wait(WAIT)
    running = poll(client, job["run_id"]).json()
    assert (running["index"], running["total"], running["phase"], running["calls"]) == (
        1,
        1,
        "extracting",
        1,
    )

    stub.release.set()
    done = settle(client, job["run_id"])

    assert done["state"] == "finished"
    assert done["error"] is None
    assert done["outcome"]["status"] == "completed"
    assert done["outcome"]["files"][0]["source_id"] == "sources/a"
    assert done["warnings"] == ["the ingest was applied but not committed: x"]
    assert stub.calls[0]["token"] == make_token()
    assert stub.calls[0]["wait_seconds"] == 5.0


def test_a_second_start_while_active_is_refused(client: TestClient, stub: Stub) -> None:
    first = start(client, {"token": _token_json(make_token())}).json()
    assert stub.entered.wait(WAIT)

    second = start(client, {"token": _token_json(make_token())})

    assert second.status_code == 409
    assert second.json() == {
        "code": "ingest_active",
        "retryable": True,
        "message": "files are already being added",
    }
    assert len(stub.calls) == 1
    stub.release.set()
    assert settle(client, first["run_id"])["state"] == "finished"
    assert start(client, {"token": _token_json(make_token())}).status_code == 202


def test_stop_is_cooperative(client: TestClient, stub: Stub) -> None:
    job = start(client, {"token": _token_json(make_token())}).json()
    assert stub.entered.wait(WAIT)

    stopping = client.post(f"/v1/ingest/runs/{job['run_id']}/stop", headers=AUTH)

    assert stopping.status_code == 200
    assert stopping.json()["state"] == "stopping"
    stub.release.set()
    done = settle(client, job["run_id"])
    assert (done["state"], done["outcome"]["status"]) == ("finished", "stopped")
    again = client.post(f"/v1/ingest/runs/{job['run_id']}/stop", headers=AUTH)
    assert again.json()["state"] == "finished"


def test_a_stale_token_answers_the_start_and_leaves_no_job(
    client: TestClient, stub: Stub
) -> None:
    stub.result = api.IngestQuoteStale(changed=("files", "model"))

    response = start(client, {"token": _token_json(make_token())})

    assert response.status_code == 409
    assert response.json() == {
        "code": "quote_stale",
        "retryable": False,
        "message": "the quote is stale (changed: files, model); quote again",
    }


@pytest.mark.parametrize("body", [{}, {"token": None}])
def test_a_missing_token_is_refused(client: TestClient, stub: Stub, body: Any) -> None:
    response = start(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "run_refused"
    assert stub.calls == []


def test_a_run_that_raises_after_admission_ends_failed(
    client: TestClient, stub: Stub
) -> None:
    stub.error = api.WorkspaceLockUnavailableError("/secret/path: nope")
    job = start(client, {"token": _token_json(make_token())}).json()
    stub.release.set()

    done = settle(client, job["run_id"])

    assert done["state"] == "failed"
    assert done["outcome"] is None
    assert done["error"]["code"] == "workspace_lock_unavailable"
    assert "secret" not in done["error"]["message"]


def test_a_refusal_before_admission_answers_the_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*args: Any, **kwargs: Any) -> api.IngestRunResult:
        raise api.IngestRefused("openkos ingest: refusing to run -- /secret.")

    monkeypatch.setattr(api, "run_ingest", refused)

    response = start(client, {"token": _token_json(make_token())})

    assert response.status_code == 503
    assert response.json() == {
        "code": "ingest_refused",
        "retryable": False,
        "message": "files cannot be added to this workspace",
    }


def test_an_unknown_run_is_not_found(client: TestClient) -> None:
    assert poll(client, "nope").status_code == 404
    stopped = client.post("/v1/ingest/runs/nope/stop", headers=AUTH)
    assert stopped.json()["code"] == "not_found"


def test_finished_runs_are_evicted_after_the_newest_sixteen(
    client: TestClient, stub: Stub
) -> None:
    stub.release.set()
    ids = []
    for _ in range(_ingests.FINISHED_RUNS_KEPT + 1):
        job = start(client, {"token": _token_json(make_token())}).json()
        settle(client, job["run_id"])
        ids.append(job["run_id"])

    assert poll(client, ids[0]).status_code == 404
    assert poll(client, ids[-1]).status_code == 200


# --- the quote ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"paths": []},
        {"paths": "/a.md"},
        {"paths": ["/a.md"], "extra": 1},
        {"paths": [""]},
        {"paths": ["/a"] * 501},
        {"paths": ["/" + "a" * 4096]},
        {},
    ],
)
def test_an_invalid_quote_request_is_rejected_before_the_operation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    called: list[object] = []
    monkeypatch.setattr(api, "quote_ingest", lambda *a, **k: called.append(a))

    response = client.post("/v1/ingest/quote", json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert called == []


# --- end to end -----------------------------------------------------------------


def test_a_real_quote_and_run(
    client: TestClient,
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = install(monkeypatch)
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    path = chosen / "notes.md"
    path.write_text(_NOTES, encoding="utf-8")
    hidden = settings.workspace.root / ".openkos" / "x.md"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text("private", encoding="utf-8")

    quoted = client.post(
        "/v1/ingest/quote", json={"paths": [str(path), str(hidden)]}, headers=AUTH
    )

    assert quoted.status_code == 200
    quote = quoted.json()
    assert [(f["status"], f["refusal"]) for f in quote["files"]] == [
        ("extracts", None),
        ("refused", "inside_workspace"),
    ]
    assert model.calls == 0
    job = start(client, {"token": quote["token"]}).json()
    done = settle(client, job["run_id"])
    assert done["state"] == "finished"
    assert [(f["name"], f["status"]) for f in done["outcome"]["files"]] == [
        ("notes.md", "added")
    ]
    assert done["outcome"]["calls"] == model.calls <= quote["calls"]
    assert (settings.workspace.root / "raw" / "notes.md").is_file()


def test_a_real_stale_quote_starts_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    path = tmp_path / "notes.md"
    path.write_text(_NOTES, encoding="utf-8")
    quote = client.post("/v1/ingest/quote", json={"paths": [str(path)]}, headers=AUTH)
    path.write_text("Otro.\n", encoding="utf-8")

    response = start(client, {"token": quote.json()["token"]})

    assert response.status_code == 409
    assert response.json()["code"] == "quote_stale"
    assert model.calls == 0


def test_a_real_busy_workspace_is_an_outcome(
    client: TestClient,
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch)
    monkeypatch.setattr(_ingests, "WRITE_WAIT_SECONDS", 0.0)
    path = tmp_path / "notes.md"
    path.write_text(_NOTES, encoding="utf-8")
    quote = client.post("/v1/ingest/quote", json={"paths": [str(path)]}, headers=AUTH)

    with lock.workspace_lock(settings.workspace.root):
        job = start(client, {"token": quote.json()["token"]}).json()
        done = settle(client, job["run_id"])

    assert done["state"] == "finished"
    assert done["outcome"]["status"] == "busy"


def test_a_refused_file_is_quoted_through_its_decision(
    client: TestClient,
    settings: ServerSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openkos.application import queue_producers
    from tests.unit.application.test_ingest_consent import _enqueue_refusal

    install(monkeypatch)
    root = settings.workspace.root
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + f"\nunattended:\n  inbox: {inbox}\n",
        encoding="utf-8",
    )
    path = inbox / "big.md"
    path.write_text(_NOTES, encoding="utf-8")
    _enqueue_refusal(root, path, queue_producers.REASON_EXCEEDS_BUDGET)
    (decision,) = [
        d
        for d in client.get("/v1/decisions", headers=AUTH).json()["decisions"]
        if d["kind"] == "watch_refusal"
    ]
    url = f"/v1/decisions/{decision['decision_key']}/ingest-quote"

    quoted = client.post(url, json={}, headers=AUTH)

    assert quoted.status_code == 200
    assert [f["path"] for f in quoted.json()["files"]] == [str(path)]
    job = start(client, {"token": quoted.json()["token"]}).json()
    assert settle(client, job["run_id"])["outcome"]["files"][0]["status"] == "added"
    again = client.post(url, json={}, headers=AUTH)
    assert again.status_code == 409
    assert again.json()["code"] == "stale"


def test_a_decision_of_another_kind_cannot_be_quoted_as_a_file(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/decisions/identity:abc/ingest-quote", json={}, headers=AUTH
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
