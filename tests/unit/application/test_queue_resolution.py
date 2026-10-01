"""Resolution of pending-work rows inside the shared write cores (#1141,
ADR-0037, unit 4.3): a human-path write that performs what an open row proposes
moves that row to `applied` (`as_proposed` or `modified`) or `declined` in the
same commit phase.

Every test drives the real core over a real workspace and reads the row back
from `findings.db`, asserting the carried resolution rather than a verdict.
"""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import (
    contradictions_service,
    duplicates_service,
    reconcile_service,
)
from openkos.application import lifecycle as application_lifecycle
from openkos.cli.main import app
from openkos.model import okf
from openkos.state import derived
from openkos.state import pending_queue as pq
from openkos.state.vectorstore import content_hash
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity
from tests.unit.cli.test_ingest import _init_workspace
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return tmp_path


def _concept(root: Path, concept_id: str, body: str = "Body.") -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": concept_id}, f"# {concept_id}\n\n{body}\n"
        ),
        encoding="utf-8",
    )


def _seed(root: Path, proposal: pq.Proposal) -> None:
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.upsert_proposal(
            conn,
            proposal,
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def _items(root: Path) -> list[pq.PendingItem]:
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def _only(root: Path) -> pq.PendingItem:
    items = _items(root)
    assert len(items) == 1
    return items[0]


def _identity_row(members: tuple[str, ...]) -> pq.Proposal:
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(members),
        producer="duplicates/1",
        payload=json.dumps({"member_ids": list(members)}),
        targets=members,
    )


def _merge(root: Path, survivor_id: str, absorbed_id: str) -> None:
    bundle_dir = root / "bundle"
    survivor_path, survivor = application_lifecycle.resolve_concept_path(
        bundle_dir, survivor_id
    )
    absorbed_path, absorbed = application_lifecycle.resolve_concept_path(
        bundle_dir, absorbed_id
    )
    prepared = application_lifecycle.prepare_merge(
        bundle_dir,
        bundle_dir / "index.md",
        bundle_dir / "log.md",
        survivor_path,
        absorbed_path,
        survivor,
        absorbed,
        root,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    application_lifecycle.merge_core(
        bundle_dir, bundle_dir / "index.md", bundle_dir / "log.md", prepared
    )


# -- merge (identity rows) --------------------------------------------------


def test_merge_in_the_proposed_direction_resolves_the_identity_row_as_proposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/rich", body="A much longer body " * 10)
    _concept(root, "concepts/thin", body="Short.")
    _seed(root, _identity_row(("concepts/rich", "concepts/thin")))

    _merge(root, "concepts/rich", "concepts/thin")

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", "as_proposed")


def test_merge_against_the_proposed_direction_resolves_the_row_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/rich", body="A much longer body " * 10)
    _concept(root, "concepts/thin", body="Short.")
    _seed(root, _identity_row(("concepts/rich", "concepts/thin")))

    _merge(root, "concepts/thin", "concepts/rich")

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", "modified")


def test_merge_leaves_an_identity_row_over_a_different_member_set_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    for name in ("a", "b", "c"):
        _concept(root, f"concepts/{name}")
    _seed(root, _identity_row(("concepts/a", "concepts/b", "concepts/c")))

    _merge(root, "concepts/a", "concepts/b")

    assert _only(root).status == "pending"


# -- relate (relation_type rows) --------------------------------------------


def _relation_row(source: str, target: str, rel: str) -> pq.Proposal:
    return pq.Proposal(
        kind="relation_type",
        key_body=pq.relation_type_key(source, target),
        producer="suggest-relations/1",
        payload=json.dumps(
            {
                "source_id": source,
                "target_id": target,
                "effective_source_id": source,
                "effective_target_id": target,
                "suggested_type": rel,
            }
        ),
        targets=(source, target),
    )


def _relate(root: Path, source_id: str, rel: str, target_id: str) -> None:
    bundle_dir = root / "bundle"
    source_path, source = application_lifecycle.resolve_concept_path(
        bundle_dir, source_id
    )
    target_path, target = application_lifecycle.resolve_concept_path(
        bundle_dir, target_id
    )
    log_path = bundle_dir / "log.md"
    prepared = application_lifecycle.prepare_relate(
        source_path,
        log_path,
        source,
        target,
        rel,
        root,
        now=datetime(2026, 1, 1, tzinfo=UTC),
        target_path=target_path,
    )
    application_lifecycle.relate_core(
        source_path, log_path, prepared, target_path=target_path
    )


@pytest.mark.parametrize(
    ("applied_type", "resolution"),
    [("depends_on", "as_proposed"), ("references", "modified")],
)
def test_relate_resolves_the_relation_row_by_whether_the_type_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    applied_type: str,
    resolution: str,
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    _seed(root, _relation_row("concepts/a", "concepts/b", "depends_on"))

    _relate(root, "concepts/a", applied_type, "concepts/b")

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", resolution)


def test_relate_in_the_other_direction_resolves_the_row_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    _seed(root, _relation_row("concepts/a", "concepts/b", "depends_on"))

    _relate(root, "concepts/b", "depends_on", "concepts/a")

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", "modified")


# -- set-volatility (volatility rows) ---------------------------------------


def _volatility_row(type_name: str, tier: str) -> pq.Proposal:
    return pq.Proposal(
        kind="volatility",
        key_body=pq.volatility_key(type_name),
        producer="suggest-volatility/1",
        payload=json.dumps(
            {
                "type_name": type_name,
                "current_default": "snapshot",
                "suggested_tier": tier,
            }
        ),
        targets=(),
    )


@pytest.mark.parametrize(
    ("applied_tier", "resolution"),
    [("static", "as_proposed"), ("volatile", "modified")],
)
def test_set_volatility_resolves_the_volatility_row_by_whether_the_tier_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    applied_tier: str,
    resolution: str,
) -> None:
    root = _init(tmp_path, monkeypatch)
    _seed(root, _volatility_row("Concept", "static"))
    config_path = config.WorkspaceLayout(root).config_path
    prepared = application_lifecycle.prepare_set_volatility(
        config_path, "Concept", applied_tier
    )

    application_lifecycle.set_volatility_core(config_path, prepared)

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", resolution)


