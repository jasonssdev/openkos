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
    the instant it returns is caught, never adopted as the new baseline.
    The rewritten target is the existing Source concept; `index.md` and
    `log.md` are re-composed at the commit phase instead (#1137)."""
    src = _source(tmp_path)
    policy = svc.IngestPolicy(skip_confirmation=True, re_extract=True)
    svc.ingest_source(workspace, src, policy, ports=_ports([]))
    concept_path = workspace / "bundle" / "sources" / "notes.md"
    concurrent = "hand-edited the instant the snapshot returned\n"
    fired = False

    def _racing(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        data = path.read_bytes()
        result = (data, data.decode("utf-8"))
        if path == concept_path and not fired:
            fired = True
            concept_path.write_text(concurrent, encoding="utf-8")
        return result

    calls: list[str] = []
    with pytest.raises(svc.DriftDetected) as excinfo:
        svc.ingest_source(
            workspace,
            src,
            policy,
            ports=_ports(calls, snapshot_read=_racing),
        )

    assert fired
    assert "bundle/sources/notes.md" in excinfo.value.message
    assert "Nothing was written." in excinfo.value.message
    assert concept_path.read_text(encoding="utf-8") == concurrent
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
        svc.SymlinkedDestination,
        svc.PreparationFailed,
        svc.WriteFailed,
        svc.DriftDetected,
        svc.ConfirmationDeclined,
        svc.ConfirmationUnavailable,
    ):
        assert issubclass(cls, svc.IngestRefused)
        assert not issubclass(cls, (OSError, ValueError))


def test_a_symlinked_destination_directory_is_refused_before_any_write(
    workspace: Path, tmp_path: Path
) -> None:
    """#1126: `write_exclusive` follows a symlinked PARENT, so a linked
    `bundle/sources` would carry the source out of the workspace. The check
    sits before extraction and before the first write, inside the service."""
    outside = tmp_path / "outside"
    outside.mkdir()
    sources = workspace / "bundle" / "sources"
    if sources.exists():
        sources.rmdir()
    try:
        sources.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform without symlinks
        pytest.skip(f"symlink privilege unavailable: {exc}")
    src = _source(tmp_path)
    calls: list[str] = []

    with pytest.raises(svc.SymlinkedDestination) as excinfo:
        svc.ingest_source(
            workspace,
            src,
            svc.IngestPolicy(skip_confirmation=True),
            ports=_ports(calls),
        )

    assert "is a symlink" in excinfo.value.message
    assert list(outside.iterdir()) == []
    assert not (workspace / "raw" / "notes.txt").exists()
    assert calls == []


# -- source supersession (#1212, #1224) ---------------------------------------

_VERSIONING = svc.IngestPolicy(skip_confirmation=True, version_changed=True)
_PLAIN = svc.IngestPolicy(skip_confirmation=True)


def _frontmatter(root: Path, concept: str) -> dict[str, object]:
    from openkos.model import okf

    text = (root / "bundle" / "sources" / f"{concept}.md").read_text(encoding="utf-8")
    return okf.load_frontmatter(text)[0]


def test_changed_bytes_import_as_a_new_version_when_asked(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    svc.ingest_source(workspace, src, _PLAIN, ports=_ports([]))
    first_raw = (workspace / "raw" / "notes.txt").read_bytes()
    src.write_text("Edited notes about self-control.\n", encoding="utf-8")

    outcome = svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))

    assert (workspace / "raw" / "notes.txt").read_bytes() == first_raw
    assert (workspace / "raw" / "notes-2.txt").read_text(encoding="utf-8") == (
        "Edited notes about self-control.\n"
    )
    assert outcome.supersessions == (
        svc.Supersession(
            source_id="sources/notes-2",
            previous_id="sources/notes",
            reason="new_version",
        ),
    )
    assert (
        _frontmatter(workspace, "notes-2")["origin_key"]
        == (_frontmatter(workspace, "notes")["origin_key"])
    )


def test_a_third_version_supersedes_the_second(workspace: Path, tmp_path: Path) -> None:
    src = _source(tmp_path)
    svc.ingest_source(workspace, src, _PLAIN, ports=_ports([]))
    src.write_text("Second.\n", encoding="utf-8")
    svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))
    src.write_text("Third.\n", encoding="utf-8")

    outcome = svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))

    assert (workspace / "raw" / "notes-3.txt").read_text(encoding="utf-8") == (
        "Third.\n"
    )
    assert [(s.source_id, s.previous_id) for s in outcome.supersessions] == [
        ("sources/notes-3", "sources/notes-2")
    ]


