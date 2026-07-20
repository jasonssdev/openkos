"""Unit tests for the `adjudicate` CLI command: read-only LLM precision
layer over slice-1 `find_candidates` output.

`adjudicate` is a FOURTH read command, mirroring `query`'s wiring exactly:
`config.require_workspace` gate -> `config.read_config` -> a real
`OllamaClient(model=cfg.model)` built from the workspace's configured model
-> `resolution.find_candidates` -> `resolution.adjudication.adjudicate_candidates`.
It is read-only: no writes, no `--auto`, no confirmation gate, distinct from
the reserved `resolve`/`merge` verbs (slice 3). `--same-only` is a
DISPLAY-only filter -- the library always receives every candidate group
regardless of the flag.

Every test that needs a specific adjudication OUTCOME patches
`openkos.cli.main.adjudicate_candidates` directly (mirrors how `test_query.py`
patches `openkos.cli.main.answer`) -- zero network, zero real Ollama
process. Only the good-life-demo integration proof patches
`openkos.cli.main.OllamaClient` itself with a fake backend, so the test
never depends on whether the real example bundle happens to contain
candidates.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.base import Message
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier

runner = CliRunner()


def _snapshot_entry(path: Path) -> tuple[bytes, int] | None:
    if path.is_dir():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _snapshot(root: Path) -> dict[Path, tuple[bytes, int] | None]:
    """Capture every entry under `root`, keyed by relative path, as its byte
    contents and `st_mtime_ns` -- so a rewrite-with-identical-bytes (touch)
    regression is caught, not just a content change."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def _adjudicated(
    group: CandidateGroup,
    *,
    verdict: Verdict = Verdict.SAME,
    confidence: float = 0.9,
    rationale: str = "stub rationale",
) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=verdict, confidence=confidence, rationale=rationale
    )


def test_adjudicate_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `adjudicate` refuses (exit 1), prints the shared
    `require_workspace` reason under an `adjudicate`-specific prefix, and
    never calls `adjudicate_candidates()` (spec: mirrors `query`/`lint`)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos adjudicate: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_adjudicate_malformed_config_maps_to_exit_one_before_calling_adjudicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, mirrors
    `query`) is caught, printed as a friendly stderr message, exits 1 with no
    raw traceback, and `adjudicate_candidates()` is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos adjudicate: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_adjudicate_fresh_bundle_reports_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle has zero candidate groups, so the
    real `adjudicate_candidates` never calls `llm.chat` -- a real
    `OllamaClient` is safe to construct here. Prints a clear "no candidates"
    line and exits 0."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_adjudicate_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- byte contents AND `st_mtime_ns` both unchanged (spec: Verb renders
    verdicts with zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Ada Lovelace")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="ada lovelace")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate"], catch_exceptions=True)

    # Regardless of exit code (a real Ollama may or may not be reachable in
    # this environment), the workspace bytes/mtimes must be identical --
    # `adjudicate` never writes, whether it succeeds or degrades.
    assert _snapshot(tmp_path) == before
    if result.exit_code == 0:
        assert "openkos adjudicate: workspace at" in result.stdout


