"""Framing-drop floor arms (#1642): generate, replay, score, decide.

`_drop_framing_objects` has no single-object floor by design (#522/#533), and
on a Spanish meeting it can remove the last surviving titles
(`Reunión de ...`), leaving the source with zero objects. Read
`PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules before any generation.

Arms, all replayed from ONE recorded run through production's own functions
(strip, dedup, wrong-language gate, framing drop, source-title twins):

- `a0`  the shipped pipeline.
- `a1`  if `a0` ends empty, the framing drop is not applied (its removals
        are restored).
- `a2`  if `a0` ends empty, the wrong-language gate is not applied; the
        framing drop still is.

Every arm equals `a0` whenever `a0` is non-empty, so a floor can only change
a source that would otherwise end with nothing. `invariant_holds` checks it.

Subcommands:

- `run`      model-bound. Production's `extract_concept_union` at production
             defaults (`selector_judge` off, ADR-0080) on the registered
             Spanish translations, every stage recorded. Needs Ollama.
- `score`    model-free. Replays the recorded runs and prints the numbers
             the registration names.
- `decide`   model-free. Applies the registered bars (needs `ADJUDICATION`
             complete for every restored object).

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_model_screen"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "framing_drop_scope"))

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.base import BackendError  # noqa: E402

Obj = dict[str, Any]

ARMS: Final = ("a0", "a1", "a2")
FLOOR_ARMS: Final = ("a1", "a2")

MODEL: Final = "qwen3:8b"
"""Production's default chat model (`DEFAULT_MODEL`)."""

SOURCES: Final = (
    "ES2015a-es",
    "ES2015d-es",
    "IS1004b-es",
    "IS1004d-es",
    "TS3009c-es",
)
"""Spanish translations of pool DA, the thesis's development series."""
LEAD: Final = "ES2015a-es"
"""The source #1642 was measured on; phase 1 of the registration."""

# Registered bars (PREREGISTRATION.md, "Bars").
DEFECT_LEAD_MIN: Final = 0.20
DEFECT_POOLED_MIN: Final = 0.10
BENEFIT_MAX_RATIO: Final = 0.5
ENGLISH_CONTAINER_PER: Final = 100
QUALITY_SUBJECT_MIN: Final = 0.5

CONTAINER: Final = "container"
SUBJECT: Final = "subject"
CONTESTED: Final = "contested"
CLASSES: Final = (CONTAINER, SUBJECT, CONTESTED)

ADJUDICATION: Final[dict[tuple[str, str], tuple[str, str]]] = {}
"""(type, title) -> (class, reason) for every object an arm restores. Empty at
registration: it is written after `score --list-restored` and BEFORE `decide`
is first run, reading each object's type, title and description only (never
its arm, model or run). A contested object counts as a container."""


# --- replay -------------------------------------------------------------------


def key(obj: Mapping[str, Any]) -> tuple[str, str]:
    return (str(obj["type"]), str(obj["title"]))


def _obj(result: concept_mod.ExtractionResult) -> Obj:
    return {
        "type": result.type,
        "title": result.title,
        "description": result.description,
    }


def replay_arms(
    raw: Sequence[Mapping[str, Any]],
    *,
    source_text: str,
    source_title: str,
    framing_shaped: bool,
) -> dict[str, list[Obj]]:
    """Each arm's final candidate list, from one recorded raw stage."""
    stripped = concept_mod._strip_ungrounded_expansions(
        [
            concept_mod.ExtractionResult(
                type=str(o["type"]),
                title=str(o["title"]),
                description=str(o.get("description", "")),
                body="",
            )
            for o in raw
        ],
        source_text=source_text,
    )
    deduped = concept_mod._dedup_merged(stripped)
    gated = concept_mod._drop_wrong_language_titles(deduped, source_text=source_text)[0]

    def framing(items: list[concept_mod.ExtractionResult]) -> Any:
        return concept_mod._drop_framing_objects(items, framing_shaped=framing_shaped)

    def twins(items: list[concept_mod.ExtractionResult]) -> list[Obj]:
        kept = concept_mod._drop_source_title_twins(items, source_title=source_title)
        return [_obj(r) for r in kept]

    a0 = twins(framing(gated))
    if a0:
        return {"a0": a0, "a1": list(a0), "a2": list(a0)}
    return {"a0": a0, "a1": twins(gated), "a2": twins(framing(deduped))}


