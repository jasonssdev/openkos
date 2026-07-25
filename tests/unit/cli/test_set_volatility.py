"""Unit tests for the `set-volatility` CLI command: writes one
`type_tiers[<ConceptType>] = <tier>` entry into `openkos.yaml` via
`config.set_type_tier`'s comment-safe text surgery (write-verb #140).
Mirrors `relate`'s two-phase (validate -> preview -> confirm -> write ->
autocommit) scaffold, `test_relate.py`'s TTY-simulation harness, and its
before/after workspace-snapshot assertions."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.vcs import git as vcs_git

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
