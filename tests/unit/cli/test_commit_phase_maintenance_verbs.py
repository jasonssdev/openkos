"""The commit phase of `sync-tags`, `normalize-names`, `repair`, `reconcile`,
`adjudicate` and `suggest-relations` (#1137, ADR-0036; tasks.md 1.6, second
group).

Each verb computes and asks lock-free and holds the workspace lock only for
its commit phase. The lock is probed from INSIDE the verb, at the exact point
under test (a stubbed prompt, a wrapped compute function, a wrapped write), by
trying to take it from the same process: `flock` on a second open file
description refuses even within one process, so "free" and "held" are both
observable without a subprocess and without timing.

Every test that asserts the lock was FREE also asserts, from the same run,
that it was HELD at the write -- otherwise "never held at all" would pass.
"""

import contextlib
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio, lock
from openkos.application import lifecycle as application_lifecycle
from openkos.application import repair as application_repair
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import (
    changed_paths,
    commit_pending_fixture_docs,
    confirm_after,
    snapshot_with_mtime,
)
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


@pytest.fixture(autouse=True)
def _git_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity-config"),
        name="Isolated Tester",
        email="tester@example.invalid",
    )


def _lock_is_free(root: Path) -> bool:
    """Whether another OpenKOS process could take the workspace lock right now."""
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _write_doc(
    tmp_path: Path,
    concept_id: str,
    metadata: dict[str, object],
    body: str = "Body.\n",
) -> Path:
    path = tmp_path / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    return path


