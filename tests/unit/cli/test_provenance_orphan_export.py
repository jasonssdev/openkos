"""retire-superseded-sources, task 1.5: the deprecated-status export stays
EDGE-ONLY. A provenance orphan of a superseded Source is hidden at read time
by `lifecycle.deprecated_concept_ids`, but no writer exports it into
frontmatter and no reader reports it as drift (design D2)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lifecycle, lint
from openkos.application import repair as application_repair
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    for name in ("v1.txt", "v2.txt"):
        (tmp_path / name).write_text("content", encoding="utf-8")
        assert runner.invoke(app, ["ingest", name, "--auto"]).exit_code == 0
    bundle_dir = tmp_path / "bundle"
    orphan = bundle_dir / "concepts" / "orphan.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Orphan",
                "sensitivity": "private",
                "provenance": ["sources/v1"],
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    return bundle_dir, orphan


def test_relate_unrelate_never_write_a_provenance_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir, orphan = _setup(tmp_path, monkeypatch)
    before = orphan.read_bytes()

    related = runner.invoke(
        app, ["relate", "sources/v2", "supersedes", "sources/v1", "--auto"]
    )
    assert related.exit_code == 0
    assert "concepts/orphan" in lifecycle.deprecated_concept_ids(bundle_dir)
    assert orphan.read_bytes() == before

    unrelated = runner.invoke(
        app, ["unrelate", "sources/v2", "supersedes", "sources/v1", "--auto"]
    )
    assert unrelated.exit_code == 0
    assert "concepts/orphan" not in lifecycle.deprecated_concept_ids(bundle_dir)
    assert orphan.read_bytes() == before


def test_export_input_repair_and_lint_ignore_a_provenance_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir, orphan = _setup(tmp_path, monkeypatch)
    assert (
        runner.invoke(
            app, ["relate", "sources/v2", "supersedes", "sources/v1", "--auto"]
        ).exit_code
        == 0
    )
    before = orphan.read_bytes()

    superseded = lifecycle.superseded_concept_ids(bundle_dir)
    assert superseded.ids == frozenset({"sources/v1"})

    plan = application_repair.plan_repair(bundle_dir)
    assert all(
        rw.changes.export is okf.ExportOutcome.UNCHANGED
        for rw in plan.document_rewrites
        if rw.concept_id == "concepts/orphan"
    )

    findings, _ = lint.check_status_export(bundle_dir)
    assert all(f.path != "concepts/orphan.md" for f in findings)
    assert orphan.read_bytes() == before
