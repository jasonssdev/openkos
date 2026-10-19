"""Per-rule drop ablation on the cached #1557 records (#1572).

The #1559 loss table shows L7, the deterministic drops, costing window lift.
This harness separates that figure by rule. Each record's recorded `raw`
stage is replayed through production's own functions (strip, dedup,
wrong-language, framing, twins, in production's chunked-path order) with one
rule turned off, and every variant is scored with the screen's unchanged
window-lift metric. Read `DROP_ABLATION_PREREGISTRATION.md` first.

No model is called. Embeddings come from the screen's `bge-m3` cache only:
the embedding function raises `EmbeddingCacheMiss` instead of computing a
vector. The judge (L10) and everything after it are out of scope, because the
judge's reply to a changed input was never generated.

Subcommands (read `--runs-dir`, write under it, never into the repository):

- `list`   the objects each rule drops, per model, as JSON for the benefit
           audit. No embedding is read.
- `score`  per-rule lift delta, objects dropped, windows emptied, and the
           recorded cap stages, as JSON plus markdown tables.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))

import screen_metrics as sm  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

RULES: Final = ("dedup", "language", "framing", "twins")
"""One toggle per deterministic rule of L6 and L7, in production order."""

EMBED_DIGEST: Final = "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
"""The `bge-m3` digest the #1557 screen's embedding cache is keyed on (the
registered prefix is `790764642607`). Pinned, not looked up: asking the local
Ollama for it would be a model-server call."""

PRIMARY: Final = (
    ("qwen3:8b", (1,)),
    ("qwen3.6:35b-a3b", (1,)),
    ("gemma4:26b-a4b", (1,)),
    ("qwen3:14b", (1,)),
    ("qwen3.6:27b", (1,)),
)
SECONDARY: Final = (("qwen3:8b", (1, 2)), ("qwen3.6:27b", (1, 2)))
ISSUE_MODELS: Final = ("qwen3:8b", "qwen3.6:35b-a3b", "gemma4:26b-a4b")
"""The three models #1572 names; the responsible-rule bar counts these."""

Obj = dict[str, Any]


class EmbeddingCacheMiss(RuntimeError):
    """A text had no cached vector. Computing one would call a model."""


def cache_only_embed(texts: list[str]) -> list[list[float]]:
    raise EmbeddingCacheMiss(
        f"{len(texts)} text(s) missing from the embedding cache; "
        "this measurement is deferred rather than embedded"
    )


# --- replay ------------------------------------------------------------------


def _to_result(obj: Mapping[str, Any]) -> concept_mod.ExtractionResult:
    # The body is not recorded (only its length) and no L6/L7 rule reads it.
    return concept_mod.ExtractionResult(
        type=str(obj["type"]),
        title=str(obj["title"]),
        description=str(obj["description"]),
        body="",
    )


def replay(
    raw: Sequence[Mapping[str, Any]],
    *,
    source_text: str,
    source_title: str,
    framing_shaped: bool,
    off: str | None = None,
) -> dict[str, list[Obj]]:
    """Production's chunked-path L6/L7 on recorded raw objects, with `off`
    turned off. Returns the `dedup` and `drops` stages as record objects."""
    if off is not None and off not in RULES:
        raise ValueError(f"unknown rule {off!r}; expected one of {RULES}")
    stripped = concept_mod._strip_ungrounded_expansions(
        [_to_result(o) for o in raw], source_text=source_text
    )
    # The strip may rewrite a title into a new instance; every later stage
    # only removes, so identity after the strip carries the window.
    origin = {id(r): o for r, o in zip(stripped, raw, strict=True)}

    def objs(results: Iterable[concept_mod.ExtractionResult]) -> list[Obj]:
        out: list[Obj] = []
        for r in results:
            source = origin[id(r)]
            out.append(
                {
                    "type": r.type,
                    "title": r.title,
                    "description": r.description,
                    "body_chars": source.get("body_chars"),
                    "window": source.get("window"),
                }
            )
        return out

    deduped = stripped if off == "dedup" else concept_mod._dedup_merged(stripped)
    kept = (
        deduped
        if off == "language"
        else concept_mod._drop_wrong_language_titles(deduped, source_text=source_text)[
            0
        ]
    )
    if off != "framing":
        kept = concept_mod._drop_framing_objects(kept, framing_shaped=framing_shaped)
    if off != "twins":
        kept = concept_mod._drop_source_title_twins(kept, source_title=source_title)
    return {"dedup": objs(deduped), "drops": objs(kept)}


def object_key(obj: Mapping[str, Any]) -> tuple[Any, ...]:
    return (obj["type"], obj["title"], obj["description"], obj["window"])


