"""Layering guards for the `llm` package that need their OWN pin,
independent of `test_ollama.py::test_llm_modules_do_not_import_config`
(issue #1057 Phase 4, task 4.2a).

That existing check scans `llm/*.py` with `Path.glob("*.py")` -- already
glob-based, so it automatically covers a NEW module dropped into `llm/`
with no hardcoded-list edit required. This module pins that inclusion
independently: if the scan is ever narrowed to a hardcoded file list, this
test fails the moment a new module (starting with `openai_compatible.py`)
silently drops out of coverage, rather than relying on someone remembering
to widen the other test's list by hand.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LLM_DIR = _REPO_ROOT / "src" / "openkos" / "llm"


def test_llm_package_no_config_import_includes_new_module() -> None:
    """`openai_compatible.py` is present among the glob-discovered `llm/`
    modules the no-config-import scan covers.

    **RED today**: `openai_compatible.py` does not exist yet (Phase 4
    creates it), so it is absent from the discovered set."""
    modules = {path.name for path in _LLM_DIR.glob("*.py")}

    assert "openai_compatible.py" in modules
