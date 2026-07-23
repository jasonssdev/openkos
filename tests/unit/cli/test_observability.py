"""Unit tests for `cli/observability.py`'s `warn_if_walk_incomplete` helper
(directory-walk-observability, S3 follow-up).

Isolated unit coverage for the CLI-layer signal helper, exercised directly
(not through a Typer command) before it is wired into any of the five
sensitivity-filter verbs -- see the per-verb CLI test files
(`test_query.py`, `test_adjudicate.py`, `test_contradictions.py`,
`test_suggest_relations.py`, `test_suggest_volatility.py`) for the
end-to-end wiring coverage.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from openkos.cli import observability


def _make_locked_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write one readable doc plus an unreadable subdirectory, and
    monkeypatch `os.walk`'s `onerror` hook to report it deterministically
    (mirrors `tests/unit/model/test_okf.py`'s
    `test_survey_bundle_reports_unreadable_subdirectory_as_finding` -- no
    real `chmod`, so the test is portable and deterministic)."""
    (tmp_path / "readable.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    walk_error = OSError(13, "Permission denied", str(locked_dir))

    original_walk = os.walk

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)
    return tmp_path


def test_warn_if_walk_incomplete_warns_on_incomplete_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An incomplete walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and returns normally -- no exception,
    no exit code of its own; this helper is signal-only (spec: Incomplete
    walk warns and still exits 0)."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "bundle scan was incomplete" in captured.err
    assert "--include-confidential" in captured.err


def test_warn_if_walk_incomplete_silent_on_clean_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fully readable bundle (`okf._walk_errors` empty) produces no STDERR
    output at all (spec: Clean bundle produces no warning)."""
    (tmp_path / "clean.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )

    observability.warn_if_walk_incomplete(tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_warn_if_walk_incomplete_include_confidential_suppresses_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`include_confidential=True` suppresses the warning even when the walk
    is incomplete -- the filter is deliberately off, so an incomplete walk
    has no bearing on what gets sent (spec: `--include-confidential`
    suppresses the warning)."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir, include_confidential=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_warn_if_walk_incomplete_mode_refuse_raises_not_implemented(
    tmp_path: Path,
) -> None:
    """`mode="refuse"` raises `NotImplementedError` -- a stable, dead-this-
    slice seam for a future cloud-egress mode that refuses instead of warns
    (design Decision 1)."""
    with pytest.raises(NotImplementedError):
        observability.warn_if_walk_incomplete(tmp_path, mode="refuse")