def matches_recorded(
    replayed: Sequence[Mapping[str, Any]], recorded: Sequence[Mapping[str, Any]]
) -> bool:
    """Same titles, types, descriptions and windows, in the same order."""
    return [object_key(o) for o in replayed] == [object_key(o) for o in recorded]


def restored_objects(without: Sequence[Obj], full: Sequence[Obj]) -> list[Obj]:
    """Objects present with the rule off and absent with every rule on
    (a multiset difference, in the rule-off order)."""
    remaining = Counter(object_key(o) for o in full)
    out: list[Obj] = []
    for obj in without:
        key = object_key(obj)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            out.append(obj)
    return out


def restore_only(
    without: Sequence[Obj], full: Sequence[Obj], *, keep: set[tuple[Any, ...]]
) -> list[Obj]:
    """The full set plus only the restored objects whose key is in `keep`,
    in the rule-off order."""
    restored = {id(o) for o in restored_objects(without, full)}
    return [o for o in without if id(o) not in restored or object_key(o) in keep]


def windows_emptied(
    without: Sequence[Obj], full: Sequence[Obj], *, gold_windows: set[int]
) -> int:
    """Gold windows with an object when the rule is off and none when on."""
    before = {o["window"] for o in without}
    after = {o["window"] for o in full}
    return sum(1 for w in gold_windows if w in before and w not in after)


# --- aggregation -------------------------------------------------------------

PerRecord = tuple[str, Mapping[str, Mapping[int, float]]]
"""(meeting, {variant: {window: lift}}) for one meeting-run."""


def _window_means(
    per_record: Sequence[PerRecord], variant: str
) -> dict[str, dict[int, float]]:
    runs: dict[str, dict[int, list[float]]] = {}
    for meeting, variants in per_record:
        for window, value in variants[variant].items():
            runs.setdefault(meeting, {}).setdefault(window, []).append(value)
    return {m: {w: sm.mean(v) for w, v in ws.items()} for m, ws in runs.items()}


def variant_effect(
    per_record: Sequence[PerRecord],
    variant: str,
    *,
    base: str = "full",
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    """Window lift of `variant` and `base` (each window's mean over runs,
    then the mean over windows) and the paired per-window delta with the
    screen's two-stage bootstrap."""
    cand = _window_means(per_record, variant)
    ref = _window_means(per_record, base)
    clusters = {
        m: [cand[m][w] - v for w, v in ws.items() if w in cand.get(m, {})]
        for m, ws in ref.items()
    }
    return {
        "lift": sm.mean([v for ws in cand.values() for v in ws.values()]),
        "base_lift": sm.mean([v for ws in ref.values() for v in ws.values()]),
        "delta": asdict(sm.two_stage_bootstrap(clusters, sm.mean, resamples=resamples)),
    }


# --- commands ----------------------------------------------------------------


def _screen() -> Any:
    """The screen's CLI module, for its corpus, records and metric. Imported
    late so the self-test and the pure functions above never load it."""
    import run_concept_screen as rcs

    return rcs


def _records(
    rcs: Any, runs_dir: pathlib.Path, model: str, runs: Sequence[int]
) -> list[tuple[str, int, dict[str, Any]]]:
    out = []
    for meeting in rcs.data.POOL_DA:
        for run in runs:
            rec = rcs.load_record(runs_dir, model, meeting, run)
            if rec is None:
                raise SystemExit(f"missing record {model} {meeting} r{run}")
            out.append((meeting, run, rec))
    return out


def _variants(
    rcs: Any, corpus: Any, meeting: str, rec: Mapping[str, Any]
) -> tuple[dict[str, list[Obj]], bool]:
    """Every variant's drops-stage objects, and whether the all-rules-on
    replay reproduces the recorded dedup and drops stages."""
    transcript, _windows, _gold = corpus.meeting(meeting)
    title = str(rec["title"])
    framing = concept_mod._framing_shaped(title, transcript.body)
    raw = rec["stages"]["raw"] or []
    full = replay(
        raw, source_text=transcript.body, source_title=title, framing_shaped=framing
    )
    valid = matches_recorded(
        full["dedup"], rec["stages"]["dedup"] or []
    ) and matches_recorded(full["drops"], rec["stages"]["drops"] or [])
    out: dict[str, list[Obj]] = {"full": full["drops"], "dedup_stage": full["dedup"]}
    for rule in RULES:
        out[rule] = replay(
            raw,
            source_text=transcript.body,
            source_title=title,
            framing_shaped=framing,
            off=rule,
        )["drops"]
    return out, valid


def cmd_list(args: argparse.Namespace) -> int:
    rcs = _screen()
    corpus = rcs.Corpus(args.runs_dir)
    listing: dict[str, Any] = {}
    for model, runs in dict(PRIMARY + SECONDARY).items():
        runs = (1, 2) if model in dict(SECONDARY) else runs
        rows: list[dict[str, Any]] = []
        for meeting, run, rec in _records(rcs, args.runs_dir, model, runs):
            variants, valid = _variants(rcs, corpus, meeting, rec)
            for rule in RULES:
                for obj in restored_objects(variants[rule], variants["full"]):
                    rows.append({"rule": rule, "meeting": meeting, "run": run, **obj})
            if not valid:
                rows.append({"rule": "INVALID", "meeting": meeting, "run": run})
        listing[model] = rows
    out = args.runs_dir / "scores" / "drop_ablation_list.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(listing, indent=2) + "\n", encoding="utf-8")
    for model, rows in listing.items():
        counts = Counter(r["rule"] for r in rows)
        print(f"{model}: {dict(counts)}")
    print(f"wrote {out}")
    return 0


def _load_classes(runs_dir: pathlib.Path) -> dict[tuple[Any, ...], str]:
    path = runs_dir / "scores" / "drop_ablation_classes.json"
    if not path.is_file():
        raise SystemExit(f"{path} missing: classify the `list` output first")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        (r["rule"], *object_key(r)): str(r["class"])
        for r in rows
        if r.get("class") in ("target", "false_positive")
    }