def _metadata(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    return okf.load_frontmatter(text)[0]


def _wrap(
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


def _hold_the_lock_from(
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


# -- sync-tags ----------------------------------------------------------------


def _seed_sync_tags(tmp_path: Path) -> None:
    _write_doc(
        tmp_path, "sources/notes", {"type": "Source", "title": "N", "tags": ["alpha"]}
    )
    _write_doc(
        tmp_path,
        "concepts/a",
        {"type": "Concept", "title": "A", "provenance": ["sources/notes"]},
    )
    commit_pending_fixture_docs()


def test_sync_tags_prompt_does_not_hold_the_lock_but_the_write_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_sync_tags(tmp_path)
    _simulate_tty(monkeypatch)
    at_prompt: list[bool] = []
    at_write: list[bool] = []
    confirm_after(monkeypatch, lambda: at_prompt.append(_lock_is_free(tmp_path)))
    _wrap(
        monkeypatch,
        application_lifecycle,
        "sync_tags_core",
        before=lambda: at_write.append(_lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="y\n")

    assert result.exit_code == 0, result.stderr
    assert at_prompt == [True]
    assert at_write == [False]
    assert _metadata(tmp_path, "concepts/a")["tags"] == ["alpha"]


def test_sync_tags_refuses_with_exit_3_when_the_lock_is_busy_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_sync_tags(tmp_path)
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        confirm_after(monkeypatch, _hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(app, ["sync-tags", "sources/notes"], input="y\n")

    assert acquired == [True]
    assert result.exit_code == 3
    assert "refusing to run" in result.stderr
    assert _snapshot(tmp_path) == before


def test_sync_tags_refuses_a_tag_whose_provenance_path_vanished_between_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel: a confidential Source's tag value must not land on a concept
    that stopped citing it while the prompt waited.

    `concepts/d` is grounded in the Source only THROUGH `concepts/m`, which
    already carries the tag and so is never written -- it is a read dependency
    and nothing else. Re-attributing `concepts/m` removes `concepts/d` from the
    Source's provenance closure."""
    sentinel = "SENTINEL-CONFIDENTIAL-TAG"
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(
        tmp_path,
        "sources/secret",
        {
            "type": "Source",
            "title": "S",
            "tags": [sentinel],
            "sensitivity": "confidential",
        },
    )
    _write_doc(tmp_path, "sources/other", {"type": "Source", "title": "O"})
    middle = _write_doc(
        tmp_path,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "provenance": ["sources/secret"],
            "tags": [sentinel],
            "sensitivity": "confidential",
        },
    )
    derived = _write_doc(
        tmp_path,
        "concepts/d",
        {
            "type": "Concept",
            "title": "D",
            "provenance": ["concepts/m"],
            "sensitivity": "confidential",
        },
    )
    commit_pending_fixture_docs()
    _simulate_tty(monkeypatch)
    derived_before = derived.read_bytes()

    def _re_attribute() -> None:
        _write_doc(
            tmp_path,
            "concepts/m",
            {
                "type": "Concept",
                "title": "M",
                "provenance": ["sources/other"],
                "tags": [sentinel],
                "sensitivity": "confidential",
            },
        )

    confirm_after(monkeypatch, _re_attribute)

    result = runner.invoke(app, ["sync-tags", "sources/secret"], input="y\n")

    assert middle.exists()
    assert result.exit_code == 3, result.stderr
    assert "refusing to write --" in result.stderr
    assert derived.read_bytes() == derived_before
    assert sentinel not in derived.read_text(encoding="utf-8")


# -- normalize-names ----------------------------------------------------------


def test_normalize_names_prompt_does_not_hold_the_lock_but_the_rename_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    nfd = unicodedata.normalize("NFD", "café") + ".md"
    (tmp_path / "bundle").joinpath(nfd).write_text("body\n", encoding="utf-8")
    commit_pending_fixture_docs()
    _simulate_tty(monkeypatch)
    at_prompt: list[bool] = []
    at_write: list[bool] = []
    confirm_after(monkeypatch, lambda: at_prompt.append(_lock_is_free(tmp_path)))
    _wrap(
        monkeypatch,
        fsio,
        "rename_two_step",
        before=lambda: at_write.append(_lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["normalize-names"], input="y\n")

    assert result.exit_code == 0, result.stderr
    assert at_prompt == [True]
    assert at_write == [False]


def test_normalize_names_refuses_with_exit_3_when_the_lock_is_busy_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    nfd = unicodedata.normalize("NFD", "café") + ".md"
    (tmp_path / "bundle").joinpath(nfd).write_text("body\n", encoding="utf-8")
    commit_pending_fixture_docs()
    _simulate_tty(monkeypatch)
    before = snapshot_with_mtime(tmp_path)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        confirm_after(monkeypatch, _hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(app, ["normalize-names"], input="y\n")

    assert acquired == [True]
    assert result.exit_code == 3
    assert "refusing to run" in result.stderr
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()


# -- repair -------------------------------------------------------------------


def _seed_repair(tmp_path: Path) -> None:
    _write_doc(
        tmp_path,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    _write_doc(
        tmp_path, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )
    commit_pending_fixture_docs()


def test_repair_plans_without_the_lock_and_writes_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_repair(tmp_path)
    at_plan: list[bool] = []
    at_write: list[bool] = []
    _wrap(
        monkeypatch,
        application_repair,
        "plan_repair",
        before=lambda: at_plan.append(_lock_is_free(tmp_path)),
    )
    _wrap(
        monkeypatch,
        application_repair,
        "apply_repair",
        before=lambda: at_write.append(_lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0, result.stderr
    assert at_plan == [True]
    assert at_write == [False]
    assert _metadata(tmp_path, "concepts/b")["status"] == "deprecated"


def test_repair_refuses_an_export_whose_superseding_edge_vanished_between_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel: `concepts/b`'s deprecation is decided by `concepts/a`'s
    `supersedes` edge, a document `repair` reads but never writes. If that edge
    is removed after the plan was computed, the export must not be written."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_repair(tmp_path)
    target = tmp_path / "bundle" / "concepts" / "b.md"
    target_before = target.read_bytes()
    real = application_repair.plan_repair

    def _plan_then_remove_the_edge(bundle_dir: Path) -> object:
        plan = real(bundle_dir)
        _write_doc(tmp_path, "concepts/a", {"type": "Concept", "title": "A"})
        return plan

    monkeypatch.setattr(application_repair, "plan_repair", _plan_then_remove_the_edge)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 3, result.stderr
    assert "refusing to write --" in result.stderr
    assert "bundle/concepts/a.md" in result.stderr
    assert target.read_bytes() == target_before
    assert "deprecated" not in target.read_text(encoding="utf-8")
