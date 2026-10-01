"""CLI wiring of the lifecycle verbs' commit phase (ADR-0036, issue #1137):
`merge`, `unmerge`, `forget`, `relate`, `set-sensitivity`, `set-volatility`.

Each verb plans and asks its confirmation question with NO workspace lock and
takes the lock only for its commit phase. The window between the two is
reproduced by stubbing `typer.confirm`, which is exactly where a person reads a
preview: from inside it the test proves the lock is free, and mutates the
workspace the way a concurrent writer would.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import lock
from openkos.bundle import ledger as bundle_ledger
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import commit_pending_fixture_docs, snapshot_bytes

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _concept(
    root: Path,
    concept_id: str,
    *,
    body: str = "Body.\n",
    **metadata: object,
) -> Path:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {
        "type": "Concept",
        "title": concept_id.rsplit("/", 1)[-1].title(),
        **metadata,
    }
    path.write_text(okf.dump_frontmatter(meta, body), encoding="utf-8")
    # Track the document before a verb that deletes it reaches its auto-commit
    # (#819), mirroring the other CLI test modules' document helpers.
    commit_pending_fixture_docs()
    return path


def _lock_is_free(root: Path) -> bool:
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


class _Window:
    """What a stubbed `typer.confirm` observed, and the edit it applied."""

    def __init__(self) -> None:
        self.lock_free_during_prompt: list[bool] = []
        self.lock_free_during_commit: list[bool] = []
        self.asked = 0


def _install_window(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    during_prompt: Callable[[], object] | None = None,
) -> _Window:
    """Stub the prompt (answers yes) and the auto-commit. The prompt records
    whether the lock is free, then applies `during_prompt`; the auto-commit,
    which belongs to the commit phase, records the same."""
    window = _Window()

    def _confirm(*args: object, **kwargs: object) -> bool:
        window.asked += 1
        window.lock_free_during_prompt.append(_lock_is_free(root))
        if during_prompt is not None:
            during_prompt()
        return True

    def _autocommit(*args: object, **kwargs: object) -> None:
        window.lock_free_during_commit.append(_lock_is_free(root))
        return None

    monkeypatch.setattr(typer, "confirm", _confirm)
    monkeypatch.setattr(main, "_autocommit", _autocommit)
    return window


# --- the prompt does not hold the lock, the commit does ---------------------


def test_merge_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/survivor")
    _concept(tmp_path, "concepts/absorbed")
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["merge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/survivor")
    _concept(tmp_path, "concepts/absorbed")
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
        ).exit_code
        == 0
    )
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_forget_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/doomed")
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["forget", "concepts/doomed"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]
    assert not (tmp_path / "bundle" / "concepts" / "doomed.md").exists()


def test_relate_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/a")
    _concept(tmp_path, "concepts/b")
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["relate", "concepts/a", "references", "concepts/b"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]


def test_set_sensitivity_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/a", sensitivity="private")
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["set-sensitivity", "concepts/a", "confidential"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]


def test_set_volatility_prompt_does_not_hold_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    window = _install_window(monkeypatch, tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"])

    assert result.exit_code == 0, result.stderr
    assert window.lock_free_during_prompt == [True]
    assert window.lock_free_during_commit == [False]
    assert "volatile" in (tmp_path / "openkos.yaml").read_text(encoding="utf-8")


# --- a busy lock refuses at the commit, after compute, writing nothing ------


@pytest.mark.parametrize(
    "argv",
    [
        ["merge", "concepts/a", "concepts/b", "--auto"],
        ["forget", "concepts/b", "--auto"],
        ["relate", "concepts/a", "references", "concepts/b", "--auto"],
        ["set-sensitivity", "concepts/a", "confidential", "--auto"],
        ["set-volatility", "Person", "volatile", "--auto"],
    ],
    ids=lambda argv: argv[0],
)
def test_a_busy_lock_exits_3_and_writes_nothing(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/a", sensitivity="private")
    _concept(tmp_path, "concepts/b")
    before = snapshot_bytes(tmp_path)

    with lock.workspace_lock(tmp_path):
        result = runner.invoke(app, argv)

    assert result.exit_code == 3, result.stderr
    assert f"openkos {argv[0]}: refusing to run" in result.stderr
    assert snapshot_bytes(tmp_path) == before


# --- read-dependency sentinels ----------------------------------------------


def test_forget_refuses_when_a_referrer_appears_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel for forget's reference scope. The inbound-reference gate ran
    over the bundle snapshot; a referrer that lands during the prompt lives
    OUTSIDE the drift-checked targets, so only the read-dependency set sees it.
    Without it the concept would be deleted under a live reference the gate
    never saw."""
    _init(tmp_path, monkeypatch)
    doomed = _concept(tmp_path, "concepts/doomed")
    bystander = _concept(tmp_path, "concepts/bystander")
    _simulate_tty(monkeypatch)

    def _referrer_lands() -> None:
        bystander.write_text(
            bystander.read_text(encoding="utf-8")
            + "\nSee [doomed](/concepts/doomed.md).\n",
            encoding="utf-8",
        )

    window = _install_window(monkeypatch, tmp_path, during_prompt=_referrer_lands)

    result = runner.invoke(app, ["forget", "concepts/doomed"])

    assert window.asked == 1
    assert result.exit_code == 3, result.stderr
    assert "1 read dependency(ies) changed on disk" in result.stderr
    assert "concepts/bystander.md" in result.stderr
    assert doomed.exists(), "nothing was deleted"
    assert window.lock_free_during_commit == [], "no commit ran"


