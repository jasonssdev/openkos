"""Measures the transcript decision pass (#1348) on AMI meetings against a human gold set.

Question: of the decisions people hand-annotated in a meeting, how many does
the pass turn into a Decision whose evidence line points at that very dialogue
act, and how clean is what it produces? Deterministic scoring only -- no LLM
judge. The pre-registration is `PREREGISTRATION-1348.md`; read it and the
README before trusting a number from here.

The data is NOT in this repository and is never copied into it: the gold CSV
and the rendered transcripts live in the maintainer's thesis checkout and are
passed by flag. Only the DEVELOPMENT AMI series (`scoring.DEV_SERIES`) can be
scored -- the thesis keeps the other series for its final evaluation, and a
harness that tunes OpenKOS behaviour must never touch them. There is no
override flag.

Arms:

- `pass`: the decision pass alone (`decision_pass.run_decision_pass`) over the
  windows the wiring builds. Each produced Decision's evidence line is matched
  against the gold dialogue acts.
- `baseline`: the general extraction (`extract_concept_union` with the key
  off, i.e. today's behaviour); each Decision it returns is matched through
  its BODY, because a topic-bundle body is where its pasted transcript lives.

Usage:

    uv run python evals/transcript_decisions/run_transcript_decisions.py --self-test
    uv run python evals/transcript_decisions/run_transcript_decisions.py \\
        --gold <SERIES>.decisions.adjudicated.csv --transcripts <dir> --arm pass --runs 5
    uv run python evals/transcript_decisions/run_transcript_decisions.py \\
        --gold <csv> --transcripts <dir> --rescore
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.append(str(REPO_ROOT / "evals"))

import scoring  # noqa: E402
from harness_prompts import extraction_prompts  # noqa: E402
from harness_stamp import TimedBackend, build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

DEFAULT_MODEL: Final = "qwen3:8b"
DEFAULT_RUNS: Final = 5
"""Five is the floor the repo's earlier harnesses used and also the size at
which one of them saw an arm swing 0.25 against ITSELF; the pre-registration
asks for more on the treatment run."""
RESULTS_DIR: Final = pathlib.Path(__file__).resolve().parent / "results"
ARMS: Final = ("pass", "baseline")


# --- running ---------------------------------------------------------------


def load_transcripts(
    directory: pathlib.Path, meetings: Sequence[str]
) -> dict[str, str]:
    """`{meeting: text}` for each meeting; a missing file is a hard error --
    a meeting silently absent would shrink the recall denominator's coverage."""
    texts: dict[str, str] = {}
    for meeting in meetings:
        path = directory / f"{meeting}.md"
        if not path.is_file():
            raise SystemExit(f"no transcript for {meeting}: {path} does not exist")
        texts[meeting] = path.read_text(encoding="utf-8")
    return texts


def check_gate(texts: dict[str, str]) -> list[str]:
    """Meetings for which the production gate `_is_meeting_shaped` is FALSE.

    With the gate false the pass never fires in production, so "the pass found
    nothing" would be a false zero. Callers refuse to run rather than score it."""
    return [
        meeting
        for meeting, text in texts.items()
        if not concept_mod._is_meeting_shaped(meeting, text)
    ]


def pass_windows(text: str, title: str) -> list[str]:
    """The windows `concept._run_decision_pass` builds, by the same helpers."""
    if concept_mod.fans_out(text, source_title=title):
        return concept_mod._chunk_lines(text)
    return [text]