# -- reconcile (contradiction rows) -----------------------------------------


def _contradiction_row(
    pair: tuple[str, str], merged_absorbed_id: str | None = None
) -> pq.Proposal:
    return pq.Proposal(
        kind="contradiction",
        key_body=pq.contradiction_key(pair, merged_absorbed_id),
        producer="contradictions/1",
        payload=json.dumps({"pair_ids": list(pair)}),
        targets=pair,
        merged_absorbed_id=merged_absorbed_id,
    )


def _reconcile(root: Path, id_a: str, id_b: str) -> None:
    reconcile_service.reconcile_concepts(
        root,
        id_a,
        id_b,
        auto=True,
        ports=reconcile_service.ReconcilePorts(autocommit=lambda *a: None),
    )


def test_reconcile_resolves_the_typed_edge_contradiction_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    pair = ("concepts/a", "concepts/b")
    _seed(root, _contradiction_row(pair))

    _reconcile(root, "concepts/b", "concepts/a")

    row = _only(root)
    assert (row.status, row.resolution) == ("applied", "as_proposed")
    assert row.decision_key == f"contradiction:{pq.contradiction_key(pair, None)}"


def test_reconcile_leaves_a_merged_body_contradiction_row_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    _seed(root, _contradiction_row(("concepts/a", "concepts/b"), "concepts/gone"))

    _reconcile(root, "concepts/a", "concepts/b")

    assert _only(root).status == "pending"


# -- decline / keep-distinct writers ----------------------------------------


