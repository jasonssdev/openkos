"""Guards for the `imported` extension key (okf-import, slice 3, design D4).

Three properties, each pinned structurally so a future change cannot erode it
by accident:

(a) Classification totality: every engine key constant in `model/okf.py`, plus
    the literal keys the engine reads, belongs to exactly one treatment group,
    so a key added to the engine later fails here until it is classified
    instead of being trusted from foreign input by default.
(b) No module under `src/openkos/` reads `IMPORTED_KEY` (or the literal
    `"imported"`) outside the functions that own the key; the shape of
    `tests/unit/test_sources_key_guard.py`.
(c) `IMPORTED_KEY` is in `_union_frontmatter`'s skip list, so an absorbed
    imported document's `imported` block is never gap-filled into a local
    survivor, where it would misstate the survivor's origin.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pytest

from openkos.model import okf

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "openkos"
_OKF_PATH = _SRC_ROOT / "model" / "okf.py"

# --- (a) classification totality ---------------------------------------------

_LITERAL_ENGINE_KEYS = (
    "status",
    "generated",
    "verified",
    "version",
    "timestamp",
    "resource",
    "sensitivity",
    "provenance",
)
"""The engine keys that have no `*_KEY` constant: read by bare literal."""


def engine_key_values(source: str) -> list[str]:
    """Every module-level `NAME_KEY: Final = "literal"` value in `source`, in
    order, plus the literal keys the engine reads without a constant."""
    values: list[str] = []
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id.endswith("_KEY")
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values.append(node.value.value)
    return [*values, *_LITERAL_ENGINE_KEYS]


def unclassified_keys(source: str) -> list[str]:
    """The engine keys in `source` that are in no classification group."""
    classified = Counter(key for keys in okf.IMPORT_KEY_GROUPS.values() for key in keys)
    return sorted(key for key in engine_key_values(source) if classified[key] == 0)


def test_every_engine_key_is_classified() -> None:
    assert unclassified_keys(_OKF_PATH.read_text(encoding="utf-8")) == []


def test_the_scan_finds_the_engine_keys_it_must_classify() -> None:
    """A scan that found nothing would pass the totality check vacuously."""
    values = set(engine_key_values(_OKF_PATH.read_text(encoding="utf-8")))
    assert {
        "relations",
        "merged_from",
        "origin_key",
        "sources",
        "ingest_pending",
        "extraction_status",
        "extraction_notice",
        "status_derived_from",
        "source_frontmatter",
        "event_date",
        "type_alternative",
        "okf_version",
        "imported",
    } <= values
    assert len(values) >= 20


def test_a_new_unclassified_engine_key_is_reported() -> None:
    source = 'BRAND_NEW_KEY: Final = "brand_new"\nSOURCES_KEY: Final = "sources"\n'
    assert unclassified_keys(source) == ["brand_new"]


def test_every_key_is_in_exactly_one_group() -> None:
    counts = Counter(key for keys in okf.IMPORT_KEY_GROUPS.values() for key in keys)
    assert counts, "no classified keys"
    assert {key: n for key, n in counts.items() if n != 1} == {}


@pytest.mark.parametrize(
    ("key", "group"),
    [
        ("type", "keep"),
        ("sensitivity", "replace"),
        ("provenance", "inert"),
        ("sources", "inert"),
        ("okf_version", "inert"),
        ("resource", "resource"),
        ("relations", "relations"),
        ("origin_key", "drop"),
        ("merged_from", "drop"),
        ("imported", "nest"),
        ("an_unknown_key", "keep"),
    ],
)
def test_the_treatment_lookup(key: str, group: str) -> None:
    assert okf.import_key_treatment(key) == group


# --- (b) no reader of the imported key outside its owners --------------------

_IMPORTED_OWNERS = frozenset(
    {
        "adopt_foreign_document",
        "build_import_anchor",
        "adopted_violations",
        "is_import_anchor_of",
    }
)


def _is_imported_key(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value == "imported":
        return True
    return isinstance(node, ast.Attribute | ast.Name) and (
        getattr(node, "attr", None) == "IMPORTED_KEY"
        or getattr(node, "id", None) == "IMPORTED_KEY"
    )


class _ImportedReadVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []
        self._owner_depth = 0

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        owner = node.name in _IMPORTED_OWNERS
        self._owner_depth += owner
        self.generic_visit(node)
        self._owner_depth -= owner

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            not self._owner_depth
            and isinstance(node.ctx, ast.Load)
            and _is_imported_key(node.slice)
        ):
            self.violations.append(f"{self.path}:{node.lineno}: subscript read")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            not self._owner_depth
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and _is_imported_key(node.args[0])
        ):
            self.violations.append(f"{self.path}:{node.lineno}: .get() read")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if (
            not self._owner_depth
            and any(isinstance(op, ast.In) for op in node.ops)
            and _is_imported_key(node.left)
        ):
            self.violations.append(f"{self.path}:{node.lineno}: membership read")
        self.generic_visit(node)


def find_imported_key_reads(paths: Iterable[Path] | None = None) -> list[str]:
    scan = list(paths) if paths is not None else sorted(_SRC_ROOT.rglob("*.py"))
    violations: list[str] = []
    for path in scan:
        visitor = _ImportedReadVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        violations.extend(visitor.violations)
    return violations


def test_no_module_reads_the_imported_key_outside_its_owners() -> None:
    assert find_imported_key_reads() == []


@pytest.mark.parametrize(
    "read",
    [
        'metadata.get("imported")',
        "metadata.get(okf.IMPORTED_KEY)",
        'metadata["imported"]',
        "metadata[IMPORTED_KEY]",
        '"imported" in metadata',
        "okf.IMPORTED_KEY in metadata",
    ],
)
def test_the_scan_catches_a_planted_reader(tmp_path: Path, read: str) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(f"def rogue(metadata):\n    return {read}\n", encoding="utf-8")
    assert len(find_imported_key_reads([planted])) == 1


def test_the_scan_exempts_only_the_owning_functions(tmp_path: Path) -> None:
    owner = tmp_path / "owner.py"
    owner.write_text(
        "def adopted_violations(metadata):\n    return metadata.get('imported')\n",
        encoding="utf-8",
    )
    assert find_imported_key_reads([owner]) == []
    lookalike = tmp_path / "lookalike.py"
    lookalike.write_text(
        "def adopted_violations_extra(metadata):\n    return metadata.get('imported')\n",
        encoding="utf-8",
    )
    assert len(find_imported_key_reads([lookalike])) == 1


def test_a_store_is_not_a_read(tmp_path: Path) -> None:
    writer = tmp_path / "writer.py"
    writer.write_text(
        "def writer(metadata):\n    metadata['imported'] = {}\n", encoding="utf-8"
    )
    assert find_imported_key_reads([writer]) == []


# --- (c) the merge union skips the imported key ------------------------------


def test_an_absorbed_imported_block_is_not_gap_filled_into_a_survivor() -> None:
    survivor: dict[str, object] = {
        "type": "Concept",
        "title": "Local",
        "provenance": ["sources/a"],
    }
    absorbed: dict[str, object] = {
        "type": "Concept",
        "title": "Imported",
        "sensitivity": "private",
        "provenance": ["imports/demo--private"],
        okf.IMPORTED_KEY: {"namespace": "demo", "id": "x", "sha256": "00"},
    }
    merged = okf._union_frontmatter(survivor, absorbed)
    assert okf.IMPORTED_KEY not in merged
    # The control: the union still gap-fills a key it does not skip.
    absorbed_with_extra: dict[str, object] = {**absorbed, "x_extra": "filled"}
    assert okf._union_frontmatter(survivor, absorbed_with_extra)["x_extra"] == "filled"
    # And the survivor gains the anchor through the ordinary provenance union.
    assert merged["provenance"] == ["sources/a", "imports/demo--private"]


def test_a_survivors_own_imported_block_is_kept() -> None:
    own = {"namespace": "demo", "id": "mine", "sha256": "11"}
    survivor: dict[str, object] = {"type": "Concept", okf.IMPORTED_KEY: own}
    absorbed: dict[str, object] = {
        "type": "Concept",
        okf.IMPORTED_KEY: {"namespace": "other", "id": "theirs", "sha256": "22"},
    }
    assert okf._union_frontmatter(survivor, absorbed)[okf.IMPORTED_KEY] == own
