"""Re-ask on an empty extraction reply (#1690): generate, re-ask, score, decide.

The bounded re-ask (`_add_reask_subjects`, #584/#642) fires only when the
final, filtered list holds exactly one object. A reply that ends with zero
objects never gets a second ask. Read `PREREGISTRATION.md` first: it fixes
the arms, the bars and the stopping rules before any generation.

Arms, on the same recorded runs:

- `a0`  the shipped pipeline (replayed from the recorded raw stage through
        production's own functions): no re-ask on an empty list.
- `a1`  when `a0` is empty and the source is at least
        `_REASK_LOW_YIELD_THRESHOLD` characters, one re-ask with an
        anchor-free prompt (defined here, not in production). Its additions
        pass production's own filters.

`a1` equals `a0` whenever `a0` is non-empty: the re-ask only ADDS, and only on
an empty list.

Subcommands:

- `generate` model-bound. Fresh `ES2015a-es` runs 16 to 30 through the
             framing-floor harness's `run`. Needs Ollama.
- `reask`    model-bound. The re-ask call on every run whose `a0` is empty.
- `score`    model-free. Replays the recorded runs and replies.
- `decide`   model-free. Applies the registered bars (needs `ADJUDICATION`
             complete for every added object).

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_model_screen"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_drop_scope"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_floor"))

import measure_framing_floor as mff  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm import parsing  # noqa: E402

Obj = dict[str, Any]

MODEL: Final = mff.MODEL
SOURCES: Final = mff.SOURCES
LEAD: Final = mff.LEAD
STORED_RUNS: Final = tuple(range(1, 16))
"""Runs 1 to 15 of every source: the framing-floor records, read-only."""
FRESH_RUNS: Final = tuple(range(16, 31))
"""Runs 16 to 30 of `LEAD`: generated for this registration."""
SAMPLES: Final = (1, 2, 3)
"""Independent re-ask samples per empty run."""
SEED_STRIDE: Final = 1000

# Registered bars (PREREGISTRATION.md, "Bars").
DEFECT_LEAD_MIN: Final = 0.20
BENEFIT_MAX_RATIO: Final = 0.5
SLIPPED_PER: Final = 20
QUALITY_SUBJECT_MIN: Final = 0.5
CALL_FAILURE_MAX: Final = 0.10
RECORD_FAILURE_MAX: Final = 0.15
MIN_EMPTY_RECORDS: Final = 6
FUTILITY_STILL_EMPTY: Final = 0.75

CONTAINER: Final = "container"
SUBJECT: Final = "subject"
CONTESTED: Final = "contested"
WRONG_LANGUAGE: Final = "wrong-language"
CLASSES: Final = (CONTAINER, SUBJECT, CONTESTED, WRONG_LANGUAGE)

ADJUDICATION: Final[dict[tuple[str, str], tuple[str, str]]] = {}
"""(type, title) -> (class, reason) for every object the re-ask adds. Empty at
registration; written by the owner after `score` listed the added objects and
BEFORE `decide` is first run, reading each object's type, title and
description only (never its run or sample). A contested object and a
wrong-language one count against the arm."""

EMPTY_REASK_SYSTEM_PROMPT: Final = (
    "A first extraction pass over the SOURCE below returned NO usable object: "
    "everything it proposed was discarded because it named the gathering "
    "itself (a meeting, a session, a call, a discussion) or because its title "
    "was in a language other than the source's. This is a narrow follow-up "
    "question, NOT a request to extract the source again.\n\n"
    "Question: does the BODY of this source develop any distinct subject in "
    "its own right -- a specific idea, artifact, decision, procedure, person, "
    "organization, place, event, or project that the text says something "
    "substantive about?\n\n"
    "Answer with ONLY those subjects. Do NOT return the gathering itself under "
    "any name (the meeting, the session, the discussion, an agenda, the list "
    "of attendees), and do NOT promote a section heading, a passing mention "
    "or a supporting detail. Name each subject by what it is about, not by the "
    "occasion on which it was discussed.\n\n"
    "An empty array [] is a CORRECT and EXPECTED answer here. Do not invent a "
    "subject in order to fill this answer. If you are unsure whether something "
    "is a separate subject, answer [].\n\n"
    'Vocabulary: each object\'s "type" MUST be one of exactly nine values: '
    '"Person", "Organization", "Place", "Event", "Procedure", "Decision", '
    '"Project", "Concept", or "Entity".\n\n'
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code fences "
    "around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"|"Decision"'
    '|"Project"|"Concept"|"Entity", "title": "...", "description": "...", '
    '"body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)


# --- the arm -----------------------------------------------------------------


def reask_fires(a0: Sequence[Mapping[str, Any]], source_text: str) -> bool:
    """The registered trigger: the filtered list is empty and the source is at
    least production's own low-yield length."""
    return not a0 and len(source_text) >= concept_mod._REASK_LOW_YIELD_THRESHOLD