def test_keep_distinct_declines_the_identity_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    members = ("concepts/a", "concepts/b")
    _seed(root, _identity_row(members))

    duplicates_service.record_identity_ruling(
        root, list(members), flag="--keep-distinct", target_state="declined"
    )

    row = _only(root)
    assert (row.status, row.resolution) == ("declined", "declined")


def test_reopening_an_identity_ruling_does_not_resolve_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    members = ("concepts/a", "concepts/b")
    _seed(root, _identity_row(members))

    duplicates_service.record_identity_ruling(
        root, list(members), flag="--reopen", target_state="open"
    )

    assert _only(root).status == "pending"


def test_decline_resolves_only_the_contradiction_row_it_addresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    pair = ("concepts/a", "concepts/b")
    _seed(root, _contradiction_row(pair))
    _seed(root, _contradiction_row(pair, "concepts/gone"))

    contradictions_service.record_contradiction_decision(
        root, pair, None, target_state="declined"
    )

    by_key = {item.decision_key: item for item in _items(root)}
    typed = by_key[f"contradiction:{pq.contradiction_key(pair, None)}"]
    merged = by_key[f"contradiction:{pq.contradiction_key(pair, 'concepts/gone')}"]
    assert (typed.status, typed.resolution) == ("declined", "declined")
    assert merged.status == "pending"


# -- ingest (watch_refusal rows) --------------------------------------------


class _NoExtractLLM:
    locality = LOCAL_BACKEND_LOCALITY

    def chat(self, messages: object) -> str:
        return '{"extract": false}'


def _refusal_row(source_id: str, digest: str) -> pq.Proposal:
    return pq.Proposal(
        kind="watch_refusal",
        key_body=pq.watch_refusal_key(source_id),
        producer="watch/1",
        payload=json.dumps({"reason": "source changed after import"}),
        targets=(source_id,),
        input_digests=(pq.InputDigest(source_id, digest),),
    )


def test_ingest_landing_the_refused_bytes_applies_the_watch_refusal_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _NoExtractLLM()
    )
    payload = b"the refused bytes"
    (tmp_path / "note-v2.txt").write_bytes(payload)
    _seed(tmp_path, _refusal_row("sources/note", content_hash(payload)))
    _seed(tmp_path, _refusal_row("sources/other", content_hash(b"different bytes")))

    result = runner.invoke(app, ["ingest", "note-v2.txt", "--auto"])

    assert result.exit_code == 0, result.stderr
    by_target = {item.targets[0]: item for item in _items(tmp_path)}
    matched = by_target["sources/note"]
    assert (matched.status, matched.resolution) == ("applied", "as_proposed")
    assert by_target["sources/other"].status == "pending"


def test_a_re_extract_that_reuses_the_raw_copy_resolves_no_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _NoExtractLLM()
    )
    payload = b"already imported bytes"
    (tmp_path / "notes.txt").write_bytes(payload)
    first = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert first.exit_code == 0, first.stderr
    _seed(tmp_path, _refusal_row("sources/notes", content_hash(payload)))

    result = runner.invoke(app, ["ingest", "notes.txt", "--re-extract", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _only(tmp_path).status == "pending"


# -- degradation --------------------------------------------------------------


def test_an_absent_queue_is_a_no_op_and_is_never_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    findings = config.WorkspaceLayout(root).findings_db_path
    assert not findings.exists()

    _relate(root, "concepts/a", "references", "concepts/b")

    assert not findings.exists()


def test_a_queue_that_cannot_be_read_never_fails_the_human_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path, monkeypatch)
    _concept(root, "concepts/a")
    _concept(root, "concepts/b")
    findings = config.WorkspaceLayout(root).findings_db_path
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_bytes(b"this is not a sqlite database" * 50)

    _relate(root, "concepts/a", "references", "concepts/b")

    text = (root / "bundle" / "concepts" / "a.md").read_text(encoding="utf-8")
    assert "references" in text
