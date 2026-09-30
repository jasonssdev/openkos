"""Crash-window recovery for `ingest` Phase B (#1136).

Phase B is a sequence of individually atomic writes with no transaction
around them. `converged_reingest` decides "already extracted" from the
Source alone, so a process killed after the Source landed and before the
derived objects, `index.md` and `log.md` did must NOT leave a Source the
next ingest skips forever. The contract under test: the Source is written
carrying `ingest_pending` first, and rewritten without it as the LAST write,
so a pending Source is the durable trace of an interrupted run and a
byte-identical re-ingest completes it.

The crash is simulated by making the write at ONE exact point raise a
`BaseException` (a kill is not an `OSError` the CLI could translate), and
each parametrization asserts the crash actually fired -- a stale patch
target would otherwise pass silently.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import fsio
from openkos.application import ingest as application_ingest
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.test_ingest import (
    _GROUNDED_NOTES,
    _concept_reply,
    _multi_object_reply,
    _patch_llm,
    _person_reply,
)
from tests.unit.cli.test_ingest import (
    _init_workspace as _base_init_workspace,
)

runner = CliRunner()

_CONCEPT_FILE = "bundle/concepts/stoic-dichotomy-of-control.md"
_PERSON_FILE = "bundle/people/epictetus.md"
_SOURCE_FILE = "bundle/sources/notes.md"
_TWO_OBJECTS = _multi_object_reply(_concept_reply(), _person_reply())


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh workspace with the union judge off: one extraction call, no
    judge reply to script, and no judge-degrade notice (which is itself
    retryable debt and would mask what these tests pin)."""
    _base_init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nunion_judge: false\n",
        encoding="utf-8",
    )


class _Crash(BaseException):
    """A kill: deliberately not an `Exception`, so no `except (OSError,
    ValueError)` in the CLI can turn it into a tidy exit."""


def _crash_at(
    monkeypatch: pytest.MonkeyPatch, point: str, tmp_path: Path
) -> dict[str, bool]:
    """Make the write at `point` raise `_Crash`, on the module attributes
    `cli.main` actually calls (`main.fsio.*` IS `openkos.fsio`)."""
    fired = {"hit": False}
    real_exclusive = fsio.write_exclusive
    real_atomic = fsio.write_atomic
    source_path = tmp_path / _SOURCE_FILE

    def _maybe(kind: str, path: Path) -> None:
        crash = (
            (kind == "exclusive" and path == source_path and point == "source")
            or (
                kind == "exclusive"
                and path == tmp_path / _CONCEPT_FILE
                and point == "first-derived"
            )
            or (
                kind == "exclusive"
                and path == tmp_path / _PERSON_FILE
                and point == "second-derived"
            )
            or (kind == "atomic" and path.name == "index.md" and point == "index")
            or (kind == "atomic" and path.name == "log.md" and point == "log")
        )
        if kind == "atomic" and path == source_path:
            # On a fresh ingest the only atomic Source write is the final
            # stamp, so it is the "before the stamp" crash point.
            crash = crash or point == "stamp"
        if crash:
            fired["hit"] = True
            raise _Crash(point)

    def exclusive(path: Path, content: str) -> None:
        _maybe("exclusive", Path(path))
        real_exclusive(path, content)

    def atomic(path: Path, content: str) -> None:
        _maybe("atomic", Path(path))
        real_atomic(path, content)

    monkeypatch.setattr(fsio, "write_exclusive", exclusive)
    monkeypatch.setattr(fsio, "write_atomic", atomic)
    return fired


# Crash points, in Phase B order. `raw` needs no patch beyond the Source:
# a crash after the raw copy and before the Source write is "source".
_CRASH_POINTS = [
    "source",
    "first-derived",
    "second-derived",
    "index",
    "log",
    "stamp",
]


