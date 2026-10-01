"""MCP `pending` lists open queue rows through the disclosure gate
(mvp4 unit 4.6, mcp "`pending` Also Lists Open Queue Rows Through The
Disclosure Gate"): a row naming a concept that is not disclosable is withheld
whole, and no count of withheld rows is present anywhere in the result.
"""

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import queue_producers
from openkos.cli.main import app
from openkos.llm.base import Embedder, LLMBackend
from openkos.mcp import tools
from openkos.state import derived
from openkos.state import pending_queue as pq

runner = CliRunner()

_SENTINEL = "concepts/zz-sentinel-confidential-9f3a"


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


def _no_llm(cfg: config.Config) -> LLMBackend:
    raise AssertionError("pending never calls a model")


def _no_embedder(cfg: config.Config) -> Embedder:
    raise AssertionError("pending never calls an embedder")


def _no_exemption(client: LLMBackend, cfg: config.Config) -> bool:
    raise AssertionError("pending never asks for a local exemption")


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.WorkspaceLayout:
    for name, value in (
        ("GIT_CONFIG_COUNT", "2"),
        ("GIT_CONFIG_KEY_0", "user.name"),
        ("GIT_CONFIG_VALUE_0", "openkos tests"),
        ("GIT_CONFIG_KEY_1", "user.email"),
        ("GIT_CONFIG_VALUE_1", "tests@openkos.invalid"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return config.WorkspaceLayout(tmp_path)


def _concept(layout: config.WorkspaceLayout, concept_id: str, sensitivity: str) -> None:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\nsensitivity: {sensitivity}\n---\n\n# {concept_id}\n",
        encoding="utf-8",
    )


def _enqueue(
    layout: config.WorkspaceLayout, kind: str, targets: tuple[str, ...]
) -> None:
    key = {
        "identity": pq.identity_key(targets),
        "volatility": pq.volatility_key(targets[0] if targets else "Concept"),
    }[kind]
    conn: sqlite3.Connection = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind=kind,
                key_body=key,
                producer="test/1",
                payload="{}",
                targets=targets,
            ),
            commit_section=_section,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def _call(layout: config.WorkspaceLayout) -> dict[str, object]:
    ctx = tools.ToolContext(
        layout=layout,
        expose_confidential=False,
        make_llm=_no_llm,
        make_embedder=_no_embedder,
        local_exemption_for=_no_exemption,
    )
    is_error, result = tools.execute(tools.REGISTRY["pending"], {}, ctx, None)
    assert not is_error
    return result


def test_row_naming_a_confidential_concept_is_withheld_whole(
    layout: config.WorkspaceLayout,
) -> None:
    _concept(layout, "concepts/open-a", "private")
    _concept(layout, "concepts/open-b", "private")
    _concept(layout, _SENTINEL, "confidential")
    _enqueue(layout, "identity", ("concepts/open-a", "concepts/open-b"))
    _enqueue(layout, "identity", ("concepts/open-a", _SENTINEL))

    result = _call(layout)

    wire = json.dumps(result)
    assert _SENTINEL not in wire
    assert "zz-sentinel" not in wire
    queue = result["queue"]
    assert isinstance(queue, dict)
    assert queue["state"] == "present"
    assert queue["rows"] == [
        {
            "kind": "identity",
            "targets": ["concepts/open-a", "concepts/open-b"],
            "resolve": "openkos duplicates --keep-distinct",
        }
    ]
    # No count of withheld rows, in the queue block or anywhere else.
    assert set(queue) == {"state", "rows"}
    assert result["withheld"] == 0


def test_all_rows_withheld_leaves_an_empty_list_not_a_count(
    layout: config.WorkspaceLayout,
) -> None:
    _concept(layout, _SENTINEL, "confidential")
    _enqueue(layout, "volatility", (_SENTINEL,))

    result = _call(layout)

    assert _SENTINEL not in json.dumps(result)
    assert result["queue"] == {"state": "present", "rows": []}
    assert result["withheld"] == 0


def test_absent_queue_says_not_computed(layout: config.WorkspaceLayout) -> None:
    result = _call(layout)

    assert result["queue"] == {"state": "not_computed"}
    assert not layout.findings_db_path.exists()


def test_listing_does_not_change_a_rows_status(layout: config.WorkspaceLayout) -> None:
    _concept(layout, "concepts/open-a", "private")
    _concept(layout, "concepts/open-b", "private")
    _enqueue(layout, "identity", ("concepts/open-a", "concepts/open-b"))
    before = layout.findings_db_path.read_bytes()

    _call(layout)
    _call(layout)

    conn = sqlite3.connect(f"file:{layout.findings_db_path}?mode=ro", uri=True)
    try:
        statuses = [r[0] for r in conn.execute("SELECT status FROM pending_items")]
    finally:
        conn.close()
    assert statuses == ["pending"]
    assert layout.findings_db_path.read_bytes() == before


def test_a_volatility_row_names_no_concept_and_is_listed(
    layout: config.WorkspaceLayout,
) -> None:
    """A type is not a concept: the row declares no subjects, so nothing in it
    can fail the gate."""
    _enqueue(layout, "volatility", ())

    result = _call(layout)

    assert result["queue"] == {
        "state": "present",
        "rows": [{"kind": "volatility", "targets": [], "resolve": "openkos curate"}],
    }


def _enqueue_watch_refusal(layout: config.WorkspaceLayout, source_id: str) -> None:
    conn: sqlite3.Connection = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        queue_producers.enqueue_watch_refusal(
            conn,
            source_id=source_id,
            inbox_path="inbox-file.md",
            digest="0" * 64,
            reason=queue_producers.REASON_SOURCE_CHANGED,
            bundle_dir=layout.bundle_dir,
            commit_section=_section,
        )
    finally:
        conn.close()


def test_a_watch_refusal_for_a_confidential_source_is_withheld(
    layout: config.WorkspaceLayout,
) -> None:
    """The refused Source's id is a target like any other: a confidential Source
    must not cross the boundary, while a disclosable one is listed."""
    hidden_source = "sources/zz-sentinel-confidential-9f3a"
    _concept(layout, "sources/open-note", "private")
    _concept(layout, hidden_source, "confidential")
    _enqueue_watch_refusal(layout, "sources/open-note")
    _enqueue_watch_refusal(layout, hidden_source)

    result = _call(layout)

    assert "zz-sentinel" not in json.dumps(result)
    queue = result["queue"]
    assert isinstance(queue, dict)
    assert queue["rows"] == [
        {
            "kind": "watch_refusal",
            "targets": ["sources/open-note"],
            "resolve": "openkos ingest <the refused file>",
        }
    ]
