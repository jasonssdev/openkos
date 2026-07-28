"""Unit tests for the `init` CLI command: pre-flight refusal and workspace creation.

Pre-flight (D1 Phase A) is a pure read: `config.refusal_reason`'s five
conditions are checked before any write happens, so a refusal leaves the
directory exactly as it was found.
"""

import os
import stat
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.cli.main import app
from openkos.config import DEFAULT_MODEL
from openkos.llm.ollama import InstalledModel, OllamaUnavailable
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _fake_ollama_client(
    *,
    installed: list[str | InstalledModel] | None = None,
    error: Exception | None = None,
) -> Callable[..., Any]:
    """Build a fake `OllamaClient` factory exposing ONLY `list_models` --
    mirrors `test_doctor.py`'s stub. Deliberately has no `pull`/`serve`-style
    method, so any attempt by the preflight or picker to call one raises
    `AttributeError` and fails the test loudly (task 2.6 guard).

    `installed` entries may be a bare `str` (shorthand for a chat model with
    `family=None`, used by every pre-picker test) or a full `InstalledModel`
    (needed by the B-ii picker tests to set `family` and exercise the
    embedding-model filter)."""

    class _FakeOllamaClient:
        def __init__(self, model: str, **kwargs: object) -> None:
            self.model = model

        def list_models(self) -> list[InstalledModel]:
            if error is not None:
                raise error
            return [
                entry
                if isinstance(entry, InstalledModel)
                else InstalledModel(tag=entry, family=None)
                for entry in (installed or [])
            ]

    return _FakeOllamaClient


@pytest.fixture(autouse=True)
def _mock_ollama_client_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default for every `init` invocation in this module.

    `init`'s post-success preflight builds a real `OllamaClient` and probes
    `localhost:11434` on every successful run -- without this autouse
    fixture, every test in this file that does not set up its own
    `OllamaClient` mock (i.e. all the non-preflight-focused tests) would
    perform real, unmocked network I/O. The default fake reports Ollama
    reachable with `DEFAULT_MODEL` already installed, so the preflight stays
    silent and none of this module's existing stdout/stderr assertions are
    perturbed. The 6 preflight-focused tests below call
    `monkeypatch.setattr("openkos.cli.main.OllamaClient", ...)` themselves,
    which cleanly overrides this fixture's default for that one test.
    """
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call.

    `CliRunner.isolation()` replaces `sys.stdin` with a fresh
    `_NamedTextIOWrapper` for the duration of `invoke()`, so patching the
    *current* `sys.stdin` object before calling `invoke` has no effect --
    the class method is patched instead, so it applies to whatever instance
    `isolation()` creates next.
    """
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _snapshot_entry(path: Path) -> bytes | None | tuple[str, str]:
    """Classify one path for `_snapshot`, without ever following a symlink.

    A symlink is checked first because `Path.is_dir()`/`is_file()` follow
    it -- checking those first would either misread a symlink-to-dir as a
    plain directory or crash `read_bytes()` on a broken symlink.
    """
    if path.is_symlink():
        return ("symlink", str(path.readlink()))
    if path.is_dir():
        return None
    return path.read_bytes()


