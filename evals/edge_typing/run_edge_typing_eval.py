"""Scores `suggest_edge_types` so a prompt change to it can be measured.

Issue #508 asked for confidence-threshold auto-acceptance and named a
cap-harness A/B as the gate. That gate did not exist: `evals/` scored
EXTRACTION and nothing scored this suggester at all, so any change to its
prompt would have been adopted on intuition -- which this project has
already paid for once.

Three numbers, per arm:

**Accuracy** against `fixtures.LabelledEdge.expected_type`. The labels are
CONSTRUCTED (see `fixtures.py`), so read this as "does the rubric still
decide these pairs the way the rubric says it should", not as field
accuracy.

**Stability**, the modal type's share across runs for each edge. Needs no
labels at all, which makes it the number to trust when the labels are
arguable: a prompt that makes the model less sure of itself shows up here
even on pairs nobody has adjudicated. Same self-contradiction logic as
`extraction_cap/measure_acronym_fabrication.py`.

**Type distribution**, the share of each relation type over all emissions.
`related_to` is 67% of accepted edges on a real bundle
(`edge_typing.py:146`), and the rubric's stated aim is NOT to drive that
share down -- so a prompt change that moves it sharply either way is a
finding to explain before adopting, in either direction.

Usage:

    python evals/edge_typing/run_edge_typing_eval.py --arm baseline --runs 5

**The pinned-language arm (#812).** `--rationale-language Spanish` appends
one sentence to the system prompt, telling the model which language to write
each `rationale` in. It exists because #812's fix is a prompt change on a
common path, and this repository does not adopt one on intuition -- a longer
extraction prompt has already been measured here to lose its A/B. Run it
against a baseline arm from the same session:

    python evals/edge_typing/run_edge_typing_eval.py --arm baseline --runs 5
    python evals/edge_typing/run_edge_typing_eval.py --arm es --runs 5 \
        --rationale-language Spanish

Read **stability** first, as always, and read accuracy second: the labels
are constructed, and the pinned arm changes only the language of a field
the labels do not score. What the arm is looking for is COLLATERAL -- a
longer prompt moving the `type` decision, or the type distribution, at all.
It should not, and the reason to measure is that the last prompt everyone
was sure about did.

Writes `results/edge-typing-<arm>-<stamp>.md` and a sibling `runs-*.json`
in the same shape the cap harness uses, so the stored emissions stay
re-analyzable without re-spending them.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero: an insert would put the evals root
# AHEAD of this harness's own directory, so a module added at the root
# would shadow a same-named one beside this file (`fixtures.py` is the
# obvious candidate).
sys.path.append(str(REPO_ROOT / "evals"))

from fixtures import DOCS, EDGES  # noqa: E402
from harness_report import arm_identity_line  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.graph.base import Edge  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf, types  # noqa: E402
from openkos.resolution.edge_typing import (  # noqa: E402
    _DIRECTION_TYPE_SIGNATURES,
    _contradicts_object_type_direction,
    suggest_edge_types,
)

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 5

TYPES_BY_ID: dict[str, str] = {doc.concept_id: doc.okf_type for doc in DOCS}
"""`concept_id` -> the OKF type its document declares (#990).

One map, built once: the payload, the regime split and the self-test all
read it, and three hand-built copies of the same lookup would be three
chances for the report to bucket an edge differently from the JSON beside
it."""


def _unknown_endpoints() -> list[str]:
    """Every `EDGES` endpoint id with no document behind it in `DOCS`.

    A renamed or mistyped endpoint is exactly the fixture-authoring error
    this harness's self-test exists to catch, and an unguarded
    `TYPES_BY_ID[...]` turns it into a `KeyError` traceback -- which aborts
    before the collected failures are printed, so the run reports none of
    what it had already found. Worse in `main()`, where the same lookup sits
    after the model calls have been spent."""
    known = set(TYPES_BY_ID)
    return sorted(
        {
            endpoint
            for edge in EDGES
            for endpoint in (edge.source_id, edge.target_id)
            if endpoint not in known
        }
    )


def _materialize_bundle(bundle_dir: pathlib.Path) -> None:
    """Materialize every fixture document as a real OKF bundle.

    Frontmatter is rendered by the SHIPPED `okf.dump_frontmatter` rather than
    by an f-string interpolating the title unquoted. No title here carries a
    colon, so this corpus never lost a document -- but it sat one fixture
    edit away from the silent drop #895 found in three sibling harnesses.
    `--self-test` now asks the shipped reader whether every document parses,
    so a fixture edit that reintroduces one cannot pass unnoticed."""
    for doc in DOCS:
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        # `doc.okf_type`, not a hardcoded "Concept" (issue #990). Every
        # document used to be materialized as a Concept regardless of the
        # folder its id named, so a rule reading the two ends' types had
        # nothing to read and the cross-type inversions this harness exists
        # to catch were not representable.
        frontmatter = okf.dump_frontmatter({"type": doc.okf_type, "title": doc.title})
        path.write_text(f"{frontmatter}# {doc.title}\n\n{doc.body}\n", encoding="utf-8")


def _run_once(
    bundle_dir: pathlib.Path,
    client: OllamaClient,
    rationale_language: str | None = None,
) -> list[tuple[str | None, float, str]]:
    """One pass over every labelled edge; returns `(suggested_type,
    confidence, rationale)` per edge, `(None, 0.0, "")` where the reply
    degraded.

    `confidence` is `0.0` on the baseline arm, whose prompt never asks for
    one -- the field fails closed, so a baseline run reads as "no stated
    confidence" rather than as a confident zero.

    The RATIONALE is retained (issue #807). It was previously discarded
    here, which made this harness unable to score the one thing #807 is
    about: a reply whose rationale argues for the reverse of the direction
    it asserts. Storing it costs nothing -- it is already in the reply --
    and keeps the stored runs re-analyzable for that question without
    re-spending them, exactly as the confidences arrays do.

    `rationale_language` (issue #812) is the pinned-language arm. `None` --
    the default, and every arm measured before #812 -- sends the system
    prompt byte for byte as it shipped, so the baseline in `results/` stays
    the baseline. A pinned value appends one sentence to that prompt, which
    is the whole point of measuring it: this repository does not adopt a
    longer prompt on a common path on intuition, and the operator turning
    the key on is entitled to the accuracy and stability cost."""
    edges = [Edge(source_id=e.source_id, target_id=e.target_id) for e in EDGES]
    batch = suggest_edge_types(
        edges,
        bundle_dir=bundle_dir,
        llm=client,
        rationale_language=rationale_language,
    )
    by_pair = {
        # `getattr` on purpose: `EdgeSuggestion` carries no `confidence`
        # today, and the #508 investigation concluded it should not. An arm
        # that re-adds the field to test a new prompt is measurable here
        # without touching this runner; without it, every reply reads as
        # "no stated confidence" rather than as a confident zero.
        (s.edge.source_id, s.edge.target_id): (
            s.suggested_type,
            float(getattr(s, "confidence", 0.0)),
            s.rationale,
        )
        for s in batch.results
    }
    return [by_pair.get((e.source_id, e.target_id), (None, 0.0, "")) for e in EDGES]


def _self_test() -> int:
    """Model-free: does the fixture corpus actually reach the index?

    Materializing is not indexing, and only the second one is what a
    measurement rests on. A document whose frontmatter does not parse is
    counted `skipped` by `reindex` and nothing reads that number, which is
    how three sibling harnesses each measured a corpus a whole document type
    short of the one they documented (#895). Counting files on disk cannot
    catch that. Ask the shipped READER."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = pathlib.Path(tmp) / "bundle"
        _materialize_bundle(bundle)
        materialized = len(list(bundle.rglob("*.md")))
        scans = list(okf._iter_docs(bundle))
        unparseable = sorted(
            okf.concept_id_for(scan.path, bundle)
            for scan in scans
            if scan.read_error is not None or scan.parse_error is not None
        )
        # What was actually WRITTEN, read back with the shipped reader --
        # not what `DOCS` says should have been. The distinction is the
        # whole of #990: `_materialize_bundle` hardcoded `"Concept"` for
        # every document while the fixtures named four folders, and no
        # check compared the two.
        written_types = {
            okf.concept_id_for(scan.path, bundle): (scan.metadata or {}).get("type")
            for scan in scans
            if scan.metadata is not None
        }
    failures: list[str] = []
    if materialized != len(DOCS):
        failures.append(
            f"the bundle materializes {materialized} files for {len(DOCS)} documents"
        )
    if unparseable:
        failures.append(
            "these documents do not parse, so they never enter the index: "
            f"{unparseable}"
        )

    # Issue #990. The two checks above passed for the whole life of the
    # all-`Concept` corpus, which is exactly the point: a harness discovers
    # its own corpus from `DOCS`, so a corpus that has lost the regime it
    # is supposed to measure still reads as healthy. These three ask
    # whether the corpus can still answer the question.
    mismatched = sorted(
        f"{doc.concept_id} (type {doc.okf_type} wants "
        f"{types.TYPE_TO_LINK_DIR.get(doc.okf_type, '?')}/)"
        for doc in DOCS
        if doc.concept_id.split("/", 1)[0] != types.TYPE_TO_LINK_DIR.get(doc.okf_type)
    )
    if mismatched:
        failures.append(
            "these documents' folder prefix and frontmatter type disagree, so "
            "they would be scored under the wrong one: " + ", ".join(mismatched)
        )
    miswritten = sorted(
        f"{doc.concept_id} (declares {doc.okf_type}, "
        f"materialized as {written_types.get(doc.concept_id)!r})"
        for doc in DOCS
        if written_types.get(doc.concept_id) != doc.okf_type
    )
    if miswritten:
        failures.append(
            "these documents reached the index carrying a type they do not "
            "declare: " + ", ".join(miswritten)
        )
    distinct_types = {doc.okf_type for doc in DOCS}
    if len(distinct_types) < 2:
        failures.append(
            "every document carries the same type "
            f"({sorted(distinct_types)}), so a rule that reads the two ends' "
            "types has nothing to read and cannot be measured here"
        )
    unknown = _unknown_endpoints()
    if unknown:
        failures.append(
            "these labelled-edge endpoints have no document in `DOCS`, so they "
            f"can be neither materialized nor typed: {unknown}"
        )
    # Guarded on `unknown`: with a dangling endpoint the type lookup below
    # would raise instead of reporting, and a self-test that crashes tells
    # the author less than one that lists what is wrong.
    cross_type = (
        []
        if unknown
        else [
            edge
            for edge in EDGES
            if TYPES_BY_ID[edge.source_id] != TYPES_BY_ID[edge.target_id]
        ]
    )
    if not cross_type and not unknown:
        failures.append(
            "no labelled edge joins two documents of different types, so every "
            "reported inversion's own regime is absent from this corpus"
        )

    # Issue #991: does the object-type direction-signature check actually do
    # what it exists to do? Model-free by construction (the check reads only
    # the two endpoints' OKF types, never an LLM reply), so it belongs in
    # THIS self-test rather than behind a second flag the harness sweep
    # would never discover (`evals/run_self_tests.py` only ever looks for
    # `--self-test`).
    #
    # Only a CROSS-type edge can exercise this rule at all -- same-type
    # pairs pass through unchecked by construction (`_DIRECTION_TYPE_
    # SIGNATURES`'s own docstring) -- so both counts below walk `cross_type`
    # alone. Diluting them with the 23 same-type edges this rule was never
    # asked to move would be exactly the "green by absence" this project has
    # already paid for once (#895): the exposure has to be named, not just
    # a pass/fail.
    legitimate = (
        []
        if unknown
        else [
            edge
            for edge in cross_type
            if edge.expected_type in _DIRECTION_TYPE_SIGNATURES
        ]
    )
    harmed = [
        edge
        for edge in legitimate
        if _contradicts_object_type_direction(
            edge.expected_type, TYPES_BY_ID[edge.source_id], TYPES_BY_ID[edge.target_id]
        )
    ]
    if harmed:
        failures.append(
            "the direction-signature check would swap a LEGITIMATE edge: "
            + ", ".join(
                f"{e.source_id} -> {e.target_id} ({e.expected_type})" for e in harmed
            )
        )
    inversions = (
        []
        if unknown
        else [
            edge for edge in cross_type if edge.trap_type in _DIRECTION_TYPE_SIGNATURES
        ]
    )
    prevented = [
        edge
        for edge in inversions
        if _contradicts_object_type_direction(
            edge.trap_type, TYPES_BY_ID[edge.source_id], TYPES_BY_ID[edge.target_id]
        )
    ]
    if not unknown and not inversions:
        failures.append(
            "no reversed-orientation probe uses a relation type this check "
            "declares a signature for, so a regression in it cannot be caught here"
        )
    elif len(prevented) != len(inversions):
        missed = [edge for edge in inversions if edge not in prevented]
        failures.append(
            "the direction-signature check misses an inversion it should catch: "
            + ", ".join(
                f"{e.source_id} -> {e.target_id} (trap {e.trap_type})" for e in missed
            )
        )

    for failure in failures:
        print(f"FAIL: {failure}")
    total = 9
    print(f"self-test: {total - len(failures)}/{total} passed")
    if not failures:
        print(
            f"corpus: {len(DOCS)} documents across {len(distinct_types)} types, "
            f"{len(EDGES)} labelled edges, {len(cross_type)} of them cross-type"
        )
        # Named explicitly, not folded into the pass count above: a green
        # self-test here means "0 of 0 harmed" is just as reachable as "0 of
        # 6 harmed", and only the corpus size beside it tells them apart
        # (the honest-measurement requirement issue #991 asks for).
        print(
            f"direction-signature check (#991): {len(prevented)} of "
            f"{len(inversions)} inversions prevented, {len(harmed)} of "
            f"{len(legitimate)} legitimate edges harmed, exposure "
            f"{len(cross_type)} of {len(EDGES)} edges (cross-type only)"
        )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default="baseline", help="label for this arm")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--rationale-language",
        default=None,
        help=(
            "pin the language the model writes each `rationale` in (issue "
            "#812), e.g. --rationale-language Spanish. Omit it for the "
            "unpinned baseline: every arm stored in results/ was measured "
            "with no language pinned, and the unpinned prompt is byte-"
            "identical to the one that ships."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the fixture corpus is fully indexable; no model, no network",
    )
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())

    # The same guard `_self_test` reports, enforced HERE too and before any
    # model call. `_unknown_endpoints`' own docstring names this site as the
    # worse of the two -- a dangling endpoint raises `KeyError` inside the
    # scoring loop, after every call for the run has been spent and before
    # any JSON or report is written -- and the first version of this change
    # then left exactly this site unguarded. A run that cannot be scored
    # should refuse before it is paid for, not after.
    unknown = _unknown_endpoints()
    if unknown:
        print(
            "FAIL: these labelled-edge endpoints have no document in `DOCS`, "
            f"so no run over them can be scored: {unknown}"
        )
        raise SystemExit(1)

    # Production's own generation ceiling and context window, not the client's
    # opted-out defaults (#700) -- see the same note in
    # `evals/contradictions/run_contradictions_eval.py`. It matters more here
    # than anywhere: this harness's whole purpose is comparing models against
    # each other, and an unpinned window lets each candidate reserve whatever
    # its own Modelfile ships, so the arms would not be measured under one
    # condition.
    client = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    observed: list[list[tuple[str | None, float, str]]] = []
    latencies: list[float] = []

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = pathlib.Path(tmp) / "bundle"
        bundle_dir.mkdir(parents=True)
        _materialize_bundle(bundle_dir)
        for index in range(args.runs):
            started = time.monotonic()
            observed.append(_run_once(bundle_dir, client, args.rationale_language))
            latencies.append(time.monotonic() - started)
            print(f"  run {index + 1}/{args.runs} done ({latencies[-1]:.1f}s)")

    per_edge: dict[int, list[tuple[str | None, float, str]]] = defaultdict(list)
    for run in observed:
        for position, pair in enumerate(run):
            per_edge[position].append(pair)

    correct = 0
    stabilities: list[float] = []
    distribution: Counter[str] = Counter()
    # Issue #990: the same numbers again, split by whether an edge's two
    # ends carry the SAME OKF type or different ones. Reported apart rather
    # than folded in, because the cross-type edges are a small share of the
    # corpus and an average over both would hide them -- every inversion
    # reported against a real bundle is cross-type, and a rule reading the
    # two ends' types can only move that half.
    regimes: dict[str, dict[str, int]] = {
        "same-type": {"edges": 0, "correct": 0, "total": 0},
        "cross-type": {"edges": 0, "correct": 0, "total": 0},
    }
    for bucket in regimes.values():
        bucket["trap_hits"] = 0
        bucket["trap_total"] = 0
    rows: list[dict[str, object]] = []

    for position, labelled in enumerate(EDGES):
        pairs = per_edge[position]
        answers = [a for a, _, _ in pairs]
        counts = Counter(a for a in answers if a is not None)
        distribution.update(counts)
        modal, modal_count = counts.most_common(1)[0] if counts else ("<none>", 0)
        stability = modal_count / len(answers) if answers else 0.0
        stabilities.append(stability)
        hits = sum(1 for a in answers if a == labelled.expected_type)
        correct += hits
        # Accumulated HERE, in the pass that already computes `hits`, and
        # not in a second pass of its own (#990). The regime rows are the
        # headline new signal of this change, and a separate re-derivation
        # of `correct`/`total` could drift from these without any number
        # looking wrong -- the same duplication this repository has paid for
        # elsewhere, at the scale of a report cell.
        bucket = regimes[
            "same-type"
            if TYPES_BY_ID[labelled.source_id] == TYPES_BY_ID[labelled.target_id]
            else "cross-type"
        ]
        bucket["edges"] += 1
        bucket["correct"] += hits
        bucket["total"] += len(answers)
        rows.append(
            {
                "source_id": labelled.source_id,
                "target_id": labelled.target_id,
                # Stored, not re-derived at read time (#990): a run's regime
                # split has to survive a later fixture edit, and the types
                # this run actually saw are not recoverable from `DOCS`
                # afterwards. Same reason the rationales are kept -- a
                # stored run stays re-analyzable without re-spending it.
                "source_type": TYPES_BY_ID[labelled.source_id],
                "target_type": TYPES_BY_ID[labelled.target_id],
                "expected": labelled.expected_type,
                "confusion": labelled.confusion,
                "modal": modal,
                "stability": stability,
                "accuracy": hits / len(answers) if answers else 0.0,
                "answers": answers,
                "confidences": [c for _, c, _ in pairs],
                "rationales": [r for _, _, r in pairs],
            }
        )

    total = len(EDGES) * args.runs
    accuracy = correct / total if total else 0.0
    mean_stability = statistics.fmean(stabilities) if stabilities else 0.0
    emissions = sum(distribution.values())

    # Issue #561: over the reversed-orientation probes alone, how often did
    # the model emit the asymmetric type that would only be correct with
    # the edge flipped? This is the direction-inversion rate the forward
    # fixture could not see -- accuracy on those probes measures honesty,
    # trap hits measure inversion, and the two are reported separately
    # because an honest-but-wrong `references`/`related_to` mixup is a
    # different (and much cheaper) failure than a backwards assertion.
    trap_total = 0
    trap_hits = 0
    for position, labelled in enumerate(EDGES):
        if labelled.trap_type is None:
            continue
        bucket = regimes[
            "same-type"
            if TYPES_BY_ID[labelled.source_id] == TYPES_BY_ID[labelled.target_id]
            else "cross-type"
        ]
        for answer, _, _ in per_edge[position]:
            trap_total += 1
            bucket["trap_total"] += 1
            if answer == labelled.trap_type:
                trap_hits += 1
                bucket["trap_hits"] += 1

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{args.arm}-{stamp}-{args.model.replace(':', '-')}"

    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(
            {
                "arm": args.arm,
                "model": args.model,
                "runs": args.runs,
                "generated_at": stamp,
                # Part of the arm's identity, not trivia (#700) -- see the same
                # note in `evals/contradictions/run_contradictions_eval.py`.
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                # Part of the arm's identity too (#812): a pinned language
                # is a different system prompt, so a stored run that did not
                # say which one it used could not be told apart from a
                # baseline run afterwards. `null` IS the answer for every
                # arm measured before #812.
                "rationale_language": args.rationale_language,
                "regimes": regimes,
                "outcomes": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# `suggest_edge_types` eval — arm `{args.arm}` (#508)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{args.runs} runs**"
        f" over {len(EDGES)} labelled edges.",
        "",
        # Part of the arm's identity, not trivia (#700/#740): the JSON beside
        # this file has recorded both since #738, but a reader who opens only
        # the report cannot otherwise tell this run apart from a pre-#738 one
        # measured under whatever window each candidate model happened to ship.
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        # Stated in the report and not only in the JSON, on #740's own
        # reasoning: the artifact a person opens must say which prompt it
        # measured. The unpinned case renders "**none** — the baseline
        # prompt, byte-identical to what ships" rather than a blank, so an
        # unpinned run reads as a deliberate configuration instead of a
        # field the runner forgot to fill in.
        (
            f"Rationale language pinned: **{args.rationale_language}** (#812)."
            if args.rationale_language is not None
            else "Rationale language pinned: **none** — the baseline prompt,"
            " byte-identical to what ships (#812)."
        ),
        "",
        "Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read"
        " accuracy as rubric-consistency, and trust **stability** when a"
        " label is arguable: it needs no labels at all.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| type accuracy vs label | {accuracy:.2f} |",
        f"| mean stability (modal share) | {mean_stability:.2f} |",
        f"| degraded replies | {total - emissions} of {total} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        *(
            [
                f"| **direction-trap hits (reversed probes)** | "
                f"**{trap_hits} of {trap_total} "
                f"({trap_hits / trap_total:.2f})** |"
            ]
            if trap_total
            else []
        ),
        "",
        "**No stated-confidence metric is reported here, by construction"
        " (#740).** `EdgeSuggestion` carries no `confidence` field and the"
        " #508 investigation concluded it should not gain one, so every arm"
        " run so far reads the `getattr` default. Until #740 this report"
        " printed `0.00` for CORRECT and `0.00` for WRONG answers, which"
        " reads like a calibration finding and is instead a column that"
        " could not vary. The per-edge `confidences` arrays stay in"
        " `runs-*.json` as the seam for an arm that re-adds the field; the"
        " sibling `evals/contradictions/` column, which does measure a real"
        " reply field, is unaffected.",
        "",
        "## By type regime (#990)",
        "",
        "Every inversion reported against a real bundle joins two objects of"
        " DIFFERENT types (`organizations/... -> people/...`,"
        " `concepts/... -> projects/...`). Before #990 this corpus was"
        " entirely `Concept`, so that regime was absent and a rule reading"
        " the two ends' types had nothing here to move. These rows are split"
        " so it cannot move without the report saying so.",
        "",
        "| regime | edges | accuracy | trap hits |",
        "| --- | --- | --- | --- |",
        *(
            f"| {name} | {bucket['edges']} | "
            + (
                f"{bucket['correct'] / bucket['total']:.2f} "
                f"({bucket['correct']} of {bucket['total']})"
                if bucket["total"]
                else "n/a"
            )
            + " | "
            + (
                f"{bucket['trap_hits']} of {bucket['trap_total']} "
                f"({bucket['trap_hits'] / bucket['trap_total']:.2f})"
                if bucket["trap_total"]
                else "no reversed probes"
            )
            + " |"
            for name, bucket in regimes.items()
        ),
        "",
        "## Type distribution",
        "",
        "| type | emissions | share |",
        "| --- | --- | --- |",
    ]
    for name, count in distribution.most_common():
        lines.append(f"| `{name}` | {count} | {count / emissions:.2f} |")

    lines += [
        "",
        "## Per edge",
        "",
        "| pair | probes | expected | modal | acc | stab |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        label = f"{row['source_id']} -> {row['target_id']}"
        lines.append(
            f"| {label} | {row['confusion']} | `{row['expected']}` | "
            f"`{row['modal']}` | {row['accuracy']:.2f} | {row['stability']:.2f} |"
        )

    report = "\n".join(lines) + "\n"
    (results_dir / f"edge-typing-{slug}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
