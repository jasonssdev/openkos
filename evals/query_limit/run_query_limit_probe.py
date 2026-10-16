"""Is `openkos query --limit 5` the right default? (#1377)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the embedding model AND the chat model. The bar is in
`PREREGISTRATION-1377.md`, committed BEFORE any model run; `decide()` below is
that bar written as code, and `--self-test` checks it on synthetic rows.

```
uv run python -u evals/query_limit/run_query_limit_probe.py --self-test
uv run python -u evals/query_limit/run_query_limit_probe.py --runs 15
uv run python -u evals/query_limit/run_query_limit_probe.py --rescore <runs.json>
```

The shipped `answer()` runs unmodified; `limit` is the only variable.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

_HERE = pathlib.Path(__file__).resolve().parent
_EVALS = _HERE.parent
sys.path.insert(0, str(_EVALS.parent / "src"))
sys.path.append(str(_EVALS))
sys.path.append(str(_HERE))

import limit_corpus as corpus  # noqa: E402
from harness_stamp import TimedBackend, build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.retrieval.answer import _SUFFICIENCY_PROMPT, answer  # noqa: E402
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.fts import open_fts_index_readonly  # noqa: E402
from openkos.state.vectorstore import open_vector_store  # noqa: E402

RESULTS_DIR: Final = _HERE / "results"
EMBED_MODEL: Final = "bge-m3"
DEFAULT_MODEL: Final = "qwen3:8b"
DEFAULT_TIMEOUT: Final = 1800.0
ARMS: Final = (3, 5, 8, 10)
BASELINE: Final = 5
CANDIDATES: Final = (8, 10)
_CODE_RE: Final = re.compile(r"\b[A-Z]{3}-\d{4}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    k: int
    run: int
    qi: int
    group: str
    retrieved: list[str]
    complete: bool
    gold_recall: float
    fact_recall: float
    refused: bool
    mishandled: bool
    elapsed_s: float
    context_chars: int
    omitted: int
    excerpted: int
    answer: str


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fact_recall(answer_text: str, gold: Sequence[tuple[str, str]]) -> float:
    """Fraction of the gold ticket codes present in the answer."""
    if not gold:
        return 0.0
    haystack = _norm(answer_text)
    return sum(1 for _cid, code in gold if _norm(code) in haystack) / len(gold)


def gold_recall(retrieved: Sequence[str], gold: Sequence[tuple[str, str]]) -> float:
    if not gold:
        return 0.0
    return sum(1 for cid, _c in gold if cid in set(retrieved)) / len(gold)


def mishandled(answer_text: str, *, refused: bool) -> bool:
    """An unanswerable question answered with a ticket code."""
    return (not refused) and bool(_CODE_RE.search(answer_text))


def score_row(
    question: corpus.Question,
    *,
    k: int,
    run: int,
    qi: int,
    retrieved: list[str],
    answer_text: str,
    refused: bool,
    elapsed_s: float,
    context_chars: int,
    omitted: int,
    excerpted: int,
) -> Row:
    recall = gold_recall(retrieved, question.gold)
    return Row(
        k=k,
        run=run,
        qi=qi,
        group=question.group,
        retrieved=retrieved,
        complete=bool(question.gold) and recall == 1.0,
        gold_recall=recall,
        fact_recall=0.0 if refused else fact_recall(answer_text, question.gold),
        refused=refused,
        mishandled=mishandled(answer_text, refused=refused)
        if question.group == corpus.ADJACENT
        else False,
        elapsed_s=elapsed_s,
        context_chars=context_chars,
        omitted=omitted,
        excerpted=excerpted,
        answer=answer_text[:600],
    )


# --- the registered decision rule ------------------------------------------


def _sel(rows: Sequence[Row], k: int, *groups: str) -> list[Row]:
    return [r for r in rows if r.k == k and r.group in groups]


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _run_scores(rows: Sequence[Row], k: int, group: str) -> list[float]:
    """Per-run group score: mean fact recall over the group's questions."""
    by_run: dict[int, list[float]] = {}
    for r in _sel(rows, k, group):
        by_run.setdefault(r.run, []).append(r.fact_recall)
    return [_mean(v) for _run, v in sorted(by_run.items())]