def _score_set(
    rcs: Any,
    corpus: Any,
    vec: Mapping[str, Any],
    records: Sequence[tuple[str, int, dict[str, Any]]],
    classes: Mapping[tuple[Any, ...], str],
) -> dict[str, Any]:
    per_record: list[PerRecord] = []
    counts: dict[str, dict[str, int]] = {
        r: {"dropped": 0, "windows_emptied": 0, "false_positives": 0} for r in RULES
    }
    invalid: list[str] = []
    for meeting, run, rec in records:
        _t, _w, gold = corpus.meeting(meeting)
        fvecs = [
            vec[g.text]
            for g in corpus.gold_only(rcs.data.foreign_meeting(meeting)).sentences
        ]
        gold_windows = {v for v in gold.window_of.values() if v is not None}
        variants, valid = _variants(rcs, corpus, meeting, rec)
        if not valid:
            invalid.append(f"{meeting} r{run}")
            continue
        lifts: dict[str, Mapping[int, float]] = {}
        for stage in rcs.STAGES:
            lifts[f"rec:{stage}"] = rcs.window_lifts(rec, gold, fvecs, vec, stage)
        for name, objs in variants.items():
            lifts[name] = rcs.window_lifts(
                {"stages": {"x": objs}}, gold, fvecs, vec, "x"
            )
        for rule in RULES:
            restored = restored_objects(variants[rule], variants["full"])
            fps = {
                object_key(o)
                for o in restored
                if classes.get((rule, *object_key(o))) == "false_positive"
            }
            counts[rule]["dropped"] += len(restored)
            counts[rule]["false_positives"] += len(fps)
            counts[rule]["windows_emptied"] += windows_emptied(
                variants[rule], variants["full"], gold_windows=gold_windows
            )
            lifts[f"{rule}:fp"] = rcs.window_lifts(
                {
                    "stages": {
                        "x": restore_only(variants[rule], variants["full"], keep=fps)
                    }
                },
                gold,
                fvecs,
                vec,
                "x",
            )
        per_record.append((meeting, lifts))
    if len(invalid) > 1:
        raise SystemExit(f"invalid run: {len(invalid)} records fail replay: {invalid}")
    result: dict[str, Any] = {"invalid": invalid, "rules": {}}
    l7 = variant_effect(per_record, "rec:drops", base="rec:dedup")
    l6 = variant_effect(per_record, "rec:dedup", base="rec:raw")
    result["l7_loss"] = -l7["delta"]["point"]
    result["l6_loss"] = -l6["delta"]["point"]
    result["recorded_lift"] = {
        s: variant_effect(per_record, f"rec:{s}", base="full")["lift"]
        for s in rcs.STAGES
    }
    for rule in RULES:
        effect = variant_effect(per_record, rule)
        fp = variant_effect(per_record, f"{rule}:fp")
        result["rules"][rule] = {
            **effect,
            **counts[rule],
            "false_positive_delta": fp["delta"]["point"],
        }
    result["caps"] = {
        "ceiling": {
            "delta": variant_effect(per_record, "rec:ceiling", base="rec:pre_ceiling")[
                "delta"
            ]["point"],
            "objects_cut": sum(
                len(rec["stages"]["pre_ceiling"] or [])
                - len(rec["stages"]["ceiling"] or [])
                for _m, _r, rec in records
            ),
        },
        "backstop": {
            "delta": variant_effect(per_record, "rec:final", base="rec:judge")["delta"][
                "point"
            ],
            "objects_cut": sum(
                len(rec["stages"]["judge"] or []) - len(rec["stages"]["final"] or [])
                for _m, _r, rec in records
            ),
        },
    }
    return result


