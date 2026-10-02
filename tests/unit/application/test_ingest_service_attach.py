"""`ingest_source` revises an existing concept when a later source yields a
same-type, same-key object, instead of writing `<slug>-N` (attach-at-ingest,
#1268): the guarded atomic rewrite, the commit shape, the kill switch and the
interrupted-run idempotency."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import ingest as application_ingest
from openkos.application import ingest_service as svc
from openkos.cli.main import app
from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import ExtractionResult
from openkos.llm.base import Message
from openkos.model import okf


class _LLM:
    def chat(self, messages: Sequence[Message]) -> str:  # pragma: no cover
        raise AssertionError("the extractor is patched; no model call expected")


def _ports(calls: list[str]) -> svc.IngestPorts:
    def _autocommit(root: Path, paths: Sequence[str], message: str) -> None:
        calls.append(f"{message}|{','.join(paths)}")

    return svc.IngestPorts(
        chat_client=lambda cfg: _LLM(),
        autocommit=_autocommit,
        after_commit=lambda layout, cfg: None,
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


def _extracts(monkeypatch: pytest.MonkeyPatch, *results: ExtractionResult) -> None:
    def run(*args: Any, **kwargs: Any) -> concept_mod.ExtractionOutcome:
        return concept_mod.ExtractionOutcome(
            objects=list(results),
            report=concept_mod.ExtractionReport(
                produced=len(results), retained=len(results), chunks=1, runs=1
            ),
        )

    monkeypatch.setattr(application_ingest, "extract_concept_union", run)


def _skill(
    body: str = "Skills ship scripts.", type_: str = "Concept"
) -> ExtractionResult:
    return ExtractionResult(
        type=type_, title="Skill", description="A reusable capability.", body=body
    )


def _ingest(
    workspace: Path,
    tmp_path: Path,
    name: str,
    text: str,
    *,
    calls: list[str] | None = None,
    observer: svc.IngestObserver | None = None,
    policy: svc.IngestPolicy | None = None,
) -> svc.IngestOutcome:
    src = tmp_path / "elsewhere" / name
    src.write_text(text, encoding="utf-8")
    return svc.ingest_source(
        workspace,
        src,
        policy or svc.IngestPolicy(skip_confirmation=True),
        ports=_ports(calls if calls is not None else []),
        observer=observer,
    )


def _bundle_files(workspace: Path, link_dir: str) -> list[str]:
    return sorted(p.name for p in (workspace / "bundle" / link_dir).glob("*.md"))


def test_a_second_source_attaches_instead_of_forking(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    _extracts(monkeypatch, _skill("Skills can also be shared."))
    calls: list[str] = []

    outcome = _ingest(
        workspace, tmp_path, "b.txt", "notes about skills two\n", calls=calls
    )

    assert _bundle_files(workspace, "concepts") == ["skill.md"]
    meta, body = okf.load_frontmatter(
        (workspace / "bundle" / "concepts" / "skill.md").read_text(encoding="utf-8")
    )
    assert meta["provenance"] == ["sources/a", "sources/b"]
    assert meta["version"] == 2
    assert "## Update from" in body
    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.attached_count == 1
    log = (workspace / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Attach**" in log
    index = (workspace / "bundle" / "index.md").read_text(encoding="utf-8")
    assert index.count("(/concepts/skill.md)") == 1
    assert calls == [
        "openkos: ingest b.txt (+0 concepts, ~1 revised)|"
        "raw/b.txt,bundle/sources/b.md,bundle/concepts/skill.md,"
        "bundle/index.md,bundle/log.md"
    ]


def test_the_commit_message_is_unchanged_when_nothing_attached(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill())
    calls: list[str] = []

    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n", calls=calls)

    assert calls[0].startswith("openkos: ingest a.txt (+1 concepts)|")


def test_the_kill_switch_restores_the_fork(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = workspace / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nattach_at_ingest: false\n",
        encoding="utf-8",
    )
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")

    outcome = _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")

    assert _bundle_files(workspace, "concepts") == ["skill-2.md", "skill.md"]
    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.attached_count == 0


def test_person_still_forks(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill(type_="Person"))
    _ingest(workspace, tmp_path, "a.txt", "notes about people one\n")

    _ingest(workspace, tmp_path, "b.txt", "notes about people two\n")

    assert _bundle_files(workspace, "people") == ["skill-2.md", "skill.md"]


def test_a_changed_attach_target_refuses_the_whole_ingest(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    target = workspace / "bundle" / "concepts" / "skill.md"
    _extracts(monkeypatch, _skill("Skills can also be shared."))

    class _Tamper(svc.IngestObserver):
        def preview(self, preview: svc.IngestPreview) -> None:
            target.write_text(
                target.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8"
            )

    before_raw = sorted(p.name for p in (workspace / "raw").iterdir())
    with pytest.raises(svc.DriftDetected):
        _ingest_confirming(workspace, tmp_path, _Tamper())

    assert sorted(p.name for p in (workspace / "raw").iterdir()) == before_raw
    assert "hand edit" in target.read_text(encoding="utf-8")
    assert not (workspace / "bundle" / "sources" / "b.md").exists()


def _ingest_confirming(
    workspace: Path, tmp_path: Path, observer: svc.IngestObserver
) -> svc.IngestOutcome:
    src = tmp_path / "elsewhere" / "b.txt"
    src.write_text("notes about skills two\n", encoding="utf-8")
    confirm: Callable[[svc.IngestPreview], Any] = lambda preview: "proceed"  # noqa: E731
    return svc.ingest_source(
        workspace,
        src,
        svc.IngestPolicy(skip_confirmation=False),
        ports=_ports([]),
        observer=observer,
        confirm=confirm,
    )


def test_reingesting_the_attaching_source_changes_nothing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    _extracts(monkeypatch, _skill("Skills can also be shared."))
    _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")
    target = workspace / "bundle" / "concepts" / "skill.md"
    revised = target.read_bytes()

    _ingest(
        workspace,
        tmp_path,
        "b.txt",
        "notes about skills two\n",
        policy=svc.IngestPolicy(skip_confirmation=True, re_extract=True),
    )

    assert target.read_bytes() == revised
    assert _bundle_files(workspace, "concepts") == ["skill.md"]
    assert okf.load_frontmatter(revised.decode())[0]["version"] == 2


def test_attach_to_a_deprecated_concept_falls_back_to_a_copy(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    target = workspace / "bundle" / "concepts" / "skill.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "status: stable", "status: deprecated"
        ),
        encoding="utf-8",
    )

    _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")

    assert _bundle_files(workspace, "concepts") == ["skill-2.md", "skill.md"]


def test_attach_raises_the_concepts_sensitivity_to_the_high_water_mark(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = workspace / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "default_sensitivity: private", "default_sensitivity: public"
        ),
        encoding="utf-8",
    )
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "public notes\n")
    _extracts(monkeypatch, _skill("More."))
    _ingest(
        workspace,
        tmp_path,
        "b.txt",
        "---\nsensitivity: confidential\n---\nsecret notes\n",
        policy=svc.IngestPolicy(skip_confirmation=True, include_confidential=True),
    )

    meta, _ = okf.load_frontmatter(
        (workspace / "bundle" / "concepts" / "skill.md").read_text(encoding="utf-8")
    )
    assert meta["sensitivity"] == "confidential"


def test_attach_reads_its_baseline_after_extraction(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept edited WHILE the model was extracting is not clobbered: the
    target is read after extraction returns, so the edit is the baseline."""
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    target = workspace / "bundle" / "concepts" / "skill.md"

    def run(*args: Any, **kwargs: Any) -> concept_mod.ExtractionOutcome:
        target.write_text(
            target.read_text(encoding="utf-8") + "\nedited during extraction\n",
            encoding="utf-8",
        )
        return concept_mod.ExtractionOutcome(
            objects=[_skill("More.")],
            report=concept_mod.ExtractionReport(
                produced=1, retained=1, chunks=1, runs=1
            ),
        )

    monkeypatch.setattr(application_ingest, "extract_concept_union", run)

    _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")

    assert "edited during extraction" in target.read_text(encoding="utf-8")
    assert config.read_config(workspace).attach_at_ingest is True


def test_an_interrupted_attach_is_completed_without_a_second_bump(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run was killed after the attach landed but before the Source lost
    its `ingest_pending` marker (#1136): the next run finds the concept already
    carrying the source, treats it as the same-source no-op, and finishes."""
    _extracts(monkeypatch, _skill())
    _ingest(workspace, tmp_path, "a.txt", "notes about skills one\n")
    _extracts(monkeypatch, _skill("More."))
    _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")
    target = workspace / "bundle" / "concepts" / "skill.md"
    revised = target.read_bytes()
    source_b = workspace / "bundle" / "sources" / "b.md"
    source_b.write_text(
        okf.mark_ingest_pending(source_b.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    outcome = _ingest(workspace, tmp_path, "b.txt", "notes about skills two\n")

    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.attached_count == 0
    assert target.read_bytes() == revised
    assert _bundle_files(workspace, "concepts") == ["skill.md"]
    assert not application_ingest.prior_ingest_pending(
        source_b.read_text(encoding="utf-8")
    )