def _spread(values: Sequence[float]) -> float:
    return (max(values) - min(values)) if values else 0.0


def _rate(rows: Sequence[Row], attr: str) -> float:
    return _mean([1.0 if getattr(r, attr) else 0.0 for r in rows])


GROUNDED: Final = (corpus.MULTI, corpus.SMALL, corpus.SINGLE)


def complete_questions(rows: Sequence[Row], k: int) -> tuple[int, int]:
    """`multi` questions whose whole gold set was retrieved in EVERY run."""
    by_q: dict[int, list[bool]] = {}
    for r in _sel(rows, k, corpus.MULTI):
        by_q.setdefault(r.qi, []).append(r.complete)
    return sum(1 for v in by_q.values() if all(v)), len(by_q)


def decide(rows: Sequence[Row], k: int) -> dict[str, tuple[bool, str]]:
    """The PREREGISTRATION-1377 bars for candidate `k` against `BASELINE`."""
    out: dict[str, tuple[bool, str]] = {}
    done, total = complete_questions(rows, k)
    out["R"] = (
        done >= 4,
        f"{done} of {total} multi questions fully retrieved (need 4)",
    )

    mk = _run_scores(rows, k, corpus.MULTI)
    mb = _run_scores(rows, BASELINE, corpus.MULTI)
    gap = _mean(mk) - _mean(mb)
    need = max(_spread(mk), _spread(mb))
    out["C"] = (
        gap >= 0.10 and gap > need,
        f"multi fact recall {_mean(mk):.3f} vs {_mean(mb):.3f}: gap {gap:+.3f} "
        f"(need >= 0.10 and > spread {need:.3f})",
    )

    def grp(k_: int, g: str) -> float:
        return _mean([r.fact_recall for r in _sel(rows, k_, g)])

    sm, sg = (
        grp(k, corpus.SMALL) - grp(BASELINE, corpus.SMALL),
        grp(k, corpus.SINGLE) - grp(BASELINE, corpus.SINGLE),
    )
    pooled = _mean(
        [r.fact_recall for r in _sel(rows, k, corpus.SMALL, corpus.SINGLE)]
    ) - _mean(
        [r.fact_recall for r in _sel(rows, BASELINE, corpus.SMALL, corpus.SINGLE)]
    )
    out["N1"] = (
        sm >= -0.05 and sg >= -0.05 and pooled >= -0.03,
        f"small {sm:+.3f}, single {sg:+.3f}, pooled {pooled:+.3f} (floors -0.05/-0.05/-0.03)",
    )

    ak, ab = _sel(rows, k, corpus.ADJACENT), _sel(rows, BASELINE, corpus.ADJACENT)
    mk_rate, mb_rate = _rate(ak, "mishandled"), _rate(ab, "mishandled")
    unfals = mk_rate == 0 and mb_rate == 0
    out["N2"] = (
        mk_rate <= mb_rate + 0.05,
        f"adjacent mishandled {sum(r.mishandled for r in ak)} of {len(ak)} vs "
        f"{sum(r.mishandled for r in ab)} of {len(ab)}"
        + (" (UNFALSIFIED: both zero)" if unfals else ""),
    )

    gk, gb = _sel(rows, k, *GROUNDED), _sel(rows, BASELINE, *GROUNDED)
    out["N3"] = (
        _rate(gk, "refused") <= _rate(gb, "refused") + 0.02,
        f"false refusals {sum(r.refused for r in gk)} of {len(gk)} vs "
        f"{sum(r.refused for r in gb)} of {len(gb)}",
    )

    lat_k = statistics.median(
        [r.elapsed_s for r in _sel(rows, k, *GROUNDED, corpus.ADJACENT)] or [0.0]
    )
    lat_b = statistics.median(
        [r.elapsed_s for r in _sel(rows, BASELINE, *GROUNDED, corpus.ADJACENT)] or [0.0]
    )
    ctx_k = statistics.median([r.context_chars for r in gk] or [0])
    ctx_b = statistics.median([r.context_chars for r in gb] or [0])
    omitted = sum(r.omitted for r in gk)
    ok = (
        lat_b > 0
        and lat_k <= 1.5 * lat_b
        and ctx_b > 0
        and ctx_k <= 2.0 * ctx_b
        and omitted == 0
    )
    out["B"] = (
        ok,
        f"median latency {lat_k:.1f}s vs {lat_b:.1f}s (x{lat_k / lat_b if lat_b else 0:.2f}, max 1.5), "
        f"median context {ctx_k:.0f} vs {ctx_b:.0f} chars "
        f"(x{ctx_k / ctx_b if ctx_b else 0:.2f}, max 2.0), omitted blocks {omitted} (need 0)",
    )
    return out


