"""`openkos.api.quote_ingest` and `run_ingest` (#1496): adding the files a person
chose, quoted and consented like judging.

No real model is called: the API's concrete client classes are replaced with a
scripted fake, so the default client path (`_wiring`) is the one exercised. The
derived refresh is replaced at `ingest_consent.refresh_derived_stores`, which the
API reaches through the module attribute.
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import api, config, lock
from openkos.api import _wiring
from openkos.application import ingest_consent
from openkos.extraction import judge
from openkos.llm.base import BackendHostLocality, Message
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity

_NOTES = "Notas sobre la plataforma.\nLa plataforma registra el origen de cada dato.\n"
_REPLY = (
    "["
    '{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma registra el origen de cada dato."},'
    '{"type": "Concept", "title": "Registro De Origen", '
    '"description": "Donde se anota el origen.", '
    '"body": "La plataforma registra el origen de cada dato."}'
    "]"
)
_KEEP = '{"keep": ["Trazabilidad Documental", "Registro De Origen"]}'


@dataclass
class Model:
    calls: int = 0
    built: int = 0
    local: bool = True
    refreshes: list[object] = field(default_factory=list)


def install(monkeypatch: pytest.MonkeyPatch, *, local: bool = True) -> Model:
    model = Model(local=local)

    class Client:
        def __init__(self, **kwargs: object) -> None:
            model.built += 1

        @property
        def locality(self) -> BackendHostLocality:
            host = "localhost:11434" if model.local else "llm.example.test"
            return BackendHostLocality(is_local=model.local, display_host=host)

        def chat(self, messages: Sequence[Message]) -> str:
            model.calls += 1
            if messages[0].get("content") == judge._JUDGE_SYSTEM_PROMPT:
                return _KEEP
            return _REPLY

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise AssertionError("the refresh is replaced in these tests")

    def refresh(root: Path, *, embed_client: object) -> None:
        model.refreshes.append(embed_client)

    monkeypatch.setattr(_wiring, "OllamaClient", Client)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", Client)
    monkeypatch.setattr(ingest_consent, "refresh_derived_stores", refresh)
    return model


@pytest.fixture
def root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> Path:
    return make_workspace(tmp_path, monkeypatch)


@pytest.fixture
def chosen(tmp_path: Path) -> Path:
    folder = tmp_path / "chosen"
    folder.mkdir()
    return folder


def _write(folder: Path, name: str, text: str = _NOTES) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_quote_builds_the_client_only_to_read_it(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    path = _write(chosen, "notes.md")

    quote = api.quote_ingest(api.Workspace.open(root), [path])

    assert model.calls == 0
    assert isinstance(quote, api.IngestQuote)
    cfg = config.read_config(root)
    assert quote.model == config.resolve_task_model(cfg, "extraction")
    assert (quote.is_local, quote.host) == (True, "localhost:11434")
    (file,) = quote.files
    assert (file.path, file.status) == (str(path), "extracts")
    assert quote.calls == file.calls > 0
    assert not (root / "raw" / "notes.md").exists()


def test_a_remote_model_is_named_as_remote(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, local=False)

    quote = api.quote_ingest(api.Workspace.open(root), [_write(chosen, "n.md")])

    assert (quote.is_local, quote.host) == (False, "llm.example.test")


def test_one_string_is_not_taken_for_a_list_of_paths(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)

    with pytest.raises(ValueError, match="sequence"):
        api.quote_ingest(api.Workspace.open(root), str(_write(chosen, "n.md")))


def test_a_run_adds_commits_and_refreshes(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    workspace = api.Workspace.open(root)
    quote = api.quote_ingest(
        workspace, [_write(chosen, "a.md"), _write(chosen, "b.md")]
    )
    events: list[api.IngestProgress] = []
    warnings: list[str] = []

    result = api.run_ingest(
        workspace, quote.token, progress=events.append, on_warning=warnings.append
    )

    assert isinstance(result, api.IngestRunOutcome)
    assert [(f.name, f.status, f.source_id) for f in result.files] == [
        ("a.md", "added", "sources/a"),
        ("b.md", "added", "sources/b"),
    ]
    assert result.calls == model.calls <= quote.calls
    assert result.indexes == "refreshed"
    assert len(model.refreshes) == 1
    assert warnings == []
    log = subprocess.run(
        ["git", "log", "--format=%s"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert log.count("\n") >= 3  # init plus one commit per file
    assert events[-1].phase == "refreshing"


def test_the_refresh_builds_the_embedder_the_api_wires(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    workspace = api.Workspace.open(root)
    api.run_ingest(
        workspace, api.quote_ingest(workspace, [_write(chosen, "a.md")]).token
    )

    (embed_client,) = model.refreshes
    built = model.built
    embed_client(config.read_config(root))  # type: ignore[operator]
    assert model.built == built + 1


def test_a_stale_quote_and_a_missing_token_spend_nothing(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    workspace = api.Workspace.open(root)
    path = _write(chosen, "a.md")
    quote = api.quote_ingest(workspace, [path])
    path.write_text("Otro.\n", encoding="utf-8")

    assert api.run_ingest(workspace, quote.token) == api.IngestQuoteStale(
        changed=("files",)
    )
    assert api.run_ingest(workspace, None) == api.RunRefused("no_quote_token")
    assert model.calls == 0


def test_a_stop_is_observed_before_the_first_file(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)
    workspace = api.Workspace.open(root)
    quote = api.quote_ingest(workspace, [_write(chosen, "a.md")])
    stop = api.StopToken()
    stop.set()

    result = api.run_ingest(workspace, quote.token, stop=stop)

    assert isinstance(result, api.IngestRunOutcome)
    assert (result.status, model.calls) == ("stopped", 0)


def test_the_lock_wait_is_bounded_and_validated(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)
    workspace = api.Workspace.open(root)
    quote = api.quote_ingest(workspace, [_write(chosen, "a.md")])

    with pytest.raises(ValueError, match="wait_seconds"):
        api.run_ingest(workspace, quote.token, wait_seconds=-1)
    with lock.workspace_lock(root):
        result = api.run_ingest(workspace, quote.token)

    assert isinstance(result, api.IngestRunOutcome)
    assert result.status == "busy"


def test_a_workspace_that_went_away_is_an_openkos_error(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)
    workspace = api.Workspace.open(root)
    path = _write(chosen, "a.md")
    (root / "openkos.yaml").unlink()

    with pytest.raises(api.IngestRefused) as caught:
        api.quote_ingest(workspace, [path])
    assert isinstance(caught.value, api.OpenKosError)


def test_a_commit_that_cannot_be_made_is_a_warning(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)
    workspace = api.Workspace.open(root)
    quote = api.quote_ingest(workspace, [_write(chosen, "a.md")])
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    monkeypatch.setenv("HOME", str(chosen))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(chosen / "none"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    warnings: list[str] = []

    result = api.run_ingest(workspace, quote.token, on_warning=warnings.append)

    assert isinstance(result, api.IngestRunOutcome)
    assert result.files[0].status == "added"
    assert warnings == [
        "the ingest was applied but not committed: "
        "git user.name and user.email are not set"
    ]


def test_a_refused_file_is_quoted_by_its_decision_key(
    root: Path, chosen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import queue_producers
    from tests.unit.application.test_ingest_consent import (
        _enqueue_refusal,
        _refusal_rows,
    )

    install(monkeypatch)
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + f"\nunattended:\n  inbox: {chosen}\n",
        encoding="utf-8",
    )
    path = _write(chosen, "big.md")
    _enqueue_refusal(root, path, queue_producers.REASON_EXCEEDS_BUDGET)
    (row,) = _refusal_rows(root)
    workspace = api.Workspace.open(root)
    (decision,) = [
        d for d in api.list_decisions(workspace) if d.kind == "watch_refusal"
    ]

    quote = api.quote_refused_file(workspace, decision.decision_key)

    assert decision.decision_key == row.decision_key
    assert isinstance(quote, api.IngestQuote)
    assert [f.path for f in quote.files] == [str(path)]
    api.run_ingest(workspace, quote.token)
    assert api.quote_refused_file(workspace, decision.decision_key) == api.Stale(
        decision.decision_key, "row_resolved"
    )
