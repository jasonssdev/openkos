"""Direct unit tests for `openkos.application.lint`: the `lint` command's
read core, extracted out of `cli/main.py` (issue #995, PR 4 of MVP 3's
"prerequisite zero" -- the read verbs need an application service before an
MCP adapter can be a thin layer instead of a second implementation).

Mirrors `test_status_service.py`'s posture: these exercise
`build_lint_report` directly against a real (tmp-path) workspace, never a
CLI invocation. Every individual `check_*` function's own logic (which
findings it raises, on which fixtures) is already covered by
`tests/unit/test_lint*.py`; this file tests ORCHESTRATION only -- that the
service wires `read_config`/`resolve_windows`/`collect_docs`/the thirteen
checks together correctly, propagates an unreadable-workspace error the way
the pre-extraction command body did, shapes the sensitivity split into the
two fields `LintReport` declares, and performs exactly one bundle walk for
the checks that share `docs` (design D3's no-fifth-walk guard).

Unlike `status`, `lint`'s pre-extraction command body computed EVERY read
and check before its first `typer.echo` (see `application.lint`'s module
docstring for the line-range evidence) -- there is no partial-output
property to preserve here, so one function is the whole service, not two."""

from pathlib import Path

import pytest

from openkos import config
from openkos import lint as lint_check
from openkos.application import lint as lint_service


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    """A workspace root with a real `bundle/` directory carrying a minimal
    `index.md` -- `build_lint_report` reads `bundle/index.md` directly (not
    through a CLI `init`), so it must exist before the service is called,
    same posture as `test_status_service.py`'s `_workspace` for `log.md`."""
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    return layout


# --- unreadable workspace: the ONE non-zero exit path the CLI still owns ---


def test_build_lint_report_raises_on_unreadable_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission-denied `bundle/index.md` raises `LintInputUnavailable`.

    That is the typed form of exactly the failure the pre-extraction
    command body caught in its own `try/except (OSError, ValueError)`
    around these same three input reads. The CLI adapter catches this one
    type and turns it into `openkos lint: failed while reading the
    workspace -- ...` and exit 1; a failure from any LATER check is not
    this type and propagates uncaught, as it did before the extraction.
    The distinction that used to live in the CLI's `try` SCOPE is data
    now, which is what a headless adapter needs -- it can branch on the
    type instead of parsing a line of text."""
    layout = _workspace(tmp_path)
    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "index.md":
            raise PermissionError(13, "Permission denied", str(self))
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(lint_service.LintInputUnavailable, match="Permission denied"):
        lint_service.build_lint_report(layout)


def test_build_lint_report_raises_on_malformed_config(tmp_path: Path) -> None:
    """A malformed `openkos.yaml` (not a mapping) makes `config.read_config`
    raise `ValueError`, which the input-read phase re-raises as
    `LintInputUnavailable` -- the second of the three reads the
    pre-extraction body's `try` covered, reaching the operator through the
    same message and exit code."""
    layout = _workspace(tmp_path)
    layout.config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(lint_service.LintInputUnavailable, match="expected a mapping"):
        lint_service.build_lint_report(layout)


# --- exactly one `collect_docs` walk feeds every docs-based check ---


def test_build_lint_report_calls_collect_docs_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight of the thirteen checks (`check_dangling_targets`,
    `check_unextracted`, `check_unjudged`, `check_unevidenced`,
    `check_staging_dropped`, `check_below_source_sensitivity`,
    `check_dangling_provenance`, `check_unbacked_provenance`) plus
    `check_stale_stamps`/`check_orphans` all share ONE in-memory `docs`
    list -- design D3's no-fifth-walk guard, carried over from the
    pre-extraction command body's own comments ("reuses this SAME `docs`
    list -- no new walk"). A second `collect_docs()` call would mean a
    second bundle walk, silently reintroducing the cost that guard exists
    to forbid."""
    layout = _workspace(tmp_path)
    real_collect_docs = lint_check.collect_docs
    call_count = 0

    def counting_collect_docs(
        bundle_dir: Path,
    ) -> tuple[list[lint_check.LintDoc], list[str]]:
        nonlocal call_count
        call_count += 1
        return real_collect_docs(bundle_dir)

    # Patched on the `lint_check` module object itself (the same object
    # `application.lint` imported as `lint_check`, since modules are
    # singletons) -- reaching through `lint_service.lint_check` as an
    # attribute of another module is what mypy's `attr-defined` check on
    # implicit re-exports would flag.
    monkeypatch.setattr(lint_check, "collect_docs", counting_collect_docs)

    lint_service.build_lint_report(layout)

    assert call_count == 1


