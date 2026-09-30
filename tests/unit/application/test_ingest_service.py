"""Direct tests for `openkos.application.ingest_service` (issue #1138): the
single-source ingest use case called WITHOUT the CLI -- an explicit workspace
root, injected effects, typed outcomes and typed refusals.

`tests/unit/cli/test_ingest.py` and `test_ingest_characterization.py` stay the
black-box contract for what the `ingest` verb prints and exits with; this file
pins what only a non-CLI caller can see: that the service reads no current
directory, prompts nothing, prints nothing, raises no `typer.Exit`, and hands
its typed results back as values.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import ingest_service as svc
from openkos.cli.main import app
from openkos.llm.base import Message

_NOTES = "Some raw notes about self-control.\n"


class _DecliningLLM:
    """A structural `LLMBackend` that declines extraction."""

    def chat(self, messages: Sequence[Message]) -> str:
        return '{"extract": false}'


class _Recorder(svc.IngestObserver):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.previews: list[svc.IngestPreview] = []
        self.notices: list[str] = []

    def notice(self, message: str) -> None:
        self.notices.append(message)
        self.events.append("notice")

    def preview(self, preview: svc.IngestPreview) -> None:
        self.previews.append(preview)
        self.events.append("preview")

    def imported(self, summary: svc.ImportedSummary) -> None:
        self.events.append("imported")


def _ports(
    calls: list[str],
    *,
    snapshot_read: Callable[[Path], tuple[bytes, str]] | None = None,
) -> svc.IngestPorts:
    def _autocommit(root: Path, paths: Sequence[str], message: str) -> None:
        calls.append(f"autocommit:{message}:{','.join(paths)}")

    def _after_commit(layout: config.WorkspaceLayout, cfg: config.Config) -> None:
        calls.append("after_commit")

    base = svc.IngestPorts(
        chat_client=lambda cfg: _DecliningLLM(),
        autocommit=_autocommit,
        after_commit=_after_commit,
    )
    if snapshot_read is None:
        return base
    return svc.IngestPorts(
        chat_client=base.chat_client,
        autocommit=base.autocommit,
        after_commit=base.after_commit,
        snapshot_read=snapshot_read,
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real workspace, then the process moved AWAY from it: every test
    below proves the service works from an explicit root alone."""
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


def test_ingests_against_an_explicit_root_without_reading_the_cwd(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    calls: list[str] = []
    observer = _Recorder()

    outcome = svc.ingest_source(
        workspace,
        src,
        svc.IngestPolicy(skip_confirmation=True),
        ports=_ports(calls),
        observer=observer,
    )

    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.regenerated is False
    assert outcome.derived_count == 0
    assert Path.cwd() != workspace.resolve()
    assert (workspace / "raw" / "notes.txt").read_text(encoding="utf-8") == _NOTES
    assert (workspace / "bundle" / "sources" / "notes.md").is_file()
    # The commit is asked for exactly the written paths, then the derived-index
    # step follows it -- never before (#183).
    assert calls == [
        "autocommit:openkos: ingest notes.txt (+0 concepts):"
        "raw/notes.txt,bundle/sources/notes.md,bundle/index.md,bundle/log.md",
        "after_commit",
    ]
    assert observer.events == ["preview", "imported"]
    assert observer.previews[0].name == "notes.txt"
    assert observer.previews[0].regenerate is False


def test_a_byte_identical_reingest_is_an_unchanged_outcome_that_writes_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    policy = svc.IngestPolicy(skip_confirmation=True)
    calls: list[str] = []
    first = svc.ingest_source(workspace, src, policy, ports=_ports(calls))
    assert isinstance(first, svc.IngestWritten)
    before = _snapshot(workspace)
    calls.clear()
    observer = _Recorder()

    second = svc.ingest_source(
        workspace, src, policy, ports=_ports(calls), observer=observer
    )

    assert isinstance(second, svc.IngestUnchanged)
    assert second.regenerated is True
    assert second.extraction_skipped is True
    assert calls == []
    assert observer.previews == []
    assert _snapshot(workspace) == before


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        ("declined", svc.ConfirmationDeclined),
        ("unavailable", svc.ConfirmationUnavailable),
    ],
)
def test_a_confirmation_that_is_not_a_yes_refuses_and_writes_nothing(
    workspace: Path,
    tmp_path: Path,
    answer: svc.ConfirmationAnswer,
    refusal: type[svc.IngestRefused],
) -> None:
    src = _source(tmp_path)
    before = _snapshot(workspace)
    calls: list[str] = []

    with pytest.raises(refusal):
        svc.ingest_source(
            workspace,
            src,
            svc.IngestPolicy(),
            ports=_ports(calls),
            confirm=lambda preview: answer,
        )

    assert calls == []
    assert _snapshot(workspace) == before


