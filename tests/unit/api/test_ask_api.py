"""`openkos.api.ask`: a question answered through the query service, gated by
the query disclosure rule (ADR-0068).

No real model is called: the API's concrete client classes are replaced with a
scripted fake that records every prompt it receives. The workspace has an FTS
index and no `vectors.db`, so dense retrieval never embeds."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import api, config
from openkos.api import _wiring
from openkos.llm.base import BackendHostLocality, Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.state import fts
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

_MARKER = "SECRET-MARKER-7C1D"


@dataclass
class Model:
    """What the fake clients were built with and sent."""

    replies: list[str]
    local: bool
    prompts: list[str] = field(default_factory=list)
    built: int = 0
    error: Exception | None = None

    def reply(self) -> str:
        if self.error is not None:
            raise self.error
        return self.replies.pop(0) if self.replies else "An answer.\nUSED: 1"


def install(
    monkeypatch: pytest.MonkeyPatch,
    replies: Iterable[str] = (),
    *,
    local: bool = False,
    error: Exception | None = None,
) -> Model:
    model = Model(replies=list(replies), local=local, error=error)

    class Client:
        def __init__(self, **kwargs: object) -> None:
            model.built += 1

        @property
        def locality(self) -> BackendHostLocality:
            host = "localhost" if model.local else "llm.example.test"
            return BackendHostLocality(is_local=model.local, display_host=host)

        def chat(self, messages: Sequence[Message]) -> str:
            model.prompts.append("\n".join(m["content"] for m in messages))
            return model.reply()

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise AssertionError("no vectors.db, so nothing embeds")

    monkeypatch.setattr(_wiring, "OllamaClient", Client)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", Client)
    return model


def _index(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    fts.write_fts_index(layout.fts_db_path, layout.bundle_dir)


def _sufficiency(root: Path, on: bool) -> None:
    path = root / "openkos.yaml"
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.startswith("sufficiency_check:")
    ]
    lines.append(f"sufficiency_check: {'true' if on else 'false'}\n")
    path.write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A public and a confidential concept that both match `marker value`."""
    ws = make_workspace(tmp_path, monkeypatch)
    write_doc(
        ws,
        "concepts/open",
        {"type": "Concept", "title": "Open", "sensitivity": "public"},
        "This concept explains a marker value in general terms.\n",
    )
    write_doc(
        ws,
        "concepts/secret",
        {"type": "Concept", "title": "Secret", "sensitivity": "confidential"},
        f"This concept explains a marker value. The value is {_MARKER}.\n",
    )
    _sufficiency(ws, on=False)
    _index(ws)
    return ws


def _ask(root: Path, question: str = "marker value", **kwargs: object) -> api.Answer:
    return api.ask(api.Workspace.open(root), question, **kwargs)  # type: ignore[arg-type]


def _names(answer: api.Answer) -> str:
    """Every id and title the answer carries, as one string."""
    parts = [c.concept_id + c.title for c in answer.citations]
    parts += [o.concept_id + o.title for o in answer.omitted]
    return " ".join(parts) + (answer.answer or "")


# -- grounded answers ---------------------------------------------------------- #


def test_a_grounded_answer_cites_its_concepts_and_writes_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, ["The marker is explained.\nUSED: 1"])
    before = support.snapshot(root)

    answer = _ask(root)

    assert answer.outcome == "answered"
    assert answer.answer == "The marker is explained."
    assert answer.no_match_cause == "none"
    assert [c.concept_id for c in answer.citations] == ["concepts/open"]
    assert answer.citations[0].title == "Open"
    assert answer.citations[0].confidential is False
    assert answer.attribution == "reported"
    assert answer.question == "marker value"
    assert answer.lexical_unavailable is False
    assert answer.dense_unavailable is True
    assert support.snapshot(root) == before


# -- the model boundary and the disclosure rule ------------------------------- #


def test_a_remote_model_never_receives_a_confidential_concept_without_consent(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch, local=False)

    answer = _ask(root)

    assert model.prompts
    assert all(_MARKER not in prompt for prompt in model.prompts)
    assert answer.withheld == 1
    assert "secret" not in _names(answer).casefold()


def test_a_local_models_answer_over_a_confidential_concept_is_withheld(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch, [f"It is {_MARKER}.\nUSED: 1, 2"], local=True)

    answer = _ask(root)

    assert any(_MARKER in prompt for prompt in model.prompts)
    assert answer.outcome == "withheld"
    assert answer.answer is None
    assert answer.withheld >= 1
    assert "secret" not in _names(answer).casefold()
    assert _MARKER not in _names(answer)


