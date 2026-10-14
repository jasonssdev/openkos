"""Python 3.14 is the only supported interpreter, and every source agrees (ADR-0054).

The interpreter is declared in several places that nothing links: the package
metadata, the type checker's target, the local interpreter pin, and every CI and
release job. Prose cannot keep them in step, and a drift is silent: a job pinned
to an older interpreter still passes while testing something nobody ships. This
test reads each source and fails when any of them names a different version.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FLOOR = "3.14"
_WORKFLOWS = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_requires_python_is_the_floor() -> None:
    project = _pyproject()["project"]
    assert project["requires-python"] == f">={_FLOOR}"


def test_only_the_floor_is_classified() -> None:
    project = _pyproject()["project"]
    versions = [
        c.rsplit("::", 1)[1].strip()
        for c in project["classifiers"]
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)
    ]
    assert versions == [_FLOOR]


def test_mypy_analyses_for_the_floor() -> None:
    tool = _pyproject()["tool"]
    assert tool["mypy"]["python_version"] == _FLOOR


def test_local_interpreter_pin_is_the_floor() -> None:
    pin = (_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pin == _FLOOR


def test_there_are_workflows_to_check() -> None:
    """Guards the parametrised test below from passing over an empty glob."""
    assert {w.name for w in _WORKFLOWS} >= {"ci.yml", "release.yml"}


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_interpreter_is_the_floor(workflow: Path) -> None:
    """Every `python-version:` value, a scalar or a matrix list, names only the floor."""
    text = workflow.read_text(encoding="utf-8")
    found: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*python-version:\s*(.+?)\s*$", line)
        if match:
            found.extend(re.findall(r"\d+\.\d+", match.group(1)))
    assert set(found) <= {_FLOOR}, f"{workflow.name} names {sorted(set(found))}"


def test_ci_pins_an_interpreter_somewhere() -> None:
    """Without this the workflow test above could pass because nothing matched."""
    text = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*python-version:", text, re.MULTILINE)