def test_adjudicate_renders_grouped_verdict_confidence_and_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with one real HIGH candidate group renders its type, tier,
    trigger, and members (duplicates' render style) PLUS the group's verdict,
    confidence, and rationale, and exits 0 (spec: Verb renders verdicts with
    zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe   society")
    captured: dict[str, object] = {}

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        captured["candidates"] = candidates
        return [
            _adjudicated(
                candidates[0],
                verdict=Verdict.SAME,
                confidence=0.87,
                rationale="Same concept, different casing",
            )
        ]

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "HIGH" in result.stdout
    assert "Concept" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "SAME" in result.stdout
    assert "0.87" in result.stdout
    assert "Same concept, different casing" in result.stdout
    candidates = captured["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1


def test_adjudicate_same_only_hides_non_same_verdicts_from_output_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--same-only` hides DIFFERENT/UNCERTAIN groups from the printed
    report, but `adjudicate_candidates` still receives every group -- the
    filter never touches the library call (spec: `--same-only` is a
    display-only filter)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="Person", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )
    fake_groups = [same_group, different_group, uncertain_group]
    captured: dict[str, object] = {}

    def _fake_find_candidates(bundle_dir: object) -> list[CandidateGroup]:
        return fake_groups

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        captured["candidates"] = candidates
        return [
            _adjudicated(same_group, verdict=Verdict.SAME, rationale="same rationale"),
            _adjudicated(
                different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
            ),
            _adjudicated(
                uncertain_group, verdict=Verdict.UNCERTAIN, rationale="unc rationale"
            ),
        ]

    monkeypatch.setattr("openkos.cli.main.find_candidates", _fake_find_candidates)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "same rationale" in result.stdout
    assert "diff rationale" not in result.stdout
    assert "unc rationale" not in result.stdout
    # The library call itself still received every group -- the flag is
    # display-only and never filters the data layer.
    candidates = captured["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 3


def test_adjudicate_same_only_with_no_same_verdicts_prints_empty_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--same-only` filtering every group out (no SAME verdicts at all)
    still exits 0 and prints a clear notice instead of silently rendering
    nothing -- the `displayed` list empty-after-filter branch."""
    _init_workspace(tmp_path, monkeypatch)
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(bundle_dir: object) -> list[CandidateGroup]:
        return [different_group]

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        return [
            _adjudicated(
                different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
            )
        ]

    monkeypatch.setattr("openkos.cli.main.find_candidates", _fake_find_candidates)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "diff rationale" not in result.stdout
    assert "No SAME-verdict candidates to display" in result.stdout


def test_adjudicate_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate` builds the `OllamaClient` from the model configured in
    `openkos.yaml`, not a hardcoded value (spec: mirrors `query`'s wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model
    assert kwargs["bundle_dir"] == tmp_path / "bundle"


def test_adjudicate_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising `OllamaUnavailable` is caught,
    printed as a friendly stderr message with `ollama serve` remediation,
    exits 1, and writes nothing (spec: Degrade-On-No-Model)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_unavailable(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_unavailable)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos adjudicate: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising `OllamaModelNotFound` is caught,
    printed with the CONFIGURED model tag and `ollama pull <model>`
    remediation, exits 1, and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    def _raise_model_not_found(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates", _raise_model_not_found
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos adjudicate: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising a plain `OllamaError` (neither
    `OllamaUnavailable` nor `OllamaModelNotFound`) is caught, printed as the
    generic friendly message with no cause-specific remediation, exits 1,
    and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_generic(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        raise OllamaError("boom")

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_generic)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == "openkos adjudicate: failed -- boom.\n"
    assert "ollama serve" not in result.stderr
    assert "ollama pull" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_specific_ollama_subclasses_do_not_fall_through_to_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both `OllamaUnavailable` and `OllamaModelNotFound` must reach their
    OWN handler, not the generic `OllamaError` fallback -- the direct RED
    test for the 3-tier handler ordering, mirrors `query`'s equivalent."""
    _init_workspace(tmp_path, monkeypatch)
    unavailable_message = "Ollama not reachable at http://localhost:11434"

    def _raise_unavailable(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        raise OllamaUnavailable(unavailable_message)

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_unavailable)
    result = runner.invoke(app, ["adjudicate"])
    assert result.stderr != f"openkos adjudicate: failed -- {unavailable_message}.\n"
    assert "ollama serve" in result.stderr

    def _raise_model_not_found(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> list[AdjudicatedCandidate]:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates", _raise_model_not_found
    )
    result = runner.invoke(app, ["adjudicate"])
    assert "Model not found (404)" not in result.stderr
    assert "ollama pull" in result.stderr


def test_adjudicate_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate` is read-only: no `--auto` or confirmation flag exists,
    unlike `ingest`/`forget` (spec: Read-Only `adjudicate` CLI verb)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_adjudicate_command_name_is_not_resolve() -> None:
    """`adjudicate` is registered under its own name -- `resolve` remains
    unimplemented (spec: distinct from the reserved `resolve` verb).
    `merge` is now implemented (entity-resolution-merge slice 3, U4) and is
    deliberately NOT asserted absent here anymore."""
    callback_names = {
        command.callback.__name__
        for command in app.registered_commands
        if command.callback is not None
    }
    assert "adjudicate" in callback_names
    assert "resolve" not in callback_names
    assert "merge" in callback_names

    # Typer's own dispatcher independently confirms `resolve` is an unknown
    # subcommand (exit 2, "No such command"), not merely absent from the
    # callback-name set above.
    assert runner.invoke(app, ["resolve"]).exit_code == 2


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


class _FakeOllamaClient:
    """Structural `LLMBackend` stand-in substituted for the real
    `OllamaClient` the verb constructs internally -- so the integration proof
    never depends on whether the real example bundle happens to contain
    candidate groups, and never touches a real Ollama process."""

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[Message]) -> str:
        return '{"verdict": "same", "confidence": 0.5, "rationale": "fake reply"}'


def test_adjudicate_over_good_life_demo_is_read_only_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the `adjudicate` verb against the real
    `examples/good-life-demo` workspace, with a FAKE injected `LLMBackend`
    substituted for `OllamaClient`, exits 0, writes nothing under the
    bundle (bytes AND mtimes unchanged), and renders a coherent report --
    the Unit 2 CLI-level integration proof (tasks.md 6.2)."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "openkos adjudicate: workspace at" in result.stdout
    assert _snapshot(bundle_dir) == before
