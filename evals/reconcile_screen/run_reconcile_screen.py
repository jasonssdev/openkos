"""Merged-body reconciliation model screen (#1563): forecast, run, score.

`merge` asks a model to rewrite two overlapping notes as one document
(`resolution/reconciliation.py::reconcile_merged_body`). The task key had no
harness and no model evidence; this is it. Each fixture case is two notes put
through production's own `reconcile_merged_body`, so the prompt, the length floor,
the stacked-heading refusal, the heading pin and the `Related` restoration
measured are the shipped ones. A call that fails or is refused is scored as the
stacked body production keeps in that case, never skipped.

Subcommands:

- `forecast`  the call count and Ollama wall-clock a run would cost, and the
              model-free baselines (the stack, one note alone, the longer note, a
              sentence-level union). Never contacts a model. `run --dry-run` is
              the same.
- `run`       one model, every case, N seeded runs; cached by row.
- `score`     the registered numbers and decision for the stored runs.
- `blind-sheet` / `adjudicate`  the blind second labelling: write the sheet the
              owner fills in, then compare it with the fixture's labels. No model;
              see `README.md`, "Second labelling".

Read `README.md` and `PREREGISTRATION-DRAFT.md` before trusting any number: the
labels are one author's until the owner's blind labelling is recorded. Model-free
check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import reconcile_blind_adjudication as adj  # noqa: E402
import reconcile_core as core  # noqa: E402
from harness_stamp import (  # noqa: E402
    TimedBackend,
    build_stamp,
    stamp_problems,
)

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.base import (  # noqa: E402
    BackendError,
    BackendUnavailable,
    is_timeout_failure,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.resolution import reconciliation as rec  # noqa: E402

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/reconcile-screen"
)
BASELINE: Final = "qwen3:8b"
CANDIDATES: Final = (
    "gemma4:26b-a4b",
    "qwen3:14b",
    "qwen3.6:27b",
    "qwen3.6:35b-a3b",
    "gemma2:27b",
)
DEFAULT_TIMEOUT: Final = 240.0
DEFAULT_RUNS: Final = 5
LOAD_S: Final = 60.0
ASSUMED_MEDIAN_LATENCY_S: Final[dict[str, float]] = {
    "qwen3:8b": 9.5,
    "gemma4:26b-a4b": 5.66,
    "qwen3:14b": 13.14,
    "qwen3.6:27b": 13.14,
    "qwen3.6:35b-a3b": 6.44,
    "gemma2:27b": 13.14,
}
"""ASSUMED per-call latencies, NOT measured for this task: twice the answer-call
medians #1560 recorded (`ask_model_screen/PREREGISTRATION-1596.md`), because a
reconciliation reply is a whole rewritten document (a few hundred words) rather
than a short answer. `qwen3.6:27b` and `gemma2:27b` borrow `qwen3:14b`'s because
they have none. A stage-0 timing of three calls per model replaces them before
any registered forecast."""

Row = dict[str, Any]


# --- one call ----------------------------------------------------------------


def run_item(llm: Any, case: core.Case) -> Row:
    """One production `reconcile_merged_body` over the case's two notes.

    A failed call is a row, never an exception: a transport failure is `backend`
    (`timeout` if it ran out of time), a reply production's guards refused is
    `refused`. The merged body is kept in the row so the labels can be rescored
    without a model."""
    timed = TimedBackend(llm)
    row: Row = {
        "case": case.id,
        "arm": "default",
        "output": None,
        "failure": None,
        "latency_s": 0.0,
    }
    try:
        output = rec.reconcile_merged_body(
            survivor_title=case.title,
            survivor_body=case.survivor_body,
            absorbed_body=case.absorbed_body,
            llm=timed,
        )
    except BackendError as exc:
        row["failure"] = "timeout" if is_timeout_failure(exc) else "backend"
    else:
        if output is None:
            row["failure"] = "refused"
        else:
            row["output"] = output
    row["latency_s"] = timed.call_latencies_s[-1] if timed.call_latencies_s else 0.0
    return row


def run_model(
    make_client: Callable[[int], Any],
    model: str,
    cases: Sequence[core.Case],
    *,
    runs: Sequence[int],
    existing: Sequence[Row] = (),
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Every (run, case) not already in `existing`, in a fixed order. The cache
    key is the row's identity, so an interrupted run resumes and a finished one
    costs nothing."""
    rows = list(existing)
    done = {(r["run"], r["arm"], r["case"]) for r in rows}
    for run in runs:
        client = make_client(run)
        for case in cases:
            if (run, "default", case.id) in done:
                continue
            row = run_item(client, case)
            row.update(model=model, run=run)
            rows.append(row)
            if on_row is not None:
                on_row(row)
    return rows