def cmd_score(args: argparse.Namespace) -> int:
    rcs = _screen()
    corpus = rcs.Corpus(args.runs_dir)
    store = rcs.EmbedStore(
        args.runs_dir / "cache" / "embed.sqlite", cache_only_embed, EMBED_DIGEST
    )
    classes = _load_classes(args.runs_dir)
    out: dict[str, Any] = {"primary": {}, "secondary": {}}
    for label, plan in (("primary", PRIMARY), ("secondary", SECONDARY)):
        for model, runs in plan:
            records = _records(rcs, args.runs_dir, model, runs)
            texts: list[str] = []
            for meeting, _run, rec in records:
                _t, _w, gold = corpus.meeting(meeting)
                foreign = corpus.gold_only(rcs.data.foreign_meeting(meeting))
                texts += [g.text for g in gold.sentences]
                texts += [g.text for g in foreign.sentences]
                variants, _valid = _variants(rcs, corpus, meeting, rec)
                for objs in variants.values():
                    texts += [rcs.unit_text(o) for o in objs]
                texts += rcs.collect_texts([rec])
            try:
                vec = store.vectors(texts)
            except EmbeddingCacheMiss as exc:
                out[label][model] = {"deferred": str(exc)}
                print(f"{label} {model}: DEFERRED ({exc})", flush=True)
                continue
            out[label][model] = _score_set(rcs, corpus, vec, records, classes)
            print(f"{label} {model}: scored", flush=True)
    path = args.runs_dir / "scores" / "drop_ablation.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(render(out))
    print(f"wrote {path}")
    return 0


def render(out: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for label in ("primary", "secondary"):
        lines.append(f"\n## {label}\n")
        lines.append(
            "| Model | Rule | lift delta [95% CI] | dropped | windows emptied | "
            "false positives | FP delta |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for model, row in out[label].items():
            if "deferred" in row:
                lines.append(f"| `{model}` | — | deferred | | | | |")
                continue
            for rule in RULES:
                r = row["rules"][rule]
                d = r["delta"]
                lines.append(
                    f"| `{model}` | {rule} | {d['point']:+.4f} "
                    f"[{d['low']:+.4f}, {d['high']:+.4f}] | {r['dropped']} | "
                    f"{r['windows_emptied']} | {r['false_positives']} | "
                    f"{r['false_positive_delta']:+.4f} |"
                )
        lines.append(
            "\n| Model | L6 loss | L7 loss | ceiling delta (cut) | backstop delta (cut) | invalid |"
        )
        lines.append("|---|---|---|---|---|---|")
        for model, row in out[label].items():
            if "deferred" in row:
                continue
            c = row["caps"]
            lines.append(
                f"| `{model}` | {-row['l6_loss']:+.4f} | {-row['l7_loss']:+.4f} | "
                f"{c['ceiling']['delta']:+.4f} ({c['ceiling']['objects_cut']}) | "
                f"{c['backstop']['delta']:+.4f} ({c['backstop']['objects_cut']}) | "
                f"{len(row['invalid'])} |"
            )
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _t_replay_toggles() -> None:
    def o(title: str, window: int, kind: str = "Concept") -> Obj:
        return {
            "type": kind,
            "title": title,
            "description": "d",
            "body_chars": 1,
            "window": window,
        }

    raw = [o("Kickoff Meeting", 0, "Event"), o("Remote", 0), o("Remote", 1)]
    kwargs: dict[str, Any] = {
        "source_text": "A: the remote.\n",
        "source_title": "X",
        "framing_shaped": True,
    }
    full = replay(raw, **kwargs)["drops"]
    _check([x["title"] for x in full] == ["Remote"], f"full replay: {full}")
    framing = restored_objects(replay(raw, **kwargs, off="framing")["drops"], full)
    _check([x["title"] for x in framing] == ["Kickoff Meeting"], "framing toggle")
    dedup = restored_objects(replay(raw, **kwargs, off="dedup")["drops"], full)
    _check([x["window"] for x in dedup] == [1], "dedup toggle keeps the window")


def _t_cache_only() -> None:
    try:
        cache_only_embed(["x"])
    except EmbeddingCacheMiss:
        return
    raise AssertionError("cache_only_embed returned a vector")


def self_test() -> int:
    tests: list[Callable[[], None]] = [_t_replay_toggles, _t_cache_only]
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
    parser.add_argument(
        "--runs-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/concept-screen",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list")
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"list": cmd_list, "score": cmd_score}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
