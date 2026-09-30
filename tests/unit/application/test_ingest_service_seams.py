"""Absence guards for the ingest application-service extraction (issue
#1138), the `test_lifecycle_seams.py` pattern: the names that moved off
`openkos.cli.main` are deliberately never aliased back, so a stale
`monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads."""

import inspect

import pytest

from openkos.application import drift as application_drift
from openkos.application import ingest as application_ingest
from openkos.application import ingest_service
from openkos.cli import main as cli_main


@pytest.mark.parametrize(
    "name",
    [
        "_SingleIngestOutcome",
        "_raw_collision_family",
        "_first_free_raw_name",
        "_member_source_exists",
        "_raw_member_origin_key",
        "_RawDestination",
        "_resolve_raw_destination",
        "is_timeout_failure",
        "fans_out",
        "FAN_OUT_CONCURRENCY",
    ],
)
def test_moved_ingest_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(application_ingest.resolve_raw_destination)
    assert callable(application_ingest.raw_collision_family)
    assert callable(application_ingest.first_free_raw_name)
    assert callable(application_drift.describe_drift)
    assert callable(ingest_service.ingest_source)


def test_ingest_single_is_a_thin_adapter_that_delegates_to_the_service() -> None:
    """`_ingest_single` stays on `cli.main` -- `ingest` and `_ingest_batch`
    both call it -- but as an adapter: it must hand the work to
    `ingest_service.ingest_source` rather than carry the orchestration."""
    source = inspect.getsource(cli_main._ingest_single)
    assert "ingest_service.ingest_source(" in source
    assert "fsio." not in source
    assert "_reject_drifted_targets" not in source