def test_a_required_confirmation_with_no_callback_is_unavailable_never_a_yes(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    before = _snapshot(workspace)

    with pytest.raises(svc.ConfirmationUnavailable):
        svc.ingest_source(workspace, src, svc.IngestPolicy(), ports=_ports([]))

    assert _snapshot(workspace) == before


def test_skip_confirmation_never_asks(workspace: Path, tmp_path: Path) -> None:
    src = _source(tmp_path)
    asked: list[svc.IngestPreview] = []

    def _confirm(preview: svc.IngestPreview) -> svc.ConfirmationAnswer:
        asked.append(preview)
        return "declined"

    svc.ingest_source(
        workspace,
        src,
        svc.IngestPolicy(skip_confirmation=True),
        ports=_ports([]),
        confirm=_confirm,
    )

    assert asked == []


def test_a_granted_confirmation_is_handed_the_preview_and_proceeds(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    asked: list[svc.IngestPreview] = []

    def _confirm(preview: svc.IngestPreview) -> svc.ConfirmationAnswer:
        asked.append(preview)
        return "proceed"

    outcome = svc.ingest_source(
        workspace, src, svc.IngestPolicy(), ports=_ports([]), confirm=_confirm
    )

    assert isinstance(outcome, svc.IngestWritten)
    assert [p.slug for p in asked] == ["notes"]


def test_a_missing_source_is_a_typed_refusal_carrying_the_cli_wording(
    workspace: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "elsewhere" / "gone.txt"

    with pytest.raises(svc.SourceNotReadable) as excinfo:
        svc.ingest_source(
            workspace,
            gone,
            svc.IngestPolicy(skip_confirmation=True),
            ports=_ports([]),
        )

    assert excinfo.value.message == (
        f"openkos ingest: refusing to ingest -- '{gone}' does not exist "
        "or is not a readable file."
    )


def test_a_directory_that_is_not_a_workspace_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text(_NOTES, encoding="utf-8")
    bare = tmp_path / "bare"
    bare.mkdir()

    with pytest.raises(svc.NotAWorkspace):
        svc.ingest_source(
            bare, src, svc.IngestPolicy(skip_confirmation=True), ports=_ports([])
        )


def test_changed_bytes_under_a_matched_name_refuse_raw_immutability(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    policy = svc.IngestPolicy(skip_confirmation=True)
    svc.ingest_source(workspace, src, policy, ports=_ports([]))
    src.write_text("Different bytes now.\n", encoding="utf-8")
    before = _snapshot(workspace)

    with pytest.raises(svc.RawImmutabilityRefused):
        svc.ingest_source(workspace, src, policy, ports=_ports([]))

    assert _snapshot(workspace) == before


def test_a_concept_without_its_raw_copy_is_an_inconsistent_workspace(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    policy = svc.IngestPolicy(skip_confirmation=True)
    svc.ingest_source(workspace, src, policy, ports=_ports([]))
    (workspace / "raw" / "notes.txt").unlink()

    with pytest.raises(svc.InconsistentWorkspace):
        svc.ingest_source(workspace, src, policy, ports=_ports([]))


def test_a_target_edited_after_its_snapshot_is_drift_and_nothing_is_written(
    workspace: Path, tmp_path: Path
) -> None:
    """The drift guard runs on the unprompted path too, and the baseline is
    the ONE observation the snapshot port returned (#318): an edit landing
    the instant it returns is caught, never adopted as the new baseline."""
    src = _source(tmp_path)
    index_path = workspace / "bundle" / "index.md"
    concurrent = "hand-edited the instant the snapshot returned\n"
    fired = False

    def _racing(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        data = path.read_bytes()
        result = (data, data.decode("utf-8"))
        if path == index_path and not fired:
            fired = True
            index_path.write_text(concurrent, encoding="utf-8")
        return result

    calls: list[str] = []
    with pytest.raises(svc.DriftDetected) as excinfo:
        svc.ingest_source(
            workspace,
            src,
            svc.IngestPolicy(skip_confirmation=True),
            ports=_ports(calls, snapshot_read=_racing),
        )

    assert fired
    assert "bundle/index.md" in excinfo.value.message
    assert "Nothing was written." in excinfo.value.message
    assert index_path.read_text(encoding="utf-8") == concurrent
    assert not (workspace / "raw" / "notes.txt").exists()
    assert calls == []


def test_a_malformed_config_is_a_preparation_failure(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    (workspace / "openkos.yaml").write_text("not: valid: yaml: [", encoding="utf-8")

    with pytest.raises(svc.PreparationFailed) as excinfo:
        svc.ingest_source(
            workspace,
            src,
            svc.IngestPolicy(skip_confirmation=True),
            ports=_ports([]),
        )

    assert excinfo.value.message.startswith(
        "openkos ingest: failed while preparing the ingest -- "
    )


def test_refusals_are_never_os_or_value_errors() -> None:
    """The service's own `except (OSError, ValueError)` blocks would swallow
    a refusal that subclassed either one."""
    for cls in (
        svc.SourceNotReadable,
        svc.NotAWorkspace,
        svc.RawImmutabilityRefused,
        svc.InconsistentWorkspace,
        svc.SourceCheckFailed,
        svc.PreparationFailed,
        svc.WriteFailed,
        svc.DriftDetected,
        svc.ConfirmationDeclined,
        svc.ConfirmationUnavailable,
    ):
        assert issubclass(cls, svc.IngestRefused)
        assert not issubclass(cls, (OSError, ValueError))
