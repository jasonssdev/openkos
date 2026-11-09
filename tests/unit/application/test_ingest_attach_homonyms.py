"""Characterization: the exact-title attach rule ignores meeting series and
subject matter (#1736).

This test PINS CURRENT BEHAVIOR, it does not endorse it. Attach-at-ingest
(#1268, ADR-0045) compares only the OKF type and the normalized title key, so
two sources from different meeting series that yield a Decision with the same
title, or two same-type concepts that are homonyms of different things, are
joined into one concept. If a guard (series or provenance compatibility) is
ever added, these assertions are expected to change with it.
"""

from pathlib import Path

import pytest

from openkos.application import ingest_service as svc
from openkos.extraction.concept import ExtractionResult
from openkos.model import okf

from . import test_ingest_service_attach as _base

_bundle_files = _base._bundle_files
_extracts = _base._extracts
_ingest = _base._ingest
workspace = _base.workspace  # re-exported so pytest finds the fixture here


def _decision(body: str) -> ExtractionResult:
    return ExtractionResult(
        type="Decision",
        title="Adopt the new vendor",
        description="Vendor decision.",
        body=body,
    )


def test_identical_decision_title_from_two_series_attaches(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extracts(monkeypatch, _decision("Hardware team: adopt vendor X for sensors."))
    _ingest(workspace, tmp_path, "hardware-weekly-2026-01-05.txt", "hardware weekly\n")
    _extracts(monkeypatch, _decision("Marketing: adopt vendor Y for email."))

    outcome = _ingest(
        workspace, tmp_path, "marketing-standup-2026-01-06.txt", "marketing standup\n"
    )

    assert _bundle_files(workspace, "decisions") == ["adopt-the-new-vendor.md"]
    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.attached_count == 1
    meta, body = okf.load_frontmatter(
        (workspace / "bundle" / "decisions" / "adopt-the-new-vendor.md").read_text(
            encoding="utf-8"
        )
    )
    assert meta["provenance"] == [
        "sources/hardware-weekly-2026-01-05",
        "sources/marketing-standup-2026-01-06",
    ]
    assert meta["version"] == 2
    assert "vendor X" in body
    assert "vendor Y" in body


def test_same_type_homonym_with_different_subject_attaches(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def concept(body: str) -> ExtractionResult:
        return ExtractionResult(
            type="Concept", title="Pipeline", description="d", body=body
        )

    _extracts(monkeypatch, concept("A CI/CD pipeline builds and ships code."))
    _ingest(workspace, tmp_path, "devops.txt", "devops notes\n")
    _extracts(monkeypatch, concept("An oil pipeline moves crude across borders."))

    outcome = _ingest(workspace, tmp_path, "energy.txt", "energy notes\n")

    assert _bundle_files(workspace, "concepts") == ["pipeline.md"]
    assert isinstance(outcome, svc.IngestWritten)
    assert outcome.attached_count == 1
