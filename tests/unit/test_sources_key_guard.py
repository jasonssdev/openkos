"""AST guards for `sources`/`provenance` frontmatter key discipline
(okf-v02-migration, issue #1064, Phase 3; ingestion's ADDED requirement
"`sources` Is A Generated, One-Way Projection Of `provenance`"):

- Guard 1 (`find_sources_key_reads`): no module under `src/openkos/`
  outside `okf.project_sources`/`okf.refresh_sources`/`okf.migrate_document`
  (the last a forward reference to Phase 4, allowed now so this guard does
  not need editing again there) reads the `sources` frontmatter key back --
  `provenance` remains the sole internal source of truth for sensitivity,
  trust, and merge decisions.
- Guard 2 (`find_provenance_key_writers`): the set of functions that assign
  a LITERAL `"provenance"` dict key is confirmed to stay WITHIN the pinned
  builder/retarget allow-list -- a fail-closed net against a new,
  unauthorized bypass of the builder seam. It is a SUBSET check, not an
  equality check: `build_merged_document`'s existing generic per-key union
  loop (pre-dating this change) propagates `provenance` through a DYNAMIC
  subscript key, never a literal one, so it is not detectable by this
  literal scan -- and `migrate_document` (Phase 4) does not exist yet.
  Neither absence weakens the guard's actual safety property.

  Scoped to `model/okf.py` and `bundle/provenance.py` ONLY, not the whole
  `src/openkos/` tree, per design.md Decision 4's own claim: "the
  projection is applied in exactly these functions, all in `model/okf.py`
  except the last... `bundle/provenance.apply_provenance_rewrites`" -- i.e.
  this guard tests Decision 4's OWN stated file scope. A whole-tree literal
  scan was tried first and produces a genuine false positive:
  `mcp/gate.py::disclose_get` builds an UNRELATED MCP disclosure payload
  dict that also happens to have a `"provenance"` key (a filtered echo of a
  concept's provenance ids, never a document's frontmatter) -- literal
  string matching cannot distinguish "the frontmatter key" from "any dict
  anywhere with the same key name" without deeper semantic analysis this
  guard does not attempt. Scoping to the two files Decision 4 names is
  the accurate, false-positive-free implementation of the guard's actual
  intent (recorded as a deliberate implementation choice in
  apply-progress.md).

Both guards mirror `tests/unit/bundle/test_layering.py`'s AST-based scan
shape, applied to a different question."""

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "openkos"

# -- Guard 1: no read of `sources` outside the projection --------------------

_SOURCES_READ_EXEMPT_FUNCTIONS = frozenset(
    {"project_sources", "refresh_sources", "migrate_document"}
)


def _is_sources_key_constant(node: ast.expr) -> bool:
    """`True` for the literal `"sources"` string, or an `okf.SOURCES_KEY`-
    shaped attribute access (any base, matched structurally by attribute
    name alone -- an over-broad match only ever WIDENS this guard, never
    narrows it, so it is safe without import resolution)."""
    if isinstance(node, ast.Constant) and node.value == "sources":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "SOURCES_KEY"


