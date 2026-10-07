"""`openkos query --json` (#1345): one machine-readable object on stdout.

Observability only. Every test drives the REAL `answer()` through the CLI with
a scripted fake chat client -- no network, no Ollama.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from openkos.state import fts, vectorstore
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


class _ScriptedClient:
    """Chat + embed stand-in. `replies` are consumed in order, one per chat
    call; the class attribute is reset by `_install`."""

    locality = LOCAL_BACKEND_LOCALITY
    replies: ClassVar[list[str]] = []
    seen: ClassVar[list[list[dict[str, str]]]] = []

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[dict[str, str]]) -> str:
        type(self).seen.append(list(messages))
        return type(self).replies.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]


def _install(monkeypatch: pytest.MonkeyPatch, *replies: str) -> type[_ScriptedClient]:
    client: type[_ScriptedClient] = type(
        "Client", (_ScriptedClient,), {"replies": list(replies), "seen": []}
    )
    monkeypatch.setattr("openkos.cli.main.OllamaClient", client)
    return client


def _doc(
    root: Path, rel: str, title: str, body: str, sensitivity: str = "private"
) -> None:
    path = root / "bundle" / f"{rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\ndescription: ''\n"
        f"sensitivity: {sensitivity}\n---\n{body}\n",
        encoding="utf-8",
    )


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docs: list[tuple[str, str, str, str]] | None = None,
) -> Path:
    """An initialized workspace holding `docs` (rel, title, body, sensitivity),
    with the lexical index built and an empty vector store."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    for rel, title, body, sens in docs or [
        ("concepts/alpha", "Alpha", "dichotomyzz alpha body", "private"),
        ("concepts/beta", "Beta", "dichotomyzz beta body", "private"),
    ]:
        _doc(tmp_path, rel, title, body, sens)
    vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db").close()
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", tmp_path / "bundle")
    sqlite_graph.write_graph_store(
        tmp_path / ".openkos" / "graph.db", tmp_path / "bundle"
    )
    return tmp_path


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(result: Any) -> Any:
    return json.loads(result.stdout)


def test_answered_query_emits_one_json_object_with_the_v1_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    client = _install(monkeypatch, "quoted sentence", "The answer.\nUSED: 1")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert result.stdout.endswith("}\n")
    assert list(payload) == [
        "schema_version",
        "openkos_version",
        "question",
        "limit",
        "outcome",
        "answer",
        "sufficiency",
        "attribution",
        "context_blocks",
        "omitted",
        "retrieved",
        "citations",
        "counts",
        "withheld",
        "llm",
        "prompts",
    ]
    assert payload["schema_version"] == 1
    assert payload["question"] == "dichotomyzz"
    assert payload["limit"] == 5
    assert payload["outcome"] == "answered"
    assert payload["answer"] == "The answer."
    assert payload["sufficiency"] == {
        "enabled": True,
        "invoked": True,
        "refused": False,
        "degraded": False,
        "raw_reply": "quoted sentence",
    }
    assert payload["attribution"] == {"status": "reported", "used_indices": [1]}
    blocks = payload["context_blocks"]
    assert [b["index"] for b in blocks] == [1, 2]
    assert {b["concept_id"] for b in blocks} == {"concepts/alpha", "concepts/beta"}
    assert all(
        set(b) == {"index", "concept_id", "title", "excerpted", "text"} for b in blocks
    )
    assert payload["omitted"] == []
    retrieved = payload["retrieved"]
    assert [r["rank"] for r in retrieved] == [1, 2]
    assert all(
        set(r) == {"rank", "concept_id", "title", "fts_rank", "dense_rank", "rrf_score"}
        for r in retrieved
    )
    assert all(
        r["dense_rank"] is None and isinstance(r["fts_rank"], int) for r in retrieved
    )
    citations = payload["citations"]
    assert [c["concept_id"] for c in citations] == [blocks[0]["concept_id"]]
    assert payload["counts"] == {
        "fts_hits": 2,
        "dense_hits": 0,
        "fused": 2,
        "context_blocks": 2,
    }
    assert payload["withheld"] == 0
    # Sent: sufficiency check, then synthesis.
    sufficiency_call, synthesis_call = client.seen
    assert payload["prompts"] == {
        "system_sha256": _sha(synthesis_call[0]["content"]),
        "user_sha256": _sha(synthesis_call[1]["content"]),
        "sufficiency_sha256": _sha(sufficiency_call[0]["content"]),
    }
    assert synthesis_call[1]["content"] == sufficiency_call[1]["content"]