def _snapshot(root: Path) -> dict[Path, bytes | None | tuple[str, str]]:
    """Capture every entry under `root`, keyed by relative path.

    Files map to their exact bytes, directories map to `None`, and symlinks
    map to their raw (unfollowed) target. Recording directory and symlink
    entries too (not only `is_file()`) means a refusal test also catches a
    stray directory or symlink created outside pre-flight -- a file-only
    snapshot would miss both, since neither holds byte content of its own.
    """
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def test_refuses_when_openkos_yaml_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing `openkos.yaml` refuses with exit 1 and zero writes (scenario 7)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "openkos.yaml").write_text("name: x\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos.yaml" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_when_agents_md_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing `AGENTS.md` refuses with exit 1 and zero writes (scenario 8)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# manual\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "AGENTS.md" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("dirname", ["raw", "bundle"])
def test_refuses_when_dir_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirname: str
) -> None:
    """A non-empty `raw/` or `bundle/` refuses with exit 1 and zero writes (scenario 9)."""
    monkeypatch.chdir(tmp_path)
    target_dir = tmp_path / dirname
    target_dir.mkdir()
    (target_dir / "existing.txt").write_text("original", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert dirname in result.stderr
    assert "not empty" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_stray_bundle_names_crashed_init_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray non-empty `bundle/` names the likely crashed-init cause + remediation.

    Uses its own `tmp_path` fixture, distinct from
    `test_refuses_when_dir_non_empty`'s parametrized `raw`/`bundle` fixture,
    so this scenario-14 message assertion and that generic "not empty"
    assertion cannot mask each other.
    """
    monkeypatch.chdir(tmp_path)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "leftover.md").write_text("stray", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "bundle" in result.stderr
    assert "crashed" in result.stderr or "interrupted" in result.stderr
    assert "not empty" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(
    os.name != "posix", reason="symlink creation without privilege requires POSIX"
)
@pytest.mark.parametrize("dirname", ["raw", "bundle"])
@pytest.mark.parametrize(
    "target_kind", ["dir", "file", "broken"], ids=["to_dir", "to_file", "broken"]
)
def test_refuses_when_dir_is_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirname: str, target_kind: str
) -> None:
    """A pre-existing symlinked `raw`/`bundle` refuses in pre-flight, never followed.

    Covers symlink-to-dir, symlink-to-file, and a broken symlink -- all
    three are silently followed by `exists()`/`is_dir()`, which is exactly
    what `is_symlink()` catches without following the link itself.
    """
    monkeypatch.chdir(tmp_path)
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside-{dirname}-{target_kind}"
    link_path = tmp_path / dirname
    if target_kind == "dir":
        outside_root.mkdir()
        link_path.symlink_to(outside_root, target_is_directory=True)
    elif target_kind == "file":
        outside_root.write_text("outside content", encoding="utf-8")
        link_path.symlink_to(outside_root)
    else:
        link_path.symlink_to(outside_root)  # nonexistent target: broken symlink
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert dirname in result.stderr
    assert "symlink" in result.stderr
    assert _snapshot(tmp_path) == before
    if target_kind == "dir":
        assert list(outside_root.iterdir()) == []
    elif target_kind == "file":
        assert outside_root.read_text(encoding="utf-8") == "outside content"
    else:
        assert not outside_root.exists()


@pytest.mark.parametrize("dirname", ["raw", "bundle"])
def test_refuses_when_dir_is_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirname: str
) -> None:
    """A `raw` or `bundle` that exists as a plain file refuses cleanly (requirement 3).

    `_non_empty_dir` (and `is_workspace`) call `Path.is_dir()`, which is
    `False` for a plain file -- so without this fifth pre-flight condition,
    a lone file named `raw` would slip past refusal into Phase B. There,
    `Path.mkdir` would still raise `FileExistsError`, but `cli/main.py`
    wraps all of Phase B in `try/except OSError`, so nothing would be
    uncaught -- the failure would just surface as a generic mid-Phase-B
    "failed while creating the workspace" error instead of this condition's
    precise pre-flight refusal message, and only after any earlier Phase-B
    writes (e.g. `raw/`) had already landed.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / dirname).write_text("not a directory", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert dirname in result.stderr
    assert "not a directory" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second `init` on an already-initialized workspace refuses and changes nothing (scenarios 10-11)."""
    monkeypatch.chdir(tmp_path)
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["init"])

    assert second.exit_code == 1
    assert isinstance(second.exception, SystemExit)
    assert "openkos.yaml" in second.stderr
    assert _snapshot(tmp_path) == before


def test_fresh_empty_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh empty directory gets all five artifacts and exits 0 (scenario 1)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    for named in ("raw/", "index.md", "log.md", "AGENTS.md", "openkos.yaml"):
        assert named in result.stdout
    assert "openkos ingest" in result.stdout


def test_init_creates_no_openkos_dir_or_vectors_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` writes only the five workspace artifacts -- it never creates
    `.openkos/` or `.openkos/vectors.db` (embedding-vector-store spec: No
    CLI Surface, No Init-Time Side Effect). Those are engine-cache paths a
    future `state.vectorstore` consumer creates lazily on first open, not
    part of what a freshly initialized workspace contains."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert not (tmp_path / ".openkos").exists()
    assert not (tmp_path / ".openkos" / "vectors.db").exists()


def test_model_flag_writes_chosen_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--model` selects the model written to `openkos.yaml` (scenario: flag override)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--model", "gemma3"])

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: gemma3" in content


def test_model_flag_with_colon_tag_writes_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--model mistral:7b` (non-TTY) survives the full CLI -> `write_config`
    path and lands verbatim in `openkos.yaml` -- an end-to-end guard that a
    colon-containing tag is not corrupted anywhere between argument parsing
    and the written file, not just at the `validate_model` unit level."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--model", "mistral:7b"])

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: mistral:7b" in content


def test_model_flag_wins_over_tty_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--model` wins over a TTY prompt; no prompt is shown (scenario: flag wins even when stdin is a TTY)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["init", "--model", "mistral"])

    assert result.exit_code == 0
    assert "Model" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: mistral" in content


def test_tty_prompt_accepts_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--model` flag, stdin is a TTY, Ollama reachable with the default
    chat model installed: the picker's numbered list is shown and pressing
    Enter selects `config.DEFAULT_MODEL` (scenario: TTY picker, accept
    recommended default). Relies on the module's autouse fixture, which
    reports `DEFAULT_MODEL` installed."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "qwen3:8b" in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: qwen3:8b" in content


def test_tty_init_prints_exact_next_step_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage guard for pre-existing behavior: `cli/main.py:129` always
    echoes this next-step hint after a successful `init`, verbatim, even
    when stdin is a TTY. Existing coverage only substring-asserts
    `"openkos ingest"` on a non-TTY run (`test_fresh_empty_directory`); this
    locks the FULL line under a TTY runner so future wording drift is caught
    immediately. This test targets already-existing behavior, so it is
    expected to pass on write (no RED phase, `main.py` is untouched)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert (
        "Next: run `openkos ingest <path>` to import your first source."
        in result.output
    )


def test_tty_prompt_custom_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--model` flag, stdin is a TTY, Ollama reports a second installed
    chat model: the user picks it by NUMBER, not free text (scenario:
    TTY+picker custom selection). The picker only accepts a numeric index
    (or Enter for the recommended default) -- superseded from B-i's
    free-text-prompt version of this test now that the picker is active."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, "mistral"]),
    )

    result = runner.invoke(app, ["init"], input="2\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: mistral" in content


def test_non_tty_no_flag_silent_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--model` flag, stdin is not a TTY: no prompt, default model written (scenario: non-TTY, no flag, silent default)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Model" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: qwen3:8b" in content


# --- Slice B (init-model-picker): interactive chat-model picker ------------


def test_picker_lists_chat_models_excludes_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The picker's numbered list shows installed chat models and marks the
    recommended default, but never lists an embedding model (family
    "bert") as a selectable option (spec: Embedding Models Excluded From
    Picker Candidates). `bge-m3` legitimately appears further down, in the
    SEPARATE embedding-model picker's own section -- this test scopes its
    exclusion assertion to the chat-model section only."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="bge-m3", family="bert"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="\n\n")

    assert result.exit_code == 0
    assert "qwen3:8b" in result.output
    assert "(recommended)" in result.output
    chat_section = result.output.split("Installed embedding models:")[0]
    assert "bge-m3" not in chat_section


def test_picker_numeric_choice_selects_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing a non-default chat model by its list number persists that
    tag to `openkos.yaml` (spec: selecting a number picks that model)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="gemma3", family="gemma"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="2\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: gemma3" in content


def test_model_flag_bypasses_picker_no_list_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--model` wins outright even on a TTY: no picker list is rendered at
    all (spec: --model on TTY -> no picker)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, "gemma3"]),
    )

    result = runner.invoke(app, ["init", "--model", "mistral"])

    assert result.exit_code == 0
    assert "(recommended)" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: mistral" in content


def test_non_tty_bypasses_picker_silent_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-TTY, no `--model` flag: the default is taken silently, no picker
    list is shown, even though Ollama reports multiple chat models (spec:
    non-TTY no flag -> silent default, no picker)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, "gemma3"]),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "(recommended)" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: qwen3:8b" in content


def test_picker_unreachable_ollama_falls_back_to_typed_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama unreachable during the picker's probe: falls back to the
    typed-prompt/default flow, never hard-fails, and the workspace is still
    created with exit 0 (spec: Graceful Degradation -- unreachable)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "(recommended)" not in result.output
    assert (tmp_path / "openkos.yaml").is_file()
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: qwen3:8b" in content


def test_picker_zero_chat_models_falls_back_to_typed_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only embedding models installed (zero chat candidates after the
    family filter): the picker falls back to the typed-prompt/default flow
    without crashing (spec: Graceful Degradation -- only embedding models
    installed). `bge-m3` legitimately IS a valid, recommended candidate for
    the SEPARATE embedding-model picker, so this test scopes its assertion
    to the chat-model picker's own header, not the whole output."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[InstalledModel(tag="bge-m3", family="bert")]),
    )

    result = runner.invoke(app, ["init"], input="\n\n")

    assert result.exit_code == 0
    assert "Installed chat models:" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: qwen3:8b" in content


def test_picker_invalid_selection_reprompts_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-range/non-numeric first answer reprompts instead of
    crashing or silently accepting garbage; a valid follow-up answer then
    completes the picker (spec design D3: invalid -> stderr err + reprompt)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="gemma3", family="gemma"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="not-a-number\n2\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: gemma3" in content


def test_picker_exhausted_invalid_selections_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reprompt loop is bounded: after `_MAX_PICKER_ATTEMPTS` invalid
    answers the picker stops reprompting and falls back to the recommended
    default instead of looping forever on a misbehaving stdin (spec:
    Graceful Degradation / bounded reprompt)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="gemma3", family="gemma"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="bad\nbad\nbad\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert f"model: {DEFAULT_MODEL}" in content


def test_picker_unicode_digit_input_reprompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Unicode digit-like character (e.g. superscript two, U+00B2) passes
    `str.isdigit()` but is not `int()`-parseable. It must be treated as an
    invalid choice and reprompt, never escape as a raw `int()` ValueError
    that hard-fails init (bounded-reprompt / graceful-degradation contract)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="gemma3", family="gemma"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="²\n2\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: gemma3" in content


def test_picker_excludes_tags_that_fail_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server-reported chat tag that would fail `validate_model` (e.g. one
    containing a space) must not be offered as a selectable option, so a user
    can never pick a listed entry that then hard-fails init on validation."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="bad tag", family="qwen"),
            ]
        ),
    )

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "bad tag" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert f"model: {DEFAULT_MODEL}" in content


@pytest.mark.parametrize("bad_model", ["", " ", "a b", 'a"b', "a'b", "a#b"])
def test_model_flag_rejects_blank_or_unsafe_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_model: str
) -> None:
    """A blank or unsafe `--model` value refuses with exit 1 and zero writes (scenarios: blank/unsafe token rejected)."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init", "--model", bad_model])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "refusing" in result.stderr
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "openkos.yaml").exists()


