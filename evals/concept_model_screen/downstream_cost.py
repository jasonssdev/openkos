"""What the selector judge's cut saves downstream of extraction (#1579).

Reads the #1571 replay records (`judge_study.py`) the way `cut_profile.py`
does and never writes them. For each record it builds the two object sets the
judge study compares (the judge's kept set, and the ceiling set under the
positional backstop of 20 that the production path applies when no judge
ran), writes each as a temporary OKF bundle OUTSIDE the repository, and runs
only the deterministic candidate generators the production code uses after
extraction. No chat call and no embedding call is made: the one stage that
embeds (`state.reindex.reindex`) is driven by a counting stand-in that returns
zero vectors, so it reports how many embedding calls a reindex WOULD issue.

Descriptive only: no decision rule, no adoption. Stages that need a real
embedding or typed relations to generate candidates are reported as not
measured, never estimated.

What the temporary bundles contain, and what they cannot: the replay records
store each object's `type`, `title`, `description` and `body_chars`, not its
body text and not its links. A bundle therefore holds the description as the
body and the object's `Related` link to its source. Title-keyed generators
(entity-resolution candidates) see the real titles; body-keyed ones do not.

Subcommand `measure`. Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import cut_precision as cp  # noqa: E402
import cut_profile as cpf  # noqa: E402
import judge_study as js  # noqa: E402
import screen_data as sd  # noqa: E402

from openkos.bundle.source_titles import slugify  # noqa: E402
from openkos.llm.base import EMBED_DIM  # noqa: E402
from openkos.model import okf, types  # noqa: E402
from openkos.resolution import candidates, contradiction, edge_typing  # noqa: E402
from openkos.resolution import decision_revision as dr  # noqa: E402
from openkos.state import reindex as state_reindex  # noqa: E402
from openkos.state import vectorstore  # noqa: E402

Obj = dict[str, Any]

ARMS: Final = cp.ARMS
BACKSTOP: Final = cpf.BACKSTOP
JUDGE: Final = "judge"
ALONE: Final = "ceiling"
_GENERATED: Final = okf.Generated(by="downstream_cost", at="2026-01-01T00:00:00Z")

NOT_MEASURED: Final = {
    "edge typing (proximity edges)": (
        "needs embeddings: proximity edges come from vector neighbours"
    ),
    "revision candidates (Decisions)": (
        "needs embeddings: the similarity threshold gates every pair"
    ),
}
"""Stages whose candidate generator cannot run without a real embedding."""


# --- materialise ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Materialized:
    """What `materialize` wrote: documents on disk, objects that attached to an
    existing document, and concept id -> source id for each document."""

    written: int
    collapsed: int
    sources: Mapping[str, str]


def materialize(
    groups: Sequence[tuple[str, Sequence[Mapping[str, Any]]]], bundle_dir: Path
) -> Materialized:
    """Write `(source_id, objects)` groups as one bundle.

    Two objects with the same type and slug are one document, as at ingest
    (identity is the path; dedup is exact-slug): the later one attaches, so it
    is counted in `collapsed` and not written twice."""
    written = 0
    collapsed = 0
    sources: dict[str, str] = {}
    for source_id, objects in groups:
        for obj in objects:
            kind = str(obj["type"])
            concept_id = f"{types.TYPE_TO_LINK_DIR[kind]}/{slugify(str(obj['title']))}"
            if concept_id in sources:
                collapsed += 1
                continue
            path = okf.concept_path_for(concept_id, bundle_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                okf.build_concept(
                    type=kind,
                    title=str(obj["title"]),
                    description=str(obj["description"]),
                    body="",
                    provenance=[f"sources/{slugify(source_id)}"],
                    sensitivity="private",
                    generated=_GENERATED,
                ),
                encoding="utf-8",
            )
            sources[concept_id] = source_id
            written += 1
    return Materialized(written, collapsed, sources)


# --- measure -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Measure:
    """Deterministic downstream workload of one bundle. `None` = not measured."""

    objects: int = 0
    collapsed: int = 0
    identity_produced: int = 0
    identity_retained: int = 0
    identity_high: int = 0
    edge_candidates: int = 0
    contradiction_candidates: int = 0
    embed_calls: int | None = 0
    decisions: int = 0
    revision_pool: int = 0


class CountingEmbedder:
    """Stand-in `Embedder`: counts calls, returns zero vectors, calls nothing."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [[0.0] * EMBED_DIM for _ in texts]


