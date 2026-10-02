"""A watched file edited after import revises the concepts its first version
produced instead of writing `<slug>-N` copies (#1268, #1259): the real watch
job over a real workspace, with the extractor patched to a fixed answer."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import ingest as application_ingest
from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import ExtractionResult
from openkos.model import okf
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace
from tests.unit.application.test_watch import _Env
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity


class _Extractor:
    def __init__(self, *results: ExtractionResult) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> concept_mod.ExtractionOutcome:
        self.calls += 1
        return concept_mod.ExtractionOutcome(
            objects=list(self.results),
            report=concept_mod.ExtractionReport(
                produced=len(self.results),
                retained=len(self.results),
                chunks=1,
                runs=1,
            ),
        )


def _object(
    title: str = "Agent Skills",
    type_: str = "Concept",
    body: str = "Skills ship scripts.",
) -> ExtractionResult:
    return ExtractionResult(
        type=type_, title=title, description=f"About {title}.", body=body
    )


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> _Env:
    root = make_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return _Env(root, inbox)


def _extract(monkeypatch: pytest.MonkeyPatch, *results: ExtractionResult) -> _Extractor:
    extractor = _Extractor(*results)
    monkeypatch.setattr(application_ingest, "extract_concept_union", extractor)
    return extractor


def _import_once(env: _Env, path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    env.job()
    env.settle()
    env.job()


def _concepts(env: _Env, link_dir: str = "concepts") -> list[str]:
    return sorted(p.name for p in (env.root / "bundle" / link_dir).glob("*.md"))


def _meta(env: _Env, concept_id: str) -> dict[str, object]:
    path = env.root / "bundle" / f"{concept_id}.md"
    return okf.load_frontmatter(path.read_text(encoding="utf-8"))[0]


def _rows(env: _Env) -> list[pq.PendingItem]:
    path = config.WorkspaceLayout(env.root).findings_db_path
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def test_an_edit_revises_the_first_versions_concept_and_writes_no_copy(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extract(monkeypatch, _object())
    path = env.inbox / "a.md"
    _import_once(env, path, "Notes about agent skills.\n")
    assert _concepts(env) == ["agent-skills.md"]

    _import_once(env, path, "Notes about agent skills.\nAnd one more section.\n")

    assert env.raw() == ["a-2.md", "a.md"]
    assert _concepts(env) == ["agent-skills.md"]
    meta = _meta(env, "concepts/agent-skills")
    assert meta["provenance"] == ["sources/a", "sources/a-2"]
    assert meta["version"] == 2


def test_a_concept_only_the_edit_adds_is_created_normally(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extract(monkeypatch, _object())
    path = env.inbox / "a.md"
    _import_once(env, path, "Notes about agent skills.\n")
    _extract(monkeypatch, _object(), _object("MCP Server", body="A server."))

    _import_once(env, path, "Notes about agent skills and MCP.\n")

    assert _concepts(env) == ["agent-skills.md", "mcp-server.md"]
    assert _meta(env, "concepts/mcp-server")["provenance"] == ["sources/a-2"]
    assert _meta(env, "concepts/mcp-server")["version"] == 1


def test_the_supersession_is_still_only_proposed(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extract(monkeypatch, _object())
    path = env.inbox / "a.md"
    _import_once(env, path, "Notes about agent skills.\n")

    _import_once(env, path, "Notes about agent skills.\nMore.\n")

    (row,) = _rows(env)
    assert row.kind == "relation_type"
    assert row.targets == ("sources/a-2", "sources/a")
    for source in ("a", "a-2"):
        text = (env.root / "bundle" / "sources" / f"{source}.md").read_text(
            encoding="utf-8"
        )
        assert "supersedes" not in text
        assert "status: deprecated" not in text


def test_a_resaved_version_converges_with_no_model_call(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = _extract(monkeypatch, _object())
    path = env.inbox / "a.md"
    _import_once(env, path, "Notes about agent skills.\n")
    edited = "Notes about agent skills.\nMore.\n"
    _import_once(env, path, edited)
    calls = extractor.calls
    revised = (env.root / "bundle" / "concepts" / "agent-skills.md").read_bytes()

    _import_once(env, path, edited)

    assert extractor.calls == calls
    assert (
        env.root / "bundle" / "concepts" / "agent-skills.md"
    ).read_bytes() == revised
    assert env.raw() == ["a-2.md", "a.md"]


def test_a_person_still_forks_across_versions(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extract(monkeypatch, _object("Ana Ruiz", "Person"))
    path = env.inbox / "a.md"
    _import_once(env, path, "Notes about Ana.\n")

    _import_once(env, path, "Notes about Ana.\nMore.\n")

    assert _concepts(env, "people") == ["ana-ruiz-2.md", "ana-ruiz.md"]
