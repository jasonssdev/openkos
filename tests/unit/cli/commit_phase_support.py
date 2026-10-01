"""Probes shared by the commit-phase tests of the curation verbs (#1137).

The lock is probed from INSIDE a verb, at the exact point under test (a
stubbed prompt, a wrapped compute function, a wrapped write), by trying to
take it from the same process: `flock` on a second open file description
refuses even within one process, so "free" and "held" are both observable
without a subprocess and without timing.
"""

import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import lock
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def lock_is_free(root: Path) -> bool:
    """Whether another OpenKOS process could take the workspace lock right now."""
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


def hold_the_lock_from(
    stack: contextlib.ExitStack, root: Path, acquired: list[bool]
) -> Callable[[], object]:
    """A prompt-time edit that takes the workspace lock as "another process"
    would and keeps it until `stack` closes. `acquired` proves the acquisition
    itself succeeded -- if the verb under test were still holding the lock
    through its prompt, this would raise and the contention test would pass
    for the wrong reason."""

    def _take() -> None:
        stack.enter_context(lock.workspace_lock(root))
        acquired.append(True)

    return _take


def simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def write_doc(
    tmp_path: Path,
    concept_id: str,
    metadata: dict[str, object],
    body: str = "Body.\n",
) -> Path:
    path = tmp_path / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    return path


def metadata_of(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    return okf.load_frontmatter(text)[0]


def wrap(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
    name: str,
    *,
    before: Callable[[], object],
) -> None:
    """Replace `target.name` with a wrapper that runs `before` and then the
    real function, so a test observes the state at the moment a phase starts."""
    real = getattr(target, name)

    def _wrapper(*args: object, **kwargs: object) -> object:
        before()
        return real(*args, **kwargs)

    monkeypatch.setattr(target, name, _wrapper)