class _SourcesKeyReadVisitor(ast.NodeVisitor):
    """Walks one module, reporting every `sources`-key READ (`Subscript`
    load, `.get(...)`, or `in`-membership test) found OUTSIDE a function
    named in `_SOURCES_READ_EXEMPT_FUNCTIONS`. A `Subscript` STORE (a
    builder WRITING `metadata[SOURCES_KEY] = ...`) is never a read, and is
    therefore never flagged regardless of which function it is in."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []
        self._exempt_depth = 0

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        exempt = node.name in _SOURCES_READ_EXEMPT_FUNCTIONS
        if exempt:
            self._exempt_depth += 1
        self.generic_visit(node)
        if exempt:
            self._exempt_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            self._exempt_depth == 0
            and isinstance(node.ctx, ast.Load)
            and _is_sources_key_constant(node.slice)
        ):
            self.violations.append(
                f"{self.path}:{node.lineno}: subscript read of the sources key"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            self._exempt_depth == 0
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and _is_sources_key_constant(node.args[0])
        ):
            self.violations.append(
                f"{self.path}:{node.lineno}: .get() read of the sources key"
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if (
            self._exempt_depth == 0
            and any(isinstance(op, ast.In) for op in node.ops)
            and _is_sources_key_constant(node.left)
        ):
            self.violations.append(
                f"{self.path}:{node.lineno}: membership read of the sources key"
            )
        self.generic_visit(node)


def find_sources_key_reads(paths: Iterable[Path] | None = None) -> list[str]:
    """Every `sources`-key read violation across `paths` (default: every
    `.py` file under `src/openkos/`). `paths` is overridable so a test can
    scan a planted fixture file instead of the real tree (task 3.19)."""
    scan_paths = list(paths) if paths is not None else sorted(_SRC_ROOT.rglob("*.py"))
    violations: list[str] = []
    for path in scan_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SourcesKeyReadVisitor(path)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def test_no_module_outside_projection_reads_sources_key() -> None:
    """Guard 1, task 3.17: the real `src/openkos/` tree has zero `sources`-
    key read violations."""
    assert find_sources_key_reads() == []


def test_sources_key_read_guard_catches_a_planted_violation(tmp_path: Path) -> None:
    """Mutation-proof (task 3.19): a planted fixture file containing a
    forbidden `sources`-key read, scanned via `paths=`, is reported by the
    scanner -- proving it is not vacuously green. The planted violation is
    a `.get("sources")` call inside an unrelated, non-exempt function.

    **MUTATION recorded**: this fixture (`_ = metadata.get("sources")`
    inside `def not_exempt(...)`) was confirmed to be REPORTED by
    `find_sources_key_reads`, then removed by simply not being part of the
    real tree -- it lives only in this test's own `tmp_path` fixture, never
    committed to `src/openkos/`."""
    violation_file = tmp_path / "planted_violation.py"
    violation_file.write_text(
        'def not_exempt(metadata):\n    _ = metadata.get("sources")\n    return _\n',
        encoding="utf-8",
    )

    violations = find_sources_key_reads(paths=[violation_file])

    assert len(violations) == 1
    assert "sources" in violations[0]

    # Triangulation: the SAME shape, but inside an exempt function name, is
    # NOT reported -- proves the exemption gate, not just the detector.
    exempt_file = tmp_path / "planted_exempt.py"
    exempt_file.write_text(
        "def refresh_sources(metadata):\n"
        '    _ = metadata.get("sources")\n'
        "    return _\n",
        encoding="utf-8",
    )

    assert find_sources_key_reads(paths=[exempt_file]) == []


# -- Guard 2: only builders + retarget store `provenance` --------------------

_ALLOWED_PROVENANCE_WRITERS = frozenset(
    {
        "build_concept",
        "build_source_concept",
        "build_merged_document",
        "migrate_document",
        "apply_provenance_rewrites",
    }
)
"""Confirmed against `model/okf.py`'s actual code (task 3.20): the key name
is the bare literal string `"provenance"` -- there is no `PROVENANCE_KEY`
constant in this codebase, unlike `SOURCES_KEY`/`RELATIONS_KEY`/etc."""


def _dict_literal_string_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


class _ProvenanceKeyWriteVisitor(ast.NodeVisitor):
    """`found` is `True` iff this function's body assigns the literal
    `"provenance"` dict key, via a dict-literal key (`build_concept`/
    `build_source_concept`'s metadata dict) or a `Subscript` STORE with a
    string-constant `"provenance"` slice (`apply_provenance_rewrites`'s
    `metadata["provenance"] = merged`)."""

    def __init__(self) -> None:
        self.found = False

    def visit_Dict(self, node: ast.Dict) -> None:
        if "provenance" in _dict_literal_string_keys(node):
            self.found = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "provenance"
        ):
            self.found = True
        self.generic_visit(node)


_PROVENANCE_WRITE_SCAN_FILES = (
    _SRC_ROOT / "model" / "okf.py",
    _SRC_ROOT / "bundle" / "provenance.py",
)
"""The exact file scope design.md Decision 4 names as WHERE the projection
is applied -- see this module's docstring for why the scan is scoped here
rather than the whole `src/openkos/` tree."""


def find_provenance_key_writers(paths: Iterable[Path] | None = None) -> set[str]:
    """Every function name across `paths` (default:
    `_PROVENANCE_WRITE_SCAN_FILES`) whose body assigns a literal
    `"provenance"` dict key."""
    scan_paths = (
        list(paths) if paths is not None else list(_PROVENANCE_WRITE_SCAN_FILES)
    )
    writers: set[str] = set()
    for path in scan_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                visitor = _ProvenanceKeyWriteVisitor()
                visitor.visit(node)
                if visitor.found:
                    writers.add(node.name)
    return writers


def test_provenance_key_writers_are_pinned_to_builders_and_retarget() -> None:
    """Guard 2, task 3.20: every LITERALLY-detected `provenance`-key writer
    across `_PROVENANCE_WRITE_SCAN_FILES` (`model/okf.py`, `bundle/
    provenance.py` -- design.md Decision 4's own named scope) is confirmed
    to lie WITHIN the pinned allow-list -- a fail-closed net against an
    unauthorized bypass of the builder/retarget seam. See this module's
    docstring for why this is a subset, not an equality, check, and why the
    scan is NOT whole-tree."""
    writers = find_provenance_key_writers()

    assert writers <= _ALLOWED_PROVENANCE_WRITERS, (
        f"unauthorized provenance-key writer(s) found: "
        f"{writers - _ALLOWED_PROVENANCE_WRITERS}"
    )
    # Positive coverage: the two writers this scan CAN see today (the
    # dynamic `build_merged_document` case is covered by
    # `test_build_merged_document_sources_matches_unioned_provenance`
    # instead, per this module's docstring) are actually detected, so this
    # guard is not vacuously satisfied by an empty `writers` set either.
    assert {"build_concept", "build_source_concept", "apply_provenance_rewrites"} <= (
        writers
    )


def test_provenance_key_write_guard_catches_a_planted_violation(
    tmp_path: Path,
) -> None:
    """Mutation-proof (task 3.22, guard-side half): a planted fixture
    function OUTSIDE the allow-list that writes a literal `"provenance"`
    dict key is detected by `find_provenance_key_writers`, and would fail
    `writers <= _ALLOWED_PROVENANCE_WRITERS` if it were part of the real
    scan -- proving the subset check is not vacuously green.

    **MUTATION recorded**: this fixture (`def rogue_writer(metadata): ...
    metadata["provenance"] = [...]`) was confirmed to be a DETECTED writer
    not in `_ALLOWED_PROVENANCE_WRITERS`; it lives only in this test's own
    `tmp_path` fixture, never committed to `src/openkos/`."""
    violation_file = tmp_path / "planted_rogue_writer.py"
    violation_file.write_text(
        "def rogue_writer(metadata):\n"
        '    metadata["provenance"] = ["sources/x"]\n'
        "    return metadata\n",
        encoding="utf-8",
    )

    writers = find_provenance_key_writers(paths=[violation_file])

    assert writers == {"rogue_writer"}
    assert not writers <= _ALLOWED_PROVENANCE_WRITERS


def test_apply_provenance_rewrites_without_refresh_sources_breaks_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation-proof (task 3.22, production-behavior half): stubbing
    `okf.refresh_sources` to a no-op (never updating/removing `sources`)
    and re-running `apply_provenance_rewrites` on a document that already
    carries a STALE `sources` key leaves that staleness in place -- proving
    the real call (`bundle/provenance.py`, added in task 3.14) is
    load-bearing, not dead code. `monkeypatch` auto-restores the real
    function after this test."""
    text = okf.dump_frontmatter(
        {
            "type": "Concept",
            "provenance": ["sources/absorbed"],
            "sources": [{"id": "sources/absorbed", "resource": "/sources/absorbed.md"}],
        },
        "Body.",
    )
    rewrite = okf.ProvenanceRewrite(file="concepts/other.md", snapshot=text)

    monkeypatch.setattr(okf, "refresh_sources", lambda metadata: dict(metadata))

    result = bundle_provenance.apply_provenance_rewrites(
        text,
        file="concepts/other.md",
        survivor_id="sources/survivor",
        absorbed_id="sources/absorbed",
        rewrites=[rewrite],
    )

    metadata, _ = okf.load_frontmatter(result)
    assert metadata["provenance"] == ["sources/survivor"]
    # Without the real `refresh_sources` call, `sources` is UNCHANGED --
    # still naming the now-retargeted-away absorbed id, no longer matching
    # `project_sources(metadata["provenance"])`. This is the parity break
    # the real call (task 3.14) exists to prevent.
    assert metadata["sources"] == [
        {"id": "sources/absorbed", "resource": "/sources/absorbed.md"}
    ]
    assert metadata["sources"] != okf.project_sources(metadata["provenance"])