def run_meeting(
    arm: str,
    meeting: str,
    text: str,
    rows: Sequence[scoring.GoldRow],
    llm: TimedBackend,
    run: int,
) -> dict[str, Any]:
    """One arm run on one meeting: the raw, JSON-safe record to persist."""
    calls_before = len(llm.call_latencies_s)
    started = time.monotonic()
    failure: str | None = None
    windows = 0
    produced: list[tuple[str, list[str]]] = []  # (title, texts to match through)
    kept_evidence: list[list[str]] = []
    # (subject, value) per pass Decision: the thesis metric embeds "object: content"
    # (`thesis_metric.py`), and the subject is not recoverable from the title.
    pairs: list[tuple[str, str] | None] = []
    if arm == "pass":
        windows_list = pass_windows(text, meeting)
        windows = len(windows_list)
        outcome = decision_pass.run_decision_pass(windows_list, llm)
        failure = outcome.failure
        for result in outcome.results:
            evidence = result.body.split("\n> ", 1)[-1] if "\n> " in result.body else ""
            produced.append((result.title, [evidence]))
            kept_evidence.append([evidence])
            pairs.append(subject_value(result.description, result.body))
    else:
        try:
            union = concept_mod.extract_concept_union(
                text, source_title=meeting, llm=llm, transcript_decisions=False
            )
        except Exception as exc:
            failure = f"extract_concept_union: {type(exc).__name__}"
        else:
            for obj in union.objects:
                if obj.type == "Decision":
                    produced.append((obj.title, [obj.body]))
                    kept_evidence.append([])  # a pasted body is not persisted
                    pairs.append(None)
    items = []
    for (title, texts), kept, pair in zip(produced, kept_evidence, pairs, strict=True):
        item: dict[str, Any] = {
            "title": title,
            "evidence": kept,
            "matched_ids": scoring.match_item(texts, rows),
        }
        if pair is not None:
            item["subject"], item["value"] = pair
        items.append(item)
    return {
        "arm": arm,
        "run": run,
        "meeting": meeting,
        "series": scoring.series_of(meeting),
        "shaped": True,
        "windows": windows,
        "calls": len(llm.call_latencies_s) - calls_before,
        "failure": failure,
        "wall_s": round(time.monotonic() - started, 2),
        "items": items,
    }


def subject_value(description: str, body: str) -> tuple[str, str]:
    """`(subject, value)` of a pass Decision, recovered from what `to_result` built.

    The body is `<value>\n\n> <evidence>` and the description `<subject>: <value>`,
    so the value is read from the body and the subject is what precedes it in the
    description (a subject may itself contain ": ")."""
    value = body.split("\n\n> ", 1)[0]
    suffix = f": {value}"
    subject = description[: -len(suffix)] if description.endswith(suffix) else ""
    return subject, value


