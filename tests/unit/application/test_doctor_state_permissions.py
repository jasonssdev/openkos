"""`doctor`'s engine-state permissions check (#1135).

Reports an existing `.openkos/`, a store inside it, or `bundle/.state/` that
group or other can read; says nothing on a healthy workspace so the fixed
check count every other `doctor` test pins is unchanged.
"""

import sys
from pathlib import Path

import pytest

from openkos import config
from openkos.application import doctor as doctor_service
from tests.unit.application.test_doctor_service import _FakeBackend, _workspace

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits are not meaningful on Windows",
)

LABEL = "Engine state is owner-only"


def _private_state(layout: config.WorkspaceLayout) -> None:
    layout.openkos_dir.mkdir(mode=0o700)
    layout.openkos_dir.chmod(0o700)
    store = layout.fts_db_path
    store.write_bytes(b"")
    store.chmod(0o600)


def test_a_healthy_workspace_reports_nothing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _private_state(layout)

    assert doctor_service.check_state_permissions(layout.root) is None


def test_a_workspace_without_engine_state_reports_nothing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)

    assert doctor_service.check_state_permissions(layout.root) is None


def test_a_world_readable_directory_and_store_are_named_with_the_fix(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _private_state(layout)
    layout.openkos_dir.chmod(0o755)
    layout.fts_db_path.chmod(0o644)

    result = doctor_service.check_state_permissions(layout.root)

    assert result is not None
    assert result.label == LABEL
    assert result.status == "fail"
    assert result.critical is False
    assert result.detail is not None
    assert ".openkos (mode 755)" in result.detail
    assert ".openkos/fts.db (mode 644)" in result.detail
    assert result.remediation == "chmod go-rwx .openkos .openkos/fts.db"


def test_a_group_readable_wal_sidecar_is_reported(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _private_state(layout)
    wal = layout.openkos_dir / "fts.db-wal"
    wal.write_bytes(b"")
    wal.chmod(0o640)

    result = doctor_service.check_state_permissions(layout.root)

    assert result is not None
    assert result.detail is not None
    assert ".openkos/fts.db-wal (mode 640)" in result.detail
    assert result.remediation == "chmod go-rwx .openkos/fts.db-wal"


def test_an_exposed_bundle_state_directory_is_reported(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    state_dir = layout.bundle_dir / ".state"
    state_dir.mkdir()
    state_dir.chmod(0o755)

    result = doctor_service.check_state_permissions(layout.root)

    assert result is not None
    assert result.detail is not None
    assert "bundle/.state (mode 755)" in result.detail
    assert result.remediation == "chmod go-rwx bundle/.state"


def test_run_diagnostics_appends_the_finding_last_and_only_when_exposed(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _private_state(layout)

    def run() -> tuple[doctor_service.CheckResult, ...]:
        return doctor_service.run_diagnostics(
            layout.root,
            build_client=lambda _cfg, _model: _FakeBackend(
                tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
            ),
            git_available=True,
            filter_repo_available=True,
            reset_point_available=lambda: True,
        )

    healthy = run()
    assert LABEL not in [r.label for r in healthy]

    layout.openkos_dir.chmod(0o755)
    exposed = run()
    assert [r.label for r in exposed][:-1] == [r.label for r in healthy]
    assert exposed[-1].label == LABEL
