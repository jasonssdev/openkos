"""Unit tests for the `suggest-volatility` CLI command: read-only LLM
volatility-tier suggestion per concept TYPE present in the bundle
(freshness-suggest-windows, S2).

`suggest-volatility` mirrors `suggest-relations`'s wiring exactly: `config.
require_workspace` gate -> `config.read_config` -> a real
`OllamaClient(model=cfg.model)` built from the workspace's configured model
-> `resolution.volatility_typing.suggest_volatility`. It is read-only: no
writes, no `--auto`, no confirmation gate, and its closing hint points at
hand-editing `type_tiers:` in `openkos.yaml` -- there is no write path for
this verb to hint at (unlike `suggest-relations` -> `relate`).

Every test that needs a specific suggestion OUTCOME patches
`openkos.cli.main.suggest_volatility` directly (mirrors how
`test_suggest_relations.py` patches `openkos.cli.main.suggest_relations`) --
zero network, zero real Ollama process.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.llm.ollama import OllamaClient, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.volatility_typing import TierSuggestion
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot

runner = CliRunner()


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors `tests/unit/model/test_okf.py`'s onerror
    monkeypatch pattern, without relying on real `chmod` bits."""
    original_walk = os.walk
    walk_error = OSError(13, "Permission denied", "locked")

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _suggestion(
    *,
    type_name: str = "Person",
    current_default: str = "slow",
    suggested_tier: str | None = "slow",
    rationale: str = "stub rationale",
) -> TierSuggestion:
    return TierSuggestion(
        type_name=type_name,
        current_default=current_default,
        suggested_tier=suggested_tier,
        rationale=rationale,
    )


def test_suggest_volatility_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `suggest-volatility` refuses (exit 1), prints the
    shared `require_workspace` reason under a `suggest-volatility`-specific
    prefix, and never calls the library function (spec: mirrors
    `suggest-relations`)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.suggest_volatility",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos suggest-volatility: refusing to run -- no OpenKOS workspace "
        "found in this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_suggest_volatility_malformed_config_maps_to_exit_one_before_calling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, mirrors
    `suggest-relations`) is caught, printed as a friendly stderr message,
    exits 1 with no raw traceback, and the library function is never
    reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.suggest_volatility",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos suggest-volatility: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_suggest_volatility_fresh_bundle_reports_no_concept_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle has zero concept types, so the
    real `suggest_volatility` never calls `llm.chat` -- a real
    `OllamaClient` is safe to construct here. Prints a clear "no concept
    types" line and exits 0."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "No concept types found." in result.stdout


def test_suggest_volatility_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- byte contents AND `st_mtime_ns` both unchanged (spec: Verb
    performs zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["suggest-volatility"], catch_exceptions=True)

    # Regardless of exit code (a real Ollama may or may not be reachable in
    # this environment), the workspace bytes/mtimes must be identical --
    # `suggest-volatility` never writes, whether it succeeds or degrades.
    assert _snapshot(tmp_path) == before
    if result.exit_code == 0:
        assert "openkos suggest-volatility: workspace at" in result.stdout


def test_suggest_volatility_renders_tier_type_and_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed suggestion renders its suggested tier, type name, and
    rationale, plus the closing `type_tiers` hint, and exits 0 (spec: Verb
    suggests one tier per type present)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _fake_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        captured["bundle_dir"] = bundle_dir
        captured["kwargs"] = kwargs
        return [
            _suggestion(
                type_name="Person",
                suggested_tier="slow",
                rationale="people change slowly",
            )
        ]

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _fake_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "[slow] Person" in result.stdout
    assert "people change slowly" in result.stdout
    assert "Next: openkos set-volatility <ConceptType> <tier>" in result.stdout
    assert captured["bundle_dir"] == tmp_path / "bundle"


def test_suggest_volatility_degraded_item_renders_as_no_valid_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded suggestion (`suggested_tier=None`) renders as `[?]` +
    "no valid tier suggested", never as if it were a valid suggestion
    (spec: Invalid/missing suggested tier is never surfaced as valid)."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        return [
            _suggestion(
                type_name="Project", suggested_tier=None, rationale="malformed reply"
            )
        ]

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _fake_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "[?] Project" in result.stdout
    assert "no valid tier suggested" in result.stdout