def invariant_holds(outputs: Mapping[str, Sequence[Mapping[str, Any]]]) -> bool:
    """A floor may change a source only when `a0` ended empty."""
    a0 = [key(o) for o in outputs["a0"]]
    return not a0 or all([key(o) for o in outputs[a]] == a0 for a in FLOOR_ARMS)


def restored(outputs: Mapping[str, Sequence[Mapping[str, Any]]], arm: str) -> list[Obj]:
    """What an arm kept that `a0` did not."""
    base = Counter(key(o) for o in outputs["a0"])
    out: list[Obj] = []
    for o in outputs[arm]:
        if base[key(o)] > 0:
            base[key(o)] -= 1
        else:
            out.append(dict(o))
    return out


def classify(obj: Mapping[str, Any]) -> str:
    entry = ADJUDICATION.get(key(obj))
    if entry is None:
        raise SystemExit(f"unadjudicated restored object: {key(obj)}")
    return entry[0]


# --- scoring ------------------------------------------------------------------


def summarize(cases: Iterable[Mapping[str, Any]], *, classes: bool) -> dict[str, Any]:
    """Counts over replayed cases, each `{"source", "outputs"}`. With
    `classes`, restored objects are classified (and an unadjudicated one stops
    the run)."""
    out: dict[str, Any] = {
        "n": 0,
        "zero": dict.fromkeys(ARMS, 0),
        "fires": dict.fromkeys(FLOOR_ARMS, 0),
        "invariant_violations": 0,
        "restored_total": dict.fromkeys(FLOOR_ARMS, 0),
        "restored_subject": dict.fromkeys(FLOOR_ARMS, 0),
        "restored_container": dict.fromkeys(FLOOR_ARMS, 0),
        "lead_n": 0,
        "lead_zero_a0": 0,
        "restored_titles": {a: [] for a in FLOOR_ARMS},
    }
    for case in cases:
        outputs = case["outputs"]
        out["n"] += 1
        if not invariant_holds(outputs):
            out["invariant_violations"] += 1
        for arm in ARMS:
            out["zero"][arm] += 0 if outputs[arm] else 1
        if case["source"] == LEAD:
            out["lead_n"] += 1
            out["lead_zero_a0"] += 0 if outputs["a0"] else 1
        for arm in FLOOR_ARMS:
            new = restored(outputs, arm)
            if new:
                out["fires"][arm] += 1
            out["restored_total"][arm] += len(new)
            for obj in new:
                out["restored_titles"][arm].append(
                    [obj["type"], obj["title"], str(obj.get("description", ""))]
                )
                if classes:
                    label = classify(obj)
                    if label == SUBJECT:
                        out["restored_subject"][arm] += 1
                    else:
                        out["restored_container"][arm] += 1
    return out