def test_an_uncited_confidential_block_still_withholds_the_answer(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule reads what entered the prompt, not only what was cited."""
    install(monkeypatch, ["Only general terms.\nUSED: none"], local=True)

    answer = _ask(root)

    assert answer.outcome == "withheld"
    assert answer.answer is None


def test_consent_names_a_confidential_citation(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, [f"It is {_MARKER}.\nUSED: 1, 2"], local=False)

    answer = _ask(root, include_confidential=True)

    assert answer.outcome == "answered"
    assert answer.answer == f"It is {_MARKER}."
    marked = {c.concept_id: c.confidential for c in answer.citations}
    assert marked == {"concepts/open": False, "concepts/secret": True}
    assert answer.withheld == 0


def test_deprecated_concepts_are_excluded_unless_included(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(
        root,
        "concepts/old",
        {
            "type": "Concept",
            "title": "Old",
            "status": "deprecated",
            "sensitivity": "public",
        },
        "An old view of the marker value.\n",
    )
    _index(root)
    install(monkeypatch, ["A.\nUSED: 1, 2", "B.\nUSED: 1, 2"])

    without = _ask(root)
    with_old = _ask(root, include_deprecated=True)

    assert "concepts/old" not in {c.concept_id for c in without.citations}
    assert "concepts/old" in {c.concept_id for c in with_old.citations}


# -- no-match outcomes --------------------------------------------------------- #


def test_an_empty_question_is_a_no_match_and_calls_no_model(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = install(monkeypatch)

    answer = _ask(root, "   ")

    assert answer.outcome == "no_match"
    assert answer.no_match_cause == "empty_query"
    assert answer.answer is None
    assert model.prompts == []


def test_zero_hits_is_a_no_match(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = install(monkeypatch)

    answer = _ask(root, "zebra xylophone")

    assert answer.outcome == "no_match"
    assert answer.no_match_cause == "zero_hits"
    assert model.prompts == []


def test_a_sufficiency_refusal_is_its_own_outcome(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sufficiency(root, on=True)
    install(monkeypatch, ["NONE"])

    answer = _ask(root)

    assert answer.outcome == "no_answer_in_context"
    assert answer.no_match_cause == "insufficient_context"
    assert answer.answer is None
    assert answer.sufficiency_checked is True


# -- progress ------------------------------------------------------------------ #


def test_progress_reports_the_phases_in_order(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sufficiency(root, on=True)
    install(monkeypatch, ["a marker value in general terms", "An answer.\nUSED: 1"])
    heard: list[api.AskProgress] = []

    _ask(root, progress=heard.append)

    assert [(p.phase, p.completed, p.total) for p in heard] == [
        ("retrieving", 0, 4),
        ("assembling", 1, 4),
        ("checking", 2, 4),
        ("synthesizing", 3, 4),
    ]


# -- failures ------------------------------------------------------------------ #


@pytest.mark.parametrize("limit", [0, -1, True])
def test_an_invalid_limit_is_refused_before_any_client(
    root: Path, monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    support.forbid_model_clients(monkeypatch, "an invalid ask")

    with pytest.raises(ValueError, match="limit"):
        _ask(root, limit=limit)


def test_the_limit_bounds_the_context(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, ["A.\nUSED: 1"])

    answer = _ask(root, limit=1, include_confidential=True)

    assert len(answer.citations) == 1


def test_an_unreadable_configuration_is_workspace_unreadable(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = api.Workspace.open(root)
    (root / "openkos.yaml").write_text("backend: [not, a, string]\n", encoding="utf-8")
    support.forbid_model_clients(monkeypatch, "an unreadable workspace")

    with pytest.raises(api.WorkspaceUnreadable):
        api.ask(workspace, "marker value")


def test_an_unreachable_backend_is_the_neutral_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, error=OllamaUnavailable("down"))

    with pytest.raises(api.BackendUnavailable):
        _ask(root)


def test_a_stale_index_is_reported(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch)
    write_doc(root, "concepts/late", {"type": "Concept", "title": "Late"}, "New.\n")

    answer = _ask(root)

    assert answer.stale_indexes == ("fts",)


def test_a_fresh_index_reports_nothing_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)

    assert _ask(root).stale_indexes == ()


def test_the_default_limit_is_the_query_default() -> None:
    import inspect

    from openkos.retrieval.answer import DEFAULT_QUERY_LIMIT

    default = inspect.signature(api.ask).parameters["limit"].default
    assert default == DEFAULT_QUERY_LIMIT


# -- the sufficiency model (#1560) --------------------------------------------- #


def _install_per_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_by_model: dict[str, bool],
    replies_by_model: dict[str, list[str]],
) -> list[str]:
    """Fake clients that remember the model they were built for, so a test can
    tell the check client from the synthesis one. Returns the build order."""
    built: list[str] = []

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.model = str(kwargs["model"])
            built.append(self.model)

        @property
        def locality(self) -> BackendHostLocality:
            local = local_by_model.get(self.model, True)
            return BackendHostLocality(
                is_local=local, display_host="localhost" if local else "far.example"
            )

        def chat(self, messages: Sequence[Message]) -> str:
            return replies_by_model[self.model].pop(0)

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise AssertionError("no vectors.db, so nothing embeds")

    monkeypatch.setattr(_wiring, "OllamaClient", Client)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", Client)
    return built


def test_the_check_runs_on_the_sufficiency_model_and_synthesis_on_the_chat_model(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sufficiency(root, on=True)
    built = _install_per_model(
        monkeypatch,
        local_by_model={},
        replies_by_model={
            config.DEFAULT_JUDGE_MODEL: ["a sentence that answers it"],
            config.DEFAULT_MODEL: ["An answer.\nUSED: 1"],
        },
    )

    answer = _ask(root)

    assert answer.sufficiency_checked is True
    # Both clients are local, so the exemption holds and the confidential
    # concept entered the prompt: the answer is withheld, not refused.
    assert answer.outcome == "withheld"
    chat_builds = [m for m in built if m != config.DEFAULT_EMBEDDING_MODEL]
    assert chat_builds == [config.DEFAULT_MODEL, config.DEFAULT_JUDGE_MODEL]


def test_a_remote_check_model_withholds_the_confidential_exemption(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confidential bodies reach the check model too: with synthesis local and
    the check remote, the exemption must not hold, so the confidential concept
    never enters the prompt."""
    _sufficiency(root, on=True)
    _install_per_model(
        monkeypatch,
        local_by_model={config.DEFAULT_JUDGE_MODEL: False},
        replies_by_model={
            config.DEFAULT_JUDGE_MODEL: ["a sentence that answers it"],
            config.DEFAULT_MODEL: ["An answer.\nUSED: 1"],
        },
    )

    answer = _ask(root)

    assert answer.outcome == "answered"
    assert "concepts/secret" not in {c.concept_id for c in answer.citations}