@pytest.mark.parametrize("point", _CRASH_POINTS)
def test_reingest_after_a_crash_completes_the_interrupted_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    """A kill at ANY Phase B write leaves a workspace the next byte-identical
    ingest completes: every derived object, its index entry and the Source's
    index entry exist exactly once, and the Source no longer carries
    `ingest_pending`."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with monkeypatch.context() as crashing:
        fired = _crash_at(crashing, point, tmp_path)
        with pytest.raises(_Crash):
            runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert fired["hit"], f"the {point!r} crash point was never reached"

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "source unchanged and already extracted" not in result.stderr
    assert (tmp_path / _CONCEPT_FILE).is_file()
    assert (tmp_path / _PERSON_FILE).is_file()
    for parent in ("concepts", "people"):
        assert len(list((tmp_path / "bundle" / parent).glob("*.md"))) == 1
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index_text.count("(/concepts/stoic-dichotomy-of-control.md)") == 1
    assert index_text.count("(/people/epictetus.md)") == 1
    assert index_text.count("(/sources/notes.md)") == 1
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "/sources/notes.md" in log_text
    metadata, _ = okf.load_frontmatter(
        (tmp_path / _SOURCE_FILE).read_text(encoding="utf-8")
    )
    assert okf.INGEST_PENDING_KEY not in metadata
    # And the workspace is now genuinely converged: a third run skips.
    third = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert third.exit_code == 0
    assert "source unchanged and already extracted" in third.stderr


@pytest.mark.parametrize("point", ["first-derived", "second-derived", "index"])
def test_a_crashed_run_leaves_the_source_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    """The durable trace: a Source written before its derived objects
    carries `ingest_pending: true` until the run completes."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")

    with monkeypatch.context() as crashing:
        fired = _crash_at(crashing, point, tmp_path)
        with pytest.raises(_Crash):
            runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert fired["hit"]

    metadata, _ = okf.load_frontmatter(
        (tmp_path / _SOURCE_FILE).read_text(encoding="utf-8")
    )
    assert metadata[okf.INGEST_PENDING_KEY] is True


def test_a_completed_ingest_carries_no_pending_marker_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker never survives a run that finished, and a finished
    workspace still skips extraction (no LLM call on the second run)."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    calls_after_first = len(fake.calls)
    metadata, _ = okf.load_frontmatter(
        (tmp_path / _SOURCE_FILE).read_text(encoding="utf-8")
    )
    assert okf.INGEST_PENDING_KEY not in metadata

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "source unchanged and already extracted" in result.stderr
    assert len(fake.calls) == calls_after_first


def test_retry_adopts_derived_objects_the_crashed_run_never_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill after the derived writes and before `index.md` leaves objects
    on disk with no catalog entry. The retry's own staging treats them as
    same-source no-ops, so without adoption they would stay orphaned."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    with monkeypatch.context() as crashing:
        _crash_at(crashing, "index", tmp_path)
        with pytest.raises(_Crash):
            runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    before = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "epictetus" not in before

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    after = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "* [Epictetus](/people/epictetus.md) - " in after
    assert "* [Stoic Dichotomy Of Control](/concepts/" in after


def test_raw_copy_without_a_source_is_completed_by_the_next_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill after the raw copy and before the Source write: the raw file
    is there, no Source is. The next ingest must produce the Source and
    everything after it (this point was already recoverable; pinned so the
    reordering cannot regress it)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    with monkeypatch.context() as crashing:
        fired = _crash_at(crashing, "source", tmp_path)
        with pytest.raises(_Crash):
            runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert fired["hit"]
    assert (tmp_path / "raw" / "notes.txt").is_file()
    assert not (tmp_path / _SOURCE_FILE).exists()

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / _SOURCE_FILE).is_file()
    assert (tmp_path / _CONCEPT_FILE).is_file()