def _embed_calls(bundle_dir: Path, scratch: Path) -> int | None:
    embedder = CountingEmbedder()
    try:
        with vectorstore.open_vector_store(scratch / "vectors.db") as db:
            state_reindex.reindex(
                bundle_dir,
                db,
                embedder,
                fts_db_path=scratch / "fts.db",
                model_tag="downstream-cost",
            )
    except vectorstore.VecUnavailable:
        return None
    return embedder.calls


def revision_pool(sources: Mapping[str, str]) -> tuple[int, int]:
    """(Decision count, pairs of Decisions from different sources).

    The production planner with every similarity forced equal and its per-
    Decision top-k and cap lifted: what is left is the eligibility exclusion
    alone, the pool the embedding threshold would then thin."""
    decisions = [
        dr.DecisionInput(cid, None, frozenset({src}), frozenset())
        for cid, src in sorted(sources.items())
        if cid.startswith(f"{types.TYPE_TO_LINK_DIR['Decision']}/")
    ]
    vectors = {d.concept_id: [1.0] for d in decisions}
    plan = dr.plan_revision_candidates(
        decisions, vectors, top_k=10**9, cap=10**9, threshold=0.0
    )
    return len(decisions), plan.total


def measure_bundle(bundle_dir: Path, mat: Materialized, scratch: Path) -> Measure:
    report = candidates.find_candidates_report(bundle_dir)
    n_decisions, pool = revision_pool(mat.sources)
    return Measure(
        objects=mat.written,
        collapsed=mat.collapsed,
        identity_produced=report.produced,
        identity_retained=report.retained,
        identity_high=sum(1 for g in report.groups if g.tier is candidates.Tier.HIGH),
        edge_candidates=len(edge_typing.candidate_edges(bundle_dir)),
        contradiction_candidates=contradiction.plan_candidates(bundle_dir).total_count,
        embed_calls=_embed_calls(bundle_dir, scratch),
        decisions=n_decisions,
        revision_pool=pool,
    )


def measure_groups(
    groups: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
) -> Measure:
    """Materialise `groups` in a fresh temporary bundle outside the repo and
    measure it."""
    with tempfile.TemporaryDirectory(prefix="openkos-downstream-") as tmp:
        root = Path(tmp)
        bundle = root / "bundle"
        bundle.mkdir()
        mat = materialize(groups, bundle)
        return measure_bundle(bundle, mat, root)


def add(a: Measure, b: Measure) -> Measure:
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(Measure):
        x, y = getattr(a, f.name), getattr(b, f.name)
        fields[f.name] = None if x is None or y is None else x + y
    return Measure(**fields)


def ratio(num: int | None, den: int | None) -> float | None:
    """with-judge over without-judge; None when either is unmeasured or the
    denominator is zero."""
    if num is None or den is None or den == 0:
        return None
    return num / den


# --- sets ----------------------------------------------------------------------


def sets_for_record(
    ceiling: Sequence[Obj], flags: Sequence[bool]
) -> dict[str, list[Obj]]:
    """The two object sets of one record: what the judge kept, and what the
    production path would hand on with no judge (the positional backstop)."""
    return {
        JUDGE: [o for o, kept in zip(ceiling, flags, strict=True) if kept],
        ALONE: list(ceiling[:BACKSTOP]),
    }


