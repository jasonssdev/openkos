"""PyInstaller is a build tool of the desktop app, never a dependency of openkos.

The engine is frozen for the desktop app with PyInstaller (ADR-0056) from a
dedicated build environment synced with ``--group freeze``. A dependency group
is not package metadata, so declaring PyInstaller there keeps it out of what
the wheel and the sdist require; listing it anywhere else would ship it.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
FREEZE_TOOL = "pyinstaller"


def _names(requirements: list[str]) -> set[str]:
    out: set[str] = set()
    for requirement in requirements:
        name = requirement
        for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
            name = name.split(separator, 1)[0]
        out.add(name.lower())
    return out


@pytest.fixture(scope="module")
def project() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_the_freeze_group_carries_pyinstaller(project: dict[str, Any]) -> None:
    assert FREEZE_TOOL in _names(project["dependency-groups"]["freeze"])


def test_pyinstaller_is_not_a_runtime_dependency(project: dict[str, Any]) -> None:
    assert FREEZE_TOOL not in _names(project["project"]["dependencies"])


def test_pyinstaller_is_in_no_extra(project: dict[str, Any]) -> None:
    for name, requirements in project["project"]["optional-dependencies"].items():
        assert FREEZE_TOOL not in _names(requirements), name


def test_the_development_environment_does_not_install_pyinstaller(
    project: dict[str, Any],
) -> None:
    assert FREEZE_TOOL not in _names(project["dependency-groups"]["dev"])