def test_context_block_text_reassembles_into_the_user_prompt_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    client = _install(monkeypatch, "q", "Answer.\nUSED: 2")

    payload = _json(runner.invoke(app, ["query", "dichotomyzz", "--json"]))

    blocks = payload["context_blocks"]
    numbered = [f"[{b['index']}] {b['text']}" for b in blocks]
    prompt = "CONTEXT:\n\n" + "\n\n".join(numbered) + "\n\nQUESTION:\ndichotomyzz"
    assert prompt == client.seen[-1][1]["content"]
    prompts = payload["prompts"]
    assert prompts["user_sha256"] == _sha(prompt)


def test_llm_section_reports_what_is_sent_and_null_for_what_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, "q", "Answer.")

    payload = _json(runner.invoke(app, ["query", "dichotomyzz", "--json"]))

    llm = payload["llm"]
    assert list(llm) == [
        "backend",
        "model",
        "embedding_model",
        "num_ctx",
        "num_predict",
        "temperature",
        "seed",
    ]
    assert llm["backend"] == "ollama"
    assert llm["temperature"] is None
    assert llm["seed"] is None
    assert llm["model"]
    assert llm["embedding_model"]


def test_llm_section_follows_pinned_sampling_and_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    config_path = root / "openkos.yaml"
    text = config_path.read_text(encoding="utf-8")
    for old, new in (
        ("max_generation_tokens: 8192", "max_generation_tokens: 2048"),
        ("context_window: 12288", "context_window: 16384"),
    ):
        assert old in text
        text = text.replace(old, new)
    config_path.write_text(text + "\ntemperature: 0\nseed: 7\n", encoding="utf-8")
    _install(monkeypatch, "q", "Answer.")

    llm = _json(runner.invoke(app, ["query", "dichotomyzz", "--json"]))["llm"]

    assert llm["temperature"] == 0
    assert llm["seed"] == 7
    assert llm["num_ctx"] == 16384
    assert llm["num_predict"] == 2048


