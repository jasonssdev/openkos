"""Unit tests for the `set-volatility` CLI command: writes one
`type_tiers[<ConceptType>] = <tier>` entry into `openkos.yaml` via
`config.set_type_tier`'s comment-safe text surgery (write-verb #140).
Mirrors `relate`'s two-phase (validate -> preview -> confirm -> write ->
autocommit) scaffold, `test_relate.py`'s TTY-simulation harness, and its
before/after workspace-snapshot assertions."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli import main
from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import (
    changed_paths,
    confirm_after,
    echo_after,
    snapshot_with_mtime,
)
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect GIT_CONFIG_GLOBAL/_SYSTEM to isolated files with a set identity
    # so the autocommit path is exercised deterministically regardless of the
    # host's git config. Without this, a runner lacking a global git identity
    # (e.g. CI) makes `has_git_identity` return False, `openkos init` and
    # `set-volatility --auto` skip their commits by design, and the
    # commit-message assertions fail against an empty git log.
    isolate_git_identity(
        monkeypatch, tmp_path, name="OpenKOS Test", email="test@openkos.example"
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _config_bytes(tmp_path: Path) -> bytes:
    return (tmp_path / "openkos.yaml").read_bytes()


def _last_commit_message(tmp_path: Path) -> str:
    return vcs_git._run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path
    ).stdout.strip()


# -- 2.1: invalid tier is rejected before any read/write --------------------


def test_invalid_tier_rejected_no_write_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid tier (`bogus`) fails with a clear stderr message, non-zero
    exit, `openkos.yaml` unchanged, and no commit created (spec: "Invalid
    tier value is rejected")."""
    _init_workspace(tmp_path, monkeypatch)
    before = _config_bytes(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "bogus", "--auto"])

    assert result.exit_code != 0
    assert "bogus" in result.stderr
    assert _config_bytes(tmp_path) == before


# -- 2.2: invalid ConceptType is rejected, valid names listed ---------------


def test_invalid_concept_type_rejected_lists_valid_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown `ConceptType` (`Widget`) fails with stderr listing the
    valid `REGISTRY` type names, non-zero exit, `openkos.yaml` unchanged
    (spec: "Unknown ConceptType is rejected")."""
    _init_workspace(tmp_path, monkeypatch)
    before = _config_bytes(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Widget", "slow", "--auto"])

    assert result.exit_code != 0
    assert "Widget" in result.stderr
    assert "Person" in result.stderr
    assert "Source" in result.stderr
    assert _config_bytes(tmp_path) == before


# -- 2.4: unparseable existing config shape fails closed --------------------


def test_unparseable_config_shape_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inline flow-mapping `type_tiers:` shape makes `config.set_type_tier`
    raise -- the CLI catches it, reports refusal on stderr, exits non-zero,
    and leaves `openkos.yaml` byte-identical (spec: "Inline flow-mapping
    shape is refused")."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    original = config_path.read_text(encoding="utf-8")
    # `slow` (not `volatile`) so the parsed-map idempotence short-circuit
    # does NOT trigger -- the CLI must reach `config.set_type_tier`, which
    # then raises on this un-editable inline flow-mapping shape.
    config_path.write_text(original + "type_tiers: {Person: slow}\n", encoding="utf-8")
    before = _config_bytes(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code != 0
    assert _config_bytes(tmp_path) == before


# -- 2.6: idempotence -- already at target tier is a no-op ------------------


def test_idempotent_already_set_tier_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Person` already mapped to `volatile` in the parsed `type_tiers` map:
    no-op message, exit 0, no write, no commit (spec: "Re-setting the same
    tier is a no-op")."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "type_tiers:\n  Person: volatile\n",
        encoding="utf-8",
    )
    before = _config_bytes(tmp_path)
    commit_before = _last_commit_message(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    assert _config_bytes(tmp_path) == before
    assert _last_commit_message(tmp_path) == commit_before


def test_explicit_override_equal_to_registry_default_is_real_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit override equal to the REGISTRY default (`Person` ->
    `slow`, the built-in default) is NOT present in the parsed `type_tiers`
    map, so it is a real write, not an idempotent no-op."""
    _init_workspace(tmp_path, monkeypatch)
    before = _config_bytes(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "slow", "--auto"])

    assert result.exit_code == 0
    assert _config_bytes(tmp_path) != before
    assert "type_tiers:" in _config_bytes(tmp_path).decode("utf-8")
    assert "Person: slow" in _config_bytes(tmp_path).decode("utf-8")


# -- 2.8: preview line format ------------------------------------------------