def build_empty_reask_messages(source_text: str) -> list[dict[str, str]]:
    """The anchor-free re-ask prompt. There is no kept object to name, so the
    user turn carries no title and no `ALREADY KEPT` line; it carries
    production's language anchor, as the meeting-shaped extraction turn does
    (#522), because the empty case is where wrong-language titles were
    dropped."""
    return [
        {"role": "system", "content": EMPTY_REASK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"{concept_mod._LANGUAGE_ANCHOR}\n\nSOURCE TEXT:\n{source_text}",
        },
    ]


def additions_from_reply(
    reply: str | None,
    *,
    source_text: str,
    source_title: str,
    framing_shaped: bool,
) -> list[Obj]:
    """What the re-ask adds after production's own filters, in the order of the
    chunked path: validate, strip ungrounded expansions, drop a title that
    restates the source's, dedup, wrong-language gate, framing drop, twins.
    Nothing is reimplemented."""
    if reply is None:
        return []
    items = [
        r
        for r in (concept_mod._validate(i) for i in parsing.extract_json_items(reply))
        if r is not None
    ]
    items = concept_mod._strip_ungrounded_expansions(items, source_text=source_text)
    items = [
        r
        for r in items
        if not concept_mod._restates_source_title(r, source_title=source_title)
    ]
    items = concept_mod._dedup_merged(items)
    items = concept_mod._drop_wrong_language_titles(items, source_text=source_text)[0]
    items = concept_mod._drop_framing_objects(items, framing_shaped=framing_shaped)
    items = concept_mod._drop_source_title_twins(items, source_title=source_title)
    return [mff._obj(r) for r in items]


def reask_empty(source_text: str, source_title: str, llm: Any) -> dict[str, Any]:
    """One re-ask call. Never raises: a failure is the exception TYPE only (as
    `_reask_for_further_subjects` carries it) and the call adds nothing."""
    prompt_source, bounded = concept_mod._bounded_prompt_source(
        source_text,
        overhead_chars=sum(len(m["content"]) for m in build_empty_reask_messages("")),
        llm=llm,
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )
    try:
        reply = llm.chat(build_empty_reask_messages(prompt_source))
    except Exception as exc:
        return {"reply": None, "failure": type(exc).__name__, "bounded": bounded}
    return {"reply": reply, "failure": None, "bounded": bounded}


def reask_seed(run: int, sample: int) -> int:
    """Seed of re-ask sample `sample` of run `run`: above every generation seed
    (runs are 1 to 30), so a re-ask never replays a generation call."""
    return run + SEED_STRIDE * sample


def classify(obj: Mapping[str, Any]) -> str:
    entry = ADJUDICATION.get(mff.key(obj))
    if entry is None:
        raise SystemExit(f"unadjudicated added object: {mff.key(obj)}")
    return entry[0]


# --- scoring ------------------------------------------------------------------


