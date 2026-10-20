"""Scope of the framing-object drop, measured on stored runs (#1575).

`_drop_framing_objects` removes every non-`Procedure` object whose own title
matches `_MEETING_SHAPED_TITLE_RE` on a framing-shaped source. Its documented
target (#522/#533) is the container: an object that names the gathering.
#1572 found one `Decision` removed by it that is not a container. This
harness counts, over every stored run that records the stage, how often the
rule removes something that is not a container (a false positive), and how
many container stubs each candidate narrowing would let back in (a leak).

Arms, each a predicate over the object's type, applied to the same
meeting-titled objects of the same framing-shaped sources:

- `before`  the rule in production: everything but `Procedure`.
- `opt1`    exempt `Decision` as well as `Procedure`.
- `opt2a`   drop only `Event`.
- `opt2b`   drop only `Event` and `Project`.
- `shipped` production's own `_drop_framing_objects`, called as is; it must
            equal `before` while the rule is unchanged.

No model is called and none can be: the harness reads recorded stages only.
Sources:

- the `concept-screen`-format records (`stages.dedup`), found under each
  `--records-dir`, de-duplicated by (model, source, hash of the raw stage);
- the tracked `evals/stage_attrition/results/*.jsonl` runs, whose
  `_drop_framing_objects` stage records the objects entering and leaving it.

The class of a non-`Event` object (container stub or subject) is a human
judgment, recorded in `ADJUDICATION` below, keyed by (type, title). Every
`Event` is a container by definition. An unadjudicated non-`Event` object is
an error, so a new store cannot be scored silently.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_model_screen"))

import screen_data as sd  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

Obj = dict[str, Any]

CONTAINER: Final = "container"
SUBJECT: Final = "subject"

ADJUDICATION: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("Decision", "Assignment of Next Meeting Tasks"): (
        SUBJECT,
        "Work packages due before the next meeting; a real assignment.",
    ),
    ("Decision", "Decisions Made in Previous Meeting"): (
        CONTAINER,
        "A heading that wraps an unnamed set of decisions; no decision of "
        "its own. Contested: read as a subject it moves from leak to FP.",
    ),
    ("Project", "New Product Development Meeting"): (
        CONTAINER,
        "Describes the meeting itself, typed Project.",
    ),
}
"""(type, title) -> (class, reason). Written after reading each object's
description in its run; see the results note for the reading."""

ARMS: Final[dict[str, Callable[[str], bool]]] = {
    "before": lambda t: t != "Procedure",
    "opt1": lambda t: t not in ("Procedure", "Decision"),
    "opt2a": lambda t: t == "Event",
    "opt2b": lambda t: t in ("Event", "Project"),
}
"""Whether the arm drops a meeting-titled object of this type."""

RECORD_DIRS: Final = (
    "concept-screen/records",
    "concept-screen/records-pre-dedup-fix",
    "judge-study/records",
    "window-screen/records",
    "runtime-screen/concept-replay/records",
    "runtime-screen/concept-replay/records-pre-dedup-fix",
)


def classify(obj: Mapping[str, Any]) -> str:
    kind, title = str(obj["type"]), str(obj["title"])
    if kind == "Event":
        return CONTAINER
    if kind == "Procedure":
        return SUBJECT  # #413's role exemption: never framing, in every arm
    try:
        return ADJUDICATION[(kind, title)][0]
    except KeyError:
        raise SystemExit(
            f"unadjudicated non-Event object: {kind!r} {title!r}"
        ) from None


def in_scope(objs: Iterable[Mapping[str, Any]]) -> list[Obj]:
    """The meeting-titled objects: the only ones any arm can drop."""
    return [
        dict(o) for o in objs if concept_mod._MEETING_SHAPED_TITLE_RE.search(o["title"])
    ]


def production_drops(objs: Sequence[Mapping[str, Any]]) -> list[Obj]:
    """What production's `_drop_framing_objects` removes from `objs` on a
    framing-shaped source, as record objects (multiset difference)."""
    results = [
        concept_mod.ExtractionResult(
            type=str(o["type"]),
            title=str(o["title"]),
            description=str(o.get("description", "")),
            body="",
        )
        for o in objs
    ]
    kept = Counter(
        (r.type, r.title)
        for r in concept_mod._drop_framing_objects(results, framing_shaped=True)
    )
    removed: list[Obj] = []
    for o in objs:
        key = (str(o["type"]), str(o["title"]))
        if kept[key] > 0:
            kept[key] -= 1
        else:
            removed.append(dict(o))
    return removed


# --- stores ------------------------------------------------------------------


def _prose_text(sources_dir: pathlib.Path, source: str) -> str | None:
    path = sources_dir / f"{source.removeprefix('prose-')}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def load_records(
    runs_root: pathlib.Path, prose_sources: pathlib.Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Distinct framing-shaped-or-not records with their dedup-stage objects."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    stats = {"files": 0, "failed": 0, "duplicate": 0, "no_source": 0}
    for rel in RECORD_DIRS:
        root = runs_root / rel
        transcripts = runs_root / rel.split("/records")[0] / "inputs" / "transcripts"
        for path in sorted(root.rglob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if "stages" not in rec:
                continue  # a stamp, not a record
            stats["files"] += 1
            if rec.get("failure"):
                stats["failed"] += 1
                continue
            raw = rec["stages"]["raw"] or []
            digest = hashlib.sha256(
                json.dumps(raw, sort_keys=True).encode("utf-8")
            ).hexdigest()
            key = (str(rec["model"]), str(rec["source"]), digest)
            if key in seen:
                stats["duplicate"] += 1
                continue
            seen.add(key)
            source = str(rec["source"])
            if source.startswith("prose-"):
                body = _prose_text(prose_sources, source)
            else:
                body = sd.load_transcript(transcripts, source).body
            if body is None:
                stats["no_source"] += 1
                continue
            out.append(
                {
                    "origin": f"{rel}/{rec['model']}/{source}/r{rec['run']}",
                    "model": str(rec["model"]),
                    "source": source,
                    "framing_shaped": concept_mod._framing_shaped(
                        str(rec["title"]), body
                    ),
                    "dedup": rec["stages"]["dedup"] or [],
                    "drops": rec["stages"]["drops"] or [],
                }
            )
    return out, stats


def load_attrition(results_dir: pathlib.Path) -> list[dict[str, Any]]:
    """One entry per recorded `_drop_framing_objects` stage that dropped
    nothing OR dropped a meeting-titled object. A stage that kept a
    meeting-titled object had a source that was not framing-shaped, which
    the record does not carry; it is reported, not scored."""
    out: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            run = json.loads(line)
            for stage in run["stages"]:
                if stage["stage"] != "_drop_framing_objects":
                    continue
                entered, left = stage["entered"], stage["left"]
                matched = in_scope(entered)
                dropped = Counter((o["type"], o["title"]) for o in entered)
                dropped.subtract(Counter((o["type"], o["title"]) for o in left))
                removed = [o for o in matched if dropped[(o["type"], o["title"])] > 0]
                # A kept meeting-titled object means the source was not
                # framing-shaped, which the record does not carry.
                framing = not matched or bool(removed)
                out.append(
                    {
                        "origin": f"{path.name}:{n}/{run['fixture']}/r{run['run']}",
                        "model": run["model"],
                        "source": run["fixture"],
                        "framing_shaped": framing,
                        "dedup": entered,
                        "drops": left,
                    }
                )
    return out


# --- measurement -------------------------------------------------------------


def measure(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-arm drops, false positives and leaks over meeting-titled objects of
    framing-shaped records, plus the production arm and the object listing."""
    scoped = [r for r in records if r["framing_shaped"]]
    rows: list[dict[str, Any]] = []
    for rec in scoped:
        for obj in in_scope(rec["dedup"]):
            rows.append({**obj, "origin": rec["origin"], "class": classify(obj)})
    arms: dict[str, dict[str, Any]] = {}
    for name, drops in ARMS.items():
        dropped = [r for r in rows if drops(r["type"])]
        arms[name] = {
            "dropped": len(dropped),
            "false_positives": [r for r in dropped if r["class"] == SUBJECT],
            "leaks": [
                r for r in rows if not drops(r["type"]) and r["class"] == CONTAINER
            ],
            "survivors": [r for r in rows if not drops(r["type"])],
        }
    shipped: Counter[tuple[str, str, str]] = Counter()
    for rec in scoped:
        for obj in production_drops(in_scope(rec["dedup"])):
            shipped[(obj["type"], obj["title"], rec["origin"])] += 1
    arms["shipped"] = {"dropped": sum(shipped.values())}
    return {
        "records": len(records),
        "framing_shaped_records": len(scoped),
        "meeting_titled_objects": len(rows),
        "by_type": dict(Counter(r["type"] for r in rows)),
        "arms": arms,
        "rows": rows,
    }


