"""A person's CLI run is never budget-limited, whatever `unattended:` says.

The `unattended:` section of `openkos.yaml` bounds only the job runner
(`application/budget.py`). `--auto` means "skip the question", never "subject
to the daemon's budget", so `ingest`, `curate`, `suggest-relations` and
`revisions` must behave EXACTLY as they do without the section: same stdout,
same stderr, same exit code, same files, the same number of model calls, and
no `jobs.db` (not even an empty one).

Each scenario runs twice in two fresh workspaces, once plain and once with a
restrictive section whose limits the scenario would exceed, and compares the
two complete outcomes. The comparison is between arms, not against a golden,
so it holds whatever the verbs print today.
"""

import json
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from tests.unit.cli.conftest import snapshot_bytes
from tests.unit.cli.golden_support import normalise
from tests.unit.cli.test_revisions import (
    _patch_llm as _patch_revisions_llm,
)
from tests.unit.cli.test_revisions import (
    _ScriptedLLM,
    _seed_decision_and_vector,
    _verdict_reply,
)
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()

_RESTRICTIVE = (
    "\nunattended:\n"
    "  max_sources_per_pass: 2\n"
    "  max_calls_per_pass: 1\n"
    "  max_calls_per_day: 1\n"
)
"""Limits every scenario below exceeds: a budgeted run would defer sources,
stop after one call, or refuse outright."""

_TITLES = ("Stoicism", "Stoic Ethics", "Stoic Virtue", "Stoic Logic", "Stoic Physics")


def _unit(*leading: float) -> list[float]:
    values = list(leading) + [0.0] * (EMBED_DIM - len(leading))
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