def summarize(
    population: Iterable[Mapping[str, Any]],
    trials: Iterable[Mapping[str, Any]],
    *,
    classes: bool,
) -> dict[str, Any]:
    """Counts over the population (one entry per recorded run) and the re-ask
    trials (one per empty run and sample). With `classes`, added objects are
    classified (and an unadjudicated one stops the run)."""
    pop = list(population)
    out: dict[str, Any] = {
        "n": len(pop),
        "records_total": len(pop),
        "records_failed": sum(1 for p in pop if p["failed"]),
        "a0_zero": sum(1 for p in pop if p["a0_empty"]),
        "lead_n": sum(1 for p in pop if p["source"] == LEAD),
        "lead_zero_a0": sum(1 for p in pop if p["source"] == LEAD and p["a0_empty"]),
        "trials": 0,
        "trials_empty_a1": 0,
        "fires": 0,
        "failures": 0,
        "added_total": 0,
        "added_subject": 0,
        "added_slipped": 0,
        "invariant_violations": 0,
        "added_titles": [],
    }
    lead_walls = [float(p["wall_s"]) for p in pop if p["source"] == LEAD]
    out["lead_run_wall_mean"] = sum(lead_walls) / len(lead_walls) if lead_walls else 0.0
    per_record: dict[tuple[str, int], list[bool]] = {}
    walls: list[float] = []
    for trial in trials:
        out["trials"] += 1
        walls.append(float(trial["wall_s"]))
        if trial["a0"]:
            out["invariant_violations"] += 1
        additions = trial["additions"]
        if not additions:
            out["trials_empty_a1"] += 1
        else:
            out["fires"] += 1
        if trial["failure"]:
            out["failures"] += 1
        per_record.setdefault((trial["source"], trial["run"]), []).append(not additions)
        for obj in additions:
            out["added_total"] += 1
            out["added_titles"].append(
                [obj["type"], obj["title"], str(obj.get("description", ""))]
            )
            if classes:
                if classify(obj) == SUBJECT:
                    out["added_subject"] += 1
                else:
                    out["added_slipped"] += 1
    out["a1_zero_expected"] = sum(sum(v) / len(v) for v in per_record.values())
    out["reask_wall_mean"] = sum(walls) / len(walls) if walls else 0.0
    out["missing_trials"] = sum(
        1 for p in pop if p["a0_empty"] and (p["source"], p["run"]) not in per_record
    )
    return out


def futility(summary: Mapping[str, Any]) -> bool:
    """The registered stop after phase 1: more than 75% of the re-ask trials
    still end empty, so the benefit bar is out of reach."""
    trials = summary["trials"]
    return bool(trials) and summary["trials_empty_a1"] / trials > FUTILITY_STILL_EMPTY


