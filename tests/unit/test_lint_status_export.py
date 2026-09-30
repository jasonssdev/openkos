"""Unit tests for `lint.check_status_export` (deprecated-status-export,
issue #1075, Phase 2): the read-only drift scan over the deprecated-status
export projection (`okf.project_deprecation_export`).

A dedicated own-walk check, like `check_non_nfc_names` -- it must see a
document's own `status`/marker even when its `relations:` are malformed or
it fails to parse, neither of which `collect_docs` retains (that walk drops
such a doc entirely), so it cannot reuse the shared `docs: list[LintDoc]`.
"""

from pathlib import Path

from openkos import lint
from openkos.model import okf


def _write_doc(
    path: Path,
    *,
    status: str | None = None,
    marker: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    relations_raw: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", "title: Stub"]
    if status is not None:
        lines.append(f"status: {status}")
    if marker is not None:
        lines.append(f"{okf.STATUS_DERIVED_FROM_KEY}: {marker}")
    if relations_raw is not None:
        lines.append(relations_raw)
    elif relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_superseded_concept_without_export_is_reported(tmp_path: Path) -> None:
    """spec: 'A superseded concept without an export is reported'."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="stable",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="stable")

    findings, not_run = lint.check_status_export(bundle_dir)

    assert not_run is None
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "status-export-drift"
    assert finding.path == "concepts/b.md"
    assert "openkos repair" in finding.detail


def test_stale_export_is_reported(tmp_path: Path) -> None:
    """spec: 'A stale export is reported'."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "b.md", status="deprecated", marker="supersedes"
    )

    findings, not_run = lint.check_status_export(bundle_dir)

    assert not_run is None
    assert len(findings) == 1
    assert findings[0].kind == "status-export-drift"
    assert findings[0].path == "concepts/b.md"


def test_blocked_draft_is_reported_not_called_drift(tmp_path: Path) -> None:
    """spec: 'A blocked draft is reported, not called drift'."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="stable",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="draft")

    findings, not_run = lint.check_status_export(bundle_dir)

    assert not_run is None
    kinds = {finding.path: finding.kind for finding in findings}
    assert kinds.get("concepts/b.md") == "status-export-blocked"
    assert all(
        finding.path != "concepts/b.md" or finding.kind != "status-export-drift"
        for finding in findings
    )
    blocked = next(f for f in findings if f.path == "concepts/b.md")
    assert "draft" in blocked.detail


def test_consistent_bundle_reports_nothing(tmp_path: Path) -> None:
    """spec: 'A consistent bundle reports nothing'."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="stable",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md", status="deprecated", marker="supersedes"
    )

    findings, not_run = lint.check_status_export(bundle_dir)

    assert findings == []
    assert not_run is None


def test_incomplete_walk_degrades_stale_half_to_not_run(tmp_path: Path) -> None:
    """spec: 'An incomplete walk degrades the stale half to not-run'."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "b.md", status="deprecated", marker="supersedes"
    )
    (bundle_dir / "concepts" / "broken.md").write_text(
        "not frontmatter at all", encoding="utf-8"
    )

    findings, not_run = lint.check_status_export(bundle_dir)

    assert not any(finding.path == "concepts/b.md" for finding in findings)
    assert not_run is not None
    assert "broken" in not_run.reason


def test_incomplete_walk_still_reports_export_findings(tmp_path: Path) -> None:
    """spec: 'the scan MUST still report EXPORT, BLOCKED, and DROP-MARKER
    findings' even when the walk is incomplete. An unrelated unreadable
    document with NO candidate withdrawal in the bundle does not gate the
    scan on its own -- `not_run` fires only for an actual skipped
    withdrawal, never for incompleteness alone."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        status="stable",
        relations=[("concepts/b", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "b.md", status="stable")
    (bundle_dir / "concepts" / "broken.md").write_text(
        "not frontmatter at all", encoding="utf-8"
    )

    findings, not_run = lint.check_status_export(bundle_dir)

    assert any(
        finding.path == "concepts/b.md" and finding.kind == "status-export-drift"
        for finding in findings
    )
    assert not_run is None
