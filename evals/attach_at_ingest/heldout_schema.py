"""The data schema of the held-out fixture, and a checker for it (#1701, part 2).

Read `HELDOUT_GUIDE.md` first. The held-out fixture is a Python module,
`attach_fixtures_heldout.py`, next to this file, that defines exactly two
module-level names and imports nothing:

    LIBRARY: list[dict]      the concepts that already exist
    CANDIDATES: list[dict]   the later-meeting extractions to label

`LIBRARY` entries (every key a string unless noted):

    id          required. Concept ID: "<dir>/<slug>", lowercase, no ".md",
                e.g. "decisions/retention-90-days". An imported concept lives
                under "imports/<namespace>/...".
    type        required. An OKF type, e.g. "Decision", "Concept", "Procedure",
                "Event", "Person".
    title       required. The page title.
    body        optional. Markdown text; defaults to the title.
    deprecated  optional bool. True when the page is retired.
    supersedes  optional list of Concept IDs this page replaces. Each target
                must exist in LIBRARY and have the same type. A superseded page
                is treated as retired, as in a real library.

`CANDIDATES` entries:

    cid         required. Unique id, e.g. "h001".
    type        required. The OKF type the extraction was given.
    title       required. The title the extraction produced.
    gold        required. The Concept ID in LIBRARY that the title names
                (the same thing), or None when it names no existing concept.
    klass       required. One of the class names below.
    note        optional. One line on why you labelled it so.

Classes. A class with a gold concept ("same") needs `gold` set; every other
class needs `gold` None. See the guide for what each one means.

    same:      exact, decision-restated, concept-reworded, concept-expanded,
               unreachable
    distinct:  value-changed, value-added, numeral-differs, part-whole,
               sibling-aspect, ambiguous, cross-series, exact-homonym,
               cross-type, deprecated-target, imported-target, unrelated
    excluded:  event, person   (type Event / Person; gold None)

Run this file to check your module:

    uv run python evals/attach_at_ingest/heldout_schema.py

It prints every problem and exits 1, or prints the counts and exits 0.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
MODULE_NAME: Final = "attach_fixtures_heldout"

SAME: Final = frozenset(
    {
        "exact",
        "decision-restated",
        "concept-reworded",
        "concept-expanded",
        "unreachable",
    }
)
DISTINCT: Final = frozenset(
    {
        "value-changed",
        "value-added",
        "numeral-differs",
        "part-whole",
        "sibling-aspect",
        "ambiguous",
        "cross-series",
        "exact-homonym",
        "cross-type",
        "deprecated-target",
        "imported-target",
        "unrelated",
    }
)
EXCLUDED: Final = frozenset({"event", "person"})
EXCLUDED_TYPE: Final = {"event": "Event", "person": "Person"}

LIBRARY_KEYS: Final = frozenset(
    {"id", "type", "title", "body", "deprecated", "supersedes"}
)
CANDIDATE_KEYS: Final = frozenset({"cid", "type", "title", "gold", "klass", "note"})

CLASS_MINIMA: Final[dict[str, int]] = {
    "exact": 5,
    "decision-restated": 8,
    "concept-reworded": 10,
    "concept-expanded": 4,
    "unreachable": 4,
    "value-changed": 8,
    "value-added": 8,
    "numeral-differs": 8,
    "part-whole": 8,
    "sibling-aspect": 8,
    "ambiguous": 3,
    "cross-series": 2,
    "exact-homonym": 2,
    "cross-type": 2,
    "deprecated-target": 3,
    "imported-target": 2,
    "unrelated": 6,
    "event": 8,
    "person": 8,
}
TOTAL_MINIMA: Final[dict[str, int]] = {
    "library": 60,
    "gold_same": 45,
    "gold_same_not_exact": 30,
    "gold_distinct": 70,
    "excluded": 16,
    "decisions_in_library": 20,
    "superseded_in_library": 6,
    "events_in_library": 4,
    "persons_in_library": 4,
}


def problems(
    library: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Every way the two lists break the schema or fall under a minimum."""
    out: list[str] = []
    ids: dict[str, Mapping[str, Any]] = {}
    for i, doc in enumerate(library):
        where = f"LIBRARY[{i}]"
        extra = set(doc) - LIBRARY_KEYS
        if extra:
            out.append(f"{where}: unknown keys {sorted(extra)}")
        for key in ("id", "type", "title"):
            if not isinstance(doc.get(key), str) or not str(doc.get(key)).strip():
                out.append(f"{where}: '{key}' must be a non-empty string")
        if "body" in doc and not isinstance(doc["body"], str):
            out.append(f"{where}: 'body' must be a string")
        if "deprecated" in doc and not isinstance(doc["deprecated"], bool):
            out.append(f"{where}: 'deprecated' must be a bool")
        sup = doc.get("supersedes", [])
        if not isinstance(sup, list) or not all(isinstance(s, str) for s in sup):
            out.append(f"{where}: 'supersedes' must be a list of strings")
        doc_id = doc.get("id")
        if isinstance(doc_id, str):
            if doc_id in ids:
                out.append(f"{where}: duplicate id {doc_id}")
            if doc_id.endswith(".md") or "/" not in doc_id:
                out.append(f"{where}: id must look like '<dir>/<slug>' without .md")
            ids[doc_id] = doc
    for i, doc in enumerate(library):
        for target in (
            doc.get("supersedes", []) if isinstance(doc.get("supersedes"), list) else []
        ):
            other = ids.get(target)
            if other is None:
                out.append(f"LIBRARY[{i}]: supersedes unknown id {target}")
            elif other.get("type") != doc.get("type"):
                out.append(f"LIBRARY[{i}]: supersedes {target} of another type")
    cids: set[str] = set()
    counts: Counter[str] = Counter()
    known = SAME | DISTINCT | EXCLUDED
    for i, cand in enumerate(candidates):
        where = f"CANDIDATES[{i}]"
        extra = set(cand) - CANDIDATE_KEYS
        if extra:
            out.append(f"{where}: unknown keys {sorted(extra)}")
        for key in ("cid", "type", "title", "klass"):
            if not isinstance(cand.get(key), str) or not str(cand.get(key)).strip():
                out.append(f"{where}: '{key}' must be a non-empty string")
        if "gold" not in cand:
            out.append(f"{where}: 'gold' is required (a Concept ID or None)")
        if "note" in cand and not isinstance(cand["note"], str):
            out.append(f"{where}: 'note' must be a string")
        cid = cand.get("cid")
        if isinstance(cid, str):
            if cid in cids:
                out.append(f"{where}: duplicate cid {cid}")
            cids.add(cid)
        klass = cand.get("klass")
        if klass not in known:
            out.append(f"{where}: unknown class {klass!r}")
            continue
        counts[str(klass)] += 1
        gold = cand.get("gold")
        if klass in SAME:
            target = ids.get(gold) if isinstance(gold, str) else None
            if target is None:
                out.append(f"{where}: gold {gold!r} is not in LIBRARY")
            else:
                if target.get("type") != cand.get("type"):
                    out.append(f"{where}: gold has another type than the candidate")
                if target.get("deprecated") is True or str(gold).startswith("imports/"):
                    out.append(f"{where}: gold is retired or imported")
                if any(
                    gold in (o.get("supersedes") or [])
                    for o in library
                    if isinstance(o.get("supersedes"), list)
                ):
                    out.append(f"{where}: gold is superseded by another page")
        elif gold is not None:
            out.append(f"{where}: class {klass} needs gold None")
        if klass in EXCLUDED and cand.get("type") != EXCLUDED_TYPE[str(klass)]:
            out.append(f"{where}: class {klass} needs type {EXCLUDED_TYPE[str(klass)]}")
        if klass not in EXCLUDED and cand.get("type") in EXCLUDED_TYPE.values():
            out.append(
                f"{where}: an Event or Person candidate must use class event/person"
            )
    for klass, minimum in CLASS_MINIMA.items():
        if counts[klass] < minimum:
            out.append(f"class {klass}: {counts[klass]} candidates, minimum {minimum}")
    totals = {
        "library": len(library),
        "gold_same": sum(counts[k] for k in SAME),
        "gold_same_not_exact": sum(counts[k] for k in SAME if k != "exact"),
        "gold_distinct": sum(counts[k] for k in DISTINCT),
        "excluded": sum(counts[k] for k in EXCLUDED),
        "decisions_in_library": sum(1 for d in library if d.get("type") == "Decision"),
        "superseded_in_library": len(
            {
                t
                for d in library
                for t in (d.get("supersedes") or [])
                if isinstance(d.get("supersedes"), list)
            }
        ),
        "events_in_library": sum(1 for d in library if d.get("type") == "Event"),
        "persons_in_library": sum(1 for d in library if d.get("type") == "Person"),
    }
    for name, minimum in TOTAL_MINIMA.items():
        if totals[name] < minimum:
            out.append(f"{name}: {totals[name]}, minimum {minimum}")
    return out


