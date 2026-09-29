"""AST guard: no module outside the concrete client modules may reference a
concrete backend error class at all -- in an `except` clause, an
`isinstance` call, a type annotation, or any other LOAD reference (issue
#1057 Phase 2a/2b, Decision 3).

`llm/ollama.py` must keep raising and catching its own concrete classes
(that is how it builds its error ladder), and the future
`llm/openai_compatible.py` (Phase 4) will do the same for its own classes
-- both are excluded by name. Every OTHER module must name only the
neutral bases (`BackendError`, `BackendUnavailable`, `BackendModelNotFound`,
`BackendGenerationCapped`, `BackendEmbeddingDimensionMismatch`), so a
caller scoped to `openkos.llm.base` never has to import a concrete backend
module just to catch one of its failures, and the same catch clause works
unchanged once a second backend exists.

The neutral-catch-site migration has no staged rollout (tasks.md, "Two
clarifying tasks-phase decisions"): every site outside the two excluded
modules must migrate, in Phases 2a (non-CLI) and 2b (CLI/curate/MCP). This
guard is written once, unconditionally, with no `_PENDING_SITES` allowlist
-- unlike the Phase 9-10 construction-guard ratchet, which stages ten
sites across two phases on purpose. Phase 2a's own migration (task 2.6)
narrowed this guard's failing set to the CLI/curate/MCP sites only,
without closing it; Phase 2b (task 2.11) migrated those three modules and
is what closes this guard fully -- it is unconditionally GREEN from this
point on, with no allowlist ever introduced for it."""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src" / "openkos"

_EXCLUDED_MODULES = frozenset({"llm/ollama.py", "llm/openai_compatible.py"})
"""Concrete client modules allowed to name their own error classes -- the
future `llm/openai_compatible.py` is a forward reference (it does not exist
until Phase 4), included now so this guard needs no edit when it lands."""

_CONCRETE_CLASS_PREFIXES = ("Ollama", "OpenAICompatible")
"""Every concrete backend's class-name prefix. A name is "concrete" for
this guard's purposes when it starts with one of these AND ends in one of
the error-family suffixes below -- narrow enough that an unrelated
`OllamaClient`/`OpenAICompatibleClient` construction-site reference (a
different concern, Phase 9-10's construction guard) is not mistaken for an
error-class catch."""

_ERROR_SUFFIXES = (
    "Error",
    "Unavailable",
    "ModelNotFound",
    "GenerationCapped",
    "EmbeddingDimensionMismatch",
)


def _is_concrete_error_class_name(name: str) -> bool:
    return name.startswith(_CONCRETE_CLASS_PREFIXES) and name.endswith(_ERROR_SUFFIXES)


def _scanned_modules() -> list[Path]:
    """Every `.py` file under `src/openkos/`, excluding the two concrete
    client modules, sorted for a stable iteration order."""
    return sorted(
        path
        for path in _SRC_DIR.rglob("*.py")
        if path.relative_to(_SRC_DIR).as_posix() not in _EXCLUDED_MODULES
    )


def _concrete_catch_sites(tree: ast.Module) -> list[str]:
    """Every reference to a concrete backend error class in one module's
    AST, as `"<name> (line N)"` strings.

    Scans every `Name`/`Attribute` node in LOAD context (a plain
    `OllamaError`, or `module.OllamaError`) rather than only the literal
    `except`/`isinstance` shapes, because a concrete class name can reach a
    catch site indirectly -- `mcp/server.py`'s `_TOOL_ERROR_TABLE` lists
    each concrete class as a plain tuple element, consumed later by
    `isinstance(exc, exc_type)` where `exc_type` is a loop variable, not a
    literal class name. An `except`/`isinstance`-only scan would walk
    straight past that table. No concrete class is ever legitimately
    imported, referenced, constructed or raised in production code outside
    `llm/ollama.py`/`llm/openai_compatible.py` today (confirmed: nothing
    outside those two modules raises one directly), so any LOAD reference
    found elsewhere is the violation this guard exists to catch -- an
    `import`'s `ast.alias` node is a different AST shape and is never
    matched by this scan, so the import statement that brings a name into
    scope is not itself flagged; only a later USE is."""
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and _is_concrete_error_class_name(node.id)
        ):
            found.append(f"{node.id} (line {node.lineno})")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and _is_concrete_error_class_name(node.attr)
        ):
            found.append(f"{node.attr} (line {node.lineno})")
    return found


def test_no_concrete_backend_class_named_outside_client_modules() -> None:
    """No module under `src/openkos/` other than `llm/ollama.py` and
    `llm/openai_compatible.py` may reference a concrete `Ollama*`/
    `OpenAICompatible*` error class at all (design.md Decision 3).

    **History** (issue #1057): RED on creation (Phase 2a, task 2.5) --
    about 55 known sites named a concrete Ollama class across
    `resolution/*.py`, `state/reindex.py`, `retrieval/answer.py`,
    `extraction/judge.py`, `extraction/concept.py`, `application/query.py`,
    `application/ingest.py`, `cli/main.py`, `cli/curate.py`,
    `mcp/server.py`. Task 2.6 (Phase 2a) migrated every non-CLI site,
    narrowing the failing set to exactly `cli/main.py`, `cli/curate.py`,
    `mcp/server.py` -- still RED, by design (see this module's docstring).
    Task 2.11 (Phase 2b) migrated those three modules, closing this guard
    to GREEN unconditionally, with no allowlist ever introduced."""
    offenders: dict[str, list[str]] = {}
    for path in _scanned_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = _concrete_catch_sites(tree)
        if found:
            offenders[path.relative_to(_SRC_DIR).as_posix()] = found
    assert not offenders, (
        "no module outside llm/ollama.py and llm/openai_compatible.py may "
        f"name a concrete backend error class; found: {offenders}"
    )


# Mutation-proof (task 2.7): reintroducing `except OllamaError as exc:` into
# `resolution/adjudication.py::adjudicate_candidates` (an already-migrated
# module this guard scans) in place of `except BackendError as exc:` turned
# this guard red, reporting `'resolution/adjudication.py': ['OllamaError
# (line 634)']` alongside the still-expected CLI/curate/MCP offenders --
# confirming the guard actually re-detects a regression on a migrated site,
# not merely the sites that were never migrated. Reverted immediately after
# (inverse edit), with `__pycache__` purged before re-running to confirm
# GREEN-for-this-site again.
#
# Mutation-proof (task 2.12, Phase 2b): after migrating cli/main.py,
# cli/curate.py and mcp/server.py, reintroduced `except OllamaError as
# exc:` into `cli/main.py` in place of `except BackendError as exc:`
# (one of its ~32 migrated sites); the guard went red, reporting exactly
# `{'cli/main.py': ['OllamaError (line 5475)']}`. Reverted (inverse
# edit), purged `__pycache__`, reconfirmed GREEN. Separately, reintroduced
# a concrete `OllamaUnavailable` as the first element of
# `mcp/server.py`'s `_TOOL_ERROR_TABLE` tuple (the class-name-as-data
# pattern this guard's broadened scan exists to catch, see the module
# docstring); the guard went red, reporting exactly
# `{'mcp/server.py': ['OllamaUnavailable (line 69)']}`. Reverted, purged
# `__pycache__`, reconfirmed GREEN. Both mutations confirm the guard
# detects a regression on every migrated CLI/curate/MCP module, closing
# the fully-unconditional form this guard's own docstring describes.