def test_re_ingesting_an_already_imported_version_changes_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    svc.ingest_source(workspace, src, _PLAIN, ports=_ports([]))
    src.write_text("Second.\n", encoding="utf-8")
    svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))
    before = _snapshot(workspace)

    outcome = svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))

    assert outcome.supersessions == ()
    assert _snapshot(workspace) == before


def test_restoring_an_earlier_version_imports_nothing_new(
    workspace: Path, tmp_path: Path
) -> None:
    src = _source(tmp_path)
    svc.ingest_source(workspace, src, _PLAIN, ports=_ports([]))
    src.write_text("Second.\n", encoding="utf-8")
    svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))
    src.write_text(_NOTES, encoding="utf-8")
    before = _snapshot(workspace)

    outcome = svc.ingest_source(workspace, src, _VERSIONING, ports=_ports([]))

    assert outcome.supersessions == ()
    assert _snapshot(workspace) == before


def test_a_dead_source_is_offered_for_supersession_by_its_replacement(
    workspace: Path, tmp_path: Path
) -> None:
    dead = tmp_path / "elsewhere" / "notes.txt"
    dead.write_bytes(b"\xff\x00\x01\x02")
    svc.ingest_source(workspace, dead, _PLAIN, ports=_ports([]))
    other = tmp_path / "converted"
    other.mkdir()
    fixed = other / "notes.txt"
    fixed.write_text(_NOTES, encoding="utf-8")
    observer = _Recorder()

    outcome = svc.ingest_source(
        workspace, fixed, _PLAIN, ports=_ports([]), observer=observer
    )

    assert outcome.supersessions == (
        svc.Supersession(
            source_id="sources/notes-2",
            previous_id="sources/notes",
            reason="dead_source",
        ),
    )
    assert any(
        "openkos relate sources/notes-2 supersedes sources/notes" in n
        for n in observer.notices
    )


def test_a_healthy_neighbour_is_never_offered_for_supersession(
    workspace: Path, tmp_path: Path
) -> None:
    svc.ingest_source(workspace, _source(tmp_path), _PLAIN, ports=_ports([]))
    other = tmp_path / "converted"
    other.mkdir()
    fixed = other / "notes.txt"
    fixed.write_text("A different document.\n", encoding="utf-8")
    observer = _Recorder()

    outcome = svc.ingest_source(
        workspace, fixed, _PLAIN, ports=_ports([]), observer=observer
    )

    assert outcome.supersessions == ()
    assert not any("supersedes" in n for n in observer.notices)


def test_a_replacement_that_is_itself_dead_supersedes_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    dead = tmp_path / "elsewhere" / "notes.txt"
    dead.write_bytes(b"\xff\x00\x01\x02")
    svc.ingest_source(workspace, dead, _PLAIN, ports=_ports([]))
    other = tmp_path / "converted"
    other.mkdir()
    also_dead = other / "notes.txt"
    also_dead.write_bytes(b"\xff\x00\x09\x08")

    outcome = svc.ingest_source(workspace, also_dead, _PLAIN, ports=_ports([]))

    assert outcome.supersessions == ()


def test_an_already_superseded_dead_source_is_not_offered_again(
    workspace: Path, tmp_path: Path
) -> None:
    dead = tmp_path / "elsewhere" / "notes.txt"
    dead.write_bytes(b"\xff\x00\x01\x02")
    svc.ingest_source(workspace, dead, _PLAIN, ports=_ports([]))
    concept = workspace / "bundle" / "sources" / "notes.md"
    text = concept.read_text(encoding="utf-8")
    assert "\nstatus: " in text
    concept.write_text(
        text.replace("\nstatus: stable", "\nstatus: deprecated", 1), encoding="utf-8"
    )
    other = tmp_path / "converted"
    other.mkdir()
    fixed = other / "notes.txt"
    fixed.write_text(_NOTES, encoding="utf-8")

    outcome = svc.ingest_source(workspace, fixed, _PLAIN, ports=_ports([]))

    assert outcome.supersessions == ()
