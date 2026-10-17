"""The ask routes over HTTP: start, poll, the one-active rule, failures and the
gated answer (ADR-0068).

Most tests patch `openkos.api.ask` with a stub gated on events, so every
interleaving is forced by an event and no test sleeps to win a race. The
gated-answer test runs the real `ask` in a real workspace with a scripted model.
"""

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openkos import api, config
from openkos.llm.ollama import OllamaUnavailable
from openkos.localapi import _asks
from openkos.localapi._app import ServerSettings, create_app
from openkos.state import fts
from tests.unit.api.test_ask_api import _MARKER, install
from tests.unit.cli.commit_phase_support import write_doc
from tests.unit.localapi.conftest import AUTH, PORT
from tests.unit.localapi.test_runs_registry import WAIT

ANSWER = api.Answer(
    question="q",
    outcome="answered",
    answer="An answer.",
    no_match_cause="none",
    citations=(
        api.AnswerCitation(
            concept_id="concepts/a",
            title="A",
            excerpted=False,
            confidential=False,
            history=None,
        ),
    ),
    omitted=(),
    attribution="reported",
    withheld=0,
    sufficiency_checked=False,
    sufficiency_degraded=False,
    lexical_unavailable=False,
    dense_unavailable=True,
    stale_indexes=(),
)


class Stub:
    """A replacement for `api.ask` that a test steers with events."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.error: Exception | None = None

    def __call__(
        self, workspace: api.Workspace, question: str, **kwargs: Any
    ) -> api.Answer:
        self.calls.append({"question": question, **kwargs})
        progress = kwargs.get("progress")
        if progress is not None:
            progress(api.AskProgress(phase="retrieving", completed=0, total=3))
        self.entered.set()
        assert self.release.wait(WAIT)
        if self.error is not None:
            raise self.error
        return ANSWER


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    fake = Stub()
    monkeypatch.setattr(api, "ask", fake)
    yield fake
    fake.release.set()  # never leave an ask thread parked


@pytest.fixture
def app(settings: ServerSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False
    ) as c:
        yield c


def start(client: TestClient, body: Any) -> Any:
    return client.post("/v1/ask", json=body, headers=AUTH)


def poll(client: TestClient, ask_id: str) -> Any:
    return client.get(f"/v1/ask/{ask_id}", headers=AUTH)


def settle(client: TestClient, ask_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT
    while True:
        body: dict[str, Any] = poll(client, ask_id).json()
        if body["state"] != "running":
            return body
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)


def test_start_returns_at_once_and_the_poll_reaches_the_answer(
    client: TestClient, stub: Stub
) -> None:
    response = start(client, {"question": "q"})

    assert response.status_code == 202
    job = response.json()
    assert job["state"] == "running"
    assert response.headers["Location"] == f"/v1/ask/{job['ask_id']}"
    assert stub.entered.wait(WAIT)
    running = poll(client, job["ask_id"]).json()
    assert (running["phase"], running["completed"], running["total"]) == (
        "retrieving",
        0,
        3,
    )

    stub.release.set()
    done = settle(client, job["ask_id"])

    assert done["state"] == "finished"
    assert done["error"] is None
    assert done["answer"]["outcome"] == "answered"
    assert done["answer"]["answer"] == "An answer."
    assert done["answer"]["citations"][0]["concept_id"] == "concepts/a"


def test_the_body_is_passed_through_and_an_omitted_limit_is_the_default(
    client: TestClient, stub: Stub
) -> None:
    stub.release.set()
    first = start(client, {"question": "q"}).json()
    settle(client, first["ask_id"])
    second = start(
        client,
        {
            "question": "r",
            "limit": 3,
            "include_deprecated": True,
            "include_confidential": True,
        },
    ).json()
    settle(client, second["ask_id"])

    assert "limit" not in stub.calls[0]
    assert stub.calls[0]["include_confidential"] is False
    assert stub.calls[0]["include_deprecated"] is False
    assert stub.calls[1]["question"] == "r"
    assert stub.calls[1]["limit"] == 3
    assert stub.calls[1]["include_deprecated"] is True
    assert stub.calls[1]["include_confidential"] is True


def test_a_second_start_while_active_is_refused(client: TestClient, stub: Stub) -> None:
    first = start(client, {"question": "q"}).json()
    assert stub.entered.wait(WAIT)

    second = start(client, {"question": "q"})

    assert second.status_code == 409
    assert second.json() == {
        "code": "ask_active",
        "retryable": True,
        "message": "an ask is already running",
    }
    assert len(stub.calls) == 1
    stub.release.set()
    assert settle(client, first["ask_id"])["state"] == "finished"
    assert start(client, {"question": "q"}).status_code == 202


def test_an_ask_that_raises_ends_failed_with_a_fixed_body(
    client: TestClient, stub: Stub
) -> None:
    stub.error = OllamaUnavailable("secret-host.example: refused")
    job = start(client, {"question": "q"}).json()
    stub.release.set()

    done = settle(client, job["ask_id"])

    assert done["state"] == "failed"
    assert done["answer"] is None
    assert done["error"] == {
        "code": "backend_unavailable",
        "retryable": True,
        "message": "the configured model backend is unreachable",
    }


def test_an_unknown_ask_id_is_not_found(client: TestClient) -> None:
    response = poll(client, "nope")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {"question": "q", "limit": 0},
        {"question": "q", "limit": True},
        {"question": "q", "stop": True},
        {},
        {"question": 3},
    ],
)
def test_an_invalid_body_starts_nothing(
    client: TestClient, stub: Stub, body: dict[str, Any]
) -> None:
    response = start(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert stub.calls == []


def test_old_finished_asks_are_evicted(client: TestClient, stub: Stub) -> None:
    stub.release.set()
    ids = []
    for _ in range(_asks.FINISHED_ASKS_KEPT + 1):
        job = start(client, {"question": "q"}).json()
        settle(client, job["ask_id"])
        ids.append(job["ask_id"])

    assert poll(client, ids[0]).status_code == 404
    assert all(poll(client, i).status_code == 200 for i in ids[1:])


def test_an_ask_answer_is_the_gated_one(
    client: TestClient,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real `ask`, a local model and a confidential concept in its prompt."""
    root: Path = workspace.root
    write_doc(
        root,
        "concepts/open",
        {"type": "Concept", "title": "Open", "sensitivity": "public"},
        "This concept explains a marker value in general terms.\n",
    )
    write_doc(
        root,
        "concepts/secret",
        {"type": "Concept", "title": "Secret", "sensitivity": "confidential"},
        f"This concept explains a marker value. The value is {_MARKER}.\n",
    )
    path = root / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "sufficiency_check: false\n",
        encoding="utf-8",
    )
    layout = config.WorkspaceLayout(root)
    fts.write_fts_index(layout.fts_db_path, layout.bundle_dir)
    model = install(monkeypatch, [f"It is {_MARKER}.\nUSED: 1, 2"], local=True)

    job = start(client, {"question": "marker value"}).json()
    done = settle(client, job["ask_id"])

    assert any(_MARKER in prompt for prompt in model.prompts)
    assert done["state"] == "finished"
    assert done["answer"]["outcome"] == "withheld"
    assert done["answer"]["answer"] is None
    text = str(done)
    assert _MARKER not in text
    assert "secret" not in text.casefold()


def test_the_package_states_that_an_ask_answer_is_gated() -> None:
    import openkos.localapi

    doc = " ".join((openkos.localapi.__doc__ or "").split())

    assert "owner-local" in doc
    assert "an ask job's answer is gated by `openkos.api.ask`" in doc
    assert "deferred" not in doc