def test_suggest_volatility_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest-volatility` builds the `OllamaClient` from the model
    configured in `openkos.yaml`, not a hardcoded value (spec: mirrors
    `suggest-relations`'s wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _recording_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model


def test_suggest_volatility_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest_volatility()` raising `OllamaUnavailable` is caught, printed
    as a friendly stderr message with `ollama serve` remediation, exits 1,
    and writes nothing (spec: mirrors `suggest-relations`'s degrade-on-no-model)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_unavailable(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise_unavailable)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos suggest-volatility: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_suggest_volatility_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest_volatility()` raising `OllamaModelNotFound` is caught,
    printed with the CONFIGURED model tag and `ollama pull <model>`
    remediation, exits 1, and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    def _raise_model_not_found(
        bundle_dir: Path, **kwargs: object
    ) -> list[TierSuggestion]:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise_model_not_found)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos suggest-volatility: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "openkos doctor" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_suggest_volatility_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generic `OllamaError` (neither `OllamaUnavailable` nor
    `OllamaModelNotFound`) is caught by the 3rd-tier fallback handler."""
    from openkos.llm.ollama import OllamaError

    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_generic(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        raise OllamaError("something else went wrong")

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise_generic)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos suggest-volatility: failed -- something else went wrong.\n"
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_suggest_volatility_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest-volatility` is read-only: no `--auto` or confirmation flag
    exists (spec: zero writes)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-volatility", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# `--include-confidential` (sensitivity-fail-closed-filter S3b)
# ---------------------------------------------------------------------------


def test_suggest_volatility_include_confidential_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged as
    `suggest_volatility(..., include_confidential=True)` (spec:
    `--include-confidential` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _recording_suggest)

    result = runner.invoke(app, ["suggest-volatility", "--include-confidential"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is True


def test_suggest_volatility_omitted_include_confidential_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-confidential` forwards the safe default
    `include_confidential=False` (spec: Confidential Excluded By Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _recording_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is False


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: walk-incompleteness signal
# ---------------------------------------------------------------------------


def test_suggest_volatility_warns_stderr_on_incomplete_walk_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete directory walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and the command still exits 0 -- WARN,
    not refuse (spec: Incomplete walk warns and still exits 0). A freshly
    initialized, empty bundle has zero concept types, so the real
    `suggest_volatility` never calls `llm.chat` -- a real `OllamaClient` is
    safe to construct here.

    The default workspace grants the confidential local exemption (#240),
    which is the OTHER hatch that suppresses this warning -- see
    `test_suggest_volatility_local_exemption_suppresses_the_warning` below.
    This test is about the walk-incompleteness signal itself, so it opts
    out of the exemption through the same workspace switch a user would
    use."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" in result.stderr


def test_suggest_volatility_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces no incomplete-walk warning (spec:
    Clean bundle produces no warning)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" not in result.stderr


def test_suggest_volatility_include_confidential_suppresses_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the incomplete-walk warning too --
    the filter is deliberately off (spec: `--include-confidential`
    suppresses the warning)."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-volatility", "--include-confidential"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" not in result.stderr


def test_suggest_volatility_local_exemption_suppresses_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidential local exemption (#240) suppresses the incomplete-walk
    warning too, the SAME way `--include-confidential` does: on a stock
    workspace (default local backend, exemption active) the filter is off
    entirely, so an incomplete walk has no bearing on what gets sent and the
    walk is never even run."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" not in result.stderr


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


def test_suggest_volatility_over_good_life_demo_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running `suggest-volatility` against the real `examples/good-life-demo`
    workspace writes nothing under the bundle regardless of outcome -- the
    real `OllamaClient` may or may not reach a live Ollama in this
    environment, but the zero-writes contract must hold either way."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["suggest-volatility"], catch_exceptions=True)

    assert _snapshot(bundle_dir) == before
    if result.exit_code == 0:
        assert "openkos suggest-volatility: workspace at" in result.stdout


# ---------------------------------------------------------------------------
# Issue #190: TTY-gated per-type progress wiring
# ---------------------------------------------------------------------------


def test_suggest_volatility_wires_tty_gated_progress_callback_into_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `suggest-volatility` passes
    `observability.progress_callback`'s hook into `suggest_volatility` as
    `on_progress`; each invocation renders `openkos suggest-volatility:
    suggesting type <i>/<n>...` on STDERR while STDOUT keeps the clean
    report (issue #190, mirroring `suggest-relations`' #134 wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    def _fake_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        on_progress = kwargs["on_progress"]
        assert callable(on_progress)
        results = [
            _suggestion(type_name="Person", suggested_tier="slow", rationale="steady")
        ]
        for index, suggestion in enumerate(results, start=1):
            on_progress(index, len(results), suggestion)
        return results

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _fake_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert "openkos suggest-volatility: suggesting type 1/1..." in result.stderr
    assert "suggesting type" not in result.stdout


def test_suggest_volatility_passes_no_progress_hook_when_stderr_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the factory returns `None`
    and `suggest_volatility` receives `on_progress=None` -- piped output
    stays byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _fake_suggest(bundle_dir: Path, **kwargs: object) -> list[TierSuggestion]:
        captured["on_progress"] = kwargs["on_progress"]
        return []

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _fake_suggest)

    result = runner.invoke(app, ["suggest-volatility"])

    assert result.exit_code == 0
    assert captured["on_progress"] is None