def render(label: str, result: Mapping[str, Any]) -> str:
    total = result["meeting_titled_objects"]
    lines = [
        f"### {label}",
        "",
        f"- records: {result['framing_shaped_records']} of {result['records']} "
        "have a framing-shaped source",
        f"- meeting-titled objects on those: {total}; by type {result['by_type']}",
        "",
        "| arm | dropped | false positives | container stubs leaked |",
        "|---|---|---|---|",
    ]
    for name in ("before", "opt1", "opt2a", "opt2b"):
        arm = result["arms"][name]
        lines.append(
            f"| `{name}` | {arm['dropped']} of {total} "
            f"| {len(arm['false_positives'])} of {total} "
            f"| {len(arm['leaks'])} of {total} |"
        )
    lines.append(
        f"| `shipped` (production, equals `before`) | {result['arms']['shipped']['dropped']} of {total} | | |"
    )
    lines += ["", "Non-Event objects the `before` arm drops:", ""]
    odd = [r for r in result["rows"] if r["type"] != "Event"]
    lines += [
        f"- {r['type']} `{r['title']}` ({r['class']}), {r['origin']}" for r in odd
    ] or ["- none"]
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(kind: str, title: str) -> Obj:
    return {"type": kind, "title": title, "description": "d"}


def _t_arms() -> None:
    rec = {
        "origin": "x",
        "framing_shaped": True,
        "dedup": [
            _o("Event", "Design Meeting"),
            _o("Project", "New Product Development Meeting"),
            _o("Decision", "Assignment of Next Meeting Tasks"),
            _o("Decision", "Decisions Made in Previous Meeting"),
            _o("Procedure", "Standup Facilitation Checklist"),
            _o("Concept", "Remote control"),
        ],
    }
    result = measure([rec, {**rec, "framing_shaped": False}])
    _check(result["meeting_titled_objects"] == 5, f"scope: {result['by_type']}")
    arms = result["arms"]
    _check(arms["before"]["dropped"] == 4, "before drops all but Procedure")
    _check(len(arms["before"]["false_positives"]) == 1, "before FP")
    _check(arms["opt1"]["dropped"] == 2 and len(arms["opt1"]["leaks"]) == 1, "opt1")
    _check(arms["opt2a"]["dropped"] == 1 and len(arms["opt2a"]["leaks"]) == 2, "opt2a")
    _check(arms["opt2b"]["dropped"] == 2 and len(arms["opt2b"]["leaks"]) == 1, "opt2b")
    _check(
        not any(arms[a]["false_positives"] for a in ("opt1", "opt2a", "opt2b")), "FP"
    )


