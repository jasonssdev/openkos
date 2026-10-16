"""Import guards for `openkos.api`.

An allowlist is stronger than a denylist: every module under `src/openkos/api/`
may import only the standard library, itself, and a short list of engine
modules. The layers that print, prompt, disclose or open databases are also
asserted by name, so each scenario has a direct check. A second guard bars every
other module from importing the facade, except the local HTTP adapter under
`localapi/` (ADR-0061), and a fresh interpreter proves that importing it never
loads the CLI.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"
_API = _SRC / "api"

_ALLOWED_EXACT = {
    "openkos.errors",
    "openkos.config",
    "openkos.lock",
    "openkos.llm.base",
}
_ALLOWED_PREFIXES = ("openkos.api", "openkos.application")
_WIRING_EXTRA = {
    "openkos.llm.ollama",
    "openkos.llm.openai_compatible",
    "openkos.vcs.autocommit",
}
_NAMED_BANS = (
    "openkos.cli",
    "openkos.mcp",
    "openkos.mcp.gate",
    "typer",
    "rich",
    "asyncio",
    "openkos.state",
    "sqlite3",
)


def _imports(source: str) -> list[str]:
    """Every module a source file imports, `from openkos import config` being
    `openkos.config`. A relative import is reported as such."""
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


def _allowed(module: str, filename: str) -> bool:
    if module in _ALLOWED_EXACT:
        return True
    if module.split(".")[0] in {"typing", "collections"} and module.startswith(
        ("typing", "collections")
    ):
        return True
    if any(module == p or module.startswith(p + ".") for p in _ALLOWED_PREFIXES):
        return True
    if filename == "_wiring.py" and module in _WIRING_EXTRA:
        return True
    top = module.split(".")[0]
    return top in sys.stdlib_module_names and top not in {"sqlite3", "asyncio"}


def violations(modules: dict[str, str]) -> list[str]:
    """`"<module file>: imports <name>"` for every import outside the allowlist."""
    return [
        f"{filename}: imports {name}"
        for filename, source in sorted(modules.items())
        for name in _imports(source)
        if not _allowed(name, filename)
    ]


def _api_modules() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_API.glob("*.py"))}


def test_the_api_package_has_modules_to_scan() -> None:
    names = set(_api_modules())

    assert {
        "__init__.py",
        "_workspace.py",
        "_reads.py",
        "_decisions.py",
        "_wiring.py",
    } <= names


def test_every_api_module_imports_only_the_allowlist() -> None:
    assert violations(_api_modules()) == []


@pytest.mark.parametrize("banned", _NAMED_BANS)
def test_a_named_layer_is_rejected_with_the_module_and_the_import(banned: str) -> None:
    result = violations({"_leaky.py": f"import {banned}\n"})

    assert result == [f"_leaky.py: imports {banned}"]


@pytest.mark.parametrize(
    "line",
    [
        "from openkos.cli import main",
        "from openkos.mcp.gate import disclose",
        "from typer import Option",
        "from rich.console import Console",
        "from openkos.state import jobs",
        "from sqlite3 import Connection",
        "from openkos import cli",
        "from openkos import mcp",
        "from openkos import state",
        "from . import _workspace",
    ],
)
def test_the_from_forms_are_rejected_too(line: str) -> None:
    assert violations({"_leaky.py": line + "\n"}) != []


@pytest.mark.parametrize(
    "line",
    [
        "import os",
        "from pathlib import Path",
        "from collections.abc import Mapping",
        "from typing import Final",
        "from openkos import config, lock",
        "from openkos.errors import OpenKosError",
        "from openkos.application import judging",
        "from openkos.application.curate_decisions import Action",
        "from openkos.llm.base import BackendError",
        "from openkos.api._workspace import Workspace",
    ],
)
def test_the_allowed_forms_pass(line: str) -> None:
    assert violations({"_ok.py": line + "\n"}) == []


def test_an_engine_module_off_the_list_is_rejected() -> None:
    assert violations({"_leaky.py": "from openkos.model import okf\n"}) == [
        "_leaky.py: imports openkos.model"
    ]
    assert violations({"_leaky.py": "from openkos.llm import ollama\n"}) == [
        "_leaky.py: imports openkos.llm"
    ]


def test_only_the_wiring_module_may_import_a_backend_client_or_the_commit_core() -> (
    None
):
    source = (
        "from openkos.llm.ollama import OllamaClient\n"
        "from openkos.vcs.autocommit import commit_paths\n"
    )

    assert violations({"_wiring.py": source}) == []
    assert violations({"_reads.py": source}) == [
        "_reads.py: imports openkos.llm.ollama",
        "_reads.py: imports openkos.vcs.autocommit",
    ]


# -- only the local HTTP adapter imports the API ---------------------------- #


def _importers_of_the_api(sources: dict[str, str]) -> list[str]:
    return [
        filename
        for filename, source in sorted(sources.items())
        if any(
            m == "openkos.api" or m.startswith("openkos.api.") for m in _imports(source)
        )
    ]


def _foreign_importers(importers: list[str]) -> list[str]:
    """The importers that are neither the API itself nor the sanctioned local
    HTTP adapter (`localapi/`, ADR-0061)."""
    return [name for name in importers if not name.startswith("localapi/")]


def test_only_the_local_api_adapter_imports_the_api() -> None:
    sources = {
        str(p.relative_to(_SRC)): p.read_text(encoding="utf-8")
        for p in sorted(_SRC.rglob("*.py"))
        if _API not in p.parents
    }
    importers = _importers_of_the_api(sources)

    assert len(sources) > 100  # the scan is not vacuous
    assert importers  # the adapter uses the API, so the admission is exercised
    assert _foreign_importers(importers) == []


def test_a_module_outside_both_importing_the_api_fails_naming_it() -> None:
    sources = {
        "mcp/server.py": "from openkos import api\n",
        "localapi/_routes.py": "from openkos import api\n",
    }

    assert _foreign_importers(_importers_of_the_api(sources)) == ["mcp/server.py"]


def test_a_localapi_module_importing_the_api_passes() -> None:
    sources = {"localapi/_routes.py": "from openkos import api\n"}

    assert _importers_of_the_api(sources) == ["localapi/_routes.py"]
    assert _foreign_importers(_importers_of_the_api(sources)) == []


@pytest.mark.parametrize(
    "line",
    ["import openkos.api", "from openkos import api", "from openkos.api import status"],
)
def test_every_spelling_of_importing_the_api_is_caught(line: str) -> None:
    assert _importers_of_the_api({"mcp/server.py": line + "\n"}) == ["mcp/server.py"]


# -- a fresh interpreter -------------------------------------------------------- #


def test_importing_the_api_never_loads_the_cli_or_typer() -> None:
    code = (
        "import sys\n"
        "import openkos.api\n"
        "loaded = sorted(m for m in ('openkos.cli', 'openkos.mcp', 'typer', 'rich') "
        "if m in sys.modules)\n"
        "assert not loaded, loaded\n"
    )

    result = subprocess.run(  # noqa: S603 -- the test interpreter running fixed code
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# -- the API never depends on the process's working directory -------------------- #

_CWD_CALLS = {"cwd", "getcwd", "chdir"}


def _cwd_reads(source: str) -> list[str]:
    """The `Path.cwd()`, `os.getcwd()` and `os.chdir()` spellings a source file
    uses, as written. A workspace is addressed by its root alone."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in _CWD_CALLS:
            found.append(ast.unparse(node))
        elif isinstance(node, ast.Name) and node.id in _CWD_CALLS:
            found.append(node.id)
    return found


def test_no_api_module_reads_or_changes_the_working_directory() -> None:
    offenders = {
        name: reads
        for name, source in _api_modules().items()
        if (reads := _cwd_reads(source))
    }

    assert len(_api_modules()) >= 5  # the scan is not vacuous
    assert offenders == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from pathlib import Path\nroot = Path.cwd()\n", ["Path.cwd"]),
        ("import os\nroot = os.getcwd()\n", ["os.getcwd"]),
        ("from os import getcwd\nroot = getcwd()\n", ["getcwd"]),
        ("import os\nos.chdir('/')\n", ["os.chdir"]),
        ("from pathlib import Path\nroot = Path('.').absolute()\n", []),
    ],
)
def test_every_spelling_of_reading_the_cwd_is_caught(
    source: str, expected: list[str]
) -> None:
    assert _cwd_reads(source) == expected