def run_arm(
    arm: str,
    texts: dict[str, str],
    rows: Sequence[scoring.GoldRow],
    llm: TimedBackend,
    runs: int,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in range(1, runs + 1):
        for meeting, text in texts.items():
            meeting_rows = [r for r in rows if r.meeting_id == meeting]
            record = run_meeting(arm, meeting, text, meeting_rows, llm, run)
            records.append(record)
            log(
                f"    {arm} run {run} {meeting}: items={len(record['items'])} "
                f"windows={record['windows']} calls={record['calls']} "
                f"failure={record['failure']} {record['wall_s']}s"
            )
    return records


# --- results ---------------------------------------------------------------


def write_results(
    records: list[dict[str, Any]],
    *,
    arm: str,
    model: str,
    runs: int,
    partial: bool,
    stamp: dict[str, Any],
    directory: pathlib.Path = RESULTS_DIR,
) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    suffix = "-PARTIAL" if partial else ""
    path = directory / f"runs-{arm}-{now}-{model.replace(':', '-')}{suffix}.json"
    path.write_text(
        json.dumps(
            {
                "arm": arm,
                "model": model,
                "runs": runs,
                "partial": partial,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "max_pass_decisions": decision_pass.MAX_PASS_DECISIONS,
                "generated_at": now,
                "stamp": stamp,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def load_records(paths: Sequence[pathlib.Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(json.loads(path.read_text(encoding="utf-8"))["records"])
    return records


def stamp_for(arm: str, model: str) -> dict[str, Any]:
    prompts = {"extraction/decision_pass": decision_pass._SYSTEM_PROMPT}
    if arm == "baseline":
        prompts = extraction_prompts()
    return build_stamp(model=model, prompts=prompts)


# --- command line ----------------------------------------------------------


def prepare(
    gold_paths: Sequence[pathlib.Path], allow_partial: bool
) -> tuple[list[scoring.GoldRow], list[str]]:
    """Gold rows and the meetings to run, or SystemExit with the reason.

    Order is the safety order: the allowlist first (it must refuse before
    anything else is read), then blanks."""
    rows = scoring.load_gold(gold_paths)
    try:
        scoring.require_dev_meetings(sorted({r.meeting_id for r in rows}))
    except scoring.SeriesRefused as exc:
        raise SystemExit(f"REFUSED: {exc}") from exc
    blanks = scoring.blank_rows(rows)
    if blanks and not allow_partial:
        raise SystemExit(
            f"REFUSED: {len(blanks)} of {len(rows)} gold rows have a blank final_status "
            f"(adjudication unfinished): {', '.join(blanks)}. "
            "Finish adjudicating, or pass --allow-partial for a result stamped "
            f"'{scoring.PARTIAL_STAMP}'."
        )
    scored = {r.meeting_id for r in rows if r.status}
    return rows, sorted(scored)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--gold", action="append", type=pathlib.Path)
    parser.add_argument("--transcripts", type=pathlib.Path)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--rescore", nargs="*", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--results-dir",
        type=pathlib.Path,
        default=RESULTS_DIR,
        help="where to store (and, with a bare --rescore, read) runs",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.gold:
        parser.error("--gold is required unless --self-test")
    rows, meetings = prepare(args.gold, args.allow_partial)

    if args.rescore is not None:
        paths = args.rescore or sorted(args.results_dir.glob("runs-*.json"))
        if not paths:
            raise SystemExit(f"no stored runs in {args.results_dir}")
        records, kept = scoring.rematch_records(load_records(paths), rows)
        if kept:
            print(
                f"NOTE: {kept} stored item(s) carry no evidence (baseline bodies are "
                "not persisted); their matches were NOT recomputed and come from the "
                "rule in force when they were run. Re-run that arm to apply the "
                "current matching rule.",
                file=sys.stderr,
            )
        print(scoring.render_report(records, rows, meetings))
        return 0

    if args.arm is None or args.transcripts is None:
        parser.error("--arm and --transcripts are required to run")
    texts = load_transcripts(args.transcripts, meetings)
    unshaped = check_gate(texts)
    if unshaped:
        raise SystemExit(
            f"REFUSED: _is_meeting_shaped is FALSE for {len(unshaped)} of {len(texts)} "
            f"meeting(s): {', '.join(unshaped)}. The pass would not fire in production, "
            "so a run would measure a false zero."
        )
    llm = TimedBackend(
        OllamaClient(
            model=args.model,
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        )
    )
    print(
        f"model {args.model}, arm {args.arm}, {args.runs} run(s), {len(texts)} meeting(s)"
    )
    records = run_arm(args.arm, texts, rows, llm, args.runs)
    partial = bool(scoring.blank_rows(rows))
    path = write_results(
        records,
        arm=args.arm,
        model=args.model,
        runs=args.runs,
        partial=partial,
        stamp=stamp_for(args.arm, args.model),
        directory=args.results_dir,
    )
    print(f"stored {path}")
    print(scoring.render_report(records, rows, meetings))
    return 0


# --- self-test -------------------------------------------------------------

_SYNTH_TRANSCRIPT: Final = """---
meeting_id: ES2015a
series: ES2015
---

PM: Okay, welcome to the meeting everyone.

ID: Thanks, glad to be here today.

PM: Let's talk about the casing material first.

ID: I think we should use rubber for the casing, it feels nicer.

PM: Alright, so we'll go with rubber for the casing then.

UI: What about the buttons on the remote?

PM: We decided the remote will have exactly ten buttons.

ID: Maybe it should be bright orange or something.

PM: Maybe the colour could be orange, we can look at it later.

UI: Sure, we can come back to the colour.

PM: Next time we meet we cover the budget and the battery.

ID: The battery is a topic for another day.

PM: Fine, the remote will use a solar cell for the battery.

UI: Okay noted, solar it is.

PM: Meeting closed, thank you everybody for coming along.
"""

_SYNTH_TRANSCRIPT_B: Final = _SYNTH_TRANSCRIPT.replace("ES2015a", "ES2015b").replace(
    "rubber for the casing then", "rubber for the casing then"
)

_SYNTH_GOLD: Final = (
    "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
    'd1,ES2015a,s1,x,"A: Alright , so we\'ll go with rubber for the casing then .",decision\n'
    'd2,ES2015a,s2,x,"B: yeah | C: We decided the remote will have exactly ten buttons .",decision\n'
    'd3,ES2015a,s3,x,"D: Maybe the colour could be orange , we can look at it later .",no_decision\n'
    'd4,ES2015a,s4,x,"A: Fine , the <gap> remote will use a solar cell for the battery .",decision\n'
    'd5,ES2015a,s5,x,"B: Sure , we can come back to the colour .",compuesta\n'
    'd6,ES2015a,s6,x,"C: The battery is a topic for another day .",sin_soporte\n'
    'd7,ES2015a,s7,x,"A: a completely different sentence nobody produced here .",decision\n'
    'd8,ES2015b,s1,x,"A: Alright , so we\'ll go with rubber for the casing then .",decision\n'
    'd10,ES2015a,s8,x,"B: yeah .",decision\n'
)

_PASS_REPLY: Final = json.dumps(
    [
        {  # matches d1 (decision)
            "subject": "casing",
            "value": "Use rubber for the casing",
            "evidence": "PM: Alright, so we'll go with rubber for the casing then.",
        },
        {  # matches d3 only (no_decision) -> false positive against gold
            "subject": "colour",
            "value": "The colour is orange",
            "evidence": "PM: Maybe the colour could be orange, we can look at it later.",
        },
        {  # matches nothing -> unannotated
            "subject": "closing",
            "value": "The meeting is closed",
            "evidence": "PM: Meeting closed, thank you everybody for coming along.",
        },
        {  # unannotated: nothing in the gold contains it
            "subject": "ack",
            "value": "Solar was noted down",
            "evidence": "UI: Okay noted, solar it is.",
        },
        {  # matches d5 only (compuesta)
            "subject": "colour later",
            "value": "The colour comes back later",
            "evidence": "UI: Sure, we can come back to the colour.",
        },
        {  # matches d6 only (sin_soporte) -> false positive against gold
            "subject": "battery topic",
            "value": "The battery is deferred",
            "evidence": "ID: The battery is a topic for another day.",
        },
        {  # matches d4 (decision, gold carries a <gap>) and d5? no: d5 differs
            "subject": "battery",
            "value": "Solar cell for the battery",
            "evidence": "Fine, the remote will use a solar cell for the battery.",
        },
    ]
)


class _FakeLLM:
    """Canned replies; counts calls. Never touches a network."""

    def __init__(self, reply: str, first: str | None = None) -> None:
        self.reply = reply
        self.first = first  # the reply to call 1, when it differs
        self.calls = 0

    def chat(self, messages: Sequence[Any]) -> str:
        self.calls += 1
        if self.calls == 1 and self.first is not None:
            return self.first
        return self.reply


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        gold_path = root / "ES2015.decisions.adjudicated.csv"
        gold_path.write_text(_SYNTH_GOLD, encoding="utf-8")
        rows = scoring.load_gold([gold_path])

        # tokenisation and matching
        check(
            "tokens ignore punctuation, gap tag and speaker prefix",
            scoring.act_tokens("A: Um , the <gap> remote ."),
            ("um", "the", "remote"),
        )
        long_act = "A: Alright , so we'll go with rubber for the casing then ."
        check(
            "AMI-tokenised gold matches the rendered line",
            scoring.evidence_matches(
                "PM: Alright, so we'll go with rubber for the casing then.", long_act
            ),
            True,
        )
        check(
            "containment works in the other direction",
            scoring.evidence_matches("go with rubber for the casing", long_act),
            True,
        )
        check(
            "min-length guard: a short quote never matches",
            scoring.evidence_matches("Alright, so", long_act),
            False,
        )
        check(
            "min-length guard: a short gold act never matches",
            scoring.evidence_matches(long_act, "B: go with rubber ."),
            False,
        )
        check(
            "unrelated text does not match",
            scoring.evidence_matches("The weather is lovely today ok", long_act),
            False,
        )

        # longest-common-run rule (segmentation differs between gold and render)
        def match(left: str, right: str) -> bool:
            return scoring.evidence_matches(left, right)

        check(
            "partial overlap: run 5 covering 5/6 of the shorter side matches",
            match(
                "we've decided on kinetic right okay so that's good",
                "A: energy we've decided on kinetic right",
            ),
            True,
        )
        check(
            "run of 4 covering under 60% of the shorter side does not match",
            match(
                "one two three four x1 x2 x3 x4 x5 x6",
                "y1 one two three four y2 y3 y4 y5 y6 y7",
            ),
            False,
        )
        check(
            "run of 4 covering exactly 4/6 of the shorter side matches",
            match("one two three four x1 x2", "y1 one two three four y2 y3 y4"),
            True,
        )
        check(
            "run of 4 covering 4/7 of the shorter side does not match",
            match("one two three four x1 x2 x3", "y1 one two three four y2 y3 y4"),
            False,
        )
        check(
            "a run of 3 never matches, even at full coverage of a short side",
            match("one two three", "y1 one two three y2 y3"),
            False,
        )
        check(
            "a run of 3 never matches inside long sides",
            match("one two three x1 x2 x3", "y1 one two three y2 y3 y4"),
            False,
        )
        check(
            "runs of 3 and 3 split by a gap do not add up",
            match("one two three x1 four five six", "one two three y1 four five six"),
            False,
        )
        check(
            "full containment is still a match",
            match("go with rubber for the casing", long_act),
            True,
        )
        gold_rows = [
            scoring.GoldRow(
                "g1",
                "ES2015a",
                "decision",
                (scoring.act_tokens("energy we've decided on kinetic right"),),
            )
        ]
        check(
            "pass-arm style evidence line matches through match_item",
            scoring.match_item(["PM: we've decided on kinetic right okay"], gold_rows),
            ["g1"],
        )
        check(
            "baseline-arm style multi-line body matches through match_item",
            scoring.match_item(
                ["Some title\nA: so we've decided on kinetic\nB: right okay"],
                gold_rows,
            ),
            ["g1"],
        )
        stale: list[dict[str, Any]] = [
            {
                "arm": "pass",
                "items": [
                    {
                        "title": "t",
                        "evidence": ["we've decided on kinetic right"],
                        "matched_ids": [],
                    },
                    {"title": "b", "evidence": [], "matched_ids": ["old"]},
                ],
            }
        ]
        refreshed, kept_stale = scoring.rematch_records(stale, gold_rows)
        check(
            "rescore re-matches stored evidence",
            refreshed[0]["items"][0]["matched_ids"],
            ["g1"],
        )
        check(
            "rescore keeps matches without stored evidence",
            refreshed[0]["items"][1]["matched_ids"],
            ["old"],
        )
        check("rescore counts items it could not re-match", kept_stale, 1)
        check(
            "rescore does not mutate its input",
            stale[0]["items"][0]["matched_ids"],
            [],
        )

        # allowlist
        check("series_of", scoring.series_of("ES2015a"), "ES2015")
        for bad in ("TS3005a", "ES2002a", "IS1003b", "garbage"):
            try:
                scoring.require_dev_meetings([bad])
                failures.append(f"allowlist let {bad!r} through")
            except scoring.SeriesRefused:
                pass
        scoring.require_dev_meetings(["ES2015a", "IS1004c", "TS3009d"])
        eval_csv = root / "TS3005.decisions.adjudicated.csv"
        eval_csv.write_text(
            "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
            "e1,TS3005a,s1,x,A: some words here for it,decision\n",
            encoding="utf-8",
        )
        try:
            prepare([eval_csv], allow_partial=True)
            failures.append("prepare() accepted an evaluation-series gold file")
        except SystemExit as exc:
            check("allowlist refusal is named", "REFUSED" in str(exc), True)

        # blank refusal / partial stamping
        blank_csv = root / "blank.csv"
        blank_csv.write_text(
            _SYNTH_GOLD
            + 'd9,ES2015b,s2,x,"A: Another distinct sentence for blank .",\n',
            encoding="utf-8",
        )
        try:
            prepare([blank_csv], allow_partial=False)
            failures.append("blank final_status was not refused")
        except SystemExit as exc:
            check("blank refusal names the row", "d9" in str(exc), True)
        blank_rows, _ = prepare([blank_csv], allow_partial=True)
        check("allow-partial keeps the blank row", len(blank_rows), 10)

        # gate
        shaped = {"ES2015a": _SYNTH_TRANSCRIPT}
        check("gate fires on a transcript", check_gate(shaped), [])
        check(
            "gate reports a non-transcript",
            check_gate({"ES2015a": "just one paragraph of prose."}),
            ["ES2015a"],
        )

        # run + score the pass arm over two meetings, two runs
        texts = {"ES2015a": _SYNTH_TRANSCRIPT, "ES2015b": _SYNTH_TRANSCRIPT_B}
        fake = _FakeLLM(_PASS_REPLY)
        records = run_arm(
            "pass", texts, rows, TimedBackend(fake), 2, log=lambda _line: None
        )
        check("records per run and meeting", len(records), 4)
        check("one chat call per window", fake.calls, 4)
        retry = run_arm(
            "pass",
            {"ES2015a": _SYNTH_TRANSCRIPT},
            rows,
            TimedBackend(_FakeLLM(_PASS_REPLY, first="not json")),
            1,
            log=lambda _line: None,
        )[0]
        check("a retried window counts both real calls", retry["calls"], 2)
        check("a retried window is not a failure", retry["failure"], None)
        first = records[0]
        check("windows reported", first["windows"], 1)
        check("calls reported", first["calls"], 1)
        check("no failure", first["failure"], None)
        check("items produced", len(first["items"]), 7)
        check(
            "matched ids of item 0",
            first["items"][0]["matched_ids"],
            ["d1"],
        )
        check(
            "a pass item keeps its subject and value (thesis metric input)",
            (first["items"][0]["subject"], first["items"][0]["value"]),
            ("casing", "Use rubber for the casing"),
        )
        check(
            "subject_value keeps a subject that contains a colon",
            subject_value("a: b: the thing is done", "the thing is done\n\n> x"),
            ("a: b", "the thing is done"),
        )
        check(
            "subject_value refuses a description it cannot split",
            subject_value("unrelated", "the thing is done\n\n> x"),
            ("", "the thing is done"),
        )
        metrics = scoring.series_run_metrics(
            [r for r in records if r["run"] == 1], rows
        )["ES2015"]
        # decisions in ES2015a/b: d1 d2 d4 d7 d8 ; matched: d1, d4 (A) + d8 (B)
        check("decision rows", metrics["decision_rows"], 6)
        check("unrecallable rows", metrics["unrecallable_rows"], 1)
        check("recalled", metrics["recalled"], 3)
        check("matched_decision", metrics["matched_decision"], 3)
        check("matched_non_decision", metrics["matched_non_decision"], 2)
        check("unannotated", metrics["unannotated"], 8)
        check("compuesta bucket", metrics["matched_compuesta"], 1)
        check("produced total", metrics["produced"], 14)
        check("precision-on-annotated", metrics["precision_on_annotated"], 3 / 5)
        check("compuesta rows counted apart", metrics["compuesta_rows"], 1)
        # identical titles across the two meetings: 7 shared titles, 7 extra
        check("duplicate titles", metrics["duplicate_titles"], 7)
        check("merge-prone decisions", metrics["duplicate_extra"], 7)
        check("windows summed", metrics["windows"], 2)

        report = scoring.render_report(records, rows, ["ES2015a", "ES2015b"])
        check("report carries n of TOTAL", "of 6 decision rows" in report, True)
        check("report is not stamped partial", scoring.PARTIAL_STAMP in report, False)
        check("report names unannotated apart", "unannotated" in report, True)

        # stored round trip + rescore: no model, same numbers; partial stamping
        results_dir = root / "results"
        path = write_results(
            records,
            arm="pass",
            model="fake",
            runs=2,
            partial=False,
            stamp={"stamp_version": 1},
            directory=results_dir,
        )
        stored = json.loads(path.read_text(encoding="utf-8"))
        blob = json.dumps(stored)
        check(
            "no gold annotation text persisted", "a completely different" in blob, False
        )
        check("no transcript text persisted", "welcome to the meeting" in blob, False)
        re_report = scoring.render_report(
            load_records([path]), rows, ["ES2015a", "ES2015b"]
        )
        check("rescore equals the live report", re_report, report)
        partial_report = scoring.render_report(
            load_records([path]), blank_rows, ["ES2015a", "ES2015b"]
        )
        check(
            "blank gold stamps the report PARTIAL",
            scoring.PARTIAL_STAMP in partial_report,
            True,
        )
        missing = scoring.render_report(
            load_records([path]), rows, ["ES2015a", "ES2015b", "ES2015c"]
        )
        check(
            "a meeting without records stamps PARTIAL",
            scoring.PARTIAL_STAMP in missing,
            True,
        )

        # `descartar`, shared-evidence groups (synthetic gold, hand-built records)
        split_csv = root / "split.csv"
        split_csv.write_text(
            "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
            'g1,ES2015c,s1,x,"A: we will use the kinetic battery for power .",compuesta\n'
            'g1-1,ES2015c,s1,x,"A: we will use the kinetic battery for power .",decision\n'
            'g1-2,ES2015c,s1,x,"A: we will use the kinetic battery for power .",decision\n'
            'g2,ES2015c,s2,x,"B: that is a broken sentence about the scroll wheel .",descartar\n'
            'g3,ES2015c,s3,x,"C: maybe we could add a speaker to the remote .",no_decision\n'
            'g4,ES2015c,s4,x,"D: the case will be made of yellow plastic .",decision\n'
            'g5,ES2015c,s5,x,"A: the scroll wheel stays out of the design .",decision\n'
            'g6,ES2015c,s6,x,"A: the scroll wheel stays out of the design .",decision\n',
            encoding="utf-8",
        )
        split_rows = scoring.load_gold([split_csv])
        check(
            "descartar is an accepted status",
            [r.status for r in split_rows if r.decision_id == "g2"],
            ["descartar"],
        )
        unknown_csv = root / "unknown.csv"
        unknown_csv.write_text(
            "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
            "u1,ES2015c,s1,x,A: some words here for it,descartada\n",
            encoding="utf-8",
        )
        try:
            scoring.load_gold([unknown_csv])
            failures.append("an unknown final_status was accepted")
        except SystemExit as exc:
            check("unknown status is named", "descartada" in str(exc), True)

        blank_csv = root / "blank_meeting.csv"
        blank_csv.write_text(
            "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
            'TS3009c.d07,TS3009c,s1,x,"A: we will use the kinetic battery for power .",compuesta\n'
            "TS3009c.d07-1,,s1,x,,decision\n"
            "TS3009c.d07-2,,s1,x,,decision\n"
            'orphan,,s2,x,"B: some words here for it today .",decision\n',
            encoding="utf-8",
        )
        blank_rows_loaded = scoring.load_gold([blank_csv])
        check(
            "a blank meeting_id is derived from the decision id",
            [r.meeting_id for r in blank_rows_loaded],
            ["TS3009c", "TS3009c", "TS3009c", ""],
        )
        try:
            prepare([blank_csv], allow_partial=True)
            failures.append("an underivable blank meeting_id was accepted")
        except SystemExit as exc:
            check("an underivable meeting is refused", "REFUSED" in str(exc), True)
        derived = scoring.series_run_metrics(
            [
                {
                    "arm": "pass",
                    "run": 1,
                    "meeting": "TS3009c",
                    "series": "TS3009",
                    "windows": 1,
                    "calls": 1,
                    "failure": None,
                    "wall_s": 0.0,
                    "items": [],
                }
            ],
            blank_rows_loaded[:3],
        )["TS3009"]
        check(
            "derived children count in the recall denominator, unrecallable",
            (derived["decision_rows"], derived["unrecallable_rows"]),
            (2, 2),
        )

        def split_record(item_matches: Sequence[Sequence[str]]) -> dict[str, Any]:
            return {
                "arm": "pass",
                "run": 1,
                "meeting": "ES2015c",
                "series": "ES2015",
                "windows": 1,
                "calls": 1,
                "failure": None,
                "wall_s": 0.0,
                "items": [
                    {"title": f"t{n}", "evidence": [], "matched_ids": list(ids)}
                    for n, ids in enumerate(item_matches)
                ],
            }

        def split_metrics(item_matches: Sequence[Sequence[str]]) -> dict[str, Any]:
            record = split_record(item_matches)
            return scoring.series_run_metrics([record], split_rows)["ES2015"]

        group = ["g1", "g1-1", "g1-2"]
        one = split_metrics([group])
        check("compuesta parent is not a decision row", one["decision_rows"], 5)
        check(
            "shared groups counted (children, g5/g6)", one["shared_evidence_groups"], 2
        )
        check("one item on a 2-child group credits 1", one["recalled"], 1)
        two = split_metrics([group, group[1:]])
        check("two distinct items credit 2", two["recalled"], 2)
        three = split_metrics([group, group[1:], ["g1-2"]])
        check("three items on a 2-row group credit 2", three["recalled"], 2)
        mixed = split_metrics([["g5", "g6"], ["g4"]])
        check("id-prefix-free groups share too", mixed["recalled"], 2)
        check("recall is credit over decision rows", mixed["recall"], 2 / 5)
        check("descartar rows counted apart", one["descartar_rows"], 1)
        only_d = split_metrics([["g2"]])
        check("descartar-only item has its own bucket", only_d["matched_descartar"], 1)
        check("descartar is not unannotated", only_d["unannotated"], 0)
        check("descartar is no false positive", only_d["precision_on_annotated"], None)
        check("descartar adds no recall", only_d["recalled"], 0)
        check(
            "decision beats descartar",
            split_metrics([["g2", "g4"]])["matched_decision"],
            1,
        )
        check(
            "non_decision beats descartar",
            split_metrics([["g2", "g3"]])["matched_non_decision"],
            1,
        )
        check(
            "compuesta beats descartar",
            split_metrics([["g2", "g1"]])["matched_compuesta"],
            1,
        )
        split_report = scoring.render_report(
            [split_record([group])], split_rows, ["ES2015c"]
        )
        check(
            "report names the shared groups",
            "shared_evidence_groups" in split_report,
            True,
        )
        check(
            "report names matched_descartar", "matched_descartar" in split_report, True
        )

        # the aggregate: spread over runs
        agg = scoring.aggregate([{"recall": 0.2}, {"recall": 0.6}, {"recall": None}])
        check("aggregate", agg["recall"], {"mean": 0.4, "min": 0.2, "max": 0.6, "n": 2})

        # a failing window is reported, not raised
        broken = _FakeLLM("not json at all")
        rec = run_meeting(
            "pass", "ES2015a", _SYNTH_TRANSCRIPT, rows, TimedBackend(broken), 1
        )
        check("failure surfaced", bool(rec["failure"]), True)
        check("failure yields no items", rec["items"], [])

        # the baseline arm scores a Decision through its body
        baseline_reply = json.dumps(
            [
                {
                    "type": "Decision",
                    "title": "Casing and buttons",
                    "description": "Choices made",
                    "body": "PM: Alright, so we'll go with rubber for the casing then. "
                    "PM: Fine, the remote will use a solar cell for the battery.",
                }
            ]
        )
        rows_a = [r for r in rows if r.meeting_id == "ES2015a"]
        base = run_meeting(
            "baseline",
            "ES2015a",
            _SYNTH_TRANSCRIPT,
            rows_a,
            TimedBackend(_FakeLLM(baseline_reply)),
            1,
        )
        check(
            "baseline body matches both acts",
            base["items"][0]["matched_ids"] if base["items"] else None,
            ["d1", "d4"],
        )
        check(
            "baseline persists no body",
            base["items"][0]["evidence"] if base["items"] else None,
            [],
        )

    # the whole thing never asked a model for anything
    if failures:
        for line in failures:
            print(f"FAIL {line}")
        print("\nself-test: FAILED")
        return 1
    print("self-test: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
