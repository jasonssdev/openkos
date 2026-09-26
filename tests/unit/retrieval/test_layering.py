"""Layering-boundary guard for `openkos.retrieval` (superseded-history-in-
query, slice 2b, design.md's stated invariants).

Mirrors `tests/unit/resolution/test_layering.py`'s AST-based canonical-import
guard: `retrieval/` must never import `openkos.resolution` (a sibling derived
package, not canonical, but one `retrieval` has no reason to depend on --
design.md's Layering section). `retrieval/history.py` (slice 2a) MAY import
`openkos.event_dates`, a package-root leaf with no cycle -- a POSITIVE
assertion so this guard stays non-vacuous. `retrieval/answer.py` staying
config-free, and `revision_history` staying caller-supplied rather than
config-read, are restated here alongside the other retrieval layering guards;
the primary pin for the config-import half lives in
`test_answer.py::test_answer_module_does_not_import_config`.
"""

import ast
import inspect
from pathlib import Path

from openkos.retrieval import answer as answer_mod

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETRIEVAL_DIR = _REPO_ROOT / "src" / "openkos" / "retrieval"


def _collect_imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_retrieval_does_not_import_resolution() -> None:
    """`retrieval/` never imports `openkos.resolution` (design.md's
    Layering section: "retrieval/ imports nothing from resolution/")."""
    for path in _RETRIEVAL_DIR.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text(encoding="utf-8"))
        assert not any(
            module == "openkos.resolution" or module.startswith("openkos.resolution.")
            for module in modules
        ), f"{path} imports openkos.resolution"


def test_history_module_imports_event_dates() -> None:
    """A POSITIVE assertion: `retrieval/history.py` really does import
    `openkos.event_dates` -- so the negative guard above stays non-vacuous,
    proving the allowed dependency actually exists rather than merely being
    permitted."""
    history_path = _RETRIEVAL_DIR / "history.py"
    modules = _collect_imported_modules(history_path.read_text(encoding="utf-8"))
    assert any(
        module == "openkos.event_dates" or module.startswith("openkos.event_dates.")
        for module in modules
    ), "expected retrieval/history.py to import openkos.event_dates"


def test_answer_module_does_not_import_config() -> None:
    """`retrieval/answer.py` does not import `openkos.config` (leaf
    discipline), restated alongside the other retrieval layering guards."""
    answer_path = _RETRIEVAL_DIR / "answer.py"
    modules = _collect_imported_modules(answer_path.read_text(encoding="utf-8"))
    assert not any("config" in module for module in modules)


def test_revision_history_parameter_has_no_internal_config_read() -> None:
    """`answer()`'s `revision_history` parameter has no internal read of
    `openkos.config` anywhere in `answer.py` -- it is caller-supplied, never
    config-read (query-answer: "revision_history is caller-supplied, not
    config-read"). The import-absence half of this guard is
    `test_answer_module_does_not_import_config` above; this half pins the
    parameter's own default and that no CODE identifier (not prose) reads
    `cfg.revision_history`."""
    parameters = inspect.signature(answer_mod.answer).parameters
    assert "revision_history" in parameters
    assert parameters["revision_history"].default is False
    answer_path = _RETRIEVAL_DIR / "answer.py"
    tree = ast.parse(answer_path.read_text(encoding="utf-8"))
    attribute_names = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "read_config" not in attribute_names