def test_forget_source_scope_refuses_when_a_new_descendant_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cascade set was resolved from the snapshot. A new concept citing
    the Source lands during the prompt: it has no baseline, so the commit must
    refuse rather than orphan it."""
    _init(tmp_path, monkeypatch)
    source = _concept(tmp_path, "sources/s", type="Source")
    _simulate_tty(monkeypatch)

    def _descendant_lands() -> None:
        _concept(tmp_path, "concepts/latecomer", provenance=["sources/s"])

    _install_window(monkeypatch, tmp_path, during_prompt=_descendant_lands)

    result = runner.invoke(app, ["forget", "sources/s", "--scope", "source"])

    assert result.exit_code == 3, result.stderr
    assert "1 new document(s) appeared: concepts/latecomer.md" in result.stderr
    assert source.exists()


def test_set_sensitivity_refuses_when_a_new_descendant_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel for the sensitivity computation. Raising a Source raises every
    provenance descendant found in the snapshot. A descendant created during
    the prompt would be left BELOW the Source's new level -- an
    under-classification -- so the commit refuses and the Source stays put."""
    _init(tmp_path, monkeypatch)
    source = _concept(tmp_path, "sources/s", type="Source", sensitivity="private")
    _simulate_tty(monkeypatch)

    def _descendant_lands() -> None:
        _concept(
            tmp_path,
            "concepts/latecomer",
            provenance=["sources/s"],
            sensitivity="public",
        )

    _install_window(monkeypatch, tmp_path, during_prompt=_descendant_lands)

    result = runner.invoke(app, ["set-sensitivity", "sources/s", "confidential"])

    assert result.exit_code == 3, result.stderr
    assert "1 new document(s) appeared: concepts/latecomer.md" in result.stderr
    assert "sensitivity: private" in source.read_text(encoding="utf-8")
    late = (tmp_path / "bundle" / "concepts" / "latecomer.md").read_text("utf-8")
    assert "sensitivity: public" in late


def test_set_sensitivity_refuses_when_a_bystander_becomes_a_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document that was NOT a descendant when the closure was resolved
    starts citing the Source during the prompt. It is a read dependency (its
    absence from the closure decided what the raise covers)."""
    _init(tmp_path, monkeypatch)
    source = _concept(tmp_path, "sources/s", type="Source", sensitivity="private")
    bystander = _concept(
        tmp_path, "concepts/bystander", provenance=[], sensitivity="public"
    )
    _simulate_tty(monkeypatch)

    def _starts_citing() -> None:
        _concept(
            tmp_path,
            "concepts/bystander",
            provenance=["sources/s"],
            sensitivity="public",
        )

    _install_window(monkeypatch, tmp_path, during_prompt=_starts_citing)

    result = runner.invoke(app, ["set-sensitivity", "sources/s", "confidential"])

    assert result.exit_code == 3, result.stderr
    assert "1 read dependency(ies) changed on disk" in result.stderr
    assert "concepts/bystander.md" in result.stderr
    assert "sensitivity: private" in source.read_text(encoding="utf-8")
    assert "sensitivity: public" in bystander.read_text(encoding="utf-8")


def test_relate_refuses_when_the_target_is_forgotten_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    source = _concept(tmp_path, "concepts/a")
    target = _concept(tmp_path, "concepts/b")
    before = source.read_bytes()
    _simulate_tty(monkeypatch)
    _install_window(monkeypatch, tmp_path, during_prompt=target.unlink)

    result = runner.invoke(app, ["relate", "concepts/a", "references", "concepts/b"])

    assert result.exit_code == 3, result.stderr
    assert "vanished" in result.stderr
    assert "concepts/b.md" in result.stderr
    assert source.read_bytes() == before, "no dangling relation was written"


def test_merge_refuses_when_a_bystander_links_the_absorbed_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/survivor")
    absorbed = _concept(tmp_path, "concepts/absorbed")
    bystander = _concept(tmp_path, "concepts/bystander")
    _simulate_tty(monkeypatch)

    def _links() -> None:
        bystander.write_text(
            bystander.read_text(encoding="utf-8")
            + "\nSee [absorbed](/concepts/absorbed.md).\n",
            encoding="utf-8",
        )

    _install_window(monkeypatch, tmp_path, during_prompt=_links)

    result = runner.invoke(app, ["merge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 3, result.stderr
    assert "concepts/bystander.md" in result.stderr
    assert absorbed.exists()
    assert bundle_ledger.read_entries("concepts/survivor", tmp_path / "bundle") == []


# --- classification ----------------------------------------------------------

_COMMIT_PHASE_VERBS = {
    "merge",
    "unmerge",
    "forget",
    "relate",
    "unrelate",
    "set-sensitivity",
    "set-volatility",
}


def test_the_lifecycle_verbs_are_declared_commit_phase_verbs() -> None:
    """A verb that quietly returns to the whole-verb lock would starve a
    daemon again, and nothing else would notice."""
    # `vars()` reads the attributes the guard stamped on the wrapper without a
    # constant-name `getattr`, which the linter rewrites into a typing error.
    declared = {
        str(vars(info.callback)["__openkos_locked_command__"])
        for info in app.registered_commands
        if info.callback is not None
        and vars(info.callback).get("__openkos_commit_phase__", False)
    }

    assert declared >= _COMMIT_PHASE_VERBS