def _t_shipped_is_before() -> None:
    objs = [
        _o("Event", "Design Meeting"),
        _o("Project", "New Product Development Meeting"),
        _o("Decision", "Assignment of Next Meeting Tasks"),
        _o("Person", "Team Meeting"),
        _o("Procedure", "Standup Facilitation Checklist"),
    ]
    removed = [(o["type"], o["title"]) for o in production_drops(in_scope(objs))]
    want = [(o["type"], o["title"]) for o in objs if ARMS["before"](o["type"])]
    _check(removed == want, f"production drops {removed}, before drops {want}")


def _t_unadjudicated_fails() -> None:
    try:
        classify(_o("Concept", "Meeting etiquette"))
    except SystemExit:
        return
    raise AssertionError("an unadjudicated object was classified")


def _t_attrition_reading() -> None:
    import tempfile

    def line(entered: list[Obj], left: list[Obj]) -> str:
        return json.dumps(
            {
                "fixture": "f",
                "run": "1",
                "model": "m",
                "stages": [
                    {"stage": "_drop_framing_objects", "entered": entered, "left": left}
                ],
            }
        )

    ev, keep = _o("Event", "Team Meeting"), _o("Concept", "Remote")
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        (path / "a.jsonl").write_text(
            "\n".join([line([ev, keep], [keep]), line([ev, keep], [ev, keep])]) + "\n",
            encoding="utf-8",
        )
        got = load_attrition(path)
    _check([g["framing_shaped"] for g in got] == [True, False], f"{got}")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_arms,
        _t_shipped_is_before,
        _t_unadjudicated_fails,
        _t_attrition_reading,
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
    parser.add_argument(
        "--runs-root",
        type=pathlib.Path,
        default=pathlib.Path.home() / "Dev/Projects/openkos-eval-runs",
    )
    parser.add_argument(
        "--prose-sources",
        type=pathlib.Path,
        default=pathlib.Path.home()
        / "Dev/Projects/openkos/examples/extraction-corpus/sources",
        help="the extraction-corpus sources (untracked in a worktree)",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    records, stats = load_records(args.runs_root, args.prose_sources)
    attrition = load_attrition(REPO_ROOT / "evals" / "stage_attrition" / "results")
    print(f"record files read {stats}; distinct usable records {len(records)}")
    print(render("Concept-screen records", measure(records)))
    print()
    print(render("Stage-attrition runs", measure(attrition)))
    print()
    print(render("Both stores", measure(records + attrition)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
