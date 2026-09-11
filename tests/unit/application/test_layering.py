"""Layering guard for `openkos.application` (D5/the layering invariant,
ADR-0018): `docs/architecture.md:112` states this convention has NO
automated CI guard, so this test is the only thing that catches
`openkos.application` importing upward from `openkos.cli`, `typer`, or
`rich` -- an adapter importing another adapter, or an application module
rendering its own output, either of which would defeat the entire point of
the application layer (MVP 3's `api`/`mcp` adapters must be able to import
any `openkos.application.*` module without dragging in Typer, Rich, or
`openkos.cli`).

Generalized (issue #918, design "the layering guard is generalized, not
copied") from a single hardcoded `_QUERY_MODULE` constant to an iteration
over every module under `src/openkos/application/`, so a THIRD context
(e.g. `application/ingest.py`, or a future `application/lifecycle.py`) is
covered by construction the moment the file exists, rather than requiring
a per-module copy of this guard.

The lifecycle slice (issue #918) adds `openkos.vcs` to the offender list
-- `purge` runs `git filter-repo` and `merge`/`unmerge`/`forget` run
`_autocommit`, both of which stay adapter-side by design, so
`application/lifecycle.py` must never import the VCS layer directly
(threat matrix: Git repository selection) -- and adds `snapshot_read` to
`test_shared_write_helpers_are_never_forked`'s set (design D5): it is now
the first READ helper shared ACROSS the layer boundary
(`application/lifecycle.py` calls `fsio.snapshot_read`, promoted from
`cli/main._snapshot_read`), and a fork of it would silently break the
#318 one-observation invariant every drift guard rests on.
"""

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_APPLICATION_DIR = _REPO_ROOT / "src" / "openkos" / "application"


def _application_modules() -> list[Path]:
    """Every `.py` file directly under `application/`, sorted for a stable
    iteration order -- excludes `__init__.py`, which carries no imports of
    its own worth scanning and would otherwise show up as a spurious
    always-clean entry in a failure report."""
    return sorted(
        path for path in _APPLICATION_DIR.glob("*.py") if path.name != "__init__.py"
    )


def _imported_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_application_directory_is_scanned_completely() -> None:
    """Sanity check that the directory scan itself is not accidentally empty
    or stale. Pins that `ingest.py` -- this change's new application-layer
    module (issue #918, Slice 1) -- is discovered by the scan the moment it
    exists, so a future reader trusts the OTHER two tests in this file
    actually looked at it rather than silently scanning zero files."""
    modules = {path.name for path in _application_modules()}
    assert modules >= {"query.py", "ingest.py"}, (
        f"expected 'query.py' and 'ingest.py' among scanned application "
        f"modules; found: {modules}"
    )


def test_application_modules_never_import_cli_typer_or_rich() -> None:
    """AST-scan every `application/*.py` module's imports; none may
    reference `openkos.cli` (or a submodule of it), `typer`, `rich`, or
    `openkos.vcs` (or a submodule of it), in either `import` or
    `from ... import` form -- a runtime AST scan rather than an actual
    import, so the assertion holds even if the offending import would
    itself fail to resolve.

    `openkos.vcs` joined the offender list for the lifecycle slice (issue
    #918, threat matrix: Git repository selection) -- every `vcs_git.*`
    call stays adapter-side, so the service must never import it."""
    offenders: dict[str, list[str]] = {}
    for path in _application_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_module_names(tree)
        bad = [
            name
            for name in imported
            if name == "openkos.cli"
            or name.startswith("openkos.cli.")
            or name == "typer"
            or name.startswith("typer.")
            or name == "rich"
            or name.startswith("rich.")
            or name == "openkos.vcs"
            or name.startswith("openkos.vcs.")
        ]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "openkos.application modules must never import openkos.cli, typer, "
        f"rich, or openkos.vcs; found: {offenders}"
    )


def test_application_modules_bind_no_concrete_llm_backend() -> None:
    """Every application module takes its `LLMBackend` as a parameter (ADR-
    0018 D1) so every adapter supplies its own. Importing a CONCRETE backend
    anywhere under `application/` would bind that module to Ollama and
    defeat that -- the spec requirement "No concrete backend is bound
    inside the service" otherwise rests on static reading alone, which
    nothing re-checks on a later edit. Each module's `openkos.llm.*`
    imports, if any, must be exactly `openkos.llm.base` -- the Protocol
    seam -- never a concrete implementation module."""
    offenders: dict[str, list[str]] = {}
    for path in _application_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        llm_imports = [
            name
            for name in _imported_module_names(tree)
            if name.startswith("openkos.llm.")
        ]
        bad = [name for name in llm_imports if name != "openkos.llm.base"]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "openkos.application modules may import only openkos.llm.base, "
        f"never a concrete backend; found: {offenders}"
    )