def collect(study_dir: Path, arm: str) -> dict[str, Any]:
    """Per-record and per-extractor-workspace measures of one judge arm."""
    per_record: list[tuple[str, str, dict[str, Measure]]] = []
    workspaces: dict[str, dict[str, list[tuple[str, list[Obj]]]]] = {}
    for model, runs in js.PRIMARY:
        for meeting in sd.POOL_DA:
            for run in runs:
                base = cpf._load(
                    js.replay_path(study_dir, "self", model, meeting, run, 0)
                )
                ceiling = base["stages"]["ceiling"] or []
                judge_arm = "fixed" if model == js.JUDGE_MODEL else arm
                rec = cpf._load(
                    js.replay_path(study_dir, judge_arm, model, meeting, run, 0)
                )
                if not js.same_objects(rec["stages"]["ceiling"], ceiling):
                    continue
                flags = cp.partition(ceiling, rec["stages"]["judge"] or [])
                sets = sets_for_record(ceiling, flags)
                per_record.append(
                    (
                        model,
                        meeting,
                        {k: measure_groups([(meeting, v)]) for k, v in sets.items()},
                    )
                )
                for k, v in sets.items():
                    workspaces.setdefault(model, {}).setdefault(k, []).append(
                        (meeting, v)
                    )
    pooled = {
        model: {k: measure_groups(g) for k, g in by_set.items()}
        for model, by_set in workspaces.items()
    }
    return {"per_record": per_record, "workspaces": pooled}


# --- render --------------------------------------------------------------------

_STAGE_ROWS: Final = (
    ("objects materialised", "objects"),
    ("identity groups produced", "identity_produced"),
    (
        "identity groups retained (cap 50): LLM calls and queue rows",
        "identity_retained",
    ),
    ("  of which exact-title (HIGH)", "identity_high"),
    ("edge typing candidates (untyped body links)", "edge_candidates"),
    (
        "contradiction candidates (typed edges, merge ledger)",
        "contradiction_candidates",
    ),
    ("reindex embedding calls (chunks)", "embed_calls"),
    ("Decisions", "decisions"),
    ("revision pool, similarity-blind upper bound", "revision_pool"),
)


def _fmt(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def render_table(title: str, judge: Measure, alone: Measure) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| stage | with judge (k) | without (N) | k/N |",
        "|---|---|---|---|",
    ]
    for label, name in _STAGE_ROWS:
        k, n = getattr(judge, name), getattr(alone, name)
        r = ratio(k, n)
        lines.append(
            f"| {label} | {_fmt(k)} | {_fmt(n)} | {'-' if r is None else f'{r:.3f}'} |"
        )
    return [*lines, ""]


def render(arm: str, data: Mapping[str, Any]) -> list[str]:
    records = data["per_record"]
    total_j = Measure()
    total_a = Measure()
    for _model, _meeting, both in records:
        total_j = add(total_j, both[JUDGE])
        total_a = add(total_a, both[ALONE])
    ws_j = Measure()
    ws_a = Measure()
    for both in data["workspaces"].values():
        ws_j = add(ws_j, both[JUDGE])
        ws_a = add(ws_a, both[ALONE])
    lines = [f"## arm: {arm}", ""]
    lines += render_table(
        f"per source, summed over {len(records)} records (one bundle each)",
        total_j,
        total_a,
    )
    lines += render_table(
        f"per extractor workspace, summed over {len(data['workspaces'])} "
        "workspaces (all of one extractor's meetings in one bundle)",
        ws_j,
        ws_a,
    )
    lines += ["per-record identity groups retained (judge/without):"]
    lines.append(
        "  "
        + ", ".join(
            f"{m}/{s}:{b[JUDGE].identity_retained}/{b[ALONE].identity_retained}"
            for m, s, b in records
        )
    )
    return [*lines, ""]


def cmd_measure(args: argparse.Namespace) -> int:
    lines: list[str] = ["not measured (no model is called):"]
    lines += [f"  {k}: {v}" for k, v in NOT_MEASURED.items()]
    lines.append("")
    for arm in ARMS:
        lines += render(arm, collect(args.study_dir, arm))
    print("\n".join(lines))
    return 0


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(kind: str, title: str, desc: str = "A description.") -> Obj:
    return {"type": kind, "title": title, "description": desc, "body_chars": 50}


