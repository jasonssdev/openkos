"""Unit tests for `lint.check_state_dir_contains_no_markdown` /
`lint.scan_markdown_under_state_dir` (task 3.6, safety net for design
Decision 3's EXCLUDE/INCLUDE separation).

Deliberately a NAMES-ONLY walk, mirroring `check_non_nfc_names`'s own
rationale: `collect_docs`/`okf._iter_docs` never descends into
`bundle/.state/` at all (that IS the free structural exclusion), so there
is no shared walk to fold this into -- it must open its own."""

from pathlib import Path

from openkos import lint
from openkos.model import okf


def test_no_state_dir_yields_no_findings(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert lint.check_state_dir_contains_no_markdown(bundle_dir) == []
    assert lint.scan_markdown_under_state_dir(bundle_dir) == []


def test_state_dir_with_only_ledger_sidecars_yields_no_findings(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    ledger_dir = bundle_dir / okf.STATE_DIRNAME / "ledger" / "concepts"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "survivor.ledger.okf").write_text(
        "---\nschema: openkos.merge_ledger_sidecar/v1\n---\n", encoding="utf-8"
    )

    assert lint.check_state_dir_contains_no_markdown(bundle_dir) == []


def test_stray_markdown_file_under_state_dir_is_flagged(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    stray_dir = bundle_dir / okf.STATE_DIRNAME / "ledger"
    stray_dir.mkdir(parents=True)
    stray_path = stray_dir / "stray.md"
    stray_path.write_text("Body.\n", encoding="utf-8")

    findings = lint.check_state_dir_contains_no_markdown(bundle_dir)

    assert len(findings) == 1
    (finding,) = findings
    assert finding.kind == "state-dir-markdown"
    assert finding.path == ".state/ledger/stray.md"
    assert "bundle/.state/" in finding.detail


def test_multiple_stray_markdown_files_are_sorted_by_path(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    state_dir = bundle_dir / okf.STATE_DIRNAME
    state_dir.mkdir(parents=True)
    (state_dir / "b.md").write_text("B.\n", encoding="utf-8")
    (state_dir / "a.md").write_text("A.\n", encoding="utf-8")

    findings = lint.check_state_dir_contains_no_markdown(bundle_dir)

    assert [finding.path for finding in findings] == [
        ".state/a.md",
        ".state/b.md",
    ]


def test_markdown_outside_state_dir_is_not_flagged(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "ordinary.md").write_text(
        "---\ntype: Concept\ntitle: Ordinary\n---\nBody.\n", encoding="utf-8"
    )

    assert lint.check_state_dir_contains_no_markdown(bundle_dir) == []
