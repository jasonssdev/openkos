"""The ``api`` extra declares the local HTTP API's stack as optional.

The framework is confined to ``openkos.localapi`` (ADR-0061), so the core
install must never list it; CI carries it through the dev group.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
API_PACKAGES = ("fastapi", "uvicorn", "pydantic")
DEV_PACKAGES = ("fastapi", "httpx", "pydantic", "uvicorn")


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


@pytest.mark.parametrize("package", API_PACKAGES)
def test_api_extra_lists_each_package(project: dict[str, Any], package: str) -> None:
    extra = project["project"]["optional-dependencies"].get("api", [])
    assert package in _names(extra)


@pytest.mark.parametrize("package", API_PACKAGES)
def test_core_dependencies_exclude_each_package(
    project: dict[str, Any], package: str
) -> None:
    assert package not in _names(project["project"]["dependencies"])


@pytest.mark.parametrize("package", DEV_PACKAGES)
def test_dev_group_carries_each_package(project: dict[str, Any], package: str) -> None:
    assert package in _names(project["dependency-groups"]["dev"])


def test_watch_extra_is_untouched(project: dict[str, Any]) -> None:
    assert project["project"]["optional-dependencies"]["watch"] == ["watchdog>=4.0.0"]
