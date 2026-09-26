"""Structural seam guard for `application/next_action.py` (issue #1009,
"Prerequisite inside this change"): the relocation from `cli/next_action.py`
is worth nothing if the module still drags in an adapter's dependencies the
moment it is imported -- `test_layering.py`'s AST scan already proves no
*direct* `openkos.cli`/`typer`/`rich` import exists in the source text, but
an AST scan cannot see a TRANSITIVE pull-in through some other module this
one imports.

Measured, not assumed (the #959 lesson: "verify the headline goal, not the
suite"): a fresh subprocess -- never this pytest process, which has almost
certainly already imported `openkos.cli.main`, `typer`, and `rich` via some
other test module by the time this one runs, making an in-process
`sys.modules` check pass for a reason that has nothing to do with this
module -- imports only `openkos.application.next_action` and reports its
own `sys.modules` back. Zero adapter modules is what the MVP 3 `api`/`mcp`
promise actually requires: an adapter that imports this module must not
transitively load Typer or Rich just to read pending work.
"""

import subprocess
import sys

_PROBE = (
    "import sys\n"
    "import openkos.application.next_action as next_action\n"
    "assert next_action is not None\n"
    "sys.stdout.write(chr(124).join(sorted(sys.modules)))\n"
)


def _sys_modules_after_importing_next_action() -> set[str]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess import of openkos.application.next_action failed: {result.stderr}"
    )
    return set(result.stdout.strip().split("|"))


def test_importing_next_action_loads_no_cli_typer_or_rich() -> None:
    """Importing `openkos.application.next_action` alone, in a fresh
    interpreter, must never populate `sys.modules` with `openkos.cli` (or
    any submodule of it), `typer`, or `rich` (or any submodule of either) --
    the same offender set `test_layering.py`'s static guard already
    forbids, checked here at runtime instead of by reading source text."""
    modules = _sys_modules_after_importing_next_action()
    offenders = {
        name
        for name in modules
        if name == "openkos.cli"
        or name.startswith("openkos.cli.")
        or name == "typer"
        or name.startswith("typer.")
        or name == "rich"
        or name.startswith("rich.")
    }
    assert not offenders, (
        "importing openkos.application.next_action must not load openkos.cli, "
        f"typer, or rich; found in sys.modules: {sorted(offenders)}"
    )
