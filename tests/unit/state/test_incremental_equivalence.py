"""Equivalence property: after EVERY step of a random edit sequence, the
per-document refresh of the FTS and graph stores equals a whole rebuild of the
same bundle, in every table. Sequences come from a seeded `random.Random`, so a
failure names its seed and replays exactly (no property-test dependency)."""

# ruff: noqa: S311 -- seeded, deterministic test data; not cryptography

import random
import sqlite3
import zlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph import sqlite_graph
from openkos.graph.proximity import ProximityPair
from openkos.state import derived, fts

_SEEDS = range(25)
_STEPS = 12
_POOL = [f"{d}/n{i}" for d in ("concepts", "people") for i in range(5)]
_WORDS = ["alpha", "beta", "gamma", "delta", "kappa", "omega"]
_GRAPH_TABLES = ("nodes", "edges", "doc_outlinks", "node_facts", "doc_manifest", "meta")
_GRAPH_QUERIES = {t: f"SELECT * FROM {t}" for t in _GRAPH_TABLES}  # noqa: S608
_FTS_QUERIES = {
    "docs": "SELECT concept_id, title, description, tags, body FROM docs",
    "doc_manifest": "SELECT concept_id, content_hash FROM doc_manifest",
    "meta": "SELECT key, value FROM meta",
}


class _AllPairs:
    """Nominates every pair, so the global candidate pass is exercised."""

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        ids = sorted(concept_ids)
        return [
            ProximityPair(
                source_id=a,
                target_id=b,
                distance=(zlib.crc32(f"{a}|{b}".encode()) % 1000) / 1000,
            )
            for i, a in enumerate(ids)
            for b in ids[i + 1 :]
        ]


def _put(bundle: Path, name: str, text: str) -> None:
    path = bundle / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _content(rng: random.Random, name: str) -> str:
    """A document linking to pool names, existing or not; some typed."""
    others = [n for n in _POOL if n != name]
    body = " ".join(rng.choices(_WORDS, k=rng.randint(1, 5)))
    for target in rng.sample(others, rng.randint(0, 3)):
        body += f" see [{target}](/{target}.md)"
    if rng.random() < 0.3:
        body += f" self [me](/{name}.md)"
    relations = ""
    if rng.random() < 0.3:
        relations = (
            f"relations:\n  - target: {rng.choice(others)}\n    type: depends_on\n"
        )
    return f"---\ntype: Concept\ntitle: {name}\n{relations}---\n{body}\n"


def _present(bundle: Path) -> list[str]:
    return sorted(
        p.relative_to(bundle).with_suffix("").as_posix() for p in bundle.rglob("*.md")
    )


def _apply_step(rng: random.Random, bundle: Path) -> None:
    """One to three edits: add, change, remove (possibly a link target other
    documents point at), or rename (remove + add under a new name)."""
    for _ in range(rng.randint(1, 3)):
        present = _present(bundle)
        absent = [n for n in _POOL if n not in present]
        op = rng.choice(["add", "change", "remove", "rename"])
        if op == "add" and absent:
            name = rng.choice(absent)
            _put(bundle, name, _content(rng, name))
        elif op == "change" and present:
            name = rng.choice(present)
            _put(bundle, name, _content(rng, name))
        elif op == "remove" and present:
            (bundle / f"{rng.choice(present)}.md").unlink()
        elif op == "rename" and present and absent:
            old, new = rng.choice(present), rng.choice(absent)
            text = (bundle / f"{old}.md").read_text(encoding="utf-8")
            _put(bundle, new, text.replace(f"title: {old}", f"title: {new}"))
            (bundle / f"{old}.md").unlink()


def _seed_bundle(rng: random.Random, bundle: Path) -> None:
    for name in rng.sample(_POOL, 4):
        _put(bundle, name, _content(rng, name))


def _dump(db: Path, queries: dict[str, str]) -> dict[str, list[tuple[object, ...]]]:
    conn = sqlite3.connect(db)
    try:
        return {
            t: sorted(conn.execute(q).fetchall(), key=repr) for t, q in queries.items()
        }
    finally:
        conn.close()


def _check_outcome(step: int, outcome: str) -> None:
    # The first refresh builds whole; every later one must take the
    # per-document path (or be a no-op) -- otherwise the property would compare
    # a rebuild against itself.
    if step == 0:
        assert outcome == "rebuilt"
    else:
        assert outcome in {"incremental", "unchanged"}, outcome


@pytest.mark.parametrize("seed", _SEEDS)
def test_fts_incremental_equals_rebuild_after_every_step(
    seed: int, tmp_path: Path
) -> None:
    rng = random.Random(seed)
    bundle, db = tmp_path / "bundle", tmp_path / ".openkos" / "fts.db"
    ref = tmp_path / "ref" / "fts.db"
    _seed_bundle(rng, bundle)
    for step in range(_STEPS + 1):
        if step:
            _apply_step(rng, bundle)
        _check_outcome(step, fts.refresh_fts_index(db, bundle))
        ref.unlink(missing_ok=True)
        fts.write_fts_index(ref, bundle)
        assert _dump(db, _FTS_QUERIES) == _dump(ref, _FTS_QUERIES), (seed, step)


@pytest.mark.parametrize("with_candidates", [False, True])
@pytest.mark.parametrize("seed", _SEEDS)
def test_graph_incremental_equals_rebuild_after_every_step(
    seed: int, with_candidates: bool, tmp_path: Path
) -> None:
    cands = _AllPairs() if with_candidates else None
    rng = random.Random(seed)
    bundle, db = tmp_path / "bundle", tmp_path / ".openkos" / "graph.db"
    ref = tmp_path / "ref" / "graph.db"
    _seed_bundle(rng, bundle)
    for step in range(_STEPS + 1):
        if step:
            _apply_step(rng, bundle)
        outcome = sqlite_graph.refresh_graph_store(db, bundle, candidates=cands)
        _check_outcome(step, outcome)
        ref.unlink(missing_ok=True)
        sqlite_graph.write_graph_store(ref, bundle, candidates=cands)
        assert _dump(db, _GRAPH_QUERIES) == _dump(ref, _GRAPH_QUERIES), (seed, step)


def test_staleness_probe_stays_hash_only_for_both_stores(tmp_path: Path) -> None:
    rng = random.Random(0)
    bundle = tmp_path / "bundle"
    _seed_bundle(rng, bundle)
    fts_db = tmp_path / ".openkos" / "fts.db"
    graph_db = tmp_path / ".openkos" / "graph.db"
    fts.refresh_fts_index(fts_db, bundle)
    sqlite_graph.refresh_graph_store(graph_db, bundle)
    for db in (fts_db, graph_db):  # the probe must not need the recorded pairs
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE doc_manifest")
        conn.commit()
        conn.close()
    stores = [("fts", fts_db), ("graph", graph_db)]
    assert derived.stale_derived_stores(bundle, stores) == ()
    _put(bundle, "concepts/n0", "---\ntype: Concept\ntitle: x\n---\nchanged\n")
    assert derived.stale_derived_stores(bundle, stores) == ("fts", "graph")