def test_shared_write_helpers_are_never_forked() -> None:
    """`_reject_drifted_targets`, `_autocommit` and `_refresh_derived_after_write`
    are shared write infrastructure the query service calls THROUGH rather than
    owns (ADR-0018 D3). A second definition anywhere under `src/` would mean one
    write path silently diverged from the one every other command uses.

    `snapshot_read` joined this set for the lifecycle slice (issue #918,
    design D5): promoted from `cli/main._snapshot_read` to `fsio.
    snapshot_read`, it is now the first READ helper shared ACROSS the
    layer boundary -- `application/lifecycle.py` calls it directly, and
    `main._snapshot_read` is a one-line delegator, never a second
    implementation.

    `_echo_commit_disclosure` joined for issue #956. The
    `lifecycle-application-service` spec's "Shared Write Mechanics Stay
    Adapter-Side, Each With One Definition" requirement already named it
    alongside the other three, but it was absent from this set, so that
    clause held by inspection only. It renders the `committed as <sha> --
    undo with 'git revert <sha>'` notice for `forget`, `merge` and
    `curate`; a forked copy that drifted would hand some users the wrong
    recovery command, and only on the paths that took the fork.

    The scan counts `async def` as a definition too. Measured while adding
    `_echo_commit_disclosure` (issue #956): a second definition spelled
    `async def` passed this guard for every name in the set, because
    `ast.FunctionDef` does not match `ast.AsyncFunctionDef`. The assertion
    claims "exactly one definition", so a shape it cannot see is a
    fail-open, not a narrower claim.

    The requirement this guard stands for has TWO halves -- "each retain
    exactly one definition, all adapter-side" -- and counting only covers
    the first. Measured on the same candidate (issue #956): MOVING
    `_echo_commit_disclosure`'s one definition out of `cli/main.py` and
    into `application/lifecycle.py` kept the count at 1 and passed every
    guard in this file, even though a shared write helper living inside
    the service is precisely what the requirement forbids. The import
    guards above do not catch it either, because a pasted body needs no
    new import to be defined. So the locations are asserted, not just the
    counts.

    The location assertion is written as "never under `application/`"
    rather than as a per-helper home path. "Adapter-side" is the
    invariant; which adapter module holds a helper is not, so a
    legitimate move from `cli/main.py` to another `cli/` module must not
    redden this test."""
    shared = {
        "_reject_drifted_targets",
        "_autocommit",
        "_refresh_derived_after_write",
        "_echo_commit_disclosure",
        "snapshot_read",
    }
    homes: dict[str, list[str]] = {name: [] for name in shared}
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in shared
            ):
                homes[node.name].append(path.relative_to(_REPO_ROOT).as_posix())
    counts = {name: len(paths) for name, paths in homes.items()}
    assert counts == dict.fromkeys(shared, 1), (
        f"each shared write helper must keep exactly one definition; found: {homes}"
    )
    inside_service = {
        name: paths
        for name, paths in homes.items()
        if any(path.startswith("src/openkos/application/") for path in paths)
    }
    assert not inside_service, (
        "no shared write helper may be DEFINED inside the application "
        "service; the service calls them through the adapter "
        f"(ADR-0018 D3). Found: {inside_service}"
    )


def _is_private(name: str) -> bool:
    """Whether `name` is a name someone chose to hide. Dunders are not:
    `module.__name__` and friends are interpreter surface."""
    return name.startswith("_") and not name.startswith("__")


def _names_application_package(module: str | None) -> bool:
    """Whether an `ImportFrom`'s module names `openkos.application` or
    something under it, in ANY of the spellings Python allows.

    The test is on the SEGMENTS, not on a
    `startswith("openkos.application")` prefix that only matches the
    absolute spelling: a relative import arrives with its package prefix
    already stripped, so `from ..application import lifecycle` has
    `module="application"`. `from .. import application` arrives with
    `module` empty and carries the package among its NAMES instead, which
    is `_application_bindings`' business, not this predicate's -- which is
    why the `ImportFrom.level` this once took is not a parameter: nothing
    it decides depends on the distance, only on the segments."""
    return "application" in (module or "").split(".")


