"""CLI tests for the confidential local exemption (issue #240).

`sensitivity` governs what LEAVES the machine. The CLI is the only layer
that knows WHICH client a command will send through, so it is the only
layer that may resolve the exemption -- `_resolve_local_exemption` ANDs the
client's own verified locality with the workspace's
`confidential_local_exemption` key and threads the resulting boolean into
the five `llm.chat` seams (`query`, `contradictions`, `adjudicate`,
`suggest-relations`, `suggest-volatility`).

Two things are pinned here and nowhere else:

1. The exemption is decided on the CLIENT's resolved host, never on
   `os.environ["OLLAMA_HOST"]` read independently. An env read answers a
   different question -- it ignores an explicit `host=` argument -- so it
   could grant a local exemption to a client demonstrably sending
   off-machine.
2. It fails CLOSED on every axis: a non-local host, an unparseable host,
   and an explicit `confidential_local_exemption: false` each deny it
   alone.

What `local_exemption=True` then MEANS is `sensitivity.py`'s contract,
pinned in `tests/unit/test_sensitivity.py`; these tests only prove the
wiring reaches each seam with the right boolean.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli import curate as curate_mod
from openkos.cli import main as main_mod
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.llm.ollama import BackendHostLocality, OllamaClient
from openkos.resolution.adjudication import AdjudicationBatch
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.resolution.contradiction import ContradictionBatch
from openkos.resolution.edge_typing import EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestionBatch
from openkos.retrieval.answer import AnswerResult
from openkos.state import reindex as reindex_module

runner = CliRunner()

_REMOTE_HOST = "http://user:s3cret@remote.example:11434"
"""An off-machine host WITH credentials, so every assertion doubles as a
redaction pin: denying the exemption must never print the password."""


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _disable_exemption(tmp_path: Path) -> None:
    """Flip the workspace's `confidential_local_exemption` key to `false`,
    the documented way to restore the pre-#240 blanket behavior."""
    config_path = tmp_path / "openkos.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert "confidential_local_exemption: true" in text
    config_path.write_text(
        text.replace(
            "confidential_local_exemption: true",
            "confidential_local_exemption: false",
        ),
        encoding="utf-8",
    )


# --- _resolve_local_exemption: the ONE place the two terms are ANDed --------