def test_no_match_emits_json_and_keeps_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch)

    result = runner.invoke(app, ["query", "zzzznotpresent", "--json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["outcome"] == "no_match"
    assert payload["answer"] is None
    assert payload["context_blocks"] == []
    assert payload["retrieved"] == []
    assert payload["citations"] == []
    assert payload["attribution"] == {"status": "absent", "used_indices": []}
    assert payload["sufficiency"] == {
        "enabled": True,
        "invoked": False,
        "refused": False,
        "degraded": False,
        "raw_reply": None,
    }
    assert payload["prompts"] == {
        "system_sha256": None,
        "user_sha256": None,
        "sufficiency_sha256": None,
    }
    assert payload["counts"] == {
        "fts_hits": 0,
        "dense_hits": 0,
        "fused": 0,
        "context_blocks": 0,
    }


def test_sufficiency_refusal_is_its_own_outcome_and_lists_what_was_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    client = _install(monkeypatch, "NONE")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["outcome"] == "no_answer_in_context"
    assert payload["answer"] is None
    assert payload["sufficiency"] == {
        "enabled": True,
        "invoked": True,
        "refused": True,
        "degraded": False,
        "raw_reply": "NONE",
    }
    blocks = payload["context_blocks"]
    assert len(blocks) == 2
    prompts = payload["prompts"]
    assert prompts["system_sha256"] is None
    assert prompts["user_sha256"] == _sha(client.seen[0][1]["content"])
    assert prompts["sufficiency_sha256"] == _sha(client.seen[0][0]["content"])
    assert payload["citations"] == []


def test_limit_is_echoed_and_bounds_retrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [
        (f"concepts/c{n}", f"C{n}", f"dichotomyzz body {n}", "private")
        for n in range(6)
    ]
    _workspace(tmp_path, monkeypatch, docs)
    _install(monkeypatch, "q", "Answer.")

    payload = _json(
        runner.invoke(app, ["query", "dichotomyzz", "--json", "--limit", "3"])
    )

    assert payload["limit"] == 3
    retrieved = payload["retrieved"]
    assert 0 < len(retrieved) <= 3
    assert payload["counts"]["fused"] <= 3


@pytest.mark.parametrize(
    "extra",
    [
        ["--save"],
        ["--auto"],
        ["--title", "T"],
        ["--description", "D"],
        ["--allow-unattributed"],
        ["--save", "--auto"],
    ],
)
def test_json_with_save_or_a_save_only_flag_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    _workspace(tmp_path, monkeypatch)
    client = _install(monkeypatch, "q", "Answer.")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json", *extra])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--json" in result.stderr
    assert client.seen == []
    assert not (tmp_path / "bundle" / "insights").exists()


def test_json_stdout_is_only_the_object_and_diagnostics_stay_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, "q", "Answer with no attribution line.")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json"])

    assert result.exit_code == 0
    json.loads(result.stdout)
    assert result.stderr.startswith("retrieval: 2 FTS + 0 dense")
    assert "notice -- the answer reported no attribution line" in result.stderr


def test_without_json_stdout_stderr_and_exit_code_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Golden output captured from `openkos query` BEFORE `--json` existed."""
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, "q", "The answer.\nUSED: 1", "NONE")

    answered = runner.invoke(app, ["query", "dichotomyzz"])
    no_match = runner.invoke(app, ["query", "zzzznotpresent"])
    refused = runner.invoke(app, ["query", "dichotomyzz"])

    assert (answered.exit_code, answered.stdout, answered.stderr) == GOLDEN_ANSWERED
    assert (no_match.exit_code, no_match.stdout, no_match.stderr) == GOLDEN_NO_MATCH
    assert (refused.exit_code, refused.stdout, refused.stderr) == GOLDEN_REFUSED


GOLDEN_ANSWERED: tuple[int, str, str] = (
    0,
    "The answer.\n\nCitations:\n  → concepts/alpha (Alpha)\n",
    "retrieval: 2 FTS + 0 dense → 2 fused → LLM invoked → 1 cited\n",
)
GOLDEN_NO_MATCH: tuple[int, str, str] = (
    0,
    "No matching concepts were found in the compiled bundle for this question. "
    "Try different wording, or run `openkos status` to see what the bundle "
    "contains.\n",
    "retrieval: 0 FTS + 0 dense → 0 fused → LLM skipped → 0 cited\n",
)
GOLDEN_REFUSED: tuple[int, str, str] = (
    0,
    "Found 2 matching concepts, but none of them answers this question -- the "
    "compiled bundle does not cover it.\n"
    "Next: ingest a source that covers it, or set `sufficiency_check: false` in "
    "openkos.yaml to answer regardless.\n\nRetrieved:\n"
    "  → concepts/alpha (Alpha)\n  → concepts/beta (Beta)\n",
    "retrieval: 2 FTS + 0 dense → 2 fused → LLM refused → 0 cited\n",
)


_SECRET_DOCS = [
    ("concepts/alpha", "Alpha", "dichotomyzz alpha body", "private"),
    ("concepts/vault", "SecretTitleZ", "dichotomyzz secretbodyz", "confidential"),
]


def _assert_nothing_confidential(stdout: str) -> None:
    for needle in ("SecretTitleZ", "secretbodyz", "concepts/vault"):
        assert needle not in stdout


def test_a_confidential_concept_dropped_by_retrieval_is_only_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, _SECRET_DOCS)
    disable_local_exemption(root)
    _install(monkeypatch, "a quoted sentence", "Answer.\nUSED: 1")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json"])

    assert result.exit_code == 0
    _assert_nothing_confidential(result.stdout)
    payload = _json(result)
    assert payload["outcome"] == "answered"
    assert payload["withheld"] == 1


def test_a_confidential_concept_reaching_the_prompt_withholds_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local-backend exemption lets the concept into the prompt without
    `--include-confidential`. The machine surface applies the MCP rule: the
    wording may have drawn on it, so the answer and everything naming it are
    withheld, and only a count remains."""
    _workspace(tmp_path, monkeypatch, _SECRET_DOCS)
    _install(monkeypatch, "secretbodyz is the quoted sentence", "Answer.\nUSED: 1")

    result = runner.invoke(app, ["query", "dichotomyzz", "--json"])

    assert result.exit_code == 0
    _assert_nothing_confidential(result.stdout)
    payload = _json(result)
    assert payload["outcome"] == "withheld"
    assert payload["answer"] is None
    assert payload["withheld"] >= 1
    assert payload["sufficiency"]["raw_reply"] is None
    blocks = payload["context_blocks"]
    assert [b["concept_id"] for b in blocks] == ["concepts/alpha"]
    assert payload["counts"]["context_blocks"] == 2


def test_an_uncited_confidential_block_still_withholds_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model reports drawing on nothing, so no citation names the
    concept -- but its block was in the prompt, so the wording may have."""
    _workspace(tmp_path, monkeypatch, _SECRET_DOCS)
    _install(monkeypatch, "a quoted sentence", "Answer.\nUSED: NONE")

    payload = _json(runner.invoke(app, ["query", "dichotomyzz", "--json"]))

    assert payload["citations"] == []
    assert payload["attribution"] == {"status": "none", "used_indices": []}
    assert payload["outcome"] == "withheld"
    assert payload["answer"] is None


def test_include_confidential_discloses_the_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, _SECRET_DOCS)
    disable_local_exemption(root)
    _install(monkeypatch, "secretbodyz quoted", "Answer.\nUSED: 1 2")

    result = runner.invoke(
        app, ["query", "dichotomyzz", "--json", "--include-confidential"]
    )

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["outcome"] == "answered"
    assert payload["withheld"] == 0
    assert "concepts/vault" in {r["concept_id"] for r in payload["retrieved"]}
    assert any("secretbodyz" in b["text"] for b in payload["context_blocks"])
