"""`openkos.api` and rebuilding search (`sensitivity-and-rebuild` change, #1518):
the model-free read of whether search needs rebuilding, and the rebuild itself
with consent, progress and a stop. Through the public facade only; the
embedding client is a fake bound where the API's composition root binds the
concrete clients."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import api, config, lock
from openkos.api import _wiring
from openkos.llm.base import EMBED_DIM, BackendHostLocality, BackendUnavailable
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc


@dataclass
class Embedder:
    local: bool = True
    texts: list[str] = field(default_factory=list)
    error: Exception | None = None
    built: int = 0


def install(monkeypatch: pytest.MonkeyPatch, embedder: Embedder) -> Embedder:
    class Client:
        def __init__(self, **kwargs: object) -> None:
            embedder.built += 1

        @property
        def locality(self) -> BackendHostLocality:
            host = "localhost" if embedder.local else "llm.example.test"
            return BackendHostLocality(is_local=embedder.local, display_host=host)

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            if embedder.error is not None:
                raise embedder.error
            embedder.texts.extend(texts)
            return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]

        def chat(self, messages: object) -> str:
            raise AssertionError("a rebuild never chats")

    monkeypatch.setattr(_wiring, "OllamaClient", Client)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", Client)
    return embedder


def _set(root: Path, **values: str) -> None:
    path = root / "openkos.yaml"
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
        if line.split(":", 1)[0] not in values
    ]
    lines += [f"{key}: {value}\n" for key, value in values.items()]
    path.write_text("".join(lines), encoding="utf-8")


def _remote(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "sk-test")
    _set(root, backend="openai-compatible", base_url="https://llm.example.test/v1")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    ws = make_workspace(tmp_path, monkeypatch)
    write_doc(
        ws,
        "concepts/open",
        {"type": "Concept", "title": "Open", "sensitivity": "private"},
        "An open page about tomatoes.\n",
    )
    write_doc(
        ws,
        "concepts/secret",
        {"type": "Concept", "title": "Secret", "sensitivity": "confidential"},
        "A secret page about the safe.\n",
    )
    return ws


def _rebuild(root: Path, **kwargs: object) -> object:
    return api.rebuild_search(api.Workspace.open(root), **kwargs)  # type: ignore[arg-type]


def _vectors(root: Path) -> Path:
    return config.WorkspaceLayout(root).vectors_db_path


# -- the read ------------------------------------------------------------------ #


def test_a_library_with_nothing_embedded_needs_no_rebuild_for_a_model(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support.forbid_model_clients(monkeypatch, "the search read")
    before = support.snapshot(root)
    state = api.search_index(api.Workspace.open(root))
    assert state.model_changed is False
    assert state.sends_to is None
    assert state.needs_rebuild is bool(state.stale_indexes)
    assert not _vectors(root).exists()
    assert support.snapshot(root) == before


def test_a_rebuild_embeds_every_page_and_then_search_is_current(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = install(monkeypatch, Embedder())
    events: list[api.RebuildProgress] = []

    class Observer:
        def progress(self, event: api.RebuildProgress) -> None:
            events.append(event)

    outcome = _rebuild(root, observer=Observer())

    assert isinstance(outcome, api.RebuildOutcome), outcome
    assert outcome.embedded == 2
    assert outcome.withheld == 0
    assert outcome.complete is True
    assert any("tomatoes" in t for t in embedder.texts)
    assert events[0] == api.RebuildProgress("embedding", 1, 2)
    assert api.RebuildProgress("embedding", 2, 2) in events
    assert events[-1].phase == "finishing"
    monkeypatch.undo()
    support.forbid_model_clients(monkeypatch, "the search read")
    state = api.search_index(api.Workspace.open(root))
    assert (state.model_changed, state.stale_indexes, state.needs_rebuild) == (
        False,
        (),
        False,
    )


def test_a_changed_model_for_search_needs_a_rebuild_until_one_runs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    assert isinstance(_rebuild(root), api.RebuildOutcome)
    _set(root, embedding_model="other-embedder")

    state = api.search_index(api.Workspace.open(root))
    assert state.model_changed is True
    assert state.needs_rebuild is True

    outcome = _rebuild(root)
    assert isinstance(outcome, api.RebuildOutcome)
    assert outcome.embedded == 2
    assert api.search_index(api.Workspace.open(root)).needs_rebuild is False


def test_an_edited_page_makes_the_lexical_index_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    assert isinstance(_rebuild(root), api.RebuildOutcome)
    write_doc(root, "concepts/new", {"type": "Concept", "title": "New"}, "New.\n")
    state = api.search_index(api.Workspace.open(root))
    assert state.model_changed is False
    assert state.stale_indexes
    assert state.needs_rebuild is True


def test_a_remote_model_for_search_is_named_and_needs_consent(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = install(monkeypatch, Embedder(local=False))
    _remote(root, monkeypatch)

    assert api.search_index(api.Workspace.open(root)).sends_to == "llm.example.test"
    before = support.snapshot(root)
    assert _rebuild(root) == api.RebuildRefused("remote_not_allowed")
    assert embedder.built == 0
    assert support.snapshot(root) == before


def test_with_consent_a_remote_rebuild_withholds_confidential_pages(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = install(monkeypatch, Embedder(local=False))
    _remote(root, monkeypatch)

    outcome = _rebuild(root, allow_remote=True)

    assert isinstance(outcome, api.RebuildOutcome), outcome
    assert outcome.embedded == 1
    assert outcome.withheld == 1
    assert outcome.complete is False
    assert not any("safe" in t for t in embedder.texts)


def test_a_remote_rebuild_that_withheld_only_confidential_pages_is_not_behind(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1529: nothing more can be sent, so search no longer reads as needing a
    rebuild; a later change of the AI model for search does, and a local model
    still embeds the withheld pages."""
    install(monkeypatch, Embedder())
    assert isinstance(_rebuild(root), api.RebuildOutcome)
    install(monkeypatch, Embedder(local=False))
    _remote(root, monkeypatch)
    _set(root, embedding_model="cloud-embedder")
    assert api.search_index(api.Workspace.open(root)).needs_rebuild is True

    outcome = _rebuild(root, allow_remote=True)

    assert isinstance(outcome, api.RebuildOutcome), outcome
    assert outcome.withheld == 1
    after = api.search_index(api.Workspace.open(root))
    assert (after.model_changed, after.needs_rebuild) == (False, False)

    _set(root, embedding_model="another-cloud-embedder")
    assert api.search_index(api.Workspace.open(root)).model_changed is True

    # Back on this machine under a new model: the withheld page is embedded.
    local = install(monkeypatch, Embedder())
    _set(root, backend="ollama", embedding_model="local-embedder")
    local_outcome = _rebuild(root)
    assert isinstance(local_outcome, api.RebuildOutcome), local_outcome
    assert local_outcome.complete is True
    assert any("safe" in t for t in local.texts)