def test_re_extract_crash_leaves_a_pending_source_that_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-extract path rewrites an EXISTING converged Source: a kill
    mid-run must flip it to pending, so a plain re-ingest finishes the job
    instead of trusting the stale 'converged' Source."""
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    calls = len(fake.calls)

    with monkeypatch.context() as crashing:
        fired = _crash_at(crashing, "index", tmp_path)
        with pytest.raises(_Crash):
            runner.invoke(app, ["ingest", "notes.txt", "--auto", "--re-extract"])
    assert fired["hit"]

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert "source unchanged and already extracted" not in result.stderr
    assert len(fake.calls) > calls


def test_source_only_date_rewrite_never_goes_through_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A converged Source rewritten for a changed `event_date` extracts
    nothing, so the marker would only force a needless re-extraction after a
    crash: that path stays a single atomic Source write."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    seen: list[str] = []
    real_atomic = fsio.write_atomic

    def spy(path: Path, content: str) -> None:
        if Path(path).name == "notes.md":
            seen.append(content)
        real_atomic(path, content)

    monkeypatch.setattr(fsio, "write_atomic", spy)

    result = runner.invoke(
        app, ["ingest", "notes.txt", "--auto", "--event-date", "2026-01-02"]
    )

    assert result.exit_code == 0, result.stderr
    assert len(seen) == 1
    assert okf.INGEST_PENDING_KEY not in seen[0]


# --- pure predicates -------------------------------------------------------


def _source_text(**extra: object) -> str:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": "Notes",
        okf.ORIGIN_KEY_KEY: "abc",
        **extra,
    }
    return okf.dump_frontmatter(metadata, "# Notes\n")


def test_pending_source_is_retry_due_and_never_converged() -> None:
    text = _source_text(**{okf.INGEST_PENDING_KEY: True})
    metadata, _ = okf.load_frontmatter(text)

    assert application_ingest.extraction_retry_due(metadata) is True
    assert application_ingest.converged_reingest(text, re_extract=False) is None


@pytest.mark.parametrize("value", [False, "true", 1, None, "yes"])
def test_only_a_literal_true_marks_a_source_pending(value: object) -> None:
    """Frontmatter is hand-editable; anything but the literal `true` the
    engine writes reads as not pending (fail-closed toward the pre-existing
    behavior for every workspace that never had the key)."""
    text = _source_text(**{okf.INGEST_PENDING_KEY: value})

    assert application_ingest.converged_reingest(text, re_extract=False) is not None


def test_source_without_the_key_converges_exactly_as_before() -> None:
    """Every Source written before the key existed carries none: it must
    not be forced through a full re-extract."""
    assert (
        application_ingest.converged_reingest(_source_text(), re_extract=False)
        is not None
    )


def test_demo_workspace_sources_are_not_forced_to_re_extract() -> None:
    """The shipped example predates the key. Give each Source the origin
    identity a real ingest would have recorded and the gate must converge."""
    demo = Path(__file__).resolve().parents[3] / "examples" / "good-life-demo"
    sources = sorted((demo / "bundle" / "sources").glob("*.md"))
    assert sources
    for path in sources:
        metadata, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        assert okf.INGEST_PENDING_KEY not in metadata
        metadata[okf.ORIGIN_KEY_KEY] = "sha256:demo"
        text = okf.dump_frontmatter(metadata, body)
        assert application_ingest.converged_reingest(text, re_extract=False) is not None


def test_mark_ingest_pending_only_adds_the_key() -> None:
    """Removing the marker gives back the exact bytes the engine builds for
    the final Source, so the stamp write is the ordinary content."""
    final = okf.build_source_concept(
        title="Notes",
        description="d",
        resource="raw/notes.txt",
        tags=[],
        generated=okf.Generated(by="openkos", at="2026-01-01T00:00:00Z"),
        sensitivity="private",
        raw_content="hello",
        origin_key="k",
    )

    pending = okf.mark_ingest_pending(final)

    metadata, body = okf.load_frontmatter(pending)
    final_metadata, final_body = okf.load_frontmatter(final)
    assert metadata.pop(okf.INGEST_PENDING_KEY) is True
    assert metadata == final_metadata
    assert body == final_body
    assert okf.dump_frontmatter(metadata, body) == final


def test_reingest_predictor_does_not_skip_a_pending_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch cost gate predicts skips through `_reingest_will_skip`; a
    pending Source will be re-extracted, so it must not be counted free."""
    from openkos import config

    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _TWO_OBJECTS)
    src = tmp_path / "notes.txt"
    src.write_text(_GROUNDED_NOTES, encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes.txt", "--auto"]).exit_code == 0
    layout = config.WorkspaceLayout(tmp_path)
    assert main._reingest_will_skip(src, layout) is True

    source_path = tmp_path / _SOURCE_FILE
    source_path.write_text(
        okf.mark_ingest_pending(source_path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    assert main._reingest_will_skip(src, layout) is False