# --- Slice C (init-embedding-model-choice): embedding model wiring --------


def test_embedding_model_flag_overrides_picker_and_writes_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--embedding-model` wins outright, even on a TTY: no embedding picker
    is shown, and every other field (including `model:`) resolves to its
    default (spec: Embedding flag override selects the embedding model)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["init", "--embedding-model", "custom-embed:test"], input="\n"
    )

    assert result.exit_code == 0
    assert "Installed embedding models:" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: custom-embed:test" in content
    assert "model: qwen3:8b" in content


def test_embedding_model_flag_wins_over_default_picker_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence proof: with the flag given, the picker's installed
    candidates (which would otherwise pick `bge-m3`) are never consulted."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="bge-m3", family="bert"),
            ]
        ),
    )

    result = runner.invoke(
        app, ["init", "--embedding-model", "custom-embed:test"], input="\n\n"
    )

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: custom-embed:test" in content


def test_embedding_model_non_tty_no_flag_resolves_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--embedding-model` flag, stdin is not a TTY: no picker, the
    packaged default is written silently (precedence: flag > picker >
    default, default branch)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Installed embedding models:" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3" in content


def test_embedding_picker_lists_allowlisted_candidate_with_default_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embedding picker's numbered list is allowlist-only -- NOT
    `is_embedding_model(m) and allowlisted` -- and marks `bge-m3` as
    recommended. `InstalledModel(tag="bge-m3", family=None)` (no reported
    `details.family`) MUST still appear as a candidate (D2 regression
    guard: the allowlist is stronger evidence than the heuristic tag/family
    classifier, so it must not be stacked on top of it)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[InstalledModel(tag="bge-m3", family=None)],
        ),
    )

    result = runner.invoke(app, ["init"], input="\n\n")

    assert result.exit_code == 0
    assert "Installed embedding models:" in result.output
    embedding_section = result.output.split("Installed embedding models:")[1]
    assert "bge-m3" in embedding_section
    assert "(recommended)" in embedding_section
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3" in content


