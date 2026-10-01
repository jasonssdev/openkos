"""Structural guard for pending-work resolution (#1141, ADR-0037, unit 4.3).

Every shared write core must either resolve its queue rows or say, in
`queue_resolution.NON_RESOLVING_CORES`, why it resolves none. The cores are
ENUMERATED from the source (every function named `*_core` or
`apply_*_decision`), so a new core added without a decision fails here instead
of silently leaving rows open forever.
"""

import ast
from pathlib import Path

import pytest

import openkos
from openkos.application import queue_resolution

SRC = Path(openkos.__file__).parent


def _module_name(path: Path) -> str:
    return ".".join(("openkos", *path.relative_to(SRC).with_suffix("").parts))


def _is_core_name(name: str) -> bool:
    return name.endswith("_core") or (
        name.startswith("apply_") and name.endswith("_decision")
    )


def _functions() -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_name(path)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                found[f"{module}.{node.name}"] = node
    return found


FUNCTIONS = _functions()


def _resolution_calls(
    func: ast.FunctionDef, module: str, seen: frozenset[str] = frozenset()
) -> set[str]:
    """The `queue_resolution.<name>` functions `func` calls, directly or through
    a same-module helper."""
    calls: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "queue_resolution"
        ):
            calls.add(target.attr)
        elif isinstance(target, ast.Name):
            helper = f"{module}.{target.id}"
            if helper in FUNCTIONS and helper not in seen:
                calls |= _resolution_calls(FUNCTIONS[helper], module, seen | {helper})
    return calls


REGISTERED = {
    **queue_resolution.WRITE_CORE_RESOLUTIONS,
    **dict.fromkeys(queue_resolution.NON_RESOLVING_CORES),
}


def test_every_write_core_is_registered_as_resolving_or_not() -> None:
    cores = {name for name in FUNCTIONS if _is_core_name(name.rsplit(".", 1)[1])}
    # The resolving registry also names cores that do not match the pattern.
    assert cores, "the enumeration found no cores: the pattern is stale"
    unregistered = sorted(cores - set(REGISTERED))
    assert unregistered == [], (
        "write core(s) with no queue-resolution decision; call a "
        "`queue_resolution.resolve_*` function in each and register it in "
        f"WRITE_CORE_RESOLUTIONS, or justify it in NON_RESOLVING_CORES: {unregistered}"
    )


def test_a_core_is_never_both_resolving_and_exempt() -> None:
    both = set(queue_resolution.WRITE_CORE_RESOLUTIONS) & set(
        queue_resolution.NON_RESOLVING_CORES
    )
    assert both == set()


def test_every_registered_core_exists() -> None:
    assert sorted(set(REGISTERED) - set(FUNCTIONS)) == []


@pytest.mark.parametrize(
    ("core", "resolver"), sorted(queue_resolution.WRITE_CORE_RESOLUTIONS.items())
)
def test_a_resolving_core_calls_its_resolver(core: str, resolver: str) -> None:
    module = core.rsplit(".", 1)[0]
    assert resolver in _resolution_calls(FUNCTIONS[core], module), (
        f"{core} is registered as resolving rows but never calls "
        f"queue_resolution.{resolver}"
    )
    assert callable(getattr(queue_resolution, resolver))


def test_every_non_resolving_core_states_a_reason() -> None:
    assert all(
        reason.strip() for reason in queue_resolution.NON_RESOLVING_CORES.values()
    )


def test_no_resolver_is_orphaned() -> None:
    public = {
        name
        for name in vars(queue_resolution)
        if name.startswith("resolve_") and callable(getattr(queue_resolution, name))
    }
    assert public == set(queue_resolution.WRITE_CORE_RESOLUTIONS.values())