_PROBE: Final = [
    _o("Decision", "Adopt Postgres"),
    _o("Decision", "Adopt PostgreSQL"),
    _o("Person", "Ana Lopez"),
    _o("Concept", "Whiteboard Use"),
]


def _t_materialize() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)
        mat = materialize(
            [("m1", [_o("Person", "Ana"), _o("Person", "Ana"), _o("Concept", "Ana")])],
            bundle,
        )
        _check(mat.written == 2, f"same type+slug is one document: {mat.written}")
        _check(mat.collapsed == 1, f"one attach: {mat.collapsed}")
        _check(
            sorted(mat.sources) == ["concepts/ana", "people/ana"],
            f"ids: {sorted(mat.sources)}",
        )
        _check(len(list(bundle.rglob("*.md"))) == 2, "documents on disk match written")


def _t_measure() -> None:
    m = measure_groups([("m1", _PROBE)])
    _check(m.objects == 4, f"objects {m.objects}")
    _check(m.identity_produced == 1 and m.identity_retained == 1, f"identity {m}")
    _check(m.identity_high == 0, "the pair is a near match, not an exact title")
    _check(m.edge_candidates == 0, f"edges {m.edge_candidates}")
    _check(m.contradiction_candidates == 0, f"contradictions {m}")
    _check(m.embed_calls == 4, f"one embed call per single-chunk document: {m}")
    _check(m.decisions == 2 and m.revision_pool == 0, f"one source, no pairs: {m}")


def _t_pairwise_grows_faster() -> None:
    half = [_o("Decision", "Adopt Postgres"), _o("Decision", "Adopt PostgreSQL")]
    other = [_o("Decision", "Pick Redis"), _o("Decision", "Pick Redis cache")]
    split = add(measure_groups([("a", half)]), measure_groups([("b", other)]))
    pooled = measure_groups([("a", half), ("b", other)])
    _check(split.revision_pool == 0, "per-source bundles hold no cross-source pair")
    _check(pooled.revision_pool == 4, f"2x2 cross-source pairs: {pooled}")
    _check(pooled.objects == split.objects, "same documents either way")


def _t_sets() -> None:
    ceiling = [_o("Concept", f"c{i}") for i in range(25)]
    flags = [i % 5 == 0 for i in range(25)]
    got = sets_for_record(ceiling, flags)
    _check(len(got[JUDGE]) == 5, "judge keeps the flagged objects")
    _check(got[ALONE] == ceiling[:20], "no judge: the positional backstop of 20")
    _check(BACKSTOP == js.concept_mod._UNION_BACKSTOP, "backstop constant drifted")


def _t_ratio_and_add() -> None:
    _check(ratio(2, 4) == 0.5, "ratio")
    _check(ratio(1, 0) is None and ratio(None, 3) is None, "undefined ratio")
    a = Measure(objects=1, embed_calls=None)
    b = Measure(objects=2, embed_calls=5)
    s = add(a, b)
    _check(s.objects == 3 and s.embed_calls is None, f"None is contagious: {s}")


def _t_embedder_is_offline() -> None:
    e = CountingEmbedder()
    out = e.embed(["a", "b"])
    _check(e.calls == 2 and len(out) == 2 and len(out[0]) == EMBED_DIM, "stand-in")


def _t_not_measured_listed() -> None:
    _check(len(NOT_MEASURED) == 2, "both embedding-gated stages are declared")
    out = "\n".join(render_table("t", Measure(), Measure()))
    _check("n/a" not in out and "| 0 | 0 | - |" in out, "zero over zero is '-'")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_materialize,
        _t_measure,
        _t_pairwise_grows_faster,
        _t_sets,
        _t_ratio_and_add,
        _t_embedder_is_offline,
        _t_not_measured_listed,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--study-dir", type=Path, default=js.OUT_DIR)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("measure")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "measure":
        raise SystemExit("choose a subcommand, or --self-test")
    return cmd_measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