def load(
    module_name: str = MODULE_NAME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The module's `LIBRARY` and `CANDIDATES`. `ModuleNotFoundError` when the
    module does not exist yet."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    module = importlib.import_module(module_name)
    library: list[dict[str, Any]] = list(module.LIBRARY)
    candidates: list[dict[str, Any]] = list(module.CANDIDATES)
    return library, candidates


def _self_test() -> int:
    """Model-free check of the checker itself."""
    failures = []
    if not problems([], []):
        failures.append("an empty fixture must fail the minimums")
    lib = [{"id": "concepts/a", "type": "Concept", "title": "A"}]
    cand = [
        {
            "cid": "x",
            "type": "Concept",
            "title": "A",
            "gold": "concepts/a",
            "klass": "exact",
        }
    ]
    found = problems(lib, cand)
    if any("unknown" in line or "not in LIBRARY" in line for line in found):
        failures.append("a valid entry must raise no schema problem")
    bad = [dict(cand[0], klass="nope")]
    if not any("unknown class" in line for line in problems(lib, bad)):
        failures.append("an unknown class must be reported")
    for line in failures:
        print(f"FAIL: {line}")
    if failures:
        return 1
    print("self-test ok: 3 checks")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    try:
        library, candidates = load()
    except ModuleNotFoundError:
        print(f"{MODULE_NAME}.py does not exist next to this file")
        return 1
    found = problems(library, candidates)
    for line in found:
        print(line)
    if found:
        return 1
    print(f"ok: {len(library)} library documents, {len(candidates)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
