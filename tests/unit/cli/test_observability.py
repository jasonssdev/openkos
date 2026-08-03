"""Unit tests for `cli/observability.py`'s `warn_if_walk_incomplete` helper
(directory-walk-observability, S3 follow-up).

Isolated unit coverage for the CLI-layer signal helper, exercised directly
(not through a Typer command) rather than through any of the six
sensitivity-filter verbs it is wired into -- see the per-verb CLI test
files (`test_query.py`, `test_adjudicate.py`, `test_contradictions.py`,
`test_suggest_relations.py`, `test_suggest_volatility.py`, `test_curate.py`)
for the end-to-end wiring coverage.

Since #356 the helper carries TWO messages over one walk, so every
assertion here names the one it is about through text unique to that
message (`this command's inputs are incomplete` for the general advisory,
`confidential-content filter` for the filter-scoped one). The
`bundle scan was incomplete` prefix both share is deliberately never
asserted on alone: it would pass for either, which is exactly the
distinction these tests exist to hold.
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


def test_warn_if_walk_incomplete_emits_both_advisories_with_no_hatch_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With NEITHER hatch set, an incomplete walk (`okf._walk_errors`
    non-empty) prints BOTH advisories to STDERR and returns normally -- no
    exception, no exit code of its own; this helper is signal-only (spec:
    Incomplete walk warns and still exits 0).

    Both are true at once here, and each states its own truth: the inputs
    to this command are incomplete (#356) AND the confidential-content
    filter could not inspect every document (the original signal). They are
    asserted through text UNIQUE to each, never through the prefix they
    share -- `bundle scan was incomplete` now opens both lines, so it can no
    longer discriminate between them."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "this command's inputs are incomplete" in captured.err
    assert "confidential-content filter" in captured.err
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


def test_warn_if_walk_incomplete_silent_on_clean_bundle_under_the_exemption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fully readable bundle stays silent under the exemption too (#356).

    The incomplete-inputs advisory is never suppressed by a hatch, so the
    ONLY thing keeping it quiet on a healthy bundle is the walk itself
    coming back empty. Without this test, an advisory that fired
    unconditionally -- on every stock run, over every readable bundle --
    would pass the rest of this module, since the other exemption tests all
    break the walk first."""
    (tmp_path / "clean.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )

    observability.warn_if_walk_incomplete(tmp_path, local_exemption=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_warn_if_walk_incomplete_include_confidential_keeps_the_general_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`include_confidential=True` suppresses the FILTER-SCOPED message only
    -- the incomplete-inputs advisory still fires (#356).

    The suppression is still right for the message it governs: the filter is
    deliberately off, so "some confidential material may not have been
    excluded" would be a claim about a filter that never ran. It is wrong for
    everything ELSE the same unreadable subtree truncates -- `okf._iter_docs`
    backs the graph projection too -- and that is what the second advisory
    now says, under both hatches."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir, include_confidential=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "this command's inputs are incomplete" in captured.err
    assert "confidential-content filter" not in captured.err


def test_warn_if_walk_incomplete_local_exemption_keeps_the_general_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The confidential local exemption (#240) suppresses the FILTER-SCOPED
    message only, exactly as `include_confidential=True` does -- the
    incomplete-inputs advisory still fires (#356).

    This is the shipped DEFAULT path (local backend, exemption active), so
    before #356 it was the case that emitted nothing at all: the verb ran
    over a truncated bundle, printed a smaller result, and exited 0 with no
    signal anywhere."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir, local_exemption=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "this command's inputs are incomplete" in captured.err
    assert "confidential-content filter" not in captured.err


def test_incomplete_inputs_advisory_points_at_the_verb_that_names_the_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The #356 advisory's second remediation names `openkos status`, NOT
    `openkos lint`.

    It has to point somewhere, because it deliberately does not repeat the
    unreadable paths -- it stays one line however many directories failed,
    and the per-directory detail already exists as an
    `<path>: unreadable directory (...)` finding from `okf.survey_bundle`.
    `status` is what renders those findings (its `Needs attention:` section
    folds `survey.findings` in verbatim); `lint` does NOT, despite being the
    other read verb -- it runs on `lint.collect_docs`, which reuses
    `okf._iter_docs`, the very walk that swallows the error in the first
    place. A `lint` pointer would therefore send a user to a command that
    prints "no findings" over the exact bundle the advisory just warned
    about. `tests/unit/cli/test_status.py` pins the other end of this
    promise: that `status` really does name the directory."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    observability.warn_if_walk_incomplete(bundle_dir, local_exemption=True)

    captured = capsys.readouterr()
    assert "openkos status" in captured.err
    assert "openkos lint" not in captured.err


@pytest.mark.parametrize(
    ("include_confidential", "local_exemption"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["no-hatch", "include-confidential", "local-exemption", "both-hatches"],
)
def test_warn_if_walk_incomplete_walks_exactly_once_per_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_confidential: bool,
    local_exemption: bool,
) -> None:
    """`okf._walk_errors` runs EXACTLY ONCE per call, whatever the hatches
    say -- a cost pin, not a behavior one (#356).

    Since #356 the walk can no longer be skipped: its result is actionable
    even when the filter is off, because an unreadable subtree also shrinks
    the graph projection. The accepted price is one full bundle walk per
    invocation. What is NOT accepted is paying it twice -- one obvious way
    to write two independent advisories is two independent `_walk_errors`
    calls, which would silently double the cost of every one of the six
    verbs, on the default path, for a condition that is almost always
    absent.

    This replaces `..._local_exemption_skips_the_walk_entirely`, which
    pinned the pre-#356 zero-cost bypass that #356 deliberately gives up."""
    from openkos.model import okf

    calls: list[Path] = []

    def _counting_walk_errors(bundle_dir: Path) -> list[OSError]:
        calls.append(bundle_dir)
        return [OSError(13, "Permission denied", "locked")]

    monkeypatch.setattr(okf, "_walk_errors", _counting_walk_errors)

    observability.warn_if_walk_incomplete(
        tmp_path,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    assert calls == [tmp_path]


def test_warn_if_walk_incomplete_mode_refuse_raises_not_implemented(
    tmp_path: Path,
) -> None:
    """`mode="refuse"` raises `NotImplementedError` -- a stable, dead-this-
    slice seam for a future cloud-egress mode that refuses instead of warns
    (design Decision 1)."""
    with pytest.raises(NotImplementedError):
        observability.warn_if_walk_incomplete(tmp_path, mode="refuse")


def test_warn_if_walk_incomplete_mode_refuse_raises_before_any_walk_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`mode="refuse"` is decided BEFORE the walk and before either
    advisory, so the seam never half-runs: no bundle scan is paid for, and
    no line is printed by a call that then raises (#356).

    The mode check has to sit first now that no hatch returns early. It also
    means a hatch no longer pre-empts the seam -- pre-#356 the hatch return
    ran first, so `mode="refuse", include_confidential=True` returned
    silently instead of raising. That was an artifact of the early return,
    not a decision: a future strict mode refuses on THIS condition (the
    bundle was unreadable), which #356 established is independent of whether
    the confidential filter ran at all."""
    bundle_dir = _make_locked_bundle(tmp_path, monkeypatch)

    with pytest.raises(NotImplementedError):
        observability.warn_if_walk_incomplete(
            bundle_dir, mode="refuse", include_confidential=True, local_exemption=True
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# `progress_callback` / `stage_notice` -- TTY-gated progress signals (#190)
# ---------------------------------------------------------------------------


def test_progress_callback_returns_none_when_stderr_is_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stderr is NOT a TTY, `progress_callback` returns `None` -- the
    verb passes no hook at all, so piped/redirected runs stay byte-clean
    with zero per-item overhead (issue #190; this TTY gate is the entire
    NO_COLOR/non-TTY discipline, since no color is ever emitted)."""
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    assert observability.progress_callback("adjudicate", "adjudicating group") is None


def test_progress_callback_emits_verb_noun_counter_line_to_stderr_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On a TTY, the returned callback prints exactly
    `openkos <verb>: <noun> <index>/<total>...` to STDERR per invocation --
    STDOUT stays untouched (it belongs to the verb's report)."""
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    callback = observability.progress_callback("adjudicate", "adjudicating group")

    assert callback is not None
    callback(2, 5, object())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "openkos adjudicate: adjudicating group 2/5...\n"


def test_progress_callback_ignores_the_result_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The third callback argument (the just-built result object) is
    accepted but never rendered -- one generic factory serves every
    library `on_progress` contract regardless of result type (#190)."""
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    callback = observability.progress_callback("reindex", "embedding doc")

    assert callback is not None
    callback(1, 3, "concepts/alpha")

    captured = capsys.readouterr()
    assert captured.err == "openkos reindex: embedding doc 1/3...\n"
    assert "concepts/alpha" not in captured.err


def test_stage_notice_emits_to_stderr_only_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On a TTY, `stage_notice` prints one `openkos <verb>: <message>` line
    to STDERR -- STDOUT stays untouched (issue #190: the single-call
    sibling of `progress_callback` for verbs with ONE long LLM call)."""
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    observability.stage_notice("query", "answering (waiting on the LLM)...")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "openkos query: answering (waiting on the LLM)...\n"


def test_stage_notice_is_silent_when_stderr_is_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When stderr is NOT a TTY, `stage_notice` emits nothing -- piping
    stays clean (issue #190)."""
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    observability.stage_notice("query", "answering (waiting on the LLM)...")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
