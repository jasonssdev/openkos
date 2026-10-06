"""The recognizer cross-check and the recognizer inventory (okf-import,
slice 2).

The engine reads markdown links with several independent regexes and they
disagree. The import's link rewrite and its proof are only as good as the
list of readers they were checked against, so:

* `test_every_reader_*` runs EVERY real engine recognizer over the output of
  the whole rewrite corpus (`test_links_namespace.CORPUS`) and asserts every
  target it extracts is external, an anchor, empty, or inside
  `imports/<ns>/` and not escaping the bundle;
* `test_the_inventory_*` scans `src/openkos/**/*.py` for regex literals that
  read a markdown link, so a NEW reader fails here until it is added to the
  cross-check (or exempted with a reason).
"""

import ast
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Final

from openkos import lint
from openkos.bundle import index, links
from openkos.graph import sqlite_graph
from openkos.model import okf
from tests.unit.bundle.test_links_namespace import CORPUS, PREFIX, Case

# --- the readers ---------------------------------------------------------------

# A reader takes the output text and the Concept ID of the (local) document
# carrying it, and returns the targets it extracted as a list of problems:
# `(extracted, resolved)` pairs, one per target. `check` asserts each is safe.

Extraction = tuple[str, str | None]
Reader = Callable[[str, str], list[Extraction]]


def _frame_dir(local_id: str) -> str:
    return str(PurePosixPath(local_id).parent)


def _links_link_re(text: str, local_id: str) -> list[Extraction]:
    # `find_inbound_link_rewrites` reads one line at a time (and masks
    # fences; the cross-check does not, which only widens what it reads)
    return [
        (m.group(0), m.group(1).removesuffix(".md"))
        for line in text.split("\n")
        for m in links._LINK_RE.finditer(line)
    ]


def _links_inline_re(text: str, local_id: str) -> list[Extraction]:
    return [
        (m.group(2), links._bundle_target_id(m.group(2), file_id=local_id))
        for line in text.split("\n")
        for m in links._INLINE_LINK_RE.finditer(line)
    ]


def _links_definition_re(text: str, local_id: str) -> list[Extraction]:
    found: list[Extraction] = []
    for line in text.split("\n"):
        match = links._REFERENCE_DEFINITION_RE.match(line)
        if match is not None:
            found.append(
                (
                    match.group(2),
                    links._bundle_target_id(match.group(2), file_id=local_id),
                )
            )
    return found


def _graph_link_re(text: str, local_id: str) -> list[Extraction]:
    return [
        (m.group(0), m.group(1).removesuffix(".md"))
        for m in sqlite_graph._LINK_RE.finditer(text)
    ]


def _lint_link_re(text: str, local_id: str) -> list[Extraction]:
    return [
        (target, lint.normalize_link(target, _frame_dir(local_id)))
        for target in lint._LINK_RE.findall(text)
    ]


_INDEX_HEAD: Final = '---\nokf_version: "0.2"\n---\n\n# Concepts\n\n'


def _bullets(text: str) -> str:
    return _INDEX_HEAD + "".join(f"- {line}\n" for line in text.split("\n"))


def _index_link_re(text: str, local_id: str) -> list[Extraction]:
    # index.py reads the root `index.md` only, root-relative: an adopted
    # document is never that file, so only a bundle-absolute link is in its
    # domain (see `_in_index_domain`)
    return [
        (identity, identity)
        for identity in sorted(index.indexed_concept_ids(_bullets(text)))
    ]


def _index_labelled_re(text: str, local_id: str) -> list[Extraction]:
    found: list[Extraction] = []
    for line in _bullets(text).split("\n"):
        match = index._LABELLED_LINK_RE.search(line.lstrip())
        if match is not None and line.lstrip().startswith(index._BULLET_MARKERS):
            found.append((match.group(2), index._link_identity(match.group(2))))
    return found


READERS: dict[str, Reader] = {
    "bundle/links.py:_LINK_RE": _links_link_re,
    "bundle/links.py:_INLINE_LINK_RE": _links_inline_re,
    "bundle/links.py:_REFERENCE_DEFINITION_RE": _links_definition_re,
    "graph/sqlite_graph.py:_LINK_RE": _graph_link_re,
    "lint.py:_LINK_RE": _lint_link_re,
    "bundle/index.py:_LINK_RE": _index_link_re,
    "bundle/index.py:_LABELLED_LINK_RE": _index_labelled_re,
}