# --- the sensitivity split: one check, two `LintReport` fields ---


def test_build_lint_report_splits_sensitivity_findings_by_kind(
    tmp_path: Path,
) -> None:
    """`check_below_source_sensitivity` returns ONE list carrying both
    `"below-source-sensitivity"` and `"multi-source-uncovered"` findings
    (design D3) -- `LintReport` declares them as two SEPARATE fields
    (`below_source`/`multi_source_uncovered`), so the service must split by
    `finding.kind`, exactly as the pre-extraction command body did inline.
    One fixture produces one finding of each kind, proving the split lands
    each in its own field, not both in one or the other."""
    layout = _workspace(tmp_path)
    sources_dir = layout.bundle_dir / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    (sources_dir / "c.md").write_text(
        "---\ntype: Source\ntitle: C\nresource: raw/c.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = layout.bundle_dir / "concepts"
    concepts_dir.mkdir()
    # below-source-sensitivity: sole member of `sources/a`'s closure, whose
    # own sensitivity ranks below the combined floor.
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )
    # multi-source-uncovered: cites both `sources/a` and a concept derived
    # from `sources/c` -- a member of no single-Source closure.
    (concepts_dir / "from-c.md").write_text(
        "---\ntype: Concept\ntitle: From C\nsensitivity: confidential\n"
        "provenance:\n  - sources/c\n---\nBody.\n",
        encoding="utf-8",
    )
    (concepts_dir / "mixed.md").write_text(
        "---\ntype: Concept\ntitle: Mixed\nsensitivity: public\n"
        "provenance:\n  - sources/a\n  - concepts/from-c\n---\nBody.\n",
        encoding="utf-8",
    )

    report = lint_service.build_lint_report(layout)

    below_ids = {finding.concept_id for finding in report.below_source}
    multi_ids = {finding.concept_id for finding in report.multi_source_uncovered}
    assert "concepts/derived" in below_ids
    assert "concepts/mixed" in multi_ids
    assert "concepts/mixed" not in below_ids
    assert "concepts/derived" not in multi_ids


# --- notices: window fallback and skip notices are combined ---


def test_build_lint_report_combines_window_and_skip_notices(
    tmp_path: Path,
) -> None:
    """`report.notices` is `window_notices + skip_notices` -- one fallback
    notice from an invalid `freshness_window` and one skip notice from an
    unparseable file must BOTH appear, exactly as the pre-extraction
    command body's single concatenation did."""
    layout = _workspace(tmp_path)
    layout.config_path.write_text(
        layout.config_path.read_text(encoding="utf-8")
        + "freshness_window: not-a-duration\n",
        encoding="utf-8",
    )
    concepts_dir = layout.bundle_dir / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "broken.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    report = lint_service.build_lint_report(layout)

    assert any("not a valid duration" in notice for notice in report.notices)
    assert any(
        "concepts/broken.md: skipped (unparseable frontmatter)" in notice
        for notice in report.notices
    )


# --- read-only: no derived store, no workspace file, is ever created ---


def test_build_lint_report_creates_no_files(tmp_path: Path) -> None:
    """`lint` is read-only, Phase-A only -- `build_lint_report` must never
    create `.openkos/findings.db`, `vectors.db`, or any other file as a
    side effect of reading. Mirrors
    `test_status_service.py::test_build_status_report_creates_no_files`."""
    layout = _workspace(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    lint_service.build_lint_report(layout)

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


# --- LintReport round-trips every section the CLI renders ---


def test_build_lint_report_returns_the_shared_lint_report_type(
    tmp_path: Path,
) -> None:
    """The service returns `lint.LintReport` directly -- the SAME frozen
    dataclass `openkos/lint.py` already declares -- rather than a second,
    application-owned wrapper duplicating its fields. `status` invented
    `StatusReport` because no pre-existing report type existed for it to
    reuse; `lint` already had one, and a second type carrying the same
    thirteen fields would just be a driftable copy."""
    layout = _workspace(tmp_path)

    report = lint_service.build_lint_report(layout)

    assert isinstance(report, lint_check.LintReport)
    assert report.stale == []
    assert report.orphans == []
    assert report.notices == []