def test_embedding_picker_numbered_choice_selects_that_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering a valid number picks THAT candidate, not the recommended one.

    `EMBEDDING_MODEL_ALLOWLIST` ships with a single member today, so index 2
    is unreachable through production data alone and this branch would go
    untested until the allowlist grows -- exactly when a regression would be
    silent. The allowlist is monkeypatched to two members to exercise it now.
    """
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.config.EMBEDDING_MODEL_ALLOWLIST", ("bge-m3", "nomic-embed-text")
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag="bge-m3", family="bert"),
                InstalledModel(tag="nomic-embed-text", family="nomic-bert"),
            ],
        ),
    )

    result = runner.invoke(app, ["init"], input="\n2\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: nomic-embed-text" in content


def test_embedding_picker_invalid_choice_reprompts_then_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-numeric choice warns and reprompts rather than being accepted,
    mirroring `_pick_chat_model`'s reprompt discipline."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.config.EMBEDDING_MODEL_ALLOWLIST", ("bge-m3", "nomic-embed-text")
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag="bge-m3", family="bert"),
                InstalledModel(tag="nomic-embed-text", family="nomic-bert"),
            ],
        ),
    )

    result = runner.invoke(app, ["init"], input="\nnot-a-number\n2\n")

    assert result.exit_code == 0
    assert "isn't a valid choice" in result.stderr
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: nomic-embed-text" in content


def test_embedding_picker_exhausted_attempts_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated invalid input exhausts `_MAX_PICKER_ATTEMPTS` and falls back
    to the default rather than hanging or failing -- the workspace is still
    created with exit 0."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.config.EMBEDDING_MODEL_ALLOWLIST", ("bge-m3", "nomic-embed-text")
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                InstalledModel(tag="bge-m3", family="bert"),
                InstalledModel(tag="nomic-embed-text", family="nomic-bert"),
            ],
        ),
    )

    result = runner.invoke(app, ["init"], input="\nbad\nbad\nbad\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3" in content


def test_embedding_picker_server_latest_tag_normalizes_to_allowlist_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server-reported `bge-m3:latest` tag (Ollama's own `/api/tags`
    convention) still matches the allowlisted `bge-m3` entry via
    `ollama.model_tag_matches`, and the ALLOWLIST spelling -- not the raw
    server tag -- is what gets written, so `cfg.embedding_model` never
    diverges from `DEFAULT_EMBEDDING_MODEL` for a no-op selection (D3)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[InstalledModel(tag="bge-m3:latest", family="bert")],
        ),
    )

    result = runner.invoke(app, ["init"], input="\n\n")

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3 " in content
    assert "bge-m3:latest" not in content


def test_embedding_model_flag_off_allowlist_writes_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `--embedding-model` value that passes YAML-safety validation but is
    NOT on the vetted allowlist is still validated, written, and warned
    about on stderr -- never blocked, exit code unaffected (spec:
    Off-Allowlist Embedding Model Flag Is Warned, Not Blocked)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--embedding-model", "custom-embed:latest"])

    assert result.exit_code == 0
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: custom-embed:latest" in content
    assert "custom-embed:latest" in result.stderr
    assert "not on the" in result.stderr


def test_embedding_model_flag_latest_qualified_allowlist_entry_is_not_warned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--embedding-model bge-m3:latest` names the vetted model, so it must
    neither warn nor write the raw server-style tag.

    The flag path resolves allowlist membership with the SAME
    `model_tag_matches` normalization the picker uses (D3). Exact string
    membership would call this vetted model off-allowlist, and writing
    `bge-m3:latest` would diverge from `DEFAULT_EMBEDDING_MODEL` and trip the
    model-tag re-embed gate into a full corpus re-embed for a no-op change.
    """
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--embedding-model", "bge-m3:latest"])

    assert result.exit_code == 0
    assert "not on the" not in result.stderr
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3 " in content
    assert "bge-m3:latest" not in content


