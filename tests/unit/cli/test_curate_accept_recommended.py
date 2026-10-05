"""`openkos curate`'s accept-recommended Identity question (#1298, ADR-0049).

The selector is proven in `tests/unit/application/test_auto_merge.py`; this
module proves what only the command can: the shared write helper, the offer's
text and answers, the per-merge commits, and every situation that must NOT
offer. No test reaches a model: every judge and the installed-models listing
are stubs, built by the sibling `--auto-merge` module's fixtures.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos.cli import curate
from openkos.cli.main import app
from tests.unit.cli.test_curate_auto_merge import (
    _FOO,
    _FOO_PAIR,
    _SAME,
    _build,
    _edit_config,
    _git,
    _present,
    _simulate_tty,
)

runner = CliRunner()

_WINDOW = {"context_window: 12288": "context_window: 16384"}


def _walk_only_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Any]:
    """A statically ineligible run (a different context window), so the
    per-item walk is the only thing that can write: the accept-recommended
    offer is never made."""
    root, judge = _build(tmp_path, monkeypatch, [_FOO], {_FOO_PAIR: _SAME})
    _edit_config(root, replace=_WINDOW)
    _simulate_tty(monkeypatch)
    return root, judge


# --------------------------------------------------------------------------- #
# the write helper both paths share (tasks 4.4-4.5)
# --------------------------------------------------------------------------- #


def test_the_walk_writes_each_accepted_item_through_the_shared_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, str]] = []
    real = curate._identity_write_one

    def _spy(ctx: curate.CurateContext, prepared: Any) -> bool:
        calls.append((prepared.survivor_canonical, prepared.absorbed_canonical))
        return real(ctx, prepared)

    monkeypatch.setattr(curate, "_identity_write_one", _spy)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "y")
    before = _git(root, "rev-list", "--count", "HEAD").strip()

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert calls == [("concepts/foo", "concepts/foo-2")]
    assert not _present(root, "concepts/foo-2")
    assert int(_git(root, "rev-list", "--count", "HEAD").strip()) == int(before) + 1
    # The per-item commit disclosure is still printed, indented under its item.
    assert any(
        line.startswith("  committed as ") for line in result.stdout.splitlines()
    )


def test_a_drift_refusal_inside_the_helper_still_exits_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)

    def _answer_after_an_edit(*args: object, **kwargs: object) -> str:
        # The survivor changes while the prompt waits: the commit-phase
        # re-validation inside the helper must refuse it.
        path = root / "bundle" / "concepts" / "foo.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8"
        )
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_after_an_edit)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 3
    assert _present(root, "concepts/foo-2")


def test_a_member_forgotten_while_the_prompt_waits_is_skipped_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_only_workspace(tmp_path, monkeypatch)

    def _answer_after_a_forget(*args: object, **kwargs: object) -> str:
        (root / "bundle" / "concepts" / "foo-2.md").unlink()
        return "y"

    monkeypatch.setattr("typer.prompt", _answer_after_a_forget)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert (
        "openkos curate: Identity: skipped concepts/foo-2 -> concepts/foo -- a "
        "member no longer exists." in result.stderr.splitlines()
    )
    assert "Identity: applied 0, skipped 1." in result.stdout.splitlines()