def test_resolve_local_exemption_grants_only_on_a_local_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client resolved to loopback, with the workspace key enabled, is the
    only combination that grants the exemption (#240)."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient(model="qwen3")

    assert client.locality.is_local is True
    assert main_mod._resolve_local_exemption(client, _cfg(True)) is True


def test_resolve_local_exemption_denied_by_the_workspace_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`confidential_local_exemption: false` denies the exemption even on a
    verified-local backend -- the opt-out for users who want `confidential`
    to mean "no LLM ever, local or not" (#240)."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient(model="qwen3")

    assert main_mod._resolve_local_exemption(client, _cfg(False)) is False


def test_resolve_local_exemption_denied_by_a_non_local_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified-remote backend denies the exemption regardless of the
    workspace key -- this is the case the gate exists for (#240)."""
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    client = OllamaClient(model="qwen3")

    assert main_mod._resolve_local_exemption(client, _cfg(True)) is False


def test_resolve_local_exemption_fails_closed_on_an_unparseable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that cannot be parsed cannot be PROVEN local, so the exemption
    is denied -- unknown locality is treated as remote (#240)."""
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret@[::1")
    client = OllamaClient(model="qwen3")

    assert main_mod._resolve_local_exemption(client, _cfg(True)) is False


def test_resolve_local_exemption_reads_the_client_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict follows the client's own resolved host, not `OLLAMA_HOST`
    (#240).

    This is the decision the whole change rests on. A caller that
    constructed its client against an explicit remote host while
    `OLLAMA_HOST` happens to be loopback would, under an env-based check, be
    granted an exemption for a send that leaves the machine."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    remote_client = OllamaClient(model="qwen3", host=_REMOTE_HOST)

    assert main_mod._resolve_local_exemption(remote_client, _cfg(True)) is False

    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    local_client = OllamaClient(model="qwen3", host="http://127.0.0.1:11434")

    assert main_mod._resolve_local_exemption(local_client, _cfg(True)) is True


def _cfg(exemption: bool) -> config.Config:
    """A `Config` differing from the packaged defaults only in
    `confidential_local_exemption`."""
    return config.Config(
        model=config.DEFAULT_MODEL,
        review=config.DEFAULT_REVIEW,
        default_sensitivity=config.DEFAULT_SENSITIVITY,
        freshness_window=config.DEFAULT_FRESHNESS_WINDOW,
        embedding_model=config.DEFAULT_EMBEDDING_MODEL,
        chat_timeout=config.DEFAULT_CHAT_TIMEOUT,
        max_generation_tokens=config.DEFAULT_MAX_GENERATION_TOKENS,
        context_window=config.DEFAULT_CONTEXT_WINDOW,
        confidential_local_exemption=exemption,
        volatility_windows={},
        type_tiers={},
        models={},
        union_judge=config.DEFAULT_UNION_JUDGE,
        sufficiency_check=config.DEFAULT_SUFFICIENCY_CHECK,
        concurrent_extraction=config.DEFAULT_CONCURRENT_EXTRACTION,
        type_sensitivity_defaults=dict(config.DEFAULT_TYPE_SENSITIVITY_DEFAULTS),
        rationale_language=config.DEFAULT_RATIONALE_LANGUAGE,
    )


# --- the advisory now classifies the CLIENT, not the env var ---------------


def test_advisory_is_driven_by_the_locality_it_is_handed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`_warn_if_nonlocal_embed_host` warns from the locality it is GIVEN,
    with no environment read of its own (#240).

    `OLLAMA_HOST` is set to a remote value in both halves: if the helper
    still read the env, the first half would warn. Its #199/#353 contract is
    otherwise unchanged -- advisory only, never raising, userinfo redacted,
    silent when local."""
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)

    main_mod._warn_if_nonlocal_embed_host(
        "ingest", BackendHostLocality(is_local=True, display_host="localhost:11434")
    )

    assert capsys.readouterr().err == ""

    main_mod._warn_if_nonlocal_embed_host(
        "ingest",
        BackendHostLocality(is_local=False, display_host="remote.example:11434"),
    )

    captured = capsys.readouterr()
    assert "embedding host 'remote.example:11434' is not this machine" in captured.err
    assert "s3cret" not in captured.err


# --- each seam receives the resolved boolean -------------------------------


class _KwargSpy:
    """Records the keyword arguments of every call, returning `result`."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.result


def _answer_result() -> AnswerResult:
    return AnswerResult(
        answer="a fake answer",
        citations=[],
        fts_hit_count=0,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )


def _spy_query(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    spy = _KwargSpy(_answer_result())
    monkeypatch.setattr(main_mod, "answer", spy)
    return spy


def _spy_contradictions(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    spy = _KwargSpy((ContradictionBatch(results=[]), 0))
    monkeypatch.setattr(main_mod, "find_contradictions", spy)
    return spy


def _spy_adjudicate(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    spy = _KwargSpy(AdjudicationBatch(results=[]))
    monkeypatch.setattr(main_mod, "adjudicate_candidates", spy)
    monkeypatch.setattr(
        main_mod,
        "find_candidates_report",
        lambda *a, **k: CandidateGroupReport(
            groups=(_candidate(),), produced=1, retained=1
        ),
    )
    return spy


def _candidate() -> CandidateGroup:
    return CandidateGroup(
        okf_type="Person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )


def _spy_suggest_relations(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    spy = _KwargSpy(list[object]())
    monkeypatch.setattr(main_mod, "candidate_edges", spy)
    return spy


def _spy_suggest_volatility(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    spy = _KwargSpy(TierSuggestionBatch(results=[]))
    monkeypatch.setattr(main_mod, "suggest_volatility", spy)
    return spy


_SEAMS: dict[str, tuple[Any, list[str]]] = {
    "query": (_spy_query, ["query", "a question"]),
    "contradictions": (_spy_contradictions, ["contradictions"]),
    "adjudicate": (_spy_adjudicate, ["adjudicate"]),
    "suggest-relations": (_spy_suggest_relations, ["suggest-relations", "--auto"]),
    "suggest-volatility": (_spy_suggest_volatility, ["suggest-volatility"]),
}
"""The five `llm.chat`-calling verbs, each with the seam whose kwargs carry
`local_exemption` and the argv that reaches it."""


@pytest.mark.parametrize("verb", sorted(_SEAMS))
def test_local_backend_grants_the_exemption_at_every_seam(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a stock workspace with the default local backend, every one of the
    five seams receives `local_exemption=True` (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    make_spy, argv = _SEAMS[verb]
    spy = make_spy(monkeypatch)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.stderr
    assert spy.calls, f"{verb} never reached its seam"
    assert spy.calls[0]["local_exemption"] is True


def _spy_suggest_edge_types_direct(monkeypatch: pytest.MonkeyPatch) -> _KwargSpy:
    """Stub `candidate_edges` -- the cost-gate PROBE (issue #134) -- to
    return one edge instead of the `_SEAMS["suggest-relations"]` row's `[]`,
    so `suggest_relations_cmd` clears its zero-edge branch and reaches
    `suggest_edge_types`, the seam that actually performs the `llm.chat`
    sends, then spies THAT seam directly."""
    monkeypatch.setattr(
        main_mod,
        "candidate_edges",
        lambda *a, **k: [Edge(source_id="concepts/a", target_id="concepts/b")],
    )
    spy = _KwargSpy(EdgeSuggestionBatch(results=[]))
    monkeypatch.setattr(main_mod, "suggest_edge_types", spy)
    return spy


def test_local_backend_grants_the_exemption_at_suggest_edge_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin `local_exemption` reaching `suggest_edge_types` directly, the way
    the other four verbs are pinned on their send seam (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    spy = _spy_suggest_edge_types_direct(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls, "suggest_edge_types never reached"
    assert spy.calls[0]["local_exemption"] is True


def test_remote_backend_denies_the_exemption_at_suggest_edge_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-remote `OLLAMA_HOST` denies `local_exemption` at
    `suggest_edge_types` too -- the `_SEAMS["suggest-relations"]` row only
    ever reaches `candidate_edges`, never this seam (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    spy = _spy_suggest_edge_types_direct(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls, "suggest_edge_types never reached"
    assert spy.calls[0]["local_exemption"] is False
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


def test_workspace_opt_out_denies_the_exemption_at_suggest_edge_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`confidential_local_exemption: false` denies `local_exemption` at
    `suggest_edge_types` too, even on a local backend (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _disable_exemption(tmp_path)
    spy = _spy_suggest_edge_types_direct(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls, "suggest_edge_types never reached"
    assert spy.calls[0]["local_exemption"] is False


def test_unparseable_host_fails_closed_at_suggest_edge_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable host cannot be proven local, so `suggest_edge_types`
    fails closed too -- and the run still completes (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret@[::1")
    spy = _spy_suggest_edge_types_direct(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls, "suggest_edge_types never reached"
    assert spy.calls[0]["local_exemption"] is False
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


@pytest.mark.parametrize("verb", sorted(_SEAMS))
def test_remote_backend_denies_the_exemption_at_every_seam(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-remote `OLLAMA_HOST` denies the exemption at every seam --
    this is the case `confidential` exists to protect (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    make_spy, argv = _SEAMS[verb]
    spy = make_spy(monkeypatch)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["local_exemption"] is False
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


@pytest.mark.parametrize("verb", sorted(_SEAMS))
def test_workspace_opt_out_denies_the_exemption_at_every_seam(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`confidential_local_exemption: false` denies the exemption at every
    seam even on a local backend (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _disable_exemption(tmp_path)
    make_spy, argv = _SEAMS[verb]
    spy = make_spy(monkeypatch)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["local_exemption"] is False


@pytest.mark.parametrize("verb", sorted(_SEAMS))
def test_unparseable_host_fails_closed_at_every_seam(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable host cannot be proven local, so every seam fails closed
    (#240) -- and the run still completes, since locality classification
    never raises."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret@[::1")
    make_spy, argv = _SEAMS[verb]
    spy = make_spy(monkeypatch)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["local_exemption"] is False
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


# --- curate builds its own client, so it resolves its own exemption --------


def _capture_curate_context(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Intercept `run_curate` and record the `CurateContext` it was handed.

    `render_summary` is stubbed alongside it because it zips the outcome
    list against the stage table -- an intercepted run produces no outcomes,
    and the summary is not what these tests are about."""
    seen: list[Any] = []

    def _spy(ctx: Any) -> list[Any]:
        seen.append(ctx)
        return []

    monkeypatch.setattr(curate_mod, "run_curate", _spy)
    monkeypatch.setattr(curate_mod, "render_summary", lambda _o: [])
    return seen


def test_curate_resolves_the_exemption_on_a_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`curate` builds its OWN `OllamaClient`, so it resolves its own
    exemption rather than inheriting one (#240).

    Its stages reach `adjudicate_candidates`, `suggest_edge_types`,
    `suggest_volatility` and `find_contradictions` -- the same seams the
    standalone verbs use -- so a `curate` run that did not thread the
    exemption would silently disagree with them about the same bundle on the
    same machine."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    seen = _capture_curate_context(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert seen[0].local_exemption is True


def test_curate_denies_the_exemption_on_a_remote_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-remote backend denies `curate`'s exemption (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    seen = _capture_curate_context(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert seen[0].local_exemption is False
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


def test_curate_honors_the_workspace_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`confidential_local_exemption: false` denies `curate`'s exemption on a
    local backend too (#240)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _disable_exemption(tmp_path)
    seen = _capture_curate_context(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert seen[0].local_exemption is False


def test_curate_context_local_exemption_defaults_to_false() -> None:
    """`CurateContext.local_exemption` defaults to `False`, so a context
    built without it -- a test fixture, a future caller -- fails closed
    (#240)."""
    field = curate_mod.CurateContext.__dataclass_fields__["local_exemption"]
    assert field.default is False


# --- #922: the SIXTH seam. Embedding is egress too --------------------------
#
# `sensitivity` governs what leaves the machine, and an embed call against a
# non-local backend sends the document's text over the wire exactly as a chat
# payload does. The embed path had no sensitivity check at all: #199 gave it a
# warning and sent the document anyway. These pin the wiring for all three
# embed seams; what the boolean MEANS is `sensitivity.py`'s contract, and the
# withholding behaviour itself is pinned in `tests/unit/state/test_reindex.py`.


def _capture_reindex(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace `state.reindex.reindex` with a recorder.

    Captures the resolved `local_exemption` without a network call, which is
    what makes these tests hermetic AND what makes them prove the threading
    rather than the outcome -- a hardcoded `True` at any call site would show
    up here as the wrong boolean under a remote host."""
    seen: dict[str, Any] = {}

    def _recorder(*args: Any, **kwargs: Any) -> Any:
        seen["local_exemption"] = kwargs.get("local_exemption")
        return reindex_module.ReindexReport(
            embedded=0, cache_hits=0, pruned=0, skipped=0
        )

    monkeypatch.setattr(reindex_module, "reindex", _recorder)
    return seen


def _write_confidential_doc(tmp_path: Path) -> None:
    path = tmp_path / "bundle" / "concepts" / "secret.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: Secret\ndescription: ''\n"
        "sensitivity: confidential\n---\nsalary figures\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [("http://127.0.0.1:11434", True), (_REMOTE_HOST, False)],
)
def test_reindex_threads_the_resolved_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, expected: bool
) -> None:
    """The `reindex` command resolves the exemption from the client it built
    and hands it to `state.reindex.reindex`."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", host)
    seen = _capture_reindex(monkeypatch)

    runner.invoke(app, ["reindex"])

    assert seen["local_exemption"] is expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [("http://127.0.0.1:11434", True), (_REMOTE_HOST, False)],
)
def test_write_time_refresh_threads_the_resolved_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, expected: bool
) -> None:
    """`_refresh_derived_after_write` is the seam #640 added to every mutating
    verb, and it was the WORST of the three: it embedded every document and
    did not even emit the non-local host advisory the other two have carried
    since #199, so a write-time refresh against a remote backend sent
    document text off the machine in complete silence."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", host)
    seen = _capture_reindex(monkeypatch)
    layout = config.WorkspaceLayout(tmp_path)

    main_mod._refresh_derived_after_write(layout, None, verb="merge")

    assert seen["local_exemption"] is expected


def test_write_time_refresh_warns_about_a_non_local_embedding_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The advisory the other two embed paths have had since #199, missing
    here until #922. Silence is what made this seam the dangerous one."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    _capture_reindex(monkeypatch)
    capsys.readouterr()

    main_mod._refresh_derived_after_write(
        config.WorkspaceLayout(tmp_path), None, verb="merge"
    )

    err = capsys.readouterr().err
    assert "is not this machine" in err
    # The redaction pin: denying or warning must never print the password.
    assert "s3cret" not in err


def test_reindex_withholds_a_confidential_doc_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the REAL `state.reindex.reindex`, with a bundle
    holding nothing but a confidential document.

    Nothing is embedded, so nothing is sent -- and the suite's fail-closed
    network guard is what proves that rather than an assertion about a stub:
    had the gate not held, this test would have reached for a remote host and
    failed loudly."""
    _init_workspace(tmp_path, monkeypatch)
    _write_confidential_doc(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    assert "0 embedded" in result.output
    assert "1 withheld" in result.output
    assert "withheld from embedding" in result.output
    assert "s3cret" not in result.output


def test_reindex_embeds_the_same_doc_when_the_backend_is_this_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control, and the reason this is a boundary rather than a
    downgrade: on a local backend the very same document is embedded and
    nothing is withheld. Without this, "0 embedded" above would be
    indistinguishable from reindex simply being broken."""
    _init_workspace(tmp_path, monkeypatch)
    _write_confidential_doc(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    seen = _capture_reindex(monkeypatch)

    runner.invoke(app, ["reindex"])

    assert seen["local_exemption"] is True


@pytest.mark.parametrize(
    ("host", "expected"),
    [("http://127.0.0.1:11434", True), (_REMOTE_HOST, False)],
)
def test_embed_after_ingest_threads_the_resolved_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, expected: bool
) -> None:
    """The third embed seam: `ingest`'s per-file embed (#183).

    Resolved at the call site from the very client handed to
    `_embed_after_ingest`, so the boolean describes the host that will
    actually POST -- `_embed_after_ingest` re-derives nothing."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", host)
    seen = _capture_reindex(monkeypatch)
    cfg = config.read_config(tmp_path)
    client = OllamaClient(model=cfg.embedding_model)

    main_mod._embed_after_ingest(
        config.WorkspaceLayout(tmp_path),
        client,
        model_tag=cfg.embedding_model,
        local_exemption=main_mod._resolve_local_exemption(client, cfg),
    )

    assert seen["local_exemption"] is expected


def test_embed_after_ingest_defaults_to_withholding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-closed default, pinned as a claim rather than left implicit.

    A future caller that forgets the parameter must withhold a confidential
    document, never send one -- the same reasoning `sensitivity.should_block`
    gives for its own `False` default."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    seen = _capture_reindex(monkeypatch)
    cfg = config.read_config(tmp_path)

    # Deliberately omitting `local_exemption=` on a LOCAL host: the exemption
    # would be granted if it were resolved, so a True here would mean the
    # default is doing the granting.
    main_mod._embed_after_ingest(
        config.WorkspaceLayout(tmp_path),
        OllamaClient(model=cfg.embedding_model),
        model_tag=cfg.embedding_model,
    )

    assert seen["local_exemption"] is False


def test_write_time_refresh_can_suppress_the_duplicate_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`warn_nonlocal_host=False` suppresses ONLY the advisory line, never
    the gate.

    Teaching this seam to warn (#922) would otherwise make `ingest` and
    `query --save` print the same line twice in one run -- they embed once
    themselves and again through this refresh -- breaking the
    once-per-invocation contract #353 item 4 established. `ingest`'s own
    end-to-end tests in `test_embed_host_advisory.py` are what caught it;
    this pins the suppression lever those call sites use, including for
    `query --save`, whose end-to-end path cannot reach this seam without a
    live backend."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    seen = _capture_reindex(monkeypatch)
    capsys.readouterr()

    main_mod._refresh_derived_after_write(
        config.WorkspaceLayout(tmp_path), None, verb="query", warn_nonlocal_host=False
    )

    assert "is not this machine" not in capsys.readouterr().err
    # The gate is NOT suppressed with it: silencing a line must never
    # silence the boundary.
    assert seen["local_exemption"] is False