class _FakeBackend:
    """One fake serving the whole chain: extraction, relation typing,
    contradiction judging, and embeddings (every title close to every other,
    so proximity nominates edges). `reset` zeroes it between the two arms so
    their call counts are comparable."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self) -> None:
        self.chat_calls: list[list[Message]] = []
        self._next_title = 0

    def reset(self) -> None:
        self.chat_calls.clear()
        self._next_title = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.chat_calls.append(list(messages))
        text = " ".join(m["content"] for m in messages).lower()
        if "relation" in text and "vocabulary" in text:
            return json.dumps({"type": "related_to", "rationale": "same school"})
        if "contradict" in text:
            return json.dumps({"verdict": "no_contradiction", "rationale": "ok"})
        title = _TITLES[min(self._next_title, len(_TITLES) - 1)]
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
        out: list[list[float]] = []
        for text in texts:
            for index, title in enumerate(_TITLES):
                if title in text:
                    out.append(_unit(1.0, 0.05 * index))
                    break
            else:
                out.append(_unit(0.0, 0.0, 1.0))
        return out


_STAMP = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ|(?<=origin_key: )[0-9a-f]{32}")


def _unstamp(data: bytes) -> bytes:
    """Mask the two values that differ between two otherwise identical runs in
    two directories: `generated.at`-style UTC stamps and the `origin_key`,
    which hashes the source's absolute path. Neither is what this module
    pins."""
    return _STAMP.sub(b"<stamp>", data)


Outcome = dict[str, object]
Scenario = Callable[[Path, pytest.MonkeyPatch], list[Outcome]]


def _invoke(root: Path, args: list[str]) -> Outcome:
    result = runner.invoke(app, args)
    return {
        "args": args,
        "exit_code": result.exit_code,
        "stdout": normalise(result.stdout, root),
        "stderr": normalise(result.stderr, root),
    }


def _files(root: Path) -> dict[str, object]:
    """Every workspace file except `openkos.yaml` (the one file the arms differ
    in by construction) as bytes; the derived SQLite caches and lock files are
    compared by presence only, since their bytes embed per-run state."""
    out: dict[str, object] = {}
    for rel, entry in snapshot_bytes(root).items():
        if rel.as_posix() == "openkos.yaml":
            continue
        derived = rel.parts[0] == ".openkos" and rel.suffix in {".db", ".lock"}
        out[rel.as_posix()] = (
            None if derived else _unstamp(entry) if isinstance(entry, bytes) else entry
        )
    return out


def _arm(
    base: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    *,
    restrict: bool,
) -> tuple[list[Outcome], dict[str, object]]:
    root = base / name
    root.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(app, ["init"]).exit_code == 0
    if restrict:
        yaml_path = root / "openkos.yaml"
        yaml_path.write_text(
            yaml_path.read_text(encoding="utf-8") + _RESTRICTIVE, encoding="utf-8"
        )
        restricted = config.read_config(root).unattended
        assert restricted.max_calls_per_pass == 1
    outcomes = scenario(root, monkeypatch)
    return outcomes, _files(root)


def _assert_unbudgeted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    calls: Callable[[], int],
    *,
    minimum_calls: int,
) -> None:
    plain, plain_files = _arm(tmp_path, "plain", monkeypatch, scenario, restrict=False)
    plain_calls = calls()
    limited, limited_files = _arm(
        tmp_path, "limited", monkeypatch, scenario, restrict=True
    )
    limited_calls = calls()

    assert limited == plain
    assert limited_files == plain_files
    # The scenario really exceeds the restrictive limits, so a budgeted run
    # could not have matched it: the carried value, not just the verdict.
    assert plain_calls >= minimum_calls > 1
    assert limited_calls == plain_calls
    assert not (tmp_path / "plain" / ".openkos" / "jobs.db").exists()
    assert not (tmp_path / "limited" / ".openkos" / "jobs.db").exists()


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> _FakeBackend:
    fake = _FakeBackend()
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    return fake


def _write_notes(root: Path) -> None:
    notes = root / "notes"
    notes.mkdir()
    for index, title in enumerate(_TITLES):
        (notes / f"n{index}.md").write_text(
            f"# Study Notes {index}\n\nRaw material about {title}.\n",
            encoding="utf-8",
        )


def test_ingest_batch_auto_ingests_all_five_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: _FakeBackend,
    pinned_git_identity: None,
) -> None:
    """`ingest notes/ --auto` over five files with `max_sources_per_pass: 2`
    ingests all five, reports no deferral and exits as the plain run does."""

    def scenario(root: Path, mp: pytest.MonkeyPatch) -> list[Outcome]:
        backend.reset()
        _write_notes(root)
        outcome = _invoke(root, ["ingest", "notes", "--auto"])
        assert outcome["exit_code"] == 0, outcome
        for index in range(5):
            assert (root / "raw" / f"n{index}.md").is_file()
            assert (root / "bundle" / "sources" / f"n{index}.md").is_file()
        combined = f"{outcome['stdout']}{outcome['stderr']}".lower()
        assert "defer" not in combined
        assert "budget" not in combined
        return [outcome]

    _assert_unbudgeted(
        tmp_path,
        monkeypatch,
        scenario,
        lambda: len(backend.chat_calls),
        minimum_calls=5,
    )


def test_suggest_relations_and_curate_auto_are_unbudgeted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: _FakeBackend,
    pinned_git_identity: None,
) -> None:
    """After a five-source ingest, `suggest-relations --auto` and
    `curate --auto` run identically with and without the section."""

    def scenario(root: Path, mp: pytest.MonkeyPatch) -> list[Outcome]:
        backend.reset()
        _write_notes(root)
        outcomes = [
            _invoke(root, ["ingest", "notes", "--auto"]),
            _invoke(root, ["suggest-relations", "--auto"]),
            _invoke(root, ["curate", "--auto"]),
        ]
        assert [o["exit_code"] for o in outcomes] == [0, 0, 0], outcomes
        assert "related_to" in str(outcomes[1]["stdout"])
        return outcomes

    _assert_unbudgeted(
        tmp_path,
        monkeypatch,
        scenario,
        lambda: len(backend.chat_calls),
        minimum_calls=8,
    )


def test_revisions_auto_is_unbudgeted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_git_identity: None,
) -> None:
    """`revisions --auto` judges both candidate pairs although
    `max_calls_per_pass` is 1."""
    fakes: list[_ScriptedLLM] = []

    def scenario(root: Path, mp: pytest.MonkeyPatch) -> list[Outcome]:
        fake = _ScriptedLLM([_verdict_reply("reaffirms", 0.9)] * 2)
        fakes.append(fake)
        _patch_revisions_llm(mp, fake)
        for dim, (first, second) in enumerate((("a", "b"), ("c", "d"))):
            _seed_decision_and_vector(
                root, f"decisions/{first}", dim, title=f"Adopt {first}"
            )
            _seed_decision_and_vector(
                root, f"decisions/{second}", dim, title=f"Adopt {second}"
            )
        outcome = _invoke(root, ["revisions", "--auto"])
        assert outcome["exit_code"] == 0, outcome
        return [outcome]

    _assert_unbudgeted(
        tmp_path, monkeypatch, scenario, lambda: len(fakes[-1].calls), minimum_calls=2
    )
