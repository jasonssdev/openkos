"""The ingest commit phase (#1137, ADR-0036): extraction runs with the workspace
lock NOT held, and only the commit phase -- re-validation, the write burst, the
auto-commit -- holds it.

Every test drives `ingest_source` from an explicit root with the REAL
`lock_wait.locked_commit_section`, so "the lock is held" and "the lock is free"
are observed through `lock.workspace_lock` itself, never assumed.
"""

import contextlib
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config, lock
from openkos.application import ingest_service as svc
from openkos.application import lock_wait
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from openkos.state import reindex as state_reindex
from openkos.state import vectorstore

_NOTES = "Some raw notes about self-control.\n"


def _lock_is_free(root: Path) -> bool:
    """`True` when a second acquirer could take the workspace lock right now."""
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


class _HookedLLM:
    """A declining `LLMBackend` that runs a hook inside every `chat` call, the
    way a slow model would: this is the window a concurrent process acts in."""

    def __init__(self, hook: Callable[[], None]) -> None:
        self.hook = hook
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        self.hook()
        return '{"extract": false}'


def _ports(
    root: Path,
    llm: _HookedLLM,
    events: list[str] | None = None,
    *,
    section: lock_wait.CommitSection | None = None,
) -> svc.IngestPorts:
    log = events if events is not None else []

    def _autocommit(r: Path, paths: Sequence[str], message: str) -> None:
        log.append("autocommit")

    def _after_commit(layout: config.WorkspaceLayout, cfg: config.Config) -> None:
        log.append("after_commit")

    return svc.IngestPorts(
        chat_client=lambda cfg: llm,
        autocommit=_autocommit,
        after_commit=_after_commit,
        commit_section=(
            section
            if section is not None
            else lock_wait.locked_commit_section(root, wait_seconds=0)
        ),
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.chdir(root)
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


def _source(tmp_path: Path, name: str = "notes.txt", text: str = _NOTES) -> Path:
    path = tmp_path / "elsewhere" / name
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".openkos" not in p.parts
    }


_AUTO = svc.IngestPolicy(skip_confirmation=True)


def test_the_lock_is_not_held_while_the_model_is_called(
    workspace: Path, tmp_path: Path
) -> None:
    observed: list[bool] = []
    llm = _HookedLLM(lambda: observed.append(_lock_is_free(workspace)))

    svc.ingest_source(workspace, _source(tmp_path), _AUTO, ports=_ports(workspace, llm))

    # Precondition: the model really was called, so the list is not vacuous.
    assert llm.calls >= 1
    assert observed == [True] * llm.calls


def test_the_lock_is_held_for_the_write_burst_and_released_after(
    workspace: Path, tmp_path: Path
) -> None:
    """The mirror image: a service that never entered the section would pass
    the test above for the wrong reason."""
    during: list[bool] = []

    def _autocommit(r: Path, paths: Sequence[str], message: str) -> None:
        during.append(_lock_is_free(workspace))

    after: list[bool] = []

    def _after_commit(layout: config.WorkspaceLayout, cfg: config.Config) -> None:
        after.append(_lock_is_free(workspace))

    ports = svc.IngestPorts(
        chat_client=lambda cfg: _HookedLLM(lambda: None),
        autocommit=_autocommit,
        after_commit=_after_commit,
        commit_section=lock_wait.locked_commit_section(workspace, wait_seconds=0),
    )

    svc.ingest_source(workspace, _source(tmp_path), _AUTO, ports=ports)

    assert during == [False]
    assert after == [True]  # embedding runs lock-free


def test_a_busy_lock_refuses_at_the_commit_phase_with_nothing_written(
    workspace: Path, tmp_path: Path
) -> None:
    before = _snapshot(workspace)

    with lock.workspace_lock(workspace), pytest.raises(lock.WorkspaceBusyError):
        svc.ingest_source(
            workspace,
            _source(tmp_path),
            _AUTO,
            ports=_ports(workspace, _HookedLLM(lambda: None)),
        )

    assert _snapshot(workspace) == before