def test_sticky_reembed_warning_does_not_blame_another_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sticky warning must describe only what affects THIS workspace.

    Running `init` on a different workspace cannot force a re-embed here, so
    naming that as a cause is misleading to a reader who does not know the
    codebase.
    """
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "is sticky" in result.stderr
    assert "different workspace" not in result.stderr


def test_embedding_picker_unreachable_ollama_falls_back_to_default_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama unreachable during the shared probe: no embedding picker is
    shown, `embedding_model` silently resolves to the default, and the
    workspace is still created with exit 0 (spec: Graceful Degradation Of
    The Embedding Picker -- unreachable)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "Installed embedding models:" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3" in content


def test_embedding_picker_zero_allowlisted_candidates_falls_back_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama reachable but no installed model is on the vetted allowlist:
    no embedding picker is shown, `embedding_model` silently resolves to
    the default, no exception propagates (spec: Graceful Degradation Of The
    Embedding Picker -- zero candidates)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[InstalledModel(tag=DEFAULT_MODEL, family="qwen")]
        ),
    )

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "Installed embedding models:" not in result.output
    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: bge-m3" in content


def test_embedding_picker_reuses_shared_probe_no_second_reachability_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both the chat picker and the embedding picker resolve candidates from
    ONE shared reachability probe during Phase A (spec: Graceful
    Degradation Of The Embedding Picker -- MUST reuse the chat picker's
    existing probe call, MUST NOT issue a second, separate reachability
    request). `list_models()` is called exactly TWICE per `init` run here:
    once for the shared Phase A picker probe (chat + embedding combined),
    and once more for the separate, pre-existing post-success Ollama
    preflight (unrelated feature, unaffected by this change). If the
    embedding picker issued its OWN Phase A probe instead of reusing the
    chat picker's, the count would be THREE, not two."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)
    call_count = 0

    class _CountingFakeOllamaClient:
        def __init__(self, model: str, **kwargs: object) -> None:
            self.model = model

        def list_models(self) -> list[InstalledModel]:
            nonlocal call_count
            call_count += 1
            return [
                InstalledModel(tag=DEFAULT_MODEL, family="qwen"),
                InstalledModel(tag="bge-m3", family="bert"),
            ]

    monkeypatch.setattr("openkos.cli.main.OllamaClient", _CountingFakeOllamaClient)

    result = runner.invoke(app, ["init"], input="\n\n")

    assert result.exit_code == 0
    assert call_count == 2


def test_sticky_reembed_warning_prints_on_every_successful_init_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every successful `init` prints the sticky re-embed warning, worded
    about FUTURE cost only -- a fresh workspace has nothing to re-embed yet
    (spec: Sticky Re-Embed Warning On Every Successful Init)."""
    monkeypatch.chdir(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 0
    assert "sticky" in result.stderr.lower()
    assert "re-embed" in result.stderr.lower()
    assert "forces" in result.stderr.lower()
    assert "has been" not in result.stderr.lower()
    assert "already re-embedded" not in result.stderr.lower()


def test_sticky_reembed_warning_prints_on_every_successful_init_non_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sticky warning is unconditional -- printed on a non-TTY run too,
    regardless of which embedding model was resolved."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--embedding-model", "custom-embed:test"])

    assert result.exit_code == 0
    assert "sticky" in result.stderr.lower()
    assert "re-embed" in result.stderr.lower()


def test_raw_default_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`raw/` keeps the filesystem's default directory mode; no `chmod` runs (scenario 13).

    Compared against a sibling directory created directly by `mkdir`, rather
    than a hardcoded mode, since the default mode depends on the running
    system's umask.
    """
    monkeypatch.chdir(tmp_path)
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    expected_mode = reference_dir.stat().st_mode

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").stat().st_mode == expected_mode


def test_adopt_non_workspace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty directory with none of the four markers is adoptable (scenario 12)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("scratch", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "scratch"


def test_fresh_bundle_is_conformant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9 rules 1-2 hold vacuously on a fresh bundle (scenario 14).

    `index.md` and `log.md` are both reserved, so `check_conformance` has no
    non-reserved `.md` file to check and returns `[]` regardless of what
    `init` wrote -- this cannot fail today. Its real value is as a
    regression guard: it starts failing the moment `init` ever writes a
    non-reserved `.md` that violates rules 1-2. Rule 3 (reserved-file
    structure) is deferred to `lint` and not checked here; it holds by
    construction via the index/log shapes, genuinely enforced by
    `tests/unit/bundle/test_index.py` and `test_log.py`.
    """
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert okf.check_conformance(tmp_path / "bundle") == []