def decide(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The registered bars, in order. B0 and the sample-size rules come first."""
    lead_rate = (
        summary["lead_zero_a0"] / summary["lead_n"] if summary["lead_n"] else 0.0
    )
    trials = summary["trials"]
    added = summary["added_total"]
    record_failure = (
        summary["records_failed"] / summary["records_total"]
        if summary["records_total"]
        else 0.0
    )
    call_failure = summary["failures"] / trials if trials else 0.0
    bars = {
        "B1_benefit": summary["a1_zero_expected"]
        <= BENEFIT_MAX_RATIO * summary["a0_zero"],
        "B2_safety": summary["added_slipped"] <= max(1, trials // SLIPPED_PER),
        "B3_quality": added > 0
        and summary["added_subject"] / added >= QUALITY_SUBJECT_MIN,
        "B4_cost": call_failure <= CALL_FAILURE_MAX
        and summary["reask_wall_mean"] <= summary["lead_run_wall_mean"],
    }
    if lead_rate < DEFECT_LEAD_MIN:
        verdict = "NO-DEFECT"
    elif (
        summary["a0_zero"] < MIN_EMPTY_RECORDS
        or record_failure > RECORD_FAILURE_MAX
        or summary["missing_trials"]
        or summary["invariant_violations"]
    ):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "LICENSED" if all(bars.values()) else "REJECTED"
    return {
        "verdict": verdict,
        "defect": {"lead_rate": lead_rate, "met": lead_rate >= DEFECT_LEAD_MIN},
        "bars": bars,
        "call_failure_rate": call_failure,
        "record_failure_rate": record_failure,
        "slipped_allowance": max(1, trials // SLIPPED_PER),
        "subject_share": summary["added_subject"] / added if added else 0.0,
    }


# --- records ------------------------------------------------------------------


def reask_path(
    out_dir: pathlib.Path, model: str, source: str, run: int, sample: int
) -> pathlib.Path:
    return out_dir / "reask" / mff.safe(model) / source / f"r{run}s{sample}.json"


def _record_files(root: pathlib.Path, model: str, source: str) -> list[pathlib.Path]:
    return sorted((root / "records" / mff.safe(model) / source).glob("r*.json"))


def load_population(
    base_dir: pathlib.Path,
    out_dir: pathlib.Path,
    translations_dir: pathlib.Path,
    model: str,
    *,
    max_run: int | None = None,
) -> list[dict[str, Any]]:
    """One entry per recorded run: the stored framing-floor records (runs 1 to
    15 of every source) and this registration's fresh `LEAD` runs. `a0` is
    replayed from the recorded raw stage through production's functions."""
    out: list[dict[str, Any]] = []
    for source in SOURCES:
        text = mff.translation_text(translations_dir, source)
        roots = [base_dir] + ([out_dir] if source == LEAD else [])
        for root in roots:
            for path in _record_files(root, model, source):
                rec = json.loads(path.read_text(encoding="utf-8"))
                if max_run is not None and rec["run"] > max_run:
                    continue
                entry: dict[str, Any] = {
                    "source": source,
                    "run": rec["run"],
                    "wall_s": float(rec.get("wall_s", 0.0)),
                    "failed": bool(rec.get("failure") or rec["stages"]["raw"] is None),
                    "a0_empty": False,
                    "title": str(rec["title"]),
                }
                if not entry["failed"]:
                    title = entry["title"]
                    a0 = mff.replay_arms(
                        rec["stages"]["raw"],
                        source_text=text,
                        source_title=title,
                        framing_shaped=concept_mod._framing_shaped(title, text),
                    )["a0"]
                    entry["a0_empty"] = reask_fires(a0, text)
                out.append(entry)
    return out


def load_trials(
    out_dir: pathlib.Path,
    translations_dir: pathlib.Path,
    model: str,
    *,
    max_run: int | None = None,
) -> list[dict[str, Any]]:
    """One trial per stored re-ask reply, re-filtered through production's
    functions at score time (so the filters are never baked into a record)."""
    trials: list[dict[str, Any]] = []
    for source in SOURCES:
        text = mff.translation_text(translations_dir, source)
        folder = out_dir / "reask" / mff.safe(model) / source
        for path in sorted(folder.glob("r*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if max_run is not None and rec["run"] > max_run:
                continue
            title = str(rec["title"])
            trials.append(
                {
                    "source": source,
                    "run": rec["run"],
                    "sample": rec["sample"],
                    "a0": [],
                    "additions": additions_from_reply(
                        rec["reply"],
                        source_text=text,
                        source_title=title,
                        framing_shaped=concept_mod._framing_shaped(title, text),
                    ),
                    "failure": rec["failure"],
                    "wall_s": float(rec["wall_s"]),
                }
            )
    return trials


def _gather(
    args: argparse.Namespace, *, classes: bool, max_run: int | None
) -> dict[str, Any]:
    pop = load_population(
        args.base_dir, args.out_dir, args.translations_dir, args.model, max_run=max_run
    )
    trials = load_trials(
        args.out_dir, args.translations_dir, args.model, max_run=max_run
    )
    return summarize(pop, trials, classes=classes)


# --- generation ---------------------------------------------------------------


def cmd_generate(args: argparse.Namespace) -> int:
    if args.sources != [LEAD] or not set(args.runs) <= set(FRESH_RUNS):
        raise SystemExit(f"generate: only {LEAD} runs {FRESH_RUNS[0]}-{FRESH_RUNS[-1]}")
    return mff.cmd_run(args)


def cmd_reask(args: argparse.Namespace) -> int:
    import run_concept_screen as rcs
    import screen_pipeline as sp

    args.backend, args.models = "ollama", [args.model]
    timeouts = rcs.load_timeouts(args.screen_dir)
    digest = rcs.run_digest(args, args.model)
    stamp_path = args.out_dir / "reask" / mff.safe(args.model) / "stamp.json"
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    if not stamp_path.is_file():
        stamp = build_stamp(
            model=args.model,
            prompts={
                "reask-empty-system": EMPTY_REASK_SYSTEM_PROMPT,
                "language-anchor": concept_mod._LANGUAGE_ANCHOR,
            },
            host=args.host,
        )
        stamp_path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    population = load_population(
        args.base_dir, args.out_dir, args.translations_dir, args.model
    )
    cache = sp.ChatCache(args.out_dir / "cache" / "chat")
    warmed = False
    for entry in population:
        if not entry["a0_empty"] or entry["run"] not in args.runs:
            continue
        if entry["source"] not in args.sources:
            continue
        text = mff.translation_text(args.translations_dir, entry["source"])
        for sample in args.samples:
            path = reask_path(
                args.out_dir, args.model, entry["source"], entry["run"], sample
            )
            if path.is_file():
                continue
            if not warmed:
                print(
                    f"warm-up {args.model}: {rcs.run_warm_up(args, args.model):.1f} s"
                )
                warmed = True
            seed = reask_seed(entry["run"], sample)
            backend = sp.ScreenBackend(
                model=args.model,
                digest=digest,
                run=seed,
                window_timeout_s=timeouts[args.model],
                whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE
                * timeouts[args.model],
                cache=cache,
                send=rcs.run_sender(args),
                windows=[],
            )
            started = time.monotonic()
            outcome = reask_empty(text, entry["title"], backend)
            record = {
                **outcome,
                "model": args.model,
                "digest": digest,
                "source": entry["source"],
                "title": entry["title"],
                "run": entry["run"],
                "sample": sample,
                "seed": seed,
                "wall_s": time.monotonic() - started,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(
                f"{time.strftime('%H:%M:%S')} {entry['source']} r{entry['run']} "
                f"s{sample}: {record['wall_s']:.0f} s, failure {record['failure']}",
                flush=True,
            )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    data = _gather(args, classes=False, max_run=args.max_run)
    data["futility_stop"] = futility(data)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    data = _gather(args, classes=True, max_run=None)
    if data["invariant_violations"]:
        raise SystemExit("INVALID: a re-ask ran on a non-empty a0")
    print(
        json.dumps(
            {"numbers": data, "verdict": decide(data)}, indent=2, ensure_ascii=False
        )
    )
    return 0


# --- self-test ----------------------------------------------------------------

ES_TEXT = (
    "Hola a todos, la reunión de hoy es sobre el diseño del mando a distancia. "
    "Creo que el botón de encendido debe estar en la parte de arriba, y que el "
    "diseño tiene que ser sencillo para los usuarios que no usan mucho la "
    "tecnología. Estamos de acuerdo en que el proyecto es para el mercado.\n"
) * 30


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _reply(*items: tuple[str, str]) -> str:
    return json.dumps(
        [{"type": t, "title": ti, "description": "d", "body": "b"} for t, ti in items]
    )


def _adds(reply: str | None, *, shaped: bool = True) -> list[Obj]:
    return additions_from_reply(
        reply, source_text=ES_TEXT, source_title="Reunión X", framing_shaped=shaped
    )


class _Stub:
    context_window = 12288
    max_generation_tokens = 8192

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.seen: list[Any] = []

    def chat(self, messages: Any) -> str:
        self.seen.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _t_fires_only_on_empty_long_source() -> None:
    long_text = "x" * concept_mod._REASK_LOW_YIELD_THRESHOLD
    short_text = "x" * (concept_mod._REASK_LOW_YIELD_THRESHOLD - 1)
    one = [{"type": "Concept", "title": "A"}]
    _check(reask_fires([], long_text), "empty list on a long source must fire")
    _check(not reask_fires([], short_text), "a source below the threshold")
    _check(not reask_fires(one, long_text), "a non-empty list never fires")


def _t_messages_are_anchor_free() -> None:
    msgs = build_empty_reask_messages(ES_TEXT)
    system, user = msgs[0]["content"], msgs[1]["content"]
    _check([m["role"] for m in msgs] == ["system", "user"], "two messages")
    _check("ALREADY KEPT" not in user, "no kept-title anchor in the user turn")
    _check("already names" not in system, "no kept-subject anchor in the system turn")
    _check(system == EMPTY_REASK_SYSTEM_PROMPT, "the registered prompt is sent")
    _check(system != concept_mod._REASK_SYSTEM_PROMPT, "not production's prompt")
    _check(concept_mod._LANGUAGE_ANCHOR in user, "the language anchor is carried")
    _check("SOURCE TEXT:\n" + ES_TEXT in user, "the source text is carried")


def _t_additions_pass_production_filters() -> None:
    reply = _reply(
        ("Concept", "Diseño del mando a distancia"),
        ("Concept", "Remote control design"),
        ("Event", "Reunión de planificación del proyecto"),
        ("Concept", "Diseño del mando a distancia"),
    )
    out = _adds(reply)
    _check(
        [o["title"] for o in out] == ["Diseño del mando a distancia"],
        f"gate, framing drop and dedup must apply: {out}",
    )
    _check(_adds("not json at all") == [], "unparseable reply adds nothing")
    _check(_adds(None) == [], "a failed call adds nothing")
    _check(_adds("[]") == [], "[] is a correct answer")


def _t_additions_call_the_production_functions() -> None:
    names = (
        "_strip_ungrounded_expansions",
        "_dedup_merged",
        "_drop_wrong_language_titles",
        "_drop_framing_objects",
        "_drop_source_title_twins",
        "_restates_source_title",
        "_validate",
    )
    calls: list[str] = []
    originals = {n: getattr(concept_mod, n) for n in names}

    def wrap(name: str) -> Callable[..., Any]:
        real = originals[name]

        def spy(*a: Any, **k: Any) -> Any:
            calls.append(name)
            return real(*a, **k)

        return spy

    try:
        for n in names:
            setattr(concept_mod, n, wrap(n))
        _adds(_reply(("Concept", "Diseño del mando a distancia")))
    finally:
        for n, fn in originals.items():
            setattr(concept_mod, n, fn)
    missing = [n for n in names if n not in calls]
    _check(not missing, f"not reimplemented, must call production: {missing}")


def _t_framing_drop_is_not_applied_off_framing_shaped() -> None:
    reply = _reply(("Project", "Reunión de planificación del proyecto"))
    _check(_adds(reply, shaped=True) == [], "dropped on a framing-shaped source")
    _check(len(_adds(reply, shaped=False)) == 1, "kept when not framing-shaped")


def _t_reask_empty_degrades_and_records() -> None:
    ok = _Stub(_reply(("Concept", "Diseño del mando a distancia")))
    out = reask_empty(ES_TEXT, "ES2015a-es", ok)
    _check(out["failure"] is None and out["reply"] is not None, f"ok: {out}")
    _check(len(ok.seen) == 1, "exactly one call")
    _check(
        ok.seen[0][0]["content"] == EMPTY_REASK_SYSTEM_PROMPT, "registered prompt sent"
    )
    bad = reask_empty(ES_TEXT, "ES2015a-es", _Stub(TimeoutError("slow")))
    _check(bad["reply"] is None, "a failed call has no reply")
    _check(bad["failure"] == "TimeoutError", f"type only, never the message: {bad}")


def _t_seeds_are_distinct_and_apart_from_generation() -> None:
    seeds = {reask_seed(run, s) for run in STORED_RUNS + FRESH_RUNS for s in SAMPLES}
    _check(len(seeds) == len(SAMPLES) * 30, "one seed per (run, sample)")
    _check(
        not seeds & set(STORED_RUNS + FRESH_RUNS),
        "a re-ask never reuses a generation seed",
    )


def _pop(run: int, *, empty: bool, source: str = LEAD) -> dict[str, Any]:
    return {
        "source": source,
        "run": run,
        "a0_empty": empty,
        "wall_s": 200.0,
        "failed": False,
    }


def _trial(
    run: int,
    sample: int,
    titles: Sequence[tuple[str, str]],
    *,
    failure: str | None = None,
    wall: float = 60.0,
) -> dict[str, Any]:
    return {
        "source": LEAD,
        "run": run,
        "sample": sample,
        "a0": [],
        "additions": [{"type": t, "title": ti, "description": "d"} for t, ti in titles],
        "failure": failure,
        "wall_s": wall,
    }


def _t_summarize_counts_and_requires_adjudication() -> None:
    pop = [_pop(1, empty=True), _pop(2, empty=True), _pop(3, empty=False)]
    trials = [
        _trial(1, 1, [("Concept", "Diseño")]),
        _trial(1, 2, []),
        _trial(2, 1, [], failure="TimeoutError"),
        _trial(2, 2, []),
    ]
    s = summarize(pop, trials, classes=False)
    _check(s["n"] == 3 and s["a0_zero"] == 2, f"records: {s}")
    _check(s["lead_n"] == 3 and s["lead_zero_a0"] == 2, "lead counts")
    _check(s["trials"] == 4 and s["trials_empty_a1"] == 3, f"trials: {s}")
    _check(s["fires"] == 1 and s["added_total"] == 1, "one addition")
    _check(s["failures"] == 1, "one failed call")
    _check(abs(s["a1_zero_expected"] - 1.5) < 1e-9, f"expected zeros: {s}")
    _check(s["invariant_violations"] == 0, "no violation")
    _check(s["missing_trials"] == 0, "every empty record was re-asked")
    gap = summarize(pop, trials[:2], classes=False)
    _check(gap["missing_trials"] == 1, "an empty record without a trial is a gap")
    bad = [{**_trial(3, 1, [("Concept", "X")]), "a0": [{"type": "C", "title": "Y"}]}]
    _check(
        summarize(pop, bad, classes=False)["invariant_violations"] == 1,
        "a trial on a non-empty a0 violates the arm",
    )
    try:
        summarize(pop, trials, classes=True)
    except SystemExit:
        return
    raise AssertionError("an unadjudicated added object must stop the run")


def _t_summarize_classifies() -> None:
    saved = dict(ADJUDICATION)
    ADJUDICATION[("Concept", "Diseño")] = (SUBJECT, "r")
    ADJUDICATION[("Event", "Reunión")] = (CONTAINER, "r")
    ADJUDICATION[("Concept", "Design")] = (WRONG_LANGUAGE, "r")
    ADJUDICATION[("Concept", "Plan")] = (CONTESTED, "r")
    try:
        trials = [
            _trial(
                1,
                1,
                [
                    ("Concept", "Diseño"),
                    ("Event", "Reunión"),
                    ("Concept", "Design"),
                    ("Concept", "Plan"),
                ],
            )
        ]
        s = summarize([_pop(1, empty=True)], trials, classes=True)
    finally:
        ADJUDICATION.clear()
        ADJUDICATION.update(saved)
    _check(s["added_subject"] == 1 and s["added_slipped"] == 3, f"classes: {s}")


def _base_summary() -> dict[str, Any]:
    return {
        "n": 90,
        "a0_zero": 16,
        "lead_n": 30,
        "lead_zero_a0": 16,
        "lead_run_wall_mean": 200.0,
        "records_total": 90,
        "records_failed": 0,
        "trials": 48,
        "a1_zero_expected": 6.0,
        "failures": 2,
        "reask_wall_mean": 90.0,
        "added_total": 20,
        "added_subject": 14,
        "added_slipped": 2,
        "invariant_violations": 0,
        "missing_trials": 0,
    }


def _t_decide_bars() -> None:
    base = _base_summary()
    _check(decide(base)["verdict"] == "LICENSED", f"base: {decide(base)}")
    nodefect = {**base, "lead_zero_a0": 5}
    _check(decide(nodefect)["verdict"] == "NO-DEFECT", "B0")
    few = {**base, "a0_zero": 5}
    _check(decide(few)["verdict"] == "INCONCLUSIVE", "too few empty records")
    failed = {**base, "records_failed": 20}
    _check(decide(failed)["verdict"] == "INCONCLUSIVE", "generation failures")
    weak = {**base, "a1_zero_expected": 9.0}
    v = decide(weak)
    _check(v["verdict"] == "REJECTED" and not v["bars"]["B1_benefit"], "B1")
    edge = {**base, "a1_zero_expected": 8.0}
    _check(decide(edge)["bars"]["B1_benefit"], "exactly half passes")
    leaky = {**base, "added_slipped": 3}
    _check(not decide(leaky)["bars"]["B2_safety"], "B2 allowance is trials // 20")
    _check(decide({**base, "added_slipped": 2})["bars"]["B2_safety"], "B2 at limit")
    junk = {**base, "added_subject": 5}
    _check(not decide(junk)["bars"]["B3_quality"], "B3")
    none = {**base, "added_total": 0, "added_subject": 0}
    _check(not decide(none)["bars"]["B3_quality"], "B3 needs at least one object")
    flaky = {**base, "failures": 6}
    _check(not decide(flaky)["bars"]["B4_cost"], "B4 failure rate")
    slow = {**base, "reask_wall_mean": 250.0}
    _check(not decide(slow)["bars"]["B4_cost"], "B4 seconds")
    _check(decide({**base, "missing_trials": 1})["verdict"] == "INCONCLUSIVE", "gap")


def _t_futility() -> None:
    _check(futility({"trials": 24, "trials_empty_a1": 19}), "19 of 24 > 0.75")
    _check(not futility({"trials": 24, "trials_empty_a1": 18}), "18 of 24 = 0.75")
    _check(not futility({"trials": 0, "trials_empty_a1": 0}), "no trials, no stop")


def _t_registry_is_consistent() -> None:
    for entry_key, (label, reason) in ADJUDICATION.items():
        _check(label in CLASSES and bool(reason), f"bad entry {entry_key}")
    _check(set(SOURCES) == set(mff.SOURCES) and LEAD in SOURCES, "sources")
    _check(set(STORED_RUNS).isdisjoint(FRESH_RUNS), "fresh runs are new runs")
    _check(
        all(
            s.removesuffix("-es")[:6] in ("ES2015", "IS1004", "TS3009") for s in SOURCES
        ),
        "development series only",
    )
    _check(MODEL == "qwen3:8b", "production default model")


def _t_stamp_names_the_registered_prompt() -> None:
    _check(callable(build_stamp), "the harness stamps its stored results")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_fires_only_on_empty_long_source,
        _t_messages_are_anchor_free,
        _t_additions_pass_production_filters,
        _t_additions_call_the_production_functions,
        _t_framing_drop_is_not_applied_off_framing_shaped,
        _t_reask_empty_degrades_and_records,
        _t_seeds_are_distinct_and_apart_from_generation,
        _t_summarize_counts_and_requires_adjudication,
        _t_summarize_classifies,
        _t_decide_bars,
        _t_futility,
        _t_registry_is_consistent,
        _t_stamp_names_the_registered_prompt,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except (AssertionError, NotImplementedError) as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    home = pathlib.Path.home()
    runs_root = home / "Dev/Projects/openkos-eval-runs"
    parser.add_argument(
        "--out-dir", type=pathlib.Path, default=runs_root / "reask-on-empty"
    )
    parser.add_argument(
        "--base-dir",
        type=pathlib.Path,
        default=runs_root / "framing-floor",
        help="the framing-floor records (runs 1 to 15), read-only",
    )
    parser.add_argument(
        "--translations-dir",
        type=pathlib.Path,
        default=home / "openkos-eval-runs/retrieval-gold/translations",
    )
    parser.add_argument("--model", default=MODEL)
    sub = parser.add_subparsers(dest="command")
    for name, helptext in (
        ("generate", "model-bound: fresh LEAD runs"),
        ("reask", "model-bound: the re-ask on empty runs"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--sources", nargs="+", default=[LEAD])
        p.add_argument("--runs", nargs="+", type=int, required=True)
        p.add_argument("--host", default=None)
        p.add_argument(
            "--screen-dir",
            type=pathlib.Path,
            default=runs_root / "concept-screen",
            help="holds stage0.json, the registered per-call timeouts",
        )
        if name == "reask":
            p.add_argument("--samples", nargs="+", type=int, default=list(SAMPLES))
    score = sub.add_parser("score")
    score.add_argument("--max-run", type=int, default=None)
    sub.add_parser("decide")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {
        "generate": cmd_generate,
        "reask": cmd_reask,
        "score": cmd_score,
        "decide": cmd_decide,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