INDEX_READERS: Final = frozenset(
    {"bundle/index.py:_LINK_RE", "bundle/index.py:_LABELLED_LINK_RE"}
)

# Every other regex literal that mentions `](` or `]:`, and why it is not a
# reader the corpus must be run through.
EXEMPT: dict[str, str] = {
    "bundle/links.py:_SITE_RE": "the pointer-site scanner under test: the proof re-reads its output",
    "bundle/links.py:_SHORTCUT_REFERENCE_RE": "matches a label NOT followed by a destination (`[x]`): resolves only through a definition, which is cross-checked",
    "bundle/links.py:pattern": "`_substitute_target` rewrites an exact target it is given (merge) and reads none",
    "bundle/index.py:_LABEL_UNSAFE_CHARS_RE": "a character class that REFUSES link delimiters in a label; reads no destination",
    "source_title.py:_FORBIDDEN_IN_TITLE": "a character class that REFUSES link delimiters in a title; reads no destination",
    "resolution/reconciliation.py:_LINK_TARGET_RE": "compares the raw text of a Related bullet's target to deduplicate; resolves nothing",
}


def _in_index_domain(body: str, local_id: str) -> bool:
    """The index reader resolves root-relative, so a relative path link is
    outside what it can ever be asked to read."""
    for site in links.pointer_sites(body):
        target = links.resolve_link_target(site.destination, file_id=local_id)
        if target.kind == "path" and not target.absolute:
            return False
    return True


def _inside(resolved: str | None) -> bool:
    """A resolved identity that cannot be a different local document."""
    if resolved is None:
        return True  # external, anchor, empty, or resolves to no concept
    identity = unicodedata.normalize("NFC", resolved)
    if "<" in identity or ">" in identity:
        # the index reader does not strip `<>`: the identity names a path
        # no Concept ID can have (an unsafe name is refused at import)
        return True
    return identity == PREFIX or identity.startswith(f"{PREFIX}/")


def _local_id(case: Case) -> str:
    # the carrying document is written under its RENAMED id
    return f"{PREFIX}/{okf.renamed_foreign_id(case.foreign_id)}"


def _output(case: Case) -> str:
    """What the rewriter actually produces for the case (not the expected
    bytes: those are pinned by `test_links_namespace`)."""
    return links.rewrite_links_into_namespace(
        case.body_in, foreign_id=case.foreign_id, prefix=PREFIX
    ).text


def _run_readers() -> tuple[Counter[str], list[str]]:
    extracted: Counter[str] = Counter()
    problems: list[str] = []
    for case in CORPUS:
        local_id = _local_id(case)
        output = _output(case)
        for name, reader in READERS.items():
            if name in INDEX_READERS and not _in_index_domain(output, local_id):
                continue
            for target, resolved in reader(output, local_id):
                extracted[name] += 1
                if not _inside(resolved):
                    problems.append(f"{name} {case.id}: {target!r} -> {resolved!r}")
    return extracted, problems


def test_every_reader_resolves_every_corpus_output_inside_the_namespace() -> None:
    extracted, problems = _run_readers()
    assert problems == []
    # a reader that extracted nothing would pass vacuously: every one ran
    for name in READERS:
        assert extracted[name] >= MIN_EXTRACTIONS[name], (name, extracted[name])


MIN_EXTRACTIONS: Final = {
    "bundle/links.py:_LINK_RE": 40,
    "bundle/links.py:_INLINE_LINK_RE": 150,
    "bundle/links.py:_REFERENCE_DEFINITION_RE": 40,
    "graph/sqlite_graph.py:_LINK_RE": 40,
    "lint.py:_LINK_RE": 150,
    "bundle/index.py:_LINK_RE": 80,
    "bundle/index.py:_LABELLED_LINK_RE": 80,
}


