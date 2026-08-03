"""End-to-end reproduction of issue #183, driven entirely through the CLI.

This is the test that decides whether #183 is actually fixed. Everything
else in the change is a component; this exercises the whole chain a real
user walks:

    ingest N sources -> concepts are embedded -> proximity nominates
    candidate edges -> `suggest-relations` types them -> `contradictions`
    can judge the typed result

Before the CLI wiring lands, this test FAILS at the third step. `ingest`
writes concepts but never computes embeddings, so `vectors.db` stays
absent, `open_proximity_source` returns `None`, pass 3 is a no-op, and
`suggest-relations` reports an empty graph -- which is precisely the
symptom #183 describes: "ingest produces no concept-to-concept edges,
starving the graph layer".

Zero network: `openkos.cli.main.OllamaClient` is replaced with one fake
serving BOTH halves of the real client's surface -- `chat()` for concept
extraction, relation typing and contradiction judging, and `embed()` for
the vectors proximity scores. Patching that single seam is deliberate: it
is the same seam production constructs, so nothing about the wiring under
test is stubbed out.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


# Two concepts written to be topically close, one deliberately unrelated.
# The fake embedder keys on these titles.
_STOICISM = "Stoicism"
_STOIC_ETHICS = "Stoic Ethics"
_CROP_ROTATION = "Medieval Crop Rotation"


def _unit(*leading: float) -> list[float]:
    """An `EMBED_DIM` vector with `leading` up front, L2-normalized.

    Normalized because `graph/proximity.py`'s distance ceiling is only a
    valid cosine conversion on the unit sphere -- the same property
    `tests/unit/llm/test_ollama_embed_norm.py` pins against real Ollama.
    A fake that ignored it would let this test pass for the wrong reason."""
    values = list(leading) + [0.0] * (EMBED_DIM - len(leading))
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


# cosine(stoicism, stoic_ethics) ~= 0.995 -- comfortably above the 0.70
# floor. cosine(either, crop_rotation) == 0.0 -- comfortably below it.
_VECTORS = {
    _STOICISM: _unit(1.0, 0.1),
    _STOIC_ETHICS: _unit(1.0, 0.0),
    _CROP_ROTATION: _unit(0.0, 0.0, 1.0),
}


class _FakeOllama:
    """Structurally an `OllamaClient`: serves `chat()` and `embed()`.

    `chat()` replies are keyed on what the prompt is asking for, so one
    instance can carry the whole run -- extraction during `ingest`, typing
    during `suggest-relations`, judging during `contradictions`."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self) -> None:
        self.embed_calls: list[list[str]] = []
        self.chat_calls: list[list[Message]] = []
        self._titles = [_STOICISM, _STOIC_ETHICS, _CROP_ROTATION]
        self._next_title = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.chat_calls.append(list(messages))
        # `Message` is a TypedDict, not an object -- subscript, never attribute.
        text = " ".join(m["content"] for m in messages)
        if "relation" in text.lower() and "vocabulary" in text.lower():
            return json.dumps(
                {"type": "related_to", "rationale": "both concern the same school"}
            )
        if "contradict" in text.lower():
            return json.dumps(
                {"verdict": "no_contradiction", "rationale": "compatible claims"}
            )
        title = self._titles[min(self._next_title, len(self._titles) - 1)]
        self._next_title += 1
        return json.dumps(
            {
                "extract": True,
                "type": "Concept",
                "title": title,
                "description": f"A description of {title}.",
                "body": f"Elaboration on {title}.",
            }
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        out: list[list[float]] = []
        for text in texts:
            for title, vector in _VECTORS.items():
                if title in text:
                    out.append(vector)
                    break
            else:
                out.append(_unit(0.0, 0.0, 0.0, 1.0))
        return out


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> _FakeOllama:
    fake = _FakeOllama()
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    return fake


def _ingest_three_sources(tmp_path: Path) -> None:
    """Ingest three sources, yielding three concepts: two topically close,
    one unrelated."""
    for index, title in enumerate((_STOICISM, _STOIC_ETHICS, _CROP_ROTATION)):
        src = tmp_path / f"source-{index}.md"
        src.write_text(f"# {title}\n\nRaw source material about {title}.\n", "utf-8")
        result = runner.invoke(app, ["ingest", str(src), "--auto"])
        assert result.exit_code == 0, result.stdout


def test_ingest_produces_concept_to_concept_candidate_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: _FakeOllama
) -> None:
    """The core of #183: after `ingest`, `suggest-relations` must have
    something to work with.

    Today `ingest` never embeds, so `vectors.db` is absent, pass 3 is a
    no-op, and this reports an empty graph. That failure IS the bug."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    _ingest_three_sources(tmp_path)

    assert fake_ollama.embed_calls, (
        "ingest never called embed(): no embeddings means no candidate edges, "
        "which is the #183 symptom"
    )
    assert (tmp_path / ".openkos" / "vectors.db").exists()

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stdout
    assert "No concept relationships in the graph yet." not in result.stdout
    assert "Candidate relations unavailable" not in result.stdout


def test_suggested_type_is_accepted_and_then_contradictions_can_judge_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: _FakeOllama
) -> None:
    """The rest of the chain: a candidate edge is typeable, and once typed
    it becomes a contradiction candidate.

    This is what proves candidate edges feed BOTH consumers #183 named as
    starving, not just the one that surfaced the bug."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    _ingest_three_sources(tmp_path)

    suggest = runner.invoke(app, ["suggest-relations", "--auto"])
    assert suggest.exit_code == 0, suggest.stdout
    assert "related_to" in suggest.stdout

    relate = runner.invoke(
        app,
        # Argument order is (source_id, rel, target_id) -- NOT
        # (source, target, rel).
        [
            "relate",
            "concepts/stoicism",
            "related_to",
            "concepts/stoic-ethics",
            "--auto",
        ],
    )
    assert relate.exit_code == 0, relate.stdout

    # `contradictions` has no --auto: it never gates on a prompt.
    contradictions = runner.invoke(app, ["contradictions"])

    assert contradictions.exit_code == 0, contradictions.stdout
    assert "The graph has no typed edges yet." not in contradictions.stdout


def test_ingest_still_succeeds_when_the_embedder_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open degradation: embeddings are an enhancement, so losing them
    must never cost the user their ingest. The Source and its concept are
    still written, the exit code is still 0, and the failure is reported on
    stderr rather than swallowed silently."""
    from openkos.llm.ollama import OllamaUnavailable

    class _EmbedFailsOllama(_FakeOllama):
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise OllamaUnavailable("connection refused")

    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _EmbedFailsOllama()
    )
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0

    src = tmp_path / "source.md"
    src.write_text(f"# {_STOICISM}\n\nRaw material.\n", "utf-8")
    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0, result.stdout
    assert list((tmp_path / "bundle" / "sources").glob("*.md"))
    assert list((tmp_path / "bundle" / "concepts").glob("*.md"))
    assert "embeddings not updated" in result.stderr