# --- storage -----------------------------------------------------------------


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def runs_path(runs_dir: pathlib.Path, model: str) -> pathlib.Path:
    return runs_dir / f"runs-{slug(model)}.json"


def load_runs(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {"stamp": None, "rows": []}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def save_runs(
    path: pathlib.Path, stamp: Mapping[str, Any] | None, rows: Sequence[Row]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"stamp": stamp, "rows": list(rows)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


# --- forecast ----------------------------------------------------------------


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_forecast(
    cases: Sequence[core.Case],
    *,
    models: Sequence[str],
    runs: int,
    latencies: Mapping[str, float],
) -> str:
    plan = core.forecast(
        n_cases=len(cases),
        runs=runs,
        median_latency_s={m: latencies[m] for m in models},
        load_s=LOAD_S,
    )
    summary = core.baseline_summary(cases)
    lines = [
        "# Forecast (no model contacted)",
        "",
        f"cases: {len(cases)}  claims: {sum(len(c.active) for c in cases)}  "
        f"class balance: {core.class_balance(cases)}  "
        f"flagged arguable: {sum(c.ambiguous for case in cases for c in case.active)}",
        f"runs: {runs}  models: {list(models)}",
        f"calls per model: {plan['calls_per_model']}  total calls: {plan['calls']}",
    ]
    for model in models:
        lines.append(
            f"  {model}: {plan['seconds'][model] / 60:.1f} min "
            f"(assumed median {latencies[model]:.2f} s/call + {LOAD_S:.0f} s load)"
        )
    lines += [
        f"total Ollama wall-clock: {plan['total_seconds'] / 3600:.2f} h "
        "(latencies assumed, not measured for this task)",
        "",
        "model-free baselines on this fixture (strict, first author's labels):",
    ]
    for name, row in summary["baselines"].items():
        lines.append(
            f"  {name:<14} {row['claims_kept']:>3} of {row['n_claims']} claims kept, "
            f"mean case score {row['mean_case_score']:.3f}"
        )
    lines += [
        f"strongest: {summary['strongest']}",
        f"cases no baseline merges perfectly: {summary['cases_no_baseline_perfect']} of {len(cases)}",
        f"claims the strongest baseline gets wrong: {summary['claims_strongest_wrong']}",
        f"claims every baseline gets wrong: {summary['claims_all_baselines_wrong']}",
    ]
    return "\n".join(lines)


# --- score -------------------------------------------------------------------


def render_report(
    rows_by_model: Mapping[str, Sequence[Row]],
    cases: Sequence[core.Case],
    *,
    baseline: str,
    resamples: int = 10_000,
) -> str:
    """The registered numbers for stored runs. Descriptive: the verdict lines
    apply the registered rule mechanically and adopt nothing."""
    ref_name, ref_scores = core.reference_baseline(cases)
    stack = core.baseline_case_scores(cases)["stack"]
    lines = [
        "# Merged-body reconciliation screen",
        "",
        "Labels are the first author's unless the fixture records a blind labelling:",
        "read every number as agreement with them, not as quality of prose. `strict`",
        "decides; `lenient` and `strict (1st)` are reported and decide nothing.",
        "",
        f"reference (strongest model-free baseline): {ref_name} "
        f"{_fmt(sum(ref_scores.values()) / len(ref_scores))}; the stack production keeps "
        f"on a refusal {_fmt(sum(stack.values()) / len(stack))}",
        "",
        "| model | strict | lenient | strict (1st) | retention | dedupe | fabrication | failures | median s | p95 s | stability |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summaries = {m: core.summarize(rows, cases) for m, rows in rows_by_model.items()}
    for model, s in summaries.items():
        lines.append(
            f"| {model} | {_fmt(s['strict_accuracy'])} | {_fmt(s['lenient_accuracy'])} "
            f"| {_fmt(s['strict_first_accuracy'])} | {_fmt(s['retention'])} "
            f"| {_fmt(s['dedupe_rate'])} | {_fmt(s['fabrication_rate'])} "
            f"| {_fmt(s['failure_rate'])} | {_fmt(s['median_latency_s'], 2)} "
            f"| {_fmt(s['p95_latency_s'], 2)} | {_fmt(s['stability'])} |"
        )
    for model, s in summaries.items():
        by_kind = s["retention_by_kind"]
        lines += [
            "",
            f"## {model}: retention by who states the claim",
            "",
            ", ".join(f"{k} {_fmt(by_kind[k])}" for k in core.KINDS),
            f"failures: {s['failures'] or 'none'}",
        ]
    lines += ["", "## Decision (registered rule, applied mechanically)", ""]
    for model, rows in rows_by_model.items():
        own = core.per_case_scores(rows, cases)
        gap = core.paired_bootstrap(own, ref_scores, resamples=resamples)
        lines.append(
            f"- {model}: gain over {ref_name} {_fmt(gap['point'])} "
            f"[{_fmt(gap['lo'])}, {_fmt(gap['hi'])}] -> "
            + (
                "model call justified"
                if core.justified(gap)
                else "model call NOT justified"
            )
        )
        if model == baseline or baseline not in rows_by_model:
            continue
        delta = core.paired_bootstrap(
            own,
            core.per_case_scores(rows_by_model[baseline], cases),
            resamples=resamples,
        )
        tripped = core.vetoes(summaries[model], summaries[baseline])
        verdict = (
            "SELECTED"
            if core.selected(delta, tripped) and core.justified(gap)
            else "not selected"
        )
        lines.append(
            f"  vs {baseline}: delta {_fmt(delta['point'])} "
            f"[{_fmt(delta['lo'])}, {_fmt(delta['hi'])}], vetoes {tripped or 'none'} -> {verdict}"
        )
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


class _Stub:
    """A scripted `LLMBackend`: answers per case from the prompt it was sent."""

    def __init__(
        self,
        cases: Sequence[core.Case],
        answer: Callable[[core.Case], str | BaseException],
    ) -> None:
        self.by_title = {c.title: c for c in cases}
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        user = messages[-1]["content"]
        subject = re.search(r"^SUBJECT: (.+)$", user, re.M)
        if subject is None or subject.group(1) not in self.by_title:
            raise ValueError(f"not a reconciliation prompt: {user[:80]!r}")
        self.prompts.append((messages[0]["content"], user))
        reply = self.answer(self.by_title[subject.group(1)])
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _padded(case: core.Case, body: str) -> str:
    """`body` plus anchor-free filler up to production's length floor, so a stub's
    reply clears the guard it is not testing."""
    need = (
        int(
            rec._MIN_LENGTH_RATIO
            * max(len(case.survivor_body), len(case.absorbed_body))
        )
        + 1
    )
    filler = " filler" * (max(0, need - len(body)) // 7 + 1)
    return body + "\n\n" + filler.strip()


def _client_of(stub: _Stub) -> Callable[[int], Any]:
    return lambda _run: stub


def _timeout() -> BackendUnavailable:
    exc = BackendUnavailable("timed out")
    exc.__cause__ = TimeoutError("read timed out")
    return exc


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"{'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    cases = core.load_fixture()
    n_claims = sum(len(c.active) for c in cases)

    # --- the fixture -----------------------------------------------------------
    check("fixture: every claim is scoreable", core.integrity_problems(cases) == [])
    check(
        "fixture: every piece is still verbatim in its source",
        core.provenance_problems(cases) == [],
    )
    check(
        "fixture: case titles are unique (the stub keys on them)",
        len({c.title for c in cases}) == len(cases),
    )
    broken = [
        core.Case(
            id=cases[0].id,
            title=cases[0].title,
            survivor=cases[0].survivor,
            absorbed=cases[0].absorbed,
            claims=(
                core.Claim(
                    id="planted",
                    kind="b_only",
                    claim="x",
                    anchors=("a literal no note contains",),
                    once=None,
                    alt_label=None,
                    adjudication=None,
                    retired=None,
                ),
            ),
        )
    ]
    check(
        "negative control: an anchor in neither note is reported",
        bool(core.integrity_problems(broken)),
    )
    summary = core.baseline_summary(cases)
    check(
        "fixture discriminates: >= 10 cases no baseline merges perfectly, >= 25 claims the strongest gets wrong",
        summary["cases_no_baseline_perfect"] >= 10
        and summary["claims_strongest_wrong"] >= 25,
    )

    # --- one call through production --------------------------------------------
    case = cases[0]
    oracle = _Stub(cases, lambda c: _padded(c, core.synthetic_merge(c)))
    row = run_item(oracle, case)
    system, user = oracle.prompts[0]
    check(
        "wiring: the system prompt sent is production's", system == rec._SYSTEM_PROMPT
    )
    check(
        "wiring: the user turn is SUBJECT, NOTE A (survivor), NOTE B (absorbed) verbatim",
        user
        == f"SUBJECT: {case.title}\n\nNOTE A:\n{case.survivor_body}\n\nNOTE B:\n{case.absorbed_body}",
    )
    check(
        "an accepted reply is stored as the output",
        row["failure"] is None and row["output"] is not None,
    )

    refused = run_item(_Stub(cases, lambda c: "   "), case)
    check(
        "an empty reply is a `refused` row",
        refused["failure"] == "refused" and refused["output"] is None,
    )
    short = run_item(_Stub(cases, lambda c: "too short"), case)
    check(
        "a reply under production's length floor is `refused`",
        short["failure"] == "refused",
    )
    stacked = run_item(
        _Stub(cases, lambda c: _padded(c, "## Merged content (x)")), case
    )
    check(
        "a reply that re-emits the stacked heading is `refused`",
        stacked["failure"] == "refused",
    )
    check(
        "a transport error is a `backend` row",
        run_item(_Stub(cases, lambda c: BackendError("down")), case)["failure"]
        == "backend",
    )
    check(
        "a deadline is a `timeout` row",
        run_item(_Stub(cases, lambda c: _timeout()), case)["failure"] == "timeout",
    )
    check(
        "a refused call is scored as the stack, not skipped",
        core.effective_text(case, refused) == core.stack_text(case),
    )

    # --- stub models through the whole pipeline ----------------------------------
    b_only = {c.id for case_ in cases for c in case_.active if c.kind == "b_only"}
    shared = {c.id for case_ in cases for c in case_.active if c.kind == "both"}
    models: dict[str, Callable[[core.Case], str | BaseException]] = {
        "oracle": lambda c: _padded(c, core.synthetic_merge(c)),
        "dropper": lambda c: _padded(c, core.synthetic_merge(c, drop=b_only)),
        "duplicator": lambda c: _padded(c, core.synthetic_merge(c, duplicate=shared)),
        "inventor": lambda c: _padded(
            c,
            core.synthetic_merge(
                c, invent="Released as version 9.9.9 via `fake_call()`."
            ),
        ),
        "refuser": lambda c: "",
        "failer": lambda c: BackendError("down"),
    }
    stored: dict[str, list[Row]] = {}
    for name, answer in models.items():
        stub = _Stub(cases, answer)
        stored[name] = run_model(_client_of(stub), name, cases, runs=[1, 2])
    sums = {name: core.summarize(rows, cases) for name, rows in stored.items()}
    check(
        "a run is every case once per seed",
        all(s["n_calls"] == 2 * len(cases) for s in sums.values()),
    )
    o = sums["oracle"]
    check(
        "oracle: strict 1.0, retention 1.0, dedupe 1.0, no fabrication, no failure",
        o["strict_accuracy"] == 1.0
        and o["retention"] == 1.0
        and o["dedupe_rate"] == 1.0
        and o["fabrication_rate"] == 0.0
        and o["failure_rate"] == 0.0,
    )
    d = sums["dropper"]
    check(
        "dropper: loses exactly the one-sided-B claims and nothing else",
        d["retention_by_kind"]["b_only"] == 0.0
        and d["retention_by_kind"]["a_only"] == 1.0
        and d["retention_by_kind"]["both"] == 1.0
        and abs(d["strict_accuracy"] - (1 - len(b_only) / n_claims)) < 0.05,
    )
    check(
        "duplicator: keeps every claim but states no shared claim once",
        sums["duplicator"]["retention"] == 1.0
        and sums["duplicator"]["dedupe_rate"] == 0.0,
    )
    check(
        "inventor: every body carries an invented figure",
        sums["inventor"]["fabrication_rate"] == 1.0 and o["fabrication_rate"] == 0.0,
    )
    stack_mean = sum(core.baseline_case_scores(cases)["stack"].values()) / len(cases)
    check(
        "refuser: every call falls back, and the score is exactly the stack's",
        sums["refuser"]["failure_rate"] == 1.0
        and abs(sums["refuser"]["strict_accuracy"] - stack_mean) < 1e-9,
    )
    check(
        "failer: every call fails as `backend` and also scores the stack",
        sums["failer"]["failures"] == {"backend": 2 * len(cases)}
        and abs(sums["failer"]["strict_accuracy"] - stack_mean) < 1e-9,
    )

    ref_name, ref_scores = core.reference_baseline(cases)
    gap = {
        n: core.paired_bootstrap(
            core.per_case_scores(r, cases), ref_scores, resamples=2000
        )
        for n, r in stored.items()
    }
    check("J: the oracle beats the strongest baseline", core.justified(gap["oracle"]))
    check(
        "J: a model that drops claims, repeats shared ones, or always falls back does not",
        not any(
            core.justified(gap[n])
            for n in ("dropper", "duplicator", "refuser", "failer")
        ),
    )
    base_delta = core.paired_bootstrap(
        core.per_case_scores(stored["oracle"], cases),
        core.per_case_scores(stored["dropper"], cases),
        resamples=2000,
    )
    check(
        "selection: the oracle replaces a dropper with no veto",
        core.selected(base_delta, core.vetoes(sums["oracle"], sums["dropper"])),
    )
    check(
        "vetoes: failures trip V1, an invented figure trips V3, slowness trips V2",
        "V1" in core.vetoes(sums["failer"], o)
        and "V3" in core.vetoes(sums["inventor"], o)
        and "V2"
        in core.vetoes({**o, "median_latency_s": 21.0}, {**o, "median_latency_s": 10.0})
        and core.vetoes(o, o) == [],
    )
    check(
        "selection: a model with a veto is not selected even with a positive gain",
        not core.selected(base_delta, ["V3"]),
    )

    # --- cache and storage ---------------------------------------------------------
    counting = _Stub(cases, models["oracle"])
    again = run_model(
        lambda _run: counting, "oracle", cases, runs=[1, 2], existing=stored["oracle"]
    )
    check(
        "cache: a finished run costs no call",
        counting.prompts == [] and len(again) == len(stored["oracle"]),
    )
    more = run_model(
        lambda _run: counting,
        "oracle",
        cases,
        runs=[1, 2, 3],
        existing=stored["oracle"],
    )
    check(
        "cache: a new seed runs only that seed",
        len(counting.prompts) == len(cases) and len(more) == 3 * len(cases),
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = runs_path(pathlib.Path(tmp), "gemma4:26b-a4b")
        save_runs(path, {"stamp_version": 1}, stored["oracle"])
        loaded = load_runs(path)
        check(
            "storage: rows and the stamp round-trip",
            loaded["rows"] == stored["oracle"]
            and loaded["stamp"] == {"stamp_version": 1},
        )
        check(
            "storage: a model tag becomes a file-safe slug",
            path.name == "runs-gemma4_26b-a4b.json",
        )
        check(
            "storage: a missing file is an empty record",
            load_runs(pathlib.Path(tmp) / "none.json") == {"stamp": None, "rows": []},
        )

    # --- rescoring without a model --------------------------------------------------
    rescored = core.summarize(stored["oracle"], cases)
    check("rescore: the stored bodies score the same with no model", rescored == o)

    # --- the report and the forecast -------------------------------------------------
    report = render_report(
        {BASELINE: stored["dropper"], "gemma4:26b-a4b": stored["oracle"]},
        cases,
        baseline=BASELINE,
        resamples=500,
    )
    check(
        "report: names the reference and applies J and selection",
        ref_name in report
        and "model call justified" in report
        and "SELECTED" in report,
    )
    fc = render_forecast(
        cases,
        models=[BASELINE, *CANDIDATES],
        runs=DEFAULT_RUNS,
        latencies=ASSUMED_MEDIAN_LATENCY_S,
    )
    check(
        "forecast: calls are cases x runs per model",
        f"calls per model: {len(cases) * DEFAULT_RUNS}" in fc
        and "assumed, not measured" in fc,
    )
    check(
        "forecast: every candidate has an assumed latency",
        all(m in ASSUMED_MEDIAN_LATENCY_S for m in (BASELINE, *CANDIDATES)),
    )

    # --- the blind sheet ------------------------------------------------------------
    sheet = adj.build_sheet(cases)
    ids = adj.opaque_ids(cases)
    check(
        "sheet: every claim once",
        all(sheet.count(f"### Claim {o_}") == 1 for o_ in ids.values())
        and len(ids) == n_claims,
    )
    check(
        "sheet: no claim id, kind or anchor field name reaches it",
        not any(c.id in sheet for case_ in cases for c in case_.claims)
        and not {"a_only", "b_only", "alt_label", "anchors", "once"}
        & set(adj.sheet_chrome(sheet)),
    )
    leak = "LEAKMARK-q7"
    first = cases[0]
    leaked = [
        core.Case(
            id=f"{leak}-case",
            title=first.title,
            survivor=tuple(
                core.Piece(source={"kind": "file", "ref": f"{leak}.md"}, text=p.text)
                for p in first.survivor
            ),
            absorbed=first.absorbed,
            claims=(
                core.Claim(
                    id=f"{leak}-both",
                    kind=first.claims[0].kind,
                    claim=first.claims[0].claim,
                    anchors=first.claims[0].anchors,
                    once=first.claims[0].once,
                    alt_label="a_only",
                    adjudication=None,
                    retired=None,
                ),
                *first.claims[1:],
            ),
        ),
        *cases[1:],
    ]
    check(
        "negative control: a planted leak in a claim id, case id or source path does not reach the sheet",
        leak not in adj.build_sheet(leaked),
    )

    token = adj.TOKEN

    def fill(pick: Callable[[core.Claim], str]) -> str:
        by_opaque = {ids[c.id]: pick(c) for case_ in cases for c in case_.active}
        out, current = [], None
        for line in sheet.splitlines():
            if line.startswith("### Claim "):
                current = line.split()[-1]
            out.append(
                f"Label: {by_opaque[current]}"
                if line.startswith("Label:") and current
                else line
            )
        return "\n".join(out) + "\n"

    agreed = adj.adjudicate(cases, adj.parse_sheet(fill(lambda c: token[c.first_kind])))
    check(
        "adjudicate: a sheet that agrees everywhere has kappa 1 and no disagreement",
        agreed.kappa == 1.0 and not agreed.disagreements and not agreed.problems,
    )
    victim = cases[0].claims[0]
    flipped = adj.adjudicate(
        cases,
        adj.parse_sheet(
            fill(
                lambda c: token[
                    "b_only"
                    if c.id == victim.id and c.kind != "b_only"
                    else c.first_kind
                ]
            )
        ),
    )
    check(
        "adjudicate: one changed label is the one disagreement, with kappa below 1",
        [x.claim for x in flipped.disagreements] == [victim.id]
        and flipped.kappa is not None
        and flipped.kappa < 1.0,
    )
    check(
        "adjudicate: a blank label rejects the sheet and scores nothing",
        bool(adj.adjudicate(cases, adj.parse_sheet(fill(lambda c: ""))).problems),
    )
    got, why = adj.import_labels(
        cases, adj.parse_sheet(fill(lambda c: token[c.first_kind]))
    )
    check(
        "import-sheet: a filled sheet maps every claim id to its label",
        why == []
        and got == {c.id: token[c.first_kind] for case_ in cases for c in case_.active},
    )
    check(
        "import-sheet: an unfilled sheet returns problems and no labels",
        adj.import_labels(cases, adj.parse_sheet(sheet))[0] == {}
        and adj.import_labels(cases, adj.parse_sheet(sheet))[1] != [],
    )
    check(
        "adjudicate: a label outside A, B, AB rejects the sheet",
        bool(adj.adjudicate(cases, adj.parse_sheet(fill(lambda c: "both"))).problems),
    )
    check(
        "sheet: refused inside the repository, accepted outside",
        adj.outside_repo_problem(HERE / "sheet.md") is not None
        and adj.outside_repo_problem(pathlib.Path(tempfile.gettempdir()) / "s.md")
        is None,
    )

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


# --- cli ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="stage")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)

    fc = sub.add_parser("forecast", help="call count and wall-clock; no model")
    common(fc)
    fc.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    fc.add_argument("--runs", type=int, default=DEFAULT_RUNS)

    run = sub.add_parser("run", help="one model, every case")
    common(run)
    run.add_argument("--model", required=True)
    run.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    run.add_argument("--first-run", type=int, default=1)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--dry-run", action="store_true", help="print the forecast only")

    score = sub.add_parser("score", help="registered numbers for stored runs")
    common(score)
    score.add_argument("--models", default=",".join([BASELINE, *CANDIDATES]))
    score.add_argument("--baseline", default=BASELINE)
    score.add_argument("--resamples", type=int, default=10_000)

    sheet = sub.add_parser(
        "blind-sheet", help="write the blind labelling sheet for the owner"
    )
    sheet.add_argument("--out", type=pathlib.Path, required=True)
    sheet.add_argument("--seed", type=int, default=adj.SEED)

    adjudicate = sub.add_parser(
        "adjudicate", help="compare a filled sheet with the first author's labels"
    )
    adjudicate.add_argument("--sheet", type=pathlib.Path, required=True)
    adjudicate.add_argument("--seed", type=int, default=adj.SEED)

    imported = sub.add_parser(
        "import-sheet",
        help="write a filled sheet's labels, keyed by fixture id, to a JSON file",
    )
    imported.add_argument("--sheet", type=pathlib.Path, required=True)
    imported.add_argument("--out", type=pathlib.Path, required=True)
    imported.add_argument("--labeller", required=True)
    imported.add_argument("--seed", type=int, default=adj.SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.stage is None:
        parser.error("choose a subcommand, or --self-test")
    cases = core.load_fixture()

    if args.stage == "blind-sheet":
        problem = adj.outside_repo_problem(args.out)
        if problem:
            raise SystemExit(problem)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(adj.build_sheet(cases, seed=args.seed), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.stage == "adjudicate":
        result = adj.adjudicate(
            cases,
            adj.parse_sheet(args.sheet.read_text(encoding="utf-8")),
            seed=args.seed,
        )
        print(adj.render(result))
        return 1 if result.problems else 0
    if args.stage == "import-sheet":
        labels, problems = adj.import_labels(
            cases,
            adj.parse_sheet(args.sheet.read_text(encoding="utf-8")),
            seed=args.seed,
        )
        if problems:
            print("SHEET REJECTED, nothing imported:\n" + "\n".join(problems))
            return 1
        args.out.write_text(
            json.dumps(
                {"labeller": args.labeller, "seed": args.seed, "labels": labels},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"imported {len(labels)} labels into {args.out}")
        return 0

    if args.stage == "forecast" or (args.stage == "run" and args.dry_run):
        models = args.models.split(",") if args.stage == "forecast" else [args.model]
        unknown = [m for m in models if m not in ASSUMED_MEDIAN_LATENCY_S]
        latencies = {**ASSUMED_MEDIAN_LATENCY_S, **dict.fromkeys(unknown, 13.14)}
        print(
            render_forecast(cases, models=models, runs=args.runs, latencies=latencies)
        )
        return 0

    if args.stage == "run":
        problems = core.integrity_problems(cases) + core.provenance_problems(cases)
        if problems:
            raise SystemExit("fixture drifted:\n" + "\n".join(problems))
        stamp = build_stamp(
            model=args.model, prompts={"reconciliation/system": rec._SYSTEM_PROMPT}
        )
        if stamp_problems(stamp):
            raise SystemExit(f"cannot stamp the run: {stamp_problems(stamp)}")
        path = runs_path(args.runs_dir, args.model)
        stored = load_runs(path)
        if stored["stamp"] is not None and stored["stamp"] != stamp:
            raise SystemExit(
                f"{path} was measured under another identity; use a new --runs-dir"
            )

        def make_client(run: int) -> OllamaClient:
            return OllamaClient(
                model=args.model,
                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                context_window=DEFAULT_CONTEXT_WINDOW,
                timeout=args.timeout,
                seed=run,
            )

        rows = run_model(
            make_client,
            args.model,
            cases,
            runs=range(args.first_run, args.first_run + args.runs),
            existing=stored["rows"],
            on_row=lambda r: print(f"{r['run']} {r['case']} -> {r['failure'] or 'ok'}"),
        )
        save_runs(path, stamp, rows)
        print(f"wrote {path}")
        return 0

    rows_by_model: dict[str, list[Row]] = {}
    for model in args.models.split(","):
        stored = load_runs(runs_path(args.runs_dir, model))
        if stored["rows"]:
            rows_by_model[model] = stored["rows"]
    if not rows_by_model:
        raise SystemExit(f"no stored runs under {args.runs_dir}")
    print(
        render_report(
            rows_by_model, cases, baseline=args.baseline, resamples=args.resamples
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
