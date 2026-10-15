"""`openkos.api.quote_judging` and `run_judging`: a quote that costs no model
call, then a run against that quote, with the API's own default chat client.

Every test runs in a real workspace holding three groups of two same-titled
concepts, so the real candidate search finds three identity groups and a run
judges them one chunk at a time. The chat client is a scripted double; the
default client path is exercised by replacing the concrete client class the
API's composition root binds.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    BackendError,
    BackendUnavailable,
    JudgingObserver,
    JudgingOutcome,
    JudgingQuote,
    OpenKosError,
    QuoteStale,
    RunRefused,
    StopToken,
    Workspace,
    _wiring,
    quote_judging,
    run_judging,
)
from openkos.application import backends, judging
from openkos.llm.base import BackendHostLocality, Message
from openkos.llm.ollama import OllamaUnavailable
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    support.write_identity_groups(ws)
    return ws


def _reply() -> str:
    return json.dumps({"verdict": "same", "confidence": 0.9, "rationale": "Judged."})


class Judge:
    """A chat client that answers `same`, counts its calls and can run a hook
    inside the n-th one. It raises `error` instead when one is given."""

    def __init__(
        self,
        *,
        on_chat: Callable[[int], None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls = 0
        self.on_chat = on_chat
        self.error = error

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        if self.on_chat is not None:
            self.on_chat(self.calls)
        if self.error is not None:
            raise self.error
        return _reply()


class Factory:
    """A `ChatClientFactory` that records the tasks it was asked for."""

    def __init__(self, client: Judge | None = None) -> None:
        self.client = client if client is not None else Judge()
        self.tasks: list[str] = []

    def __call__(self, task: str) -> Judge:
        self.tasks.append(task)
        return self.client


def _forbid_default_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a default model client was built")

    monkeypatch.setattr(backends, "chat_client", fail)
    monkeypatch.setattr(_wiring, "OllamaClient", fail)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", fail)


def _quote(root: Path, factory: Factory) -> JudgingQuote:
    return quote_judging(Workspace.open(root), "identity", chat_client=factory)


def _outcome(result: object) -> JudgingOutcome:
    assert isinstance(result, JudgingOutcome), result
    return result


# -- a quote ------------------------------------------------------------------- #


def test_a_quote_builds_a_client_and_makes_no_call(root: Path) -> None:
    client = Judge(error=AssertionError("the quote called the model"))
    factory = Factory(client)

    quote = _quote(root, factory)

    assert quote.kind == "identity"
    assert quote.to_judge == 3
    assert quote.token.calls == 3
    assert factory.tasks == ["adjudication"]
    assert client.calls == 0


def test_an_override_replaces_the_default_client(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_default_clients(monkeypatch)
    factory = Factory()

    quote = _quote(root, factory)
    outcome = _outcome(
        run_judging(Workspace.open(root), quote.token, chat_client=factory)
    )

    assert outcome.status == "completed"
    assert factory.tasks == ["adjudication", "adjudication"]
    assert factory.client.calls == 3


# -- the default client ----------------------------------------------------------- #


class DefaultFactory:
    """Stands in for the concrete client class the API's wiring binds."""

    def __init__(self, client: Judge) -> None:
        self.client = client
        self.models: list[object] = []

    def __call__(self, **kwargs: object) -> Judge:
        self.models.append(kwargs["model"])
        return self.client


def test_with_no_client_supplied_the_default_follows_the_configuration(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = DefaultFactory(Judge())
    monkeypatch.setattr(_wiring, "OllamaClient", default)
    workspace = Workspace.open(root)

    quote = quote_judging(workspace, "identity")
    outcome = _outcome(run_judging(workspace, quote.token))

    assert outcome.status == "completed"
    assert len(default.models) == 2
    assert default.models[0] == default.models[1]
    assert default.client.calls == 3


def test_the_default_is_built_from_the_configuration_the_run_reads_afresh(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = DefaultFactory(Judge())
    monkeypatch.setattr(_wiring, "OllamaClient", default)
    workspace = Workspace.open(root)
    quote = quote_judging(workspace, "identity")
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\nmodels:\n  adjudication: moved-tag\n",
        encoding="utf-8",
    )

    result = run_judging(workspace, quote.token)

    assert result == QuoteStale(kind="identity", changed=("model",))
    assert default.models[-1] == "moved-tag"
    assert default.client.calls == 0


# -- a run ------------------------------------------------------------------------ #


def test_a_moved_model_returns_a_stale_quote_and_makes_no_call(root: Path) -> None:
    quote = _quote(root, Factory())
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nmodels:\n  adjudication: other\n",
        encoding="utf-8",
    )
    later = Factory()

    result = run_judging(Workspace.open(root), quote.token, chat_client=later)

    assert isinstance(result, QuoteStale)
    assert result.changed == ("model",)
    assert later.client.calls == 0


def test_a_run_with_no_token_is_refused_as_a_value(root: Path) -> None:
    factory = Factory()

    result = run_judging(Workspace.open(root), None, chat_client=factory)

    assert result == RunRefused(reason="no_quote_token")
    assert factory.client.calls == 0


def test_a_stop_set_inside_the_first_chunk_ends_the_run_before_the_second(
    root: Path,
) -> None:
    stop = StopToken()
    client = Judge(on_chat=lambda n: stop.set() if n == 1 else None)
    factory = Factory(client)
    quote = _quote(root, factory)

    outcome = _outcome(
        run_judging(Workspace.open(root), quote.token, stop=stop, chat_client=factory)
    )

    assert outcome.status == "stopped"
    assert client.calls == 1
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (1, 1, 2)


def test_the_observer_hears_each_persisted_chunk(root: Path) -> None:
    class Recorder(JudgingObserver):
        def __init__(self) -> None:
            self.persisted: list[int] = []

        def chunk_persisted(self, persisted: int) -> None:
            self.persisted.append(persisted)

    factory = Factory()
    observer = Recorder()
    quote = _quote(root, factory)

    run_judging(
        Workspace.open(root), quote.token, observer=observer, chat_client=factory
    )

    assert observer.persisted == [1, 2, 3]


def test_an_unreachable_backend_is_raised_as_the_neutral_error(root: Path) -> None:
    quote = _quote(root, Factory())
    down = Factory(Judge(error=OllamaUnavailable("connection refused")))

    with pytest.raises(BackendUnavailable) as raised:
        run_judging(Workspace.open(root), quote.token, chat_client=down)

    assert isinstance(raised.value, BackendError)
    assert isinstance(raised.value, OpenKosError)
    assert not isinstance(raised.value, QuoteStale)


def test_a_persistently_busy_workspace_ends_unpersisted_rather_than_raising(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judging, "PERSIST_BACKOFF_SECONDS", (0.0, 0.0))
    factory = Factory()
    quote = _quote(root, factory)

    with lock.workspace_lock(root):
        outcome = _outcome(
            run_judging(
                Workspace.open(root), quote.token, chat_client=factory, wait_seconds=0
            )
        )

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "workspace_busy"
    assert outcome.persisted == 0
    assert factory.client.calls == 1


def test_a_wait_out_of_range_is_refused_before_any_call(root: Path) -> None:
    factory = Factory()
    quote = _quote(root, factory)

    for wait in (-1.0, float("nan"), 3601.0):
        with pytest.raises(ValueError, match="wait_seconds"):
            run_judging(
                Workspace.open(root),
                quote.token,
                chat_client=factory,
                wait_seconds=wait,
            )

    assert factory.client.calls == 0
