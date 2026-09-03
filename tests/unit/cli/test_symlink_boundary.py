"""Workspace symlink boundary (#926): no command reads or writes through a
symlinked path segment inside the workspace.

`config._refusal_conditions` has always refused a symlinked `raw/`/`bundle/`
at INIT time, but nothing re-checked afterwards: `require_workspace` used
`is_file()`, which resolves links. A workspace could therefore be initialized
cleanly and then have a segment swapped for a link into an external tree, with
every later command operating through it.

Two distinct escapes live here and the tests keep them apart, because they do
NOT have the same consequence:

* A linked **inner directory** carries writes and deletes outside. This is the
  one that reproduced as real data loss: `forget area/secret` unlinked the
  external file, exited 0, and reported success -- only git noticed, as a
  warning ("pathspec ... is beyond a symbolic link").
* A linked **leaf** does not carry the delete (`unlink` removes the link) but
  is a READ vector: `okf.concept_path_for` resolves an exactly-named link, so
  external bytes reach prompts, answers, and the git lifecycle.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from tests.unit.cli.conftest import commit_pending_fixture_docs

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _plant_external_concept(outside: Path, name: str = "secret.md") -> Path:
    outside.mkdir(parents=True, exist_ok=True)
    victim = outside / name
    victim.write_text(
        "---\ntype: Concept\ntitle: Secret\n---\n\n# Secret\n\nExternal.\n",
        encoding="utf-8",
    )
    return victim


def _add_bullet(tmp_path: Path, link: str) -> None:
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + f"\n# Concepts\n\n* [Secret]({link}) - external.\n",
        encoding="utf-8",
    )
    # A real session reaches a delete verb with its documents already tracked
    # (issue #819); the fixture must match or the auto-commit degrades and the
    # command exits non-zero for a reason unrelated to the boundary.
    commit_pending_fixture_docs()


def test_forget_refuses_a_concept_behind_a_linked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reproduced data-loss case: `bundle/area` linked outside, so
    `bundle/area/secret.md` resolves into the external tree and `fsio.remove_file`
    unlinks the EXTERNAL file. Before #926 this exited 0 and reported success."""
    outside = tmp_path / "outside" / "area"
    victim = _plant_external_concept(outside)
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "area").symlink_to(outside)
    _add_bullet(tmp_path, "area/secret.md")

    result = runner.invoke(app, ["forget", "area/secret", "--auto"])

    assert result.exit_code == 1
    assert "symlink" in result.output
    assert victim.exists(), "forget deleted a file outside the workspace"
    assert victim.read_text(encoding="utf-8").endswith("External.\n")


def test_forget_refuses_a_linked_leaf_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked LEAF must be refused too. `unlink` would remove the link rather
    than the target, so this is not data loss -- but Phase A reads the concept
    through the link first, pulling external bytes into the plan and the log."""
    outside = tmp_path / "outside"
    victim = _plant_external_concept(outside)
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "secret.md").symlink_to(victim)
    _add_bullet(tmp_path, "secret.md")

    result = runner.invoke(app, ["forget", "secret", "--auto"])

    assert result.exit_code == 1
    assert "symlink" in result.output
    assert victim.exists()


def test_purge_refuses_a_concept_behind_a_linked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`purge` shares `_resolve_concept_path` as its root-id gate, and it is the
    more destructive verb (history rewrite on top of the unlink), so the same
    refusal must cover it."""
    outside = tmp_path / "outside" / "area"
    victim = _plant_external_concept(outside)
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "area").symlink_to(outside)
    _add_bullet(tmp_path, "area/secret.md")

    # `purge` has no --auto (the typed confirm phrase is deliberate), but the
    # boundary gate runs in Phase A, before any confirmation is asked for.
    result = runner.invoke(app, ["purge", "area/secret"])

    assert result.exit_code == 1
    assert "symlink" in result.output
    assert victim.exists(), "purge deleted a file outside the workspace"


def test_status_refuses_a_symlinked_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace-level half: a read-only command refuses too, so the boundary
    is enforced by the shared gate rather than only by the destructive verbs."""
    outside = tmp_path / "outside"
    outside.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    monkeypatch.chdir(real)
    assert runner.invoke(app, ["init"]).exit_code == 0
    (outside / "bundle").mkdir()
    for name in ("index.md", "log.md"):
        (outside / "bundle" / name).write_text("stub", encoding="utf-8")
    import shutil

    shutil.rmtree(real / "bundle")
    (real / "bundle").symlink_to(outside / "bundle")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 1
    assert "symlink" in result.output


def test_forget_still_works_in_a_clean_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-regression: the boundary must not refuse an ordinary nested concept.

    Without this, every refusal test above would still pass if the guard simply
    rejected any concept-id containing a '/'.
    """
    _init_workspace(tmp_path, monkeypatch)
    concept = tmp_path / "bundle" / "area" / "kept.md"
    concept.parent.mkdir(parents=True)
    concept.write_text(
        "---\ntype: Concept\ntitle: Kept\n---\n\n# Kept\n\nBody.\n", encoding="utf-8"
    )
    _add_bullet(tmp_path, "area/kept.md")

    result = runner.invoke(app, ["forget", "area/kept", "--auto"])

    assert result.exit_code == 0, result.output
    assert not concept.exists()