def _application_bindings(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """Every local name in one module that can reach an
    `openkos.application` submodule, as
    `(module_bindings, package_bindings)`.

    `module_bindings` maps a local name straight to the submodule it names
    (`application_lifecycle -> lifecycle`). `package_bindings` holds local
    names from which the submodule is one attribute hop further out
    (`application` in `application.lifecycle._x`).

    Every spelling is collected, not just the two the repo happens to use
    today, because the point of this guard is to survive a caller written
    by someone who did not read it. Measured when this was written, each of
    these reached a private name past an earlier version that resolved
    only the two known spellings: `import openkos.application.lifecycle`
    with no `as`, `from openkos import application`, `from ..application
    import lifecycle as lc`, and `from .. import application as ap`. The
    last one survived one further round, because its `ImportFrom` carries
    `module=None` and names the package among its aliases."""
    module_bindings: dict[str, str] = {}
    package_bindings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            segments = (node.module or "").split(".")
            if not node.module:
                # `from . import application` / `from .. import application`
                package_bindings.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "application"
                )
            elif segments[-1] == "application":
                # `from [...]application import lifecycle [as X]`
                for alias in node.names:
                    module_bindings[alias.asname or alias.name] = alias.name
            elif _names_application_package(node.module):
                # `from [...]application.lifecycle import name [as X]` binds
                # a VALUE, never a module, so it contributes no binding: a
                # private among those names is the `ImportFrom` branch's
                # business below. Binding them here made
                # `member_body_length._cache` read as a private reference on
                # a module -- a FALSE POSITIVE on innocent code, which is a
                # worse failure for a guard than the gap it was added to
                # close.
                continue
            else:
                # `from openkos import application [as A]`, and the same
                # shape spelled relatively.
                package_bindings.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "application"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                segments = alias.name.split(".")
                if "application" not in segments:
                    continue
                if alias.asname and segments[-1] != "application":
                    module_bindings[alias.asname] = segments[-1]
                elif alias.asname:
                    package_bindings.add(alias.asname)
                else:
                    # `import openkos.application.lifecycle` binds only the
                    # ROOT name; the reference is a dotted chain from there.
                    package_bindings.add(segments[0])
    return module_bindings, package_bindings


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` for a pure `Name`/`Attribute` chain, else `None` (a call, a
    subscript, or anything else whose target this guard cannot name)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return None if prefix is None else f"{prefix}.{node.attr}"
    return None


def _private_application_references(
    tree: ast.Module, application_modules: set[str]
) -> list[str]:
    """Every reference in one module to an underscore-prefixed name on an
    `openkos.application` submodule, as `"<chain> (line N)"` strings.

    Split out of the guard below so the spellings it does and does not
    catch can be asserted directly, on source strings, in
    `test_private_application_reference_detector_catches_every_spelling`.
    A guard whose negative cases were only ever checked by hand in a
    throwaway script is a guard nobody can see working later."""
    module_bindings, package_bindings = _application_bindings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_private(node.attr):
            chain = _dotted(node)
            if chain is None:
                continue
            parts = chain.split(".")
            reaches_submodule = (len(parts) == 2 and parts[0] in module_bindings) or (
                len(parts) >= 3
                and parts[-2] in application_modules
                and (parts[0] in package_bindings or parts[-3] == "application")
            )
            # `application._x` -- a private on the package's own
            # `__init__.py`, which is every bit as much a hidden name as one
            # on a submodule.
            reaches_package = (len(parts) == 2 and parts[0] in package_bindings) or (
                len(parts) >= 3 and parts[-2] == "application"
            )
            if reaches_submodule or reaches_package:
                found.append(f"{chain} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom) and _names_application_package(
            node.module
        ):
            found.extend(
                f"{node.module}.{alias.name} (line {node.lineno})"
                for alias in node.names
                if _is_private(alias.name)
            )
    return found


def test_application_private_names_are_never_consumed_across_the_boundary() -> None:
    """No module under `src/` outside `application/` may reach an
    underscore-prefixed name on an `openkos.application` module (issue
    #974).

    The leading underscore is the only signal a maintainer gets at the
    DEFINITION site that a helper is free to rename or inline. When a
    caller in another module depends on it anyway, that signal is a lie in
    the direction that breaks things silently: `application/lifecycle.py`
    looks free to refactor, and the reference that stops working lives in
    a file the refactor never opened.

    Measured on the candidate that added this guard: `cli/main.py` reached
    `application_lifecycle._member_body_length` from a `lambda` inside
    `_echo_n_gt2_skip`'s `max()` sort key -- the ninth of the nine helpers
    issue #918 Slice 5 relocated, and the only one of them not promoted to
    a public name when issue #955 deleted the `cli.main` aliases and
    repointed every call site at the module attribute. A lambda body is
    the worst possible home for the one cross-module contract in that set,
    because nothing at either end reads as a public surface.

    Scope, stated exactly, because a guard that overclaims is worse than a
    narrow one:

    - `src/` only. `tests/` is deliberately NOT scanned: a unit test
      reaching a private helper is how the helper gets tested, and the
      hazard this guard exists for -- a refactor inside `application/`
      silently breaking a SHIPPED call path -- is not what a test
      reference creates.
    - Attribute access and `from ... import _name`, in every import
      spelling (see `_application_bindings`); the two the repo uses today
      are not the two a future caller will necessarily write.
    - What it cannot see: a name reached through `getattr(module, "_x")`,
      a string, or any indirection an AST cannot resolve to a dotted
      chain. Those are invisible to this guard by construction, not
      tolerated by it.
    - What it over-reports, both by choice: a module-level name REBOUND
      away from its import (`lc = object()` after `import ... as lc`, then
      `lc._p`) is still read as the module, and a dotted chain whose
      second-to-last segment is literally `application` is treated as this
      package even when nothing bound it (a third-party `other.application.
      mod._p` would be flagged). Resolving either needs scope or
      import-graph analysis that is out of proportion to the hazard, and
      both err in the safe direction: they make this guard noisy, never
      blind. A false positive stops CI and gets read; a false negative is
      the silent break this guard exists to prevent.

    Private names WITHIN `application/` stay private and unguarded here,
    and `application/` reaching into another layer's privates is the
    import guards' business above, not this one's."""
    application_modules = {path.stem for path in _application_modules()}
    offenders: dict[str, list[str]] = {}
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        if _APPLICATION_DIR in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = _private_application_references(tree, application_modules)
        if found:
            offenders[path.relative_to(_REPO_ROOT).as_posix()] = sorted(found)
    assert not offenders, (
        "an openkos.application module's private name is consumed across the "
        "module boundary; promote it to a public name alongside its siblings, "
        f"or give the caller a public wrapper. Found: {offenders}"
    )


_REACHES_A_PRIVATE = (
    ("as-alias", "from openkos.application import lifecycle as lc\nx = lc._p\n"),
    (
        "dotted, no asname",
        "import openkos.application.lifecycle\nx = openkos.application.lifecycle._p\n",
    ),
    (
        "dotted, asname",
        "import openkos.application.lifecycle as lc\nx = lc._p\n",
    ),
    (
        "package then attr",
        "from openkos import application\nx = application.lifecycle._p\n",
    ),
    (
        "package aliased",
        "from openkos import application as ap\nx = ap.lifecycle._p\n",
    ),
    ("relative package", "from ..application import lifecycle as lc\nx = lc._p\n"),
    ("relative dot", "from .. import application as ap\nx = ap.lifecycle._p\n"),
    ("private imported", "from openkos.application.lifecycle import _p\n"),
    ("private imported, relative", "from ..application.lifecycle import _p\n"),
    ("private on the package", "from openkos import application\nx = application._p\n"),
    (
        "private on the package, aliased",
        "from openkos import application as ap\nx = ap._p\n",
    ),
)

_REACHES_NOTHING = (
    (
        "public attribute",
        "from openkos.application import lifecycle as lc\nx = lc.public\n",
    ),
    ("dunder", "from openkos.application import lifecycle as lc\nx = lc.__name__\n"),
    (
        "private on another module",
        "from openkos import fsio\nx = fsio._anything\n",
    ),
    ("public imported", "from openkos.application.lifecycle import public\n"),
    (
        "attribute on an imported VALUE",
        "from openkos.application.lifecycle import member_body_length\n"
        "x = member_body_length._cache\n",
    ),
)


@pytest.mark.parametrize(("label", "source"), _REACHES_A_PRIVATE, ids=lambda v: v)
def test_private_application_reference_detector_catches_every_spelling(
    label: str, source: str
) -> None:
    """Each import spelling Python allows for reaching
    `openkos.application.lifecycle._p` must be FLAGGED.

    This matrix exists because the first version of this guard resolved
    only the two spellings the repo uses today, and the dotted, the
    package-attribute and both relative spellings walked straight past it
    -- a fail-open in a guard whose whole job is to fail closed. The cases
    are NAMED rather than counted here on purpose: a count in prose goes
    stale the moment a row is added, and this list has already grown once.
    Three independent review lenses flagged the gap on the candidate that
    introduced it; the gap was then confirmed by running each spelling
    through the guard, not by agreeing with the lenses.

    `label` is unused in the body on purpose: it names the case in the
    test id so a failure says WHICH spelling regressed."""
    del label
    assert _private_application_references(ast.parse(source), {"lifecycle"})


@pytest.mark.parametrize(("label", "source"), _REACHES_NOTHING, ids=lambda v: v)
def test_private_application_reference_detector_has_no_false_positives(
    label: str, source: str
) -> None:
    """The complement of the matrix above, and the half that keeps it
    honest: a detector that flagged everything would pass every case in
    `_REACHES_A_PRIVATE` while being useless.

    A public attribute, a dunder (`module.__name__` is interpreter
    surface, not a hidden helper), a private on a module outside
    `application/`, and a public name imported BY name must all come back
    clean."""
    del label
    assert not _private_application_references(ast.parse(source), {"lifecycle"})