def test_preview_line_format_printed_before_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid, non-idempotent invocation prints `<ConceptType>:
    <old-or-default> -> <new>` before the confirm prompt (spec: "Confirming
    the preview writes the change")."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")

    assert result.exit_code == 0
    # Person's REGISTRY default is "slow" (see model/types.py REGISTRY).
    assert "Person: slow -> volatile" in result.output


# -- 2.9: confirm-gate matrix -------------------------------------------------


def test_auto_skips_the_prompt_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` skips the confirmation prompt and writes directly."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert "Person: volatile" in _config_bytes(tmp_path).decode("utf-8")


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto`: refuses (exit 1), writes
    nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _config_bytes(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _config_bytes(tmp_path) == before


def test_interactive_decline_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY declining the confirm prompt (`n`) writes nothing
    and creates no commit."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    before = _config_bytes(tmp_path)
    commit_before = _last_commit_message(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="n\n")

    assert result.exit_code != 0
    assert _config_bytes(tmp_path) == before
    assert _last_commit_message(tmp_path) == commit_before


def test_interactive_accept_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY accepting the confirm prompt (`y`) proceeds with
    the write."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")

    assert result.exit_code == 0
    assert "Person: volatile" in _config_bytes(tmp_path).decode("utf-8")


# -- 2.11: successful confirmed write lands and auto-commits -----------------


def test_successful_write_lands_and_autocommits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed write updates `openkos.yaml` bytes to the expected
    post-edit text and creates a new commit with message `openkos:
    set-volatility <Type> -> <tier>` covering `openkos.yaml` (spec:
    "Successful write creates a commit")."""
    _init_workspace(tmp_path, monkeypatch)
    before_config = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    from openkos import config as config_module

    expected = config_module.set_type_tier(before_config, "Person", "volatile")
    assert (tmp_path / "openkos.yaml").read_text(encoding="utf-8") == expected
    assert (
        _last_commit_message(tmp_path) == "openkos: set-volatility Person -> volatile"
    )


def test_set_volatility_accepts_source_type_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Source` is a valid `REGISTRY` type (present in `REGISTRY` though excluded
    from `CLASSIFIABLE_TYPES`) and MUST be settable end-to-end -- `suggest-volatility`
    can legitimately suggest a tier for a Source, so `set-volatility` must accept it,
    write it, and commit it (issue #140; guards against a regression that validates
    against `CLASSIFIABLE_TYPES` instead of `REGISTRY`)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["set-volatility", "Source", "slow", "--auto"])

    assert result.exit_code == 0
    assert "Source: slow" in _config_bytes(tmp_path).decode("utf-8")
    assert _last_commit_message(tmp_path) == "openkos: set-volatility Source -> slow"


# -- #335: re-validate openkos.yaml after the confirm gate --------------------


def test_the_config_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#335: the new `openkos.yaml` is rendered WHOLE from a pre-prompt read
    and written back with `write_atomic`, so an edit landing while the
    operator reads the preview -- possibly a safety setting like `review:`
    or `default_sensitivity:` -- was silently reverted and auto-committed.
    The guard mapping's single entry is `layout.config_path` itself."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    concurrent = config_path.read_text(encoding="utf-8") + "default_sensitivity: high\n"
    before = snapshot_with_mtime(tmp_path)
    commit_before = _last_commit_message(tmp_path)
    confirm_after(
        monkeypatch, lambda: config_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    # The edit survives and no commit was created.
    assert config_path.read_text(encoding="utf-8") == concurrent
    assert _last_commit_message(tmp_path) == commit_before
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("openkos.yaml")}


def test_the_config_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that has since been DELETED is drift too: re-creating it
    from a snapshot the operator can no longer see is the same silent
    revert as overwriting it."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    confirm_after(monkeypatch, config_path.unlink)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert not config_path.exists()


def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#306's constraint, re-pinned for `set-volatility`: `read_text`'s
    universal-newline translation makes a CRLF rewrite compare EQUAL to its
    own LF snapshot, and `fsio.write_atomic` (opening with `newline=""`)
    would then put the LF plan back over it."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    concurrent = config_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != config_path.read_bytes()
    confirm_after(monkeypatch, lambda: config_path.write_bytes(concurrent))

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert config_path.read_bytes() == concurrent


def test_a_config_already_crlf_at_rest_is_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: a config already CRLF at rest, untouched, must
    not be reported as drift -- otherwise `set-volatility` refuses forever
    on a CRLF workspace, naming a cause that never happened."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_bytes(config_path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert "Person: volatile" in _config_bytes(tmp_path).decode("utf-8")


def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#335: the guard must run on `--auto` too -- nothing pauses for a
    human there, which makes an unattended run the likeliest to race a
    second writer, not the least."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    concurrent = config_path.read_text(encoding="utf-8") + "review: false\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: config_path.write_text(concurrent, encoding="utf-8"),
        trigger="Person: slow -> volatile",
    )

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert config_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("openkos.yaml")}


def test_drift_under_review_false_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#335's OTHER unprompted path: config `review: false` skips the
    prompt exactly like `--auto`, but the window between Phase A's read
    and Phase B's write is still open -- so the guard must run
    unconditionally, not only when `review` is set. The config is itself
    the drift target here: `review: false` is written BEFORE Phase A
    reads it, and the concurrent edit lands during the run."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    concurrent = config_path.read_text(encoding="utf-8") + "default_sensitivity: high\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: config_path.write_text(concurrent, encoding="utf-8"),
        trigger="Person: slow -> volatile",
    )

    result = runner.invoke(app, ["set-volatility", "Person", "volatile"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert config_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("openkos.yaml")}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `set-volatility` (#327 follow-up; the pin
    existed only in `test_relate.py`): the guard's baseline and the text
    `set_type_tier`'s surgery runs over must come from the ONE
    `_snapshot_read` observation of `openkos.yaml` -- under a two-read shape
    a writer landing between the two reads becomes the guard's own
    baseline, and Phase B writes the config rendered from the EARLIER text,
    silently reverting the edit (possibly a safety setting like `review:`).

    The edit lands immediately after the config's snapshot returns -- the
    verb's ONLY snapshot, so this is the whole seam -- and the guard's
    later re-read must call it drift and refuse the run.
    """
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    concurrent = config_path.read_text(encoding="utf-8") + "default_sensitivity: high\n"
    real_snapshot_read = main._snapshot_read
    fired = False

    def racing_snapshot_read(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        snapshot = real_snapshot_read(path)
        if not fired and path == config_path:
            fired = True
            config_path.write_text(concurrent, encoding="utf-8")
        return snapshot

    before = snapshot_with_mtime(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert fired, "the racing wrapper never saw the config snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert config_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("openkos.yaml")
    }