@pytest.mark.parametrize(
    "tz_name",
    ["Etc/GMT+12", "Pacific/Kiritimati"],
    ids=["utc_minus_12", "utc_plus_14"],
)
def test_log_dated_section_uses_local_date_not_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tz_name: str
) -> None:
    """`log.md`'s dated section reflects the machine's local calendar date, not UTC's.

    `ubuntu-latest` (CI) runs in UTC, where local == UTC -- a single fixed
    timezone would pass in CI even if the implementation used UTC, the same
    blind spot that hid the `Path(".")` bug (tmp_path is always absolute).
    `Etc/GMT+12` (UTC-12) and `Pacific/Kiritimati` (UTC+14) are the two most
    extreme real UTC offsets; together their "local date differs from UTC
    date" windows cover all 24 hours of a UTC day, so at least one of the two
    parametrized cases always disagrees with a UTC-based date at any instant
    this test runs -- neither can silently pass alongside a UTC bug.

    `time.tzset()` re-reads the `TZ` environment variable into the C
    library's timezone state, which is what `datetime.now().astimezone()`
    (no-arg) consults. The environment is restored and re-synced explicitly
    in a `finally` block, rather than left to `monkeypatch`'s teardown alone,
    because `monkeypatch` reverts the `TZ` env var but does not itself call
    `tzset()` afterwards -- without the explicit re-sync here, the process's
    C library timezone state would leak into later tests even though `TZ`
    itself was reverted.
    """
    monkeypatch.chdir(tmp_path)
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = tz_name
    time.tzset()
    try:
        expected_local_date = datetime.now(ZoneInfo(tz_name)).date()

        result = runner.invoke(app, ["init"])
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert f"## {expected_local_date.isoformat()}" in log_text


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission-based read failures require a POSIX non-root user",
)
def test_phase_a_read_failure_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable pre-existing `raw/` exits cleanly instead of a raw traceback.

    `config.refusal_reason` (Phase A) calls `_non_empty_dir`, which calls
    `Path.iterdir()` -- an `OSError` (e.g. `PermissionError` on a mode-000
    `raw/`) there happens before `cli/main.py`'s Phase B `try/except OSError`
    even starts, so without its own guard this crashes with an uncaught
    traceback instead of the clean exit every other filesystem failure gets.
    Root is exempted (`geteuid() == 0`) for the same reason as
    `test_write_failure_surfaces_cleanly`: root bypasses POSIX permission
    bits, making this an untestable claim on that platform rather than a
    false one.
    """
    monkeypatch.chdir(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.txt").write_text("original", encoding="utf-8")
    original_mode = stat.S_IMODE(raw_dir.stat().st_mode)
    raw_dir.chmod(0o000)
    try:
        result = runner.invoke(app, ["init"])
    finally:
        raw_dir.chmod(original_mode)

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "openkos init" in result.stderr
    assert "checking" in result.stderr


def test_corrupt_template_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt packaged template (missing/duplicated placeholder) raises
    `ValueError` from `write_config`; Phase B routes it through the same
    graceful stderr-message + exit-1 path as an `OSError`, not a raw
    traceback, and leaves no half-written `openkos.yaml` behind.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_read_template", lambda _: "no placeholder here\n")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos init" in result.stderr
    assert "failed" in result.stderr
    assert not (tmp_path / "openkos.yaml").exists()


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission-based write failures require a POSIX non-root user",
)
def test_write_failure_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Phase-B write failure exits non-zero with a clear message, no traceback (requirement 4).

    Pre-flight (Phase A) only reads, so it passes on an empty, writable
    directory. Stripping write permission from `tmp_path` itself, after
    pre-flight would pass but before Phase B runs, forces the very first
    write (`raw/`'s `mkdir`) to raise `PermissionError` -- a write failure
    that a `chmod`-based collision on `openkos.yaml` cannot reach, since
    pre-flight would refuse before any write is attempted. Root is exempted
    (`geteuid() == 0`) because root bypasses POSIX permission bits
    entirely, which would make this test silently pass without exercising
    the failure path -- an untestable claim on that platform, not a false
    one.
    """
    monkeypatch.chdir(tmp_path)
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    tmp_path.chmod(0o500)
    try:
        result = runner.invoke(app, ["init"])
    finally:
        tmp_path.chmod(original_mode)

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "openkos init" in result.stderr
    assert "failed" in result.stderr


def test_preflight_reachable_with_model_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama reachable AND the resolved model installed: no preflight
    warning is printed, and `init` still exits 0 (scenario 2.1)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "doctor" not in result.stderr


def test_preflight_unreachable_ollama_warns_but_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama unreachable (`OllamaUnavailable`): `init` still exits 0, and
    stderr carries a warning naming `openkos doctor` (scenario 2.2)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "openkos doctor" in result.stderr


