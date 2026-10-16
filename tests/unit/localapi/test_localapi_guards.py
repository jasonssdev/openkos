"""Import guards for `openkos.localapi` (ADR-0061).

Two directions. Inside the adapter, an allowlist: standard library (but not
`sqlite3`), the three framework packages the adapter may name, exactly
`openkos.api` (never a private `openkos.api._*`) and itself. Outside it, a
confinement scan: no other module under `src/openkos/` imports the framework.
A fresh interpreter proves that starting the CLI never loads any of it.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"
_LOCALAPI = _SRC / "localapi"

_FRAMEWORK = frozenset({"fastapi", "uvicorn", "pydantic"})
_CONFINED = frozenset({"fastapi", "uvicorn", "starlette", "pydantic", "anyio"})
_STDLIB = sys.stdlib_module_names - {"sqlite3"}


def _imports(source: str) -> list[str]:
    """Every module a source file imports, `from openkos import api` being
    `openkos.api`. A relative import is reported with its leading dots."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append("." * node.level + (node.module or ""))
            elif node.module == "openkos":
                found.extend(f"openkos.{alias.name}" for alias in node.names)
            else:
                found.append(node.module or "")
    return found


def _allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top in _STDLIB or top in _FRAMEWORK:
        return True
    return (
        module == "openkos.api"
        or module == "openkos.localapi"
        or module.startswith("openkos.localapi.")
    )


def _violations(package: Path) -> list[str]:
    return [
        f"{path.name}: imports {module}"
        for path in sorted(package.rglob("*.py"))
        for module in _imports(path.read_text(encoding="utf-8"))
        if not _allowed(module)
    ]


def _confinement_offenders(root: Path, *, skip: Path | None) -> list[str]:
    return [
        f"{path.relative_to(root)}: imports {module}"
        for path in sorted(root.rglob("*.py"))
        if skip is None or skip not in path.parents
        for module in _imports(path.read_text(encoding="utf-8"))
        if module.split(".")[0] in _CONFINED
    ]


def _package(tmp_path: Path, source: str) -> Path:
    package = tmp_path / "localapi"
    package.mkdir()
    (package / "_leaky.py").write_text(source, encoding="utf-8")
    return package


# -- inside the adapter ---------------------------------------------------------- #


def test_the_adapter_imports_only_what_it_may() -> None:
    modules = list(_LOCALAPI.rglob("*.py"))

    assert len(modules) >= 5  # the scan is not vacuous
    assert _violations(_LOCALAPI) == []


@pytest.mark.parametrize(
    "line",
    [
        "import openkos.mcp",
        "from openkos.mcp import gate",
        "from openkos.mcp.gate import check",
        "import openkos.application",
        "from openkos.application import curate_decisions",
        "from openkos import state",
        "import openkos.state.pending_queue",
        "import openkos.cli",
        "from openkos.cli.main import app",
        "import typer",
        "import rich",
        "import starlette",
        "from starlette.routing import Route",
        "import anyio",
        "from anyio import to_thread",
        "from openkos.api._reads import status",
        "import openkos.api._reads",
        "import sqlite3",
        "from . import _errors",
        "import httpx",
    ],
)
def test_a_forbidden_import_is_rejected_and_named(tmp_path: Path, line: str) -> None:
    module = _imports(line + "\n")[0]

    assert _violations(_package(tmp_path, line + "\n")) == [
        f"_leaky.py: imports {module}"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "import json",
        "from collections.abc import Callable",
        "import fastapi",
        "from fastapi import FastAPI",
        "from pydantic import BaseModel",
        "import uvicorn",
        "from openkos import api",
        "from openkos.api import status",
        "import openkos.api",
        "from openkos.localapi import _errors",
        "from openkos.localapi._errors import ErrorBody",
    ],
)
def test_an_allowed_import_passes(tmp_path: Path, line: str) -> None:
    assert _violations(_package(tmp_path, line + "\n")) == []


# -- outside the adapter --------------------------------------------------------- #


def test_no_module_outside_the_adapter_imports_the_framework() -> None:
    outside = [p for p in _SRC.rglob("*.py") if _LOCALAPI not in p.parents]

    assert len(outside) > 100  # the scan is not vacuous
    assert _confinement_offenders(_SRC, skip=_LOCALAPI) == []


@pytest.mark.parametrize("name", sorted(_CONFINED))
def test_a_foreign_module_importing_the_framework_is_named(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "server.py").write_text(f"import {name}\n", encoding="utf-8")

    assert _confinement_offenders(tmp_path, skip=None) == [
        f"mcp/server.py: imports {name}"
    ]


# -- a fresh interpreter ---------------------------------------------------------- #


def test_starting_the_cli_never_loads_the_framework_or_the_adapter() -> None:
    code = (
        "import sys\n"
        "import openkos.cli.main\n"
        "names = ('fastapi', 'uvicorn', 'starlette', 'pydantic', 'openkos.localapi')\n"
        "loaded = sorted(m for m in names if m in sys.modules)\n"
        "assert not loaded, loaded\n"
    )

    result = subprocess.run(  # noqa: S603 -- the test interpreter running fixed code
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