def verdict(rows: Sequence[Row]) -> tuple[int, dict[int, dict[str, tuple[bool, str]]]]:
    """The adopted `k` (smallest passing candidate) or `BASELINE`."""
    table = {k: decide(rows, k) for k in CANDIDATES if any(r.k == k for r in rows)}
    passing = [k for k, bars in table.items() if all(ok for ok, _ in bars.values())]
    return (min(passing) if passing else BASELINE), table


# --- generation -------------------------------------------------------------


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    bundle = root / "bundle"
    for doc_id, (title, body) in corpus.documents().items():
        path = bundle / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": title,
                "description": "",
                "sensitivity": "private",
            }
        )
        path.write_text(frontmatter + body, encoding="utf-8")
    return bundle


def _write_runs(
    rows: Sequence[Row], model: str, runs: int, stamp: str, ident: Any
) -> pathlib.Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"runs-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "runs": runs,
                "generated_at": stamp,
                "arms": list(ARMS),
                "stamp": ident,
                "rows": [r.__dict__ for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def generate(model: str, runs: int, timeout: float, stamp: str) -> list[Row]:
    ident = build_stamp(
        model=model, prompts={"answer/sufficiency": _SUFFICIENCY_PROMPT}
    )
    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    llm = TimedBackend(
        OllamaClient(
            model=model,
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
            timeout=timeout,
        )
    )
    questions = corpus.questions()
    rows: list[Row] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = _write_corpus(root)
        vectors_path = root / ".openkos" / "vectors.db"
        fts_path = root / ".openkos" / "fts.db"
        with open_vector_store(vectors_path) as db:
            report = reindex_module.reindex(
                bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
            )
        expected = len(corpus.documents())
        print(
            f"reindex: embedded={report.embedded} skipped={report.skipped}", flush=True
        )
        if report.embedded != expected or report.skipped:
            raise SystemExit(f"corpus did not fully index (expected {expected})")
        fts_index = open_fts_index_readonly(fts_path)
        with open_vector_store(vectors_path) as db:
            for run in range(1, runs + 1):
                for qi, question in enumerate(questions):
                    for k in ARMS:
                        started = time.monotonic()
                        result = answer(
                            question.text,
                            bundle_dir=bundle,
                            llm=llm,
                            embedder=embedder,
                            vector_store=db,
                            fts_index=fts_index,
                            limit=k,
                            sufficiency_check=True,
                        )
                        elapsed = time.monotonic() - started
                        trace = result.trace
                        retrieved = (
                            [r.concept_id for r in trace.retrieved] if trace else []
                        )
                        blocks = trace.context_blocks if trace else ()
                        rows.append(
                            score_row(
                                question,
                                k=k,
                                run=run,
                                qi=qi,
                                retrieved=retrieved,
                                answer_text=result.answer,
                                refused=result.no_match_cause != "none",
                                elapsed_s=elapsed,
                                context_chars=sum(len(b.text) for b in blocks),
                                omitted=len(trace.omitted) if trace else 0,
                                excerpted=sum(1 for b in blocks if b.excerpted),
                            )
                        )
                print(f"=== run {run}/{runs} done ({len(rows)} rows)", flush=True)
                _write_runs(rows, model, run, stamp, ident)
    return rows


# --- report -----------------------------------------------------------------


def render(rows: Sequence[Row], *, model: str, runs: int) -> str:
    lines = [
        f"# Query limit measurement (#1377), `{model}`, {runs} runs per arm",
        "",
        "| k | multi complete (of 6) | multi fact recall | small | single | false refusals | adjacent mishandled | median s | median ctx chars | omitted | excerpted |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for k in sorted({r.k for r in rows}):
        done, total = complete_questions(rows, k)
        mrun = _run_scores(rows, k, corpus.MULTI)
        g, adj = _sel(rows, k, *GROUNDED), _sel(rows, k, corpus.ADJACENT)
        lines.append(
            f"| {k} | {done} of {total} | {_mean(mrun):.3f} ({min(mrun):.2f}-{max(mrun):.2f}) "
            f"| {_mean([r.fact_recall for r in _sel(rows, k, corpus.SMALL)]):.3f} "
            f"| {_mean([r.fact_recall for r in _sel(rows, k, corpus.SINGLE)]):.3f} "
            f"| {sum(r.refused for r in g)} of {len(g)} "
            f"| {sum(r.mishandled for r in adj)} of {len(adj)} "
            f"| {statistics.median([r.elapsed_s for r in rows if r.k == k]):.1f} "
            f"| {statistics.median([r.context_chars for r in g]):.0f} "
            f"| {sum(r.omitted for r in g)} | {sum(r.excerpted for r in g)} |"
        )
    # Retrieval determinism: identical retrieved list across runs per (k, qi).
    seen: dict[tuple[int, int], set[tuple[str, ...]]] = {}
    for r in rows:
        seen.setdefault((r.k, r.qi), set()).add(tuple(r.retrieved))
    stable = sum(1 for v in seen.values() if len(v) == 1)
    lines += [
        "",
        f"Retrieval identical across all runs: {stable} of {len(seen)} (k, question) cells.",
        "",
    ]
    adopted, table = verdict(rows)
    lines.append("## Bars")
    for k, bars in table.items():
        lines.append(f"\n### k = {k}")
        for name, (ok, why) in bars.items():
            lines.append(f"- **{name}** {'PASS' if ok else 'FAIL'}: {why}")
    lines += ["", f"## Decision: default `limit` = **{adopted}**"]
    return "\n".join(lines)


# --- self-test --------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)
        print(f"{'ok  ' if cond else 'FAIL'} {name}")

    docs = corpus.documents()
    qs = corpus.questions()
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _write_corpus(pathlib.Path(tmp))
        unparseable = [
            okf.concept_id_for(s.path, bundle)
            for s in okf._iter_docs(bundle)
            if s.read_error is not None or s.parse_error is not None
        ]
        check(
            "every document parses",
            not unparseable and len(list(bundle.rglob("*.md"))) == len(docs),
        )
    gold_all = [pair for q in qs for pair in q.gold]
    check("gold concepts exist", all(cid in docs for cid, _c in gold_all))
    codes = {c for _cid, c in gold_all}
    check(
        "each code appears in exactly its own concept",
        all(sum(code in body for _t, body in docs.values()) == 1 for code in codes),
    )
    sizes = {g: [len(q.gold) for q in qs if q.group == g] for g in corpus.GROUPS}
    check(
        "multi gold sets all exceed 5",
        all(n > 5 for n in sizes[corpus.MULTI]) and len(sizes[corpus.MULTI]) == 6,
    )
    check(
        "small gold sets all <= 5",
        all(1 < n <= 5 for n in sizes[corpus.SMALL]) and len(sizes[corpus.SMALL]) == 4,
    )
    check("single gold sets are 1", sizes[corpus.SINGLE] == [1] * 6)
    check("adjacent have no gold", sizes[corpus.ADJACENT] == [0] * 3 and len(qs) == 19)

    gold = (("a", "AUR-4100"), ("b", "AUR-4137"))
    check("fact recall full", fact_recall("codes: aur 4100 and AUR-4137", gold) == 1.0)
    check("fact recall half", fact_recall("only AUR-4100", gold) == 0.5)
    check("fact recall none", fact_recall("nothing", gold) == 0.0)
    check("gold recall", gold_recall(["a", "z"], gold) == 0.5)
    check(
        "mishandled needs a code and no refusal",
        mishandled("it is AUR-4100", refused=False)
        and not mishandled("it is AUR-4100", refused=True)
        and not mishandled("no data", refused=False),
    )

    def synth(
        k: int,
        *,
        multi: float,
        small: float,
        ctx: int,
        lat: float,
        refuse: bool = False,
        mis: bool = False,
        complete: bool = True,
        omitted: int = 0,
    ) -> list[Row]:
        out: list[Row] = []
        for run in range(1, 16):
            for qi, q in enumerate(qs):
                fr = {"multi": multi, "small": small, "single": small}.get(q.group, 0.0)
                out.append(
                    Row(
                        k,
                        run,
                        qi,
                        q.group,
                        [],
                        complete and q.group == "multi",
                        1.0,
                        fr + (0.001 * (run % 3)),
                        refuse and q.group != "adjacent" and run <= 3 and qi < 2,
                        mis and q.group == "adjacent" and run == 1,
                        lat,
                        ctx,
                        omitted,
                        0,
                        "",
                    )
                )
        return out

    base = synth(5, multi=0.6, small=0.95, ctx=2000, lat=10.0, complete=False)
    good = (
        base
        + synth(8, multi=0.9, small=0.95, ctx=3200, lat=13.0)
        + synth(10, multi=0.95, small=0.95, ctx=4000, lat=14.0)
    )
    check("rule adopts the smallest passing k", verdict(good)[0] == 8)
    slow = base + synth(8, multi=0.9, small=0.95, ctx=3200, lat=20.0)
    check("rule keeps 5 when latency blows the budget", verdict(slow)[0] == 5)
    hurt = base + synth(8, multi=0.9, small=0.80, ctx=3200, lat=13.0)
    check("rule keeps 5 when small questions are hurt", verdict(hurt)[0] == 5)
    flat = base + synth(8, multi=0.62, small=0.95, ctx=3200, lat=13.0)
    check("rule keeps 5 when the gain is inside the margin", verdict(flat)[0] == 5)
    noret = base + synth(8, multi=0.9, small=0.95, ctx=3200, lat=13.0, complete=False)
    check("rule keeps 5 when retrieval stays incomplete", verdict(noret)[0] == 5)
    refusing = base + synth(8, multi=0.9, small=0.95, ctx=3200, lat=13.0, refuse=True)
    check("rule keeps 5 when false refusals rise", verdict(refusing)[0] == 5)
    omit = base + synth(8, multi=0.9, small=0.95, ctx=3200, lat=13.0, omitted=1)
    check("rule keeps 5 when the budget omits a block", verdict(omit)[0] == 5)
    check("render runs on rows", "Decision" in render(good, model="m", runs=15))

    for name in failures:
        print(f"FAILED: {name}")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--stamp", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.rescore is not None:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        rows = [Row(**r) for r in payload["rows"]]
        print(render(rows, model=payload["model"], runs=payload["runs"]))
        return 0
    rows = generate(args.model, args.runs, args.timeout, args.stamp)
    report = render(rows, model=args.model, runs=args.runs)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"query-limit-{args.stamp}-{args.model.replace(':', '-')}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