def test_a_concurrent_index_and_log_append_is_recomposed_not_refused(
    workspace: Path, tmp_path: Path
) -> None:
    index_path = workspace / "bundle" / "index.md"
    log_path = workspace / "bundle" / "log.md"
    foreign_entry = "**Elsewhere**: another process logged this line."

    def _another_process_appends() -> None:
        index_path.write_text(
            bundle_index.insert_index_entry(
                index_path.read_text(encoding="utf-8"),
                section="Concepts",
                link_dir="concepts",
                title="Foreign Concept",
                slug="foreign-concept",
                description="written by someone else",
            ),
            encoding="utf-8",
        )
        log_path.write_text(
            bundle_log.insert_log_entry(
                log_path.read_text(encoding="utf-8"),
                datetime.now(UTC).astimezone().date(),
                foreign_entry,
            ),
            encoding="utf-8",
        )

    # The append lands AFTER the plan was composed (Phase A read both files) and
    # BEFORE the lock is taken -- the window a `--wait` or a busy lock opens.
    real_section = lock_wait.locked_commit_section(workspace, wait_seconds=0)
    appended: list[bool] = []

    @contextlib.contextmanager
    def _section_after_a_concurrent_append() -> Iterator[None]:
        _another_process_appends()
        appended.append(True)
        with real_section():
            yield

    outcome = svc.ingest_source(
        workspace,
        _source(tmp_path),
        _AUTO,
        ports=_ports(
            workspace,
            _HookedLLM(lambda: None),
            section=_section_after_a_concurrent_append,
        ),
    )

    assert appended == [True]
    assert isinstance(outcome, svc.IngestWritten)
    index_text = index_path.read_text(encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8")
    assert "foreign-concept" in index_text
    assert "/sources/notes.md" in index_text
    assert foreign_entry in log_text
    assert "Imported [" in log_text
    assert "/sources/notes.md" in log_text
    assert (workspace / "bundle" / "sources" / "notes.md").is_file()
    assert (workspace / "raw" / "notes.txt").is_file()


def test_a_changed_concept_target_refuses_with_nothing_written(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    assert isinstance(
        svc.ingest_source(
            workspace, src, _AUTO, ports=_ports(workspace, _HookedLLM(lambda: None))
        ),
        svc.IngestWritten,
    )
    concept = workspace / "bundle" / "sources" / "notes.md"
    edited = concept.read_text(encoding="utf-8") + "\nA human edit mid-extraction.\n"

    def _edit_the_concept() -> None:
        concept.write_text(edited, encoding="utf-8")

    policy = svc.IngestPolicy(skip_confirmation=True, re_extract=True)
    events: list[str] = []
    with pytest.raises(svc.DriftDetected) as excinfo:
        svc.ingest_source(
            workspace,
            src,
            policy,
            ports=_ports(workspace, _HookedLLM(_edit_the_concept), events),
        )

    assert "bundle/sources/notes.md" in excinfo.value.message
    assert "Nothing was written." in excinfo.value.message
    assert concept.read_text(encoding="utf-8") == edited
    assert events == []  # no autocommit, no after_commit


def test_a_concept_created_during_extraction_refuses_instead_of_half_writing(
    workspace: Path, tmp_path: Path
) -> None:
    """A create-only target that was absent at the plan and exists at the
    commit phase: before the split the whole-verb lock made that impossible;
    now it must refuse up front rather than fail partway through the burst."""
    concept = workspace / "bundle" / "sources" / "notes.md"

    def _someone_creates_it() -> None:
        concept.parent.mkdir(parents=True, exist_ok=True)
        concept.write_text("---\ntype: Source\ntitle: Mine\n---\nmine\n")

    events: list[str] = []
    with pytest.raises(svc.DriftDetected) as excinfo:
        svc.ingest_source(
            workspace,
            _source(tmp_path),
            _AUTO,
            ports=_ports(workspace, _HookedLLM(_someone_creates_it), events),
        )

    assert "bundle/sources/notes.md" in excinfo.value.message
    assert not (workspace / "raw" / "notes.txt").exists()
    assert concept.read_text(encoding="utf-8").endswith("mine\n")
    assert events == []


def test_a_sensitivity_input_raised_between_phases_refuses_and_writes_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    """SENTINEL. `openkos.yaml`'s `default_sensitivity` decides the Source's
    level and every derived object's floor. The plan read `private`; before the
    commit phase another process raised it to `confidential`. Writing the plan
    would stamp a LOWER level than the workspace now demands."""
    config_path = workspace / "openkos.yaml"
    assert "default_sensitivity: confidential" not in config_path.read_text("utf-8")

    def _raise_to_confidential() -> None:
        text = config_path.read_text(encoding="utf-8")
        if "default_sensitivity:" in text:
            lines = [
                "default_sensitivity: confidential"
                if line.startswith("default_sensitivity:")
                else line
                for line in text.splitlines()
            ]
            config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            config_path.write_text(
                text + "\ndefault_sensitivity: confidential\n", encoding="utf-8"
            )

    events: list[str] = []
    llm = _HookedLLM(_raise_to_confidential)
    with pytest.raises(svc.DriftDetected) as excinfo:
        svc.ingest_source(
            workspace, _source(tmp_path), _AUTO, ports=_ports(workspace, llm, events)
        )

    # Precondition: the concurrent raise really happened.
    assert "default_sensitivity: confidential" in config_path.read_text("utf-8")
    assert "openkos.yaml" in excinfo.value.message
    assert "Nothing was written." in excinfo.value.message
    assert not (workspace / "raw" / "notes.txt").exists()
    assert not (workspace / "bundle" / "sources" / "notes.md").exists()
    assert events == []


def test_a_batch_style_second_file_is_its_own_commit_phase(
    workspace: Path, tmp_path: Path
) -> None:
    """Two ingests back to back: the lock is free between them and while the
    second one extracts."""
    free_during_second: list[bool] = []
    svc.ingest_source(
        workspace,
        _source(tmp_path, "a.txt", "alpha notes\n"),
        _AUTO,
        ports=_ports(workspace, _HookedLLM(lambda: None)),
    )
    assert _lock_is_free(workspace)
    llm = _HookedLLM(lambda: free_during_second.append(_lock_is_free(workspace)))
    svc.ingest_source(
        workspace,
        _source(tmp_path, "b.txt", "beta notes\n"),
        _AUTO,
        ports=_ports(workspace, llm),
    )

    assert llm.calls >= 1
    assert free_during_second == [True] * llm.calls


# -- the vector upsert re-takes the lock with a content-hash re-check --------


class _EmbedderThatEdits:
    """Embeds, and edits a chosen document the first time it is called -- the
    edit lands while the embedding call is in flight, lock-free."""

    def __init__(self, edit: Callable[[], None]) -> None:
        self.edit = edit
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == 1:
            self.edit()
        return [[0.1] * EMBED_DIM for _ in texts]


def _doc(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: T\ndescription: ''\n"
        f"sensitivity: private\n---\n{body}",
        encoding="utf-8",
    )


@contextlib.contextmanager
def _recording_section(root: Path, log: list[str]) -> Iterator[None]:
    with lock.workspace_lock(root):
        log.append("enter")
        yield
    log.append("exit")


def test_the_vector_upsert_retakes_the_lock_and_drops_a_changed_document(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    root = tmp_path
    _doc(bundle / "concepts" / "stable.md", "stable body")
    _doc(bundle / "concepts" / "moving.md", "original body")
    moving = bundle / "concepts" / "moving.md"
    free_during_embed: list[bool] = []
    entered: list[str] = []

    def _edit() -> None:
        free_during_embed.append(_lock_is_free(root))
        _doc(moving, "edited while the embedder was running")

    embedder = _EmbedderThatEdits(_edit)
    with vectorstore.open_vector_store(tmp_path / "vectors.db") as db:
        report = state_reindex.reindex(
            bundle,
            db,
            embedder,
            commit_section=lambda: _recording_section(root, entered),
        )
        stored = set(db.meta_hashes())

    assert embedder.calls >= 2  # both documents were embedded
    assert free_during_embed == [True]
    assert entered == ["enter", "exit"]  # one brief section, around the upsert
    assert stored == {"concepts/stable"}  # the edited document is NOT stored
    assert report.embedded == 1


def test_the_vector_upsert_drops_a_document_that_vanished(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _doc(bundle / "concepts" / "keep.md", "keep")
    _doc(bundle / "concepts" / "gone.md", "gone")
    gone = bundle / "concepts" / "gone.md"
    embedder = _EmbedderThatEdits(gone.unlink)

    with vectorstore.open_vector_store(tmp_path / "vectors.db") as db:
        state_reindex.reindex(
            bundle, db, embedder, commit_section=lambda: contextlib.nullcontext()
        )
        stored = set(db.meta_hashes())

    assert stored == {"concepts/keep"}