def decide(*, spanish: Mapping[str, Any], english: Mapping[str, Any]) -> dict[str, Any]:
    """The registered bars. `spanish`: n, zero, lead_n, lead_zero_a0,
    restored_subject, restored_total. `english`: n, restored_container."""
    lead_rate = (
        spanish["lead_zero_a0"] / spanish["lead_n"] if spanish["lead_n"] else 0.0
    )
    pooled = spanish["zero"]["a0"] / spanish["n"] if spanish["n"] else 0.0
    defect = lead_rate >= DEFECT_LEAD_MIN and pooled >= DEFECT_POOLED_MIN
    allowance = max(1, english["n"] // ENGLISH_CONTAINER_PER)
    out: dict[str, Any] = {
        "defect": {"lead_rate": lead_rate, "pooled_rate": pooled, "met": defect}
    }
    for arm in FLOOR_ARMS:
        total = spanish["restored_total"][arm]
        share = spanish["restored_subject"][arm] / total if total else 0.0
        bars = {
            "B1_benefit": spanish["zero"][arm]
            <= BENEFIT_MAX_RATIO * spanish["zero"]["a0"],
            "B2_english_guard": english["restored_container"][arm] <= allowance,
            "B3_quality": total > 0 and share >= QUALITY_SUBJECT_MIN,
        }
        if not defect:
            verdict = "NO-DEFECT"
        elif all(bars.values()):
            verdict = "LICENSED" if arm == "a1" else "LICENSED-PENDING-OWNER"
        else:
            verdict = "REJECTED"
        out[arm] = {
            "verdict": verdict,
            "bars": bars,
            "restored_subject_share": share,
            "english_allowance": allowance,
        }
    return out


# --- records ------------------------------------------------------------------


def safe(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def record_path(
    out_dir: pathlib.Path, model: str, source: str, run: int
) -> pathlib.Path:
    return out_dir / "records" / safe(model) / source / f"r{run}.json"


def translation_text(translations_dir: pathlib.Path, source: str) -> str:
    """The registered translation, byte for byte as ingest would read it."""
    if source not in SOURCES:
        raise SystemExit(f"{source}: not a registered source {SOURCES}")
    return (translations_dir / f"{source}.md").read_text(encoding="utf-8")


def spanish_cases(
    out_dir: pathlib.Path, translations_dir: pathlib.Path, model: str
) -> tuple[list[dict[str, Any]], int]:
    """Replayed Spanish cases, and the number of failed records skipped."""
    cases: list[dict[str, Any]] = []
    failed = 0
    for source in SOURCES:
        text = translation_text(translations_dir, source)
        for path in sorted(
            (out_dir / "records" / safe(model) / source).glob("r*.json")
        ):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("failure") or rec["stages"]["raw"] is None:
                failed += 1
                continue
            title = str(rec["title"])
            outputs = replay_arms(
                rec["stages"]["raw"],
                source_text=text,
                source_title=title,
                framing_shaped=concept_mod._framing_shaped(title, text),
            )
            cases.append({"source": source, "run": rec["run"], "outputs": outputs})
    return cases, failed


def english_cases(
    runs_root: pathlib.Path, prose_sources: pathlib.Path
) -> list[dict[str, Any]]:
    """Replayed stored English (and prose) records that were framing-shaped:
    the population #522/#533/#1575 measured. Reads records only."""
    import measure_framing_scope as mfs

    records, _stats = mfs.load_records(runs_root, prose_sources)
    return [
        {
            "source": r["source"],
            "outputs": replay_arms(
                r["raw"],
                source_text=r["body"],
                source_title=r["title"],
                framing_shaped=True,
            ),
        }
        for r in records
        if r["framing_shaped"] and r["raw"]
    ]


# --- generation ---------------------------------------------------------------


def run_one(
    args: argparse.Namespace,
    rcs: Any,
    sp: Any,
    digest: str | None,
    timeouts: Mapping[str, float],
    source: str,
    run: int,
) -> dict[str, Any]:
    text = translation_text(args.translations_dir, source)
    windows = (
        concept_mod._chunk_lines(text)
        if concept_mod.fans_out(text, source_title=source)
        else []
    )
    cache = sp.ChatCache(args.out_dir / "cache" / "chat")
    backend = sp.ScreenBackend(
        model=args.model,
        digest=digest,
        run=run,
        window_timeout_s=timeouts[args.model],
        whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE * timeouts[args.model],
        cache=cache,
        send=rcs.run_sender(args),
        windows=windows,
    )
    record = sp.run_source(
        text=text, title=source, backend=backend, cache=cache, selector_judge=False
    )
    record.update({"model": args.model, "digest": digest, "source": source, "run": run})
    return dict(record)


def cmd_run(args: argparse.Namespace) -> int:
    import run_concept_screen as rcs
    import screen_pipeline as sp

    args.backend, args.models = "ollama", [args.model]
    timeouts = rcs.load_timeouts(args.screen_dir)
    digest = rcs.run_digest(args, args.model)
    warmed = False
    for source in args.sources:
        for run in args.runs:
            path = record_path(args.out_dir, args.model, source, run)
            if path.is_file():
                continue
            if not warmed:
                print(
                    f"warm-up {args.model}: {rcs.run_warm_up(args, args.model):.1f} s"
                )
                warmed = True
            record = run_one(args, rcs, sp, digest, timeouts, source, run)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(
                f"{time.strftime('%H:%M:%S')} {source} r{run}: "
                f"{len(record['stages']['final'] or [])} objects, "
                f"{record['wall_s']:.0f} s, failure {record['failure']}",
                flush=True,
            )
    return 0


def _gather(args: argparse.Namespace, *, classes: bool) -> dict[str, Any]:
    es, failed = spanish_cases(args.out_dir, args.translations_dir, args.model)
    en = english_cases(args.runs_root, args.prose_sources)
    return {
        "spanish": summarize(es, classes=classes),
        "spanish_failed_records": failed,
        "english": summarize(en, classes=classes),
    }


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(_gather(args, classes=False), indent=2, ensure_ascii=False))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    data = _gather(args, classes=True)
    if (
        data["spanish"]["invariant_violations"]
        or data["english"]["invariant_violations"]
    ):
        raise SystemExit("INVALID: an arm changed a source whose a0 was non-empty")
    verdict = decide(spanish=data["spanish"], english=data["english"])
    print(
        json.dumps({"numbers": data, "verdict": verdict}, indent=2, ensure_ascii=False)
    )
    return 0


# --- self-test ----------------------------------------------------------------

ES_TEXT = (
    "Hola a todos, la reunión de hoy es sobre el diseño del mando a distancia. "
    "Creo que el botón de encendido debe estar en la parte de arriba, y que el "
    "diseño tiene que ser sencillo para los usuarios que no usan mucho la "
    "tecnología. Estamos de acuerdo en que el proyecto es para el mercado.\n"
)


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _raw(*items: tuple[str, str]) -> list[Obj]:
    return [
        {"type": t, "title": ti, "description": "d", "body_chars": 1, "window": 0}
        for t, ti in items
    ]


def _titles(out: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(o["title"]) for o in out]


def _replay(raw: list[Obj], *, shaped: bool = True) -> dict[str, list[Obj]]:
    return replay_arms(
        raw, source_text=ES_TEXT, source_title="Reunión X", framing_shaped=shaped
    )


def _t_no_fire_when_a0_survives() -> None:
    out = _replay(
        _raw(
            ("Concept", "Diseño del mando a distancia"), ("Event", "Reunión de diseño")
        )
    )
    _check(_titles(out["a0"]) == ["Diseño del mando a distancia"], f"a0: {out['a0']}")
    _check(out["a1"] == out["a0"] == out["a2"], "arms must equal a0 when a0 survives")
    _check(invariant_holds(out), "invariant")


def _t_a1_restores_framing_when_a0_empty() -> None:
    out = _replay(_raw(("Project", "Reunión de planificación del proyecto")))
    _check(out["a0"] == [], f"a0 should be empty: {out['a0']}")
    _check(
        _titles(out["a1"]) == ["Reunión de planificación del proyecto"], "a1 restores"
    )
    _check(invariant_holds(out), "invariant")


def _t_a2_restores_wrong_language_topical() -> None:
    out = _replay(
        _raw(
            ("Concept", "Remote control design"),
            ("Project", "Reunión de planificación del proyecto"),
        )
    )
    _check(out["a0"] == [], f"a0 should be empty: {out['a0']}")
    _check(_titles(out["a2"]) == ["Remote control design"], f"a2: {out['a2']}")


def _t_not_framing_shaped_changes_nothing() -> None:
    out = _replay(
        _raw(("Project", "Reunión de planificación del proyecto")), shaped=False
    )
    _check(_titles(out["a0"]) == ["Reunión de planificación del proyecto"], "a0 keeps")
    _check(out["a1"] == out["a0"] == out["a2"], "no floor fires")


def _t_invariant_detects_a_violation() -> None:
    bad = {
        "a0": [{"type": "Concept", "title": "X"}],
        "a1": [{"type": "Concept", "title": "X"}, {"type": "Event", "title": "R"}],
        "a2": [{"type": "Concept", "title": "X"}],
    }
    _check(not invariant_holds(bad), "an arm that adds to a non-empty a0 must fail")


def _t_decide_bars() -> None:
    base: dict[str, Any] = {
        "spanish": {
            "n": 75,
            "zero": {"a0": 20, "a1": 5, "a2": 8},
            "lead_n": 15,
            "lead_zero_a0": 10,
            "restored_subject": {"a1": 6, "a2": 9},
            "restored_total": {"a1": 12, "a2": 12},
        },
        "english": {"n": 400, "restored_container": {"a1": 1, "a2": 0}},
    }
    v = decide(**base)
    _check(v["a1"]["verdict"] == "LICENSED", f"a1: {v['a1']}")
    _check(v["a2"]["verdict"] == "LICENSED-PENDING-OWNER", f"a2: {v['a2']}")
    nodefect = {**base, "spanish": {**base["spanish"], "lead_zero_a0": 1}}
    _check(decide(**nodefect)["a1"]["verdict"] == "NO-DEFECT", "defect bar")
    weak = {
        **base,
        "spanish": {**base["spanish"], "zero": {"a0": 20, "a1": 15, "a2": 8}},
    }
    _check(decide(**weak)["a1"]["verdict"] == "REJECTED", "benefit bar")
    leaky = {**base, "english": {"n": 400, "restored_container": {"a1": 5, "a2": 0}}}
    _check(decide(**leaky)["a1"]["verdict"] == "REJECTED", "english guard")
    junk = {
        **base,
        "spanish": {**base["spanish"], "restored_subject": {"a1": 1, "a2": 9}},
    }
    _check(decide(**junk)["a1"]["verdict"] == "REJECTED", "quality bar")


def _t_summarize_counts_and_requires_adjudication() -> None:
    out = _replay(_raw(("Project", "Reunión de planificación del proyecto")))
    cases = [{"source": LEAD, "outputs": out}]
    s = summarize(cases, classes=False)
    _check(s["zero"] == {"a0": 1, "a1": 0, "a2": 1}, f"zero: {s['zero']}")
    _check(s["fires"] == {"a1": 1, "a2": 0}, f"fires: {s['fires']}")
    _check(s["lead_n"] == 1 and s["lead_zero_a0"] == 1, "lead counts")
    try:
        summarize(cases, classes=True)
    except SystemExit:
        return
    raise AssertionError("an unadjudicated restored object must stop the run")


def _t_registry_is_consistent() -> None:
    for entry_key, (label, reason) in ADJUDICATION.items():
        _check(label in CLASSES and bool(reason), f"bad entry {entry_key}")
    _check(set(FLOOR_ARMS) < set(ARMS), "arms")


def _t_sources_are_dev_translations() -> None:
    _check(
        all(
            s.removesuffix("-es")[:6] in ("ES2015", "IS1004", "TS3009") for s in SOURCES
        ),
        "dev",
    )
    try:
        translation_text(pathlib.Path(), "ES2008a-es")
    except SystemExit:
        return
    raise AssertionError("an unregistered source must be refused")


def _t_run_source_uses_production_defaults() -> None:
    import screen_pipeline as sp

    seen: dict[str, Any] = {}

    def stub(*_a: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise BackendError("stub")

    original = concept_mod.extract_concept_union
    concept_mod.extract_concept_union = stub
    try:
        backend: Any = SimpleNamespace(windows=[], records=[])
        no_cache: Any = None
        sp.run_source(
            text="x", title="t", backend=backend, cache=no_cache, selector_judge=False
        )
    finally:
        concept_mod.extract_concept_union = original
    _check(seen.get("selector_judge") is False, f"selector_judge passed: {seen}")
    _check(seen.get("transcript_decisions") is False, "transcript_decisions default")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_no_fire_when_a0_survives,
        _t_a1_restores_framing_when_a0_empty,
        _t_a2_restores_wrong_language_topical,
        _t_not_framing_shaped_changes_nothing,
        _t_invariant_detects_a_violation,
        _t_decide_bars,
        _t_summarize_counts_and_requires_adjudication,
        _t_registry_is_consistent,
        _t_sources_are_dev_translations,
        _t_run_source_uses_production_defaults,
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
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    home = pathlib.Path.home()
    parser.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos-eval-runs/framing-floor",
    )
    parser.add_argument(
        "--translations-dir",
        type=pathlib.Path,
        default=home / "openkos-eval-runs/retrieval-gold/translations",
    )
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--runs-root",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos-eval-runs",
    )
    parser.add_argument(
        "--prose-sources",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos/examples/extraction-corpus/sources",
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="model-bound generation")
    run.add_argument("--sources", nargs="+", default=list(SOURCES))
    run.add_argument("--runs", nargs="+", type=int, required=True)
    run.add_argument("--host", default=None)
    run.add_argument(
        "--screen-dir",
        type=pathlib.Path,
        default=home / "Dev/Projects/openkos-eval-runs/concept-screen",
        help="holds stage0.json, the registered per-call timeouts",
    )
    sub.add_parser("score")
    sub.add_parser("decide")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"run": cmd_run, "score": cmd_score, "decide": cmd_decide}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
