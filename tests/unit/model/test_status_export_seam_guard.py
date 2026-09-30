"""Seam guard for the deprecated-status export (`deprecated-status-export`,
issue #1075): ALL knowledge of `status_derived_from` and every raw
`.get("status")` frontmatter read MUST live in `model/okf.py` and nowhere
else (spec: "The Export Marker Is An Engine-Owned Extension Key"; design
Interfaces/Contracts: "A test greps `src/openkos` for the string outside
that file and for raw `.get("status")` reads outside it").

A literal text scan, not an AST walk, mirroring the task's own wording
("greps `src/openkos`") -- simple and sufficient for two fixed string
patterns with no legitimate false-positive shape anywhere else in the tree
(unlike `test_sources_key_guard.py`'s `"provenance"` key, which collides
with an unrelated MCP payload key)."""

import re
from collections.abc import Iterable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "openkos"
_OKF_MODULE = _SRC_ROOT / "model" / "okf.py"

_GET_STATUS_PATTERN = re.compile(r'\.get\(\s*["\']status["\']')


def find_status_derived_from_outside_okf(
    paths: Iterable[Path] | None = None,
) -> list[str]:
    """Every occurrence of the literal string `status_derived_from` across
    `paths` (default: every `.py` file under `src/openkos/` except
    `model/okf.py`)."""
    scan_paths = (
        list(paths)
        if paths is not None
        else sorted(p for p in _SRC_ROOT.rglob("*.py") if p != _OKF_MODULE)
    )
    violations: list[str] = []
    for path in scan_paths:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "status_derived_from" in line:
                violations.append(f"{path}:{lineno}: {line.strip()}")
    return violations


def find_raw_status_get_outside_okf(paths: Iterable[Path] | None = None) -> list[str]:
    """Every `.get("status")`/`.get('status')` call across `paths` (default:
    every `.py` file under `src/openkos/` except `model/okf.py`)."""
    scan_paths = (
        list(paths)
        if paths is not None
        else sorted(p for p in _SRC_ROOT.rglob("*.py") if p != _OKF_MODULE)
    )
    violations: list[str] = []
    for path in scan_paths:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _GET_STATUS_PATTERN.search(line):
                violations.append(f"{path}:{lineno}: {line.strip()}")
    return violations


def test_no_module_outside_okf_mentions_status_derived_from() -> None:
    assert find_status_derived_from_outside_okf() == []


def test_no_module_outside_okf_reads_raw_status_key() -> None:
    assert find_raw_status_get_outside_okf() == []


def test_status_derived_from_guard_catches_a_planted_violation(
    tmp_path: Path,
) -> None:
    """Mutation-proof: a planted fixture file mentioning `status_derived_from`
    is reported when scanned via `paths=`."""
    violation_file = tmp_path / "planted_violation.py"
    violation_file.write_text('MARKER = "status_derived_from"\n', encoding="utf-8")

    violations = find_status_derived_from_outside_okf(paths=[violation_file])

    assert len(violations) == 1
    assert "status_derived_from" in violations[0]


def test_raw_status_get_guard_catches_a_planted_violation(tmp_path: Path) -> None:
    """Mutation-proof: a planted fixture file with a raw `.get("status")`
    read is reported when scanned via `paths=`."""
    violation_file = tmp_path / "planted_violation.py"
    violation_file.write_text(
        'def f(metadata):\n    return metadata.get("status")\n', encoding="utf-8"
    )

    violations = find_raw_status_get_outside_okf(paths=[violation_file])

    assert len(violations) == 1
    assert "status" in violations[0]
