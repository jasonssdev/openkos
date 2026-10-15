"""`openkos.errors`: the leaf module that holds the catchable root (design
Decision 8). It must import nothing from `openkos`, so `lock`, `llm/base.py`
and every `application/` module may depend on it without a layering edge."""

import ast
from pathlib import Path

import openkos
from openkos import errors


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(("." * node.level) + (node.module or ""))
    return names


def test_errors_module_imports_nothing_from_openkos() -> None:
    path = Path(openkos.__file__).parent / "errors.py"
    offenders = [
        name
        for name in _imports(path)
        if name.startswith(".") or name == "openkos" or name.startswith("openkos.")
    ]
    assert offenders == []


def test_open_kos_error_is_an_exception_subclass_with_a_docstring() -> None:
    assert issubclass(errors.OpenKosError, Exception)
    assert errors.OpenKosError.__doc__
    assert str(errors.OpenKosError("boom")) == "boom"