def test_the_readers_extract_the_forms_that_make_them_disagree() -> None:
    # the multi-line label is read by the graph (whole body) and not by the
    # per-line `_LINK_RE`; an extension-less target by lint and not by either
    multi = next(c for c in CORPUS if c.id == "multi-line-label/abs")
    assert len(_graph_link_re(_output(multi), _local_id(multi))) == 1
    assert _links_link_re(_output(multi), _local_id(multi)) == []
    bare = next(c for c in CORPUS if c.id == "inline/abs-no-ext")
    assert len(_lint_link_re(_output(bare), _local_id(bare))) == 1
    assert _graph_link_re(_output(bare), _local_id(bare)) == []
    fenced = next(c for c in CORPUS if c.id == "fenced-code/abs")
    assert len(_lint_link_re(_output(fenced), _local_id(fenced))) == 1


def test_the_cross_check_fails_for_an_unrewritten_body() -> None:
    # the check is live: the raw foreign body, not the rewrite, trips it
    problems = []
    for case in CORPUS:
        for name, reader in READERS.items():
            if name in INDEX_READERS:
                continue
            for target, resolved in reader(case.body_in, _local_id(case)):
                if not _inside(resolved):
                    problems.append((name, case.id, target))
    assert {name for name, _, _ in problems} == set(READERS) - INDEX_READERS


# --- the inventory (2.11) ------------------------------------------------------

_SOURCE_ROOT: Final = Path(__file__).resolve().parents[3] / "src" / "openkos"
_READS_A_LINK: Final = re.compile(r"\\\]\\?[(:]")
_RE_FUNCTIONS: Final = frozenset(
    {
        "compile",
        "search",
        "match",
        "fullmatch",
        "finditer",
        "findall",
        "sub",
        "subn",
        "split",
    }
)


def _is_re_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.func.attr in _RE_FUNCTIONS
    )


def recognizer_literals(source: str, module: str) -> set[str]:
    """`module:name` of every `re` call in `source` whose literal pattern
    mentions `](` or `]:`, escaped or not, the name being the variable the
    call is assigned to."""
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if not _is_re_call(node) or not isinstance(node, ast.Call) or not node.args:
            continue
        constants = sorted(
            (
                const
                for const in ast.walk(node.args[0])
                if isinstance(const, ast.Constant) and isinstance(const.value, str)
            ),
            key=lambda const: (const.lineno, const.col_offset),
        )
        pattern = "".join(str(const.value) for const in constants)
        if not _READS_A_LINK.search(pattern):
            continue
        parent = parents.get(node)
        name = "<call>"
        if isinstance(parent, ast.Assign) and isinstance(parent.targets[0], ast.Name):
            name = parent.targets[0].id
        elif isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            name = parent.target.id
        found.add(f"{module}:{name}")
    return found


def _scan_source_tree() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        module = path.relative_to(_SOURCE_ROOT).as_posix()
        found |= recognizer_literals(path.read_text(encoding="utf-8"), module)
    return found


def test_the_inventory_equals_the_cross_checked_readers() -> None:
    known = set(READERS) | set(EXEMPT)
    found = _scan_source_tree()
    assert found - known == set(), (
        "a new regex reads markdown links: add it to READERS (and the corpus "
        "cross-check) or to EXEMPT with a reason"
    )
    assert known - found == set(), "a listed reader no longer exists"
    assert set(READERS) & set(EXEMPT) == set()


def test_the_scan_is_live() -> None:
    synthetic = (
        "import re\n"
        'NEW_READER = re.compile(r"\\[x\\]\\((.+?)\\)")\n'
        'DEFINITIONS = re.compile(r"\\]:\\s*(\\S+)")\n'
        'SPLIT = re.compile(r"\\]" + r"\\(")\n'
        'UNRELATED = re.compile(r"\\[\\]")\n'
        'NOT_REGEX = "\\\\]("\n'
    )
    assert recognizer_literals(synthetic, "x.py") == {
        "x.py:NEW_READER",
        "x.py:DEFINITIONS",
        "x.py:SPLIT",
    }
    # the comparison the inventory test makes fails when it appears
    known = set(READERS) | set(EXEMPT)
    assert recognizer_literals(synthetic, "x.py") - known