def test_a_client_that_turns_out_remote_is_refused_without_consent(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint read says local, the built client says otherwise: the
    client's own verdict decides."""
    embedder = install(monkeypatch, Embedder(local=False))
    assert _rebuild(root) == api.RebuildRefused("remote_not_allowed")
    assert embedder.texts == []


def test_a_stop_before_the_write_changes_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    assert isinstance(_rebuild(root), api.RebuildOutcome)
    _set(root, embedding_model="other-embedder")
    before = support.snapshot(root)
    stop = api.StopToken()

    class Observer:
        def progress(self, event: api.RebuildProgress) -> None:
            if event.done >= 1:
                stop.set()

    assert _rebuild(root, stop=stop, observer=Observer()) == api.RebuildStopped()
    assert support.snapshot(root) == before


def test_a_stop_set_before_the_start_sends_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = install(monkeypatch, Embedder())
    stop = api.StopToken()
    stop.set()
    assert _rebuild(root, stop=stop) == api.RebuildStopped()
    assert embedder.texts == []


def test_a_busy_workspace_raises_at_the_write(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    with lock.workspace_lock(root), pytest.raises(api.WorkspaceBusyError):
        _rebuild(root, wait_seconds=0)
    assert not api.search_index(api.Workspace.open(root)).model_changed


def test_an_unreachable_runtime_raises_the_backend_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder(error=BackendUnavailable("down")))
    with pytest.raises(api.BackendUnavailable):
        _rebuild(root)


def test_an_unusable_store_raises_search_unavailable(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Embedder())
    _vectors(root).parent.mkdir(parents=True, exist_ok=True)
    _vectors(root).write_bytes(b"not a database")
    with pytest.raises(api.SearchUnavailable):
        _rebuild(root)


def test_a_wait_outside_the_range_raises_value_error(root: Path) -> None:
    with pytest.raises(ValueError, match="wait_seconds"):
        _rebuild(root, wait_seconds=-1)