def test_preflight_model_missing_warns_but_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama reachable but the resolved model is NOT installed
    (`model_tag_matches` is `False`): `init` still exits 0, and stderr
    carries a warning naming `openkos doctor` (scenario 2.3)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=["some-other-model:1b"]),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "openkos doctor" in result.stderr


def test_preflight_unexpected_probe_error_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe failure that is NOT an `OllamaError` subclass (an unexpected
    exception) is still caught non-fatally: `init` exits 0, prints the
    doctor-pointing warning, and no traceback reaches stderr (scenario 2.4)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=RuntimeError("unexpected probe failure")),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "openkos doctor" in result.stderr
    assert "Traceback" not in result.stderr


def test_preflight_outcome_never_changes_written_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every preflight outcome (reachable/model-present, unreachable,
    model-missing, unexpected error) writes the SAME five workspace
    artifacts -- the preflight probe runs strictly after Phase B and never
    influences what was written (scenario 2.5, pure-file-writer guarantee)."""
    # Isolate git identity to "unset" so the git-setup step (Slice 1) never
    # makes a commit here: a real commit's object SHA is timestamp-
    # dependent, which would make the four workspaces' `.git/` trees
    # byte-different for a reason unrelated to what this test actually
    # checks (preflight-outcome independence). `git init` alone (no commit)
    # is byte-identical across separate invocations, so this keeps the
    # snapshot comparison meaningful without weakening it.
    isolate_git_identity(monkeypatch, tmp_path)

    outcomes: dict[str, Callable[..., Any]] = {
        "reachable": _fake_ollama_client(installed=[DEFAULT_MODEL]),
        "unreachable": _fake_ollama_client(
            error=OllamaUnavailable("Ollama not reachable")
        ),
        "model_missing": _fake_ollama_client(installed=["other:1b"]),
        "unexpected_error": _fake_ollama_client(error=RuntimeError("boom")),
    }

    snapshots: dict[str, dict[Path, bytes | None | tuple[str, str]]] = {}
    for name, fake_client in outcomes.items():
        workspace = tmp_path / name
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("openkos.cli.main.OllamaClient", fake_client)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        snapshots[name] = _snapshot(workspace)

    reference = snapshots["reachable"]
    for name, snapshot in snapshots.items():
        assert snapshot == reference, f"{name} wrote different files than 'reachable'"


def test_preflight_never_pulls_or_spawns_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preflight probe never invokes `ollama pull`/`ollama serve` or
    spawns any subprocess: `subprocess.run`/`subprocess.Popen` are patched
    to raise if called at all, and the fake `OllamaClient` exposes ONLY
    `list_models` -- any unexpected `pull`/`serve`-style call raises
    `AttributeError` (scenario 2.6).

    The Slice 1 git-setup step legitimately DOES spawn `subprocess.run`
    (real `git` commands, via `vcs.git._run`) -- unrelated to this test's
    concern, which is scoped to the Ollama preflight only. It is neutralized
    here (no re-init: `repo_root` stubbed non-`None`; no commit: identity
    stubbed unset) so it makes no subprocess call of its own and cannot trip
    this test's blanket `subprocess.run`/`Popen` ban.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vcs_git, "repo_root", lambda cwd: cwd)
    monkeypatch.setattr(vcs_git, "has_git_identity", lambda cwd: False)

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("init preflight must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0


# --- Slice 1 (git-lifecycle): `init` sets up git ---------------------------
#
# Every test in this section isolates git identity via `isolate_git_identity`
# (redirects GIT_CONFIG_GLOBAL/_SYSTEM to files under `tmp_path`) so behavior
# is deterministic regardless of the host/CI machine's real `~/.gitconfig`.


def test_git_fresh_empty_directory_full_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh dir outside any repo, configured identity: `git init` runs, a
    `.gitignore` ignoring `.openkos/`/`.DS_Store` is written, and one commit
    with the fixed message contains all five canonical artifacts, leaving a
    clean tree (spec: Fresh empty directory / Fresh repo, full commit /
    Gitignore Scaffolding -- no existing)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".git").is_dir()
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    ignored_lines = gitignore_text.splitlines()
    assert ".openkos/" in ignored_lines
    assert ".DS_Store" in ignored_lines

    log = vcs_git._run(["git", "log", "--format=%s"], cwd=tmp_path)
    assert log.stdout.strip() == "chore(openkos): initialize workspace"

    committed = vcs_git._run(["git", "log", "--name-only", "--format="], cwd=tmp_path)
    committed_files = {line for line in committed.stdout.splitlines() if line}
    assert "bundle/index.md" in committed_files
    assert "bundle/log.md" in committed_files
    assert "AGENTS.md" in committed_files
    assert "openkos.yaml" in committed_files
    assert ".gitignore" in committed_files

    assert vcs_git.is_clean(tmp_path) is True


def test_git_existing_repo_no_gitignore_scoped_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cwd` already inside a git working tree, no `.gitignore`, unrelated
    dirty content present, configured identity: `git init` does NOT run
    again (no nested `.git`), `.gitignore` is written, and the commit
    contains only the openkos-created files -- the pre-existing unrelated
    dirty file stays untouched and uncommitted (spec: Directory already
    inside a git working tree / Existing repo, scoped commit excludes
    unrelated content)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    vcs_git.init_repo(tmp_path)
    outer_git_head_before = (tmp_path / ".git" / "HEAD").read_bytes()
    (tmp_path / "unrelated.txt").write_text(
        "pre-existing dirty content", encoding="utf-8"
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".git" / "HEAD").read_bytes() == outer_git_head_before
    assert not any((tmp_path / "raw").rglob(".git"))
    assert (tmp_path / ".gitignore").is_file()

    committed = vcs_git._run(["git", "log", "--name-only", "--format="], cwd=tmp_path)
    committed_files = {line for line in committed.stdout.splitlines() if line}
    assert "unrelated.txt" not in committed_files
    assert "openkos.yaml" in committed_files

    status = vcs_git._run(["git", "status", "--porcelain"], cwd=tmp_path)
    assert "unrelated.txt" in status.stdout
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == (
        "pre-existing dirty content"
    )


def test_git_existing_repo_with_gitignore_preserved(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cwd` inside an existing git working tree that already has a
    `.gitignore`: content is unchanged byte-for-byte, `git init` does not
    run, and the commit excludes the pre-existing `.gitignore` (spec:
    Existing .gitignore is preserved / Existing repo with pre-existing
    .gitignore, scoped commit)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    vcs_git.init_repo(tmp_path)
    original_gitignore = "# my custom rules\nnode_modules/\n"
    (tmp_path / ".gitignore").write_text(original_gitignore, encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == original_gitignore

    committed = vcs_git._run(["git", "log", "--name-only", "--format="], cwd=tmp_path)
    committed_files = {line for line in committed.stdout.splitlines() if line}
    assert ".gitignore" not in committed_files
    assert "openkos.yaml" in committed_files


def test_git_unavailable_warns_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git` unavailable on `PATH`: a non-fatal WARNING is printed to
    stderr, `init` exits 0, and every pre-existing workspace artifact is
    present (spec: Git unavailable)."""
    monkeypatch.chdir(tmp_path)

    def _raise_unavailable(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        raise vcs_git.GitUnavailable("git not found on PATH")

    monkeypatch.setattr(vcs_git, "_run", _raise_unavailable)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()


def test_git_identity_unset_warns_no_commit_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git` available but identity unset: a non-fatal WARNING is printed to
    stderr, `init` exits 0, `.gitignore` and the repository are created, and
    NO commit is made -- no fallback bot identity is used either (spec: Git
    identity unset)."""
    monkeypatch.chdir(tmp_path)
    isolate_git_identity(monkeypatch, tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()
    assert (tmp_path / ".git").is_dir()
    assert (tmp_path / ".gitignore").is_file()
    log = vcs_git._run(["git", "log"], cwd=tmp_path)
    assert log.stdout.strip() == ""


def test_git_unicode_decode_error_warns_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If git emits non-UTF-8 bytes on stdout/stderr, the `text=True` decode
    layer inside `_run` raises `UnicodeDecodeError` (a `ValueError`, not an
    `OSError`) -- it must not escape the git-setup `try`/`except` and crash
    `init`. Mapped to `GitError` in `_run`, it is caught by the existing
    non-fatal WARNING path: exit 0, valid workspace (correction fix 1)."""
    monkeypatch.chdir(tmp_path)

    def _raise_decode_error(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", _raise_decode_error)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()


def test_git_commit_failure_warns_actionably_not_misleadingly_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `git add` succeeds but `git commit` fails mid-setup (hook reject,
    lock, disk), the workspace may be left with `.git` + `.gitignore` +
    staged-but-uncommitted files. The degradation WARNING must not claim
    setup was cleanly "skipped" (misleading -- a repo may already have been
    created/partially staged) and must point the user at a concrete recovery
    step (correction fix 2)."""
    monkeypatch.chdir(tmp_path)
    isolate_git_identity(
        monkeypatch, tmp_path, name="Isolated Tester", email="tester@example.invalid"
    )

    def _raise_commit_failure(cwd: Path, rel_paths: list[str], message: str) -> None:
        raise vcs_git.GitError("git commit failed: pre-commit hook rejected")

    monkeypatch.setattr(vcs_git, "commit_paths", _raise_commit_failure)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "warning" in result.stderr.lower()
    assert "git status" in result.stderr
    assert "skipped" not in result.stderr.lower()


def test_git_setup_runs_after_workspace_marker_exists(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The git-setup step runs strictly AFTER `openkos.yaml` is already
    written -- proven via a spy on `init_repo` asserting the marker file
    already exists at call time (spec: Git step runs after the workspace
    marker)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    real_init_repo = vcs_git.init_repo
    observed: dict[str, bool] = {}

    def _spy_init_repo(cwd: Path) -> None:
        observed["openkos_yaml_exists"] = (cwd / "openkos.yaml").is_file()
        real_init_repo(cwd)

    monkeypatch.setattr(vcs_git, "init_repo", _spy_init_repo)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert observed["openkos_yaml_exists"] is True
