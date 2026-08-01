"""Unit tests for `backfill-source-titles`, objective 3A (tasks 3.1-3.4):
empty-result short circuit, three-bucket preview, confirm-gate precedence.
Mirrors `test_backfill_sensitivity.py`. Phase B (writing index.md/the
Source/log.md, autocommit) is a later objective, never reached below."""

from pathlib import Path, PurePosixPath

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.bundle.source_titles import titleize
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _write_source(
    tmp_path: Path, *, slug: str, title: str, resource: str, raw: str | None
) -> str:
    """Hand-write a Source (bypassing `ingest`) so `title` and its
    re-derived raw content can diverge -- the pre-#248 state this repairs."""
    content = okf.build_source_concept(
        title=title,
        description="A backfill test fixture.",
        resource=resource,
        tags=[],
        timestamp="2024-01-01T00:00:00Z",
        sensitivity="public",
        provenance=[],
        raw_content=raw,
    )
    path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if raw is not None and resource.startswith("raw/"):
        raw_path = tmp_path / resource
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw, encoding="utf-8")
    return f"sources/{slug}"


def _staged(tmp_path: Path, slug: str) -> str:
    """Mechanically-titled; raw content re-derives a DIFFERENT title."""
    name = f"{slug}.txt"
    return _write_source(
        tmp_path,
        slug=slug,
        title=titleize(PurePosixPath(name).stem),
        resource=f"raw/{name}",
        raw="# Real Title\n\nBody text.",
    )


def _skipped(tmp_path: Path, slug: str) -> str:
    return _write_source(
        tmp_path,
        slug=slug,
        title="A Curated Title",
        resource=f"raw/{slug}.txt",
        raw="content",
    )


def _warned(tmp_path: Path, slug: str) -> str:
    return _write_source(
        tmp_path, slug=slug, title="Untitled", resource="not-raw/x.txt", raw=None
    )


def test_fully_curated_or_warned_bundle_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _skipped(tmp_path, "curated")
    _warned(tmp_path, "warned")

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


def test_bundle_with_no_sources_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


def test_preview_shows_all_three_buckets_before_any_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    curated_id = _skipped(tmp_path, "curated")
    warned_id = _warned(tmp_path, "warned")

    result = runner.invoke(app, ["backfill-source-titles"], input="n\n")

    assert staged_id in result.output
    assert curated_id in result.output
    assert warned_id in result.output


def test_auto_skips_the_prompt_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    assert staged_id in result.output


def test_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    staged_id = _staged(tmp_path, "mechanical")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert staged_id in result.output


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _staged(tmp_path, "mechanical")

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)


def test_declining_the_prompt_performs_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    paths = [
        tmp_path / "bundle" / f"{staged_id}.md",
        tmp_path / "bundle" / "index.md",
        tmp_path / "bundle" / "log.md",
    ]
    before = [p.read_bytes() for p in paths]

    result = runner.invoke(app, ["backfill-source-titles"], input="n\n")

    assert result.exit_code == 1
    assert [p.read_bytes() for p in paths] == before
