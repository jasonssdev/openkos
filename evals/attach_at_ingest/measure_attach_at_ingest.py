"""Would a near-match attach rule avoid siblings without wrong merges? (#1701, part 2)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules. Production code, prompts and defaults are unchanged. The treatment lives
in this file only, at the one seam where an OpenSpec change would put it: the
lookup `application.ingest.build_attach_lookup` returns, which
`stage_derived_objects` consults for each extracted candidate.

NOTHING HERE CALLS A MODEL. Neither the chat model nor the embedder is
contacted: the later meetings' extractions are the labelled titles of
`attach_fixtures.py`, and every rule is deterministic over titles. A run takes
a few seconds on a CPU and needs no GPU.

Arms, on the library and candidates of `attach_fixtures.py`:

- `shipped`       the lookup as shipped: same OKF type and same normalized
                  title key (exact), Event and Person excluded.
- `near_guarded`  the candidate arm. The shipped lookup first; when it finds
                  nothing, the one existing concept of the same type whose
                  title is a near-match (`similarity.near_match_score`), with
                  the same numerals and the same number of content tokens
                  (coverage 1.0).
- `near_loose`    ablation: the same with coverage 0.75 (the first draft's
                  candidate). It never decides.
- `near`          ablation: the same without the numeral and coverage guards.
                  It shows what the guards are for. It never decides.

Subcommands: `run` (writes the outcomes), `score` (every metric, no bar),
`decide` (the registered bars). All model-free. Check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(HERE))

import attach_fixtures as fx  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.application import ingest as application_ingest  # noqa: E402
from openkos.bundle import imports as bundle_imports  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.resolution import candidates as resolution_candidates  # noqa: E402
from openkos.resolution.normalize import (  # noqa: E402
    canonical_family_member,
    is_suffix_family,
    normalize_key,
)
from openkos.resolution.similarity import (  # noqa: E402
    MATCH_FUNCTION_WORDS,
    near_match_score,
    tokenize,
)

SHIPPED: Final = "shipped"
NEAR_GUARDED: Final = "near_guarded"
NEAR: Final = "near"
NEAR_LOOSE: Final = "near_loose"
ARMS: Final = (SHIPPED, NEAR_GUARDED, NEAR_LOOSE, NEAR)
CANDIDATE_ARM: Final = NEAR_GUARDED
ABLATION_ARM: Final = NEAR

SUPPORTED: Final = "SUPPORTED"
REJECTED: Final = "REJECTED"
INCONCLUSIVE: Final = "INCONCLUSIVE"
NO_HEADROOM: Final = "NO-HEADROOM"
NO_EXPOSURE: Final = "NO-EXPOSURE"

# Registered constants (PREREGISTRATION.md, "Bars"). Every one is a proposal
# until the owner confirms it.
BARS: Final[dict[str, float]] = {
    "NEAR_SCORE_MIN": 0.85,
    "COVERAGE_MIN": 1.0,
    "LOOSE_COVERAGE_MIN": 0.75,
    "WRONG_ADDED_MAX": 0,
    "EXCLUDED_ATTACHED_MAX": 0,
    "NEGATIVES_MIN": 40,
    "EXCLUDED_MIN": 12,
    "EXPOSED_MIN": 12,
    "EXPOSED_CLASSES_MIN": 4,
    "EXPOSED_PER_CLASS_MIN": 2,
    "GAP_MIN": 12,
    "BENEFIT_MIN": 0.25,
    "RIGHT_ADDED_MIN": 8,
}

FIXTURE_DIGEST: Final = (
    "7196c2e17211a58cbe044929711c50d58e5428aae36e9ce23b4cd329608be43b"
)
"""`attach_fixtures.digest()` at registration. A change to the library or the
candidates is a new registration, so it must change this constant too."""

RULE_TEXT: Final = (
    "attach(candidate): type not in ATTACH_EXCLUDED_TYPES; shipped exact key first; "
    "else existing non-deprecated local concepts of the same type with "
    "near_match_score >= NEAR_SCORE_MIN; near_guarded also requires equal "
    "digit-bearing tokens and content-token coverage >= COVERAGE_MIN (near_loose: "
    "LOOSE_COVERAGE_MIN); attach only "
    "when the matches are one suffix family, to its canonical member"
)

DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/attach-at-ingest"
)


# --------------------------------------------------------------------------- #
# The library on disk                                                          #
# --------------------------------------------------------------------------- #


def write_library(root: pathlib.Path, docs: Sequence[fx.LibDoc]) -> pathlib.Path:
    """The library as real OKF documents under `root/bundle`."""
    bundle = root / "bundle"
    for doc in docs:
        path = bundle / f"{doc.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta: dict[str, object] = {
            "type": doc.type,
            "title": doc.title,
            "sensitivity": "private",
        }
        meta.update(dict(doc.extra))
        path.write_text(f"{okf.dump_frontmatter(meta)}{doc.body}", encoding="utf-8")
    return bundle


def _reader(bundle: pathlib.Path) -> Callable[[str], application_ingest.AttachTarget]:
    def read(concept_id: str) -> application_ingest.AttachTarget:
        path = okf.concept_path_for(concept_id, bundle)
        return application_ingest.AttachTarget(
            concept_id, path.read_text(encoding="utf-8")
        )

    return read


# --------------------------------------------------------------------------- #
# The rules                                                                    #
# --------------------------------------------------------------------------- #


def numeral_tokens(key: str) -> frozenset[str]:
    """The tokens of a normalized key that carry a digit: a number, a version, a
    period (`q3`, `2027`, `180`). Two titles that differ here name different
    things even when every other word agrees."""
    return frozenset(
        token for token in key.split() if any(ch.isdigit() for ch in token)
    )


def content_tokens(key: str) -> tuple[str, ...]:
    """The shipped near-match tokenizer's tokens minus function words."""
    return tuple(t for t in tokenize(key) if t not in MATCH_FUNCTION_WORDS)


def coverage(key_a: str, key_b: str) -> float:
    """How much of the longer title the shorter one covers, by content-token
    count: 1.0 for equal sizes. Containment alone ("Remote Control" inside
    "Remote Control Design") is what the shipped near-match rule accepts; this
    makes it symmetric. 0.0 when either has no content token."""
    a, b = content_tokens(key_a), content_tokens(key_b)
    if not a or not b:
        return 0.0
    return min(len(a), len(b)) / max(len(a), len(b))


def near_targets(
    keyed: Sequence[tuple[str, str, str]],
    okf_type: str,
    title: str,
    *,
    coverage_min: float | None,
) -> tuple[str, ...]:
    """The existing concepts a candidate is a near-match of, in id order."""
    key = normalize_key(title)
    found: list[str] = []
    for concept_id, doc_type, other in keyed:
        if doc_type != okf_type or not other:
            continue
        score = near_match_score(key, other)
        if score is None or score < BARS["NEAR_SCORE_MIN"]:
            continue
        if coverage_min is not None:
            if numeral_tokens(key) != numeral_tokens(other):
                continue
            if coverage(key, other) < coverage_min:
                continue
        found.append(concept_id)
    return tuple(sorted(found))


def one_family(ids: Sequence[str]) -> str | None:
    """The canonical member when `ids` are one `-N` suffix family, else `None`:
    two unrelated matches are an ambiguity, and an ambiguity attaches nothing."""
    if not ids:
        return None
    canonical = canonical_family_member(ids)
    if all(i == canonical or is_suffix_family(canonical, i) for i in ids):
        return canonical
    return None


class Library:
    """The library on disk, with the shipped lookup built over it."""

    def __init__(self, root: pathlib.Path, docs: Sequence[fx.LibDoc]) -> None:
        self.bundle = write_library(root, docs)
        self.lookup = application_ingest.build_attach_lookup(
            self.bundle, read=_reader(self.bundle)
        )
        self.keyed: list[tuple[str, str, str]] = [
            (cid, doc_type, key)
            for cid, doc_type, key in resolution_candidates.keyed_documents(self.bundle)
            if doc_type not in application_ingest.ATTACH_EXCLUDED_TYPES
            and not bundle_imports.is_imported_concept(cid)
        ]

    def attach(self, arm: str, okf_type: str, title: str) -> str | None:
        """The Concept ID the arm attaches the candidate to, or `None`."""
        if okf_type in application_ingest.ATTACH_EXCLUDED_TYPES:
            return None
        exact = self.lookup.find(okf_type, title)
        if exact:
            return canonical_family_member(exact)
        if arm == SHIPPED:
            return None
        coverage_min = {
            NEAR_GUARDED: BARS["COVERAGE_MIN"],
            NEAR_LOOSE: BARS["LOOSE_COVERAGE_MIN"],
            NEAR: None,
        }[arm]
        return one_family(
            near_targets(self.keyed, okf_type, title, coverage_min=coverage_min)
        )


def evaluate(
    arm: str, library: Library, cands: Sequence[fx.Candidate]
) -> dict[str, Any]:
    """`cid -> attached_to` for every candidate."""
    return {c.cid: library.attach(arm, c.type, c.title) for c in cands}


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #


def outcome(c: fx.Candidate, attached: str | None) -> str:
    """`right`, `wrong` or `none`: an attach to the gold concept is right, an
    attach to anything else (including for a candidate with no gold) is wrong."""
    if attached is None:
        return "none"
    return "right" if attached == c.gold else "wrong"


def _counted(c: fx.Candidate) -> bool:
    return not c.quoted


def score(
    cands: Sequence[fx.Candidate], attached: Mapping[str, Mapping[str, str | None]]
) -> dict[str, Any]:
    """Every metric, from the stored outcomes. Applies no bar."""
    base = attached[SHIPPED]
    out: dict[str, Any] = {"arms": {}}
    counted = [c for c in cands if _counted(c)]
    same = [c for c in counted if c.klass in fx.GOLD_SAME_CLASSES]
    distinct = [c for c in counted if c.klass in fx.DISTINCT_CLASSES]
    excluded = [c for c in counted if c.klass in fx.EXCLUDED_CLASSES]
    shipped_right = {c.cid for c in same if outcome(c, base[c.cid]) == "right"}
    gap = [c for c in same if c.cid not in shipped_right]
    abl = attached[ABLATION_ARM]
    exposed = [
        c for c in distinct if base[c.cid] is None and outcome(c, abl[c.cid]) == "wrong"
    ]
    exposed_by_class: dict[str, int] = defaultdict(int)
    for c in exposed:
        exposed_by_class[c.klass] += 1
    out["population"] = {
        "counted": len(counted),
        "gold_same": len(same),
        "gold_distinct": len(distinct),
        "excluded": len(excluded),
        "gap": len(gap),
        "exposed": len(exposed),
        "exposed_by_class": dict(sorted(exposed_by_class.items())),
        "exposed_classes": sum(
            1 for n in exposed_by_class.values() if n >= BARS["EXPOSED_PER_CLASS_MIN"]
        ),
        "quoted": len(cands) - len(counted),
    }
    for arm in ARMS:
        got = attached[arm]
        right_added = [
            c
            for c in same
            if outcome(c, got[c.cid]) == "right" and c.cid not in shipped_right
        ]
        wrong_added = [
            c
            for c in counted
            if c.klass not in fx.EXCLUDED_CLASSES
            and outcome(c, got[c.cid]) == "wrong"
            and outcome(c, base[c.cid]) != "wrong"
        ]
        by_class: dict[str, dict[str, int]] = defaultdict(
            lambda: {"right": 0, "wrong": 0, "none": 0}
        )
        for c in counted:
            by_class[c.klass][outcome(c, got[c.cid])] += 1
        out["arms"][arm] = {
            "right": sum(1 for c in same if outcome(c, got[c.cid]) == "right"),
            "right_added": len(right_added),
            "wrong": sum(
                1
                for c in counted
                if c.klass not in fx.EXCLUDED_CLASSES
                and outcome(c, got[c.cid]) == "wrong"
            ),
            "wrong_added": len(wrong_added),
            "wrong_added_ids": [c.cid for c in wrong_added],
            "excluded_attached": sum(1 for c in excluded if got[c.cid] is not None),
            "by_class": {k: dict(v) for k, v in sorted(by_class.items())},
        }
    guarded = out["arms"][CANDIDATE_ARM]
    out["benefit_share"] = guarded["right_added"] / len(gap) if gap else 0.0
    out["upper_bound_95"] = (
        3 / len(distinct) if guarded["wrong_added"] == 0 and distinct else None
    )
    return out


def invariants(
    cands: Sequence[fx.Candidate],
    attached: Mapping[str, Mapping[str, str | None]],
    docs: Sequence[fx.LibDoc],
    *,
    digest: str,
) -> list[str]:
    """What must hold before any number is read; a violation is INCONCLUSIVE."""
    problems: list[str] = []
    if digest != FIXTURE_DIGEST:
        problems.append("the fixture is not the registered one (digest differs)")
    by_id = {d.id: d for d in docs}
    for c in cands:
        if c.gold is not None:
            doc = by_id.get(c.gold)
            if doc is None:
                problems.append(f"{c.cid}: gold {c.gold} is not in the library")
            elif doc.type != c.type:
                problems.append(f"{c.cid}: gold {c.gold} has another type")
            elif dict(doc.extra).get("status") == "deprecated" or doc.id.startswith(
                "imports/"
            ):
                problems.append(f"{c.cid}: gold {c.gold} is not an attach target")
        known = (
            c.klass in fx.GOLD_SAME_CLASSES | fx.DISTINCT_CLASSES | fx.EXCLUDED_CLASSES
        )
        if not known and c.klass != "quoted":
            problems.append(f"{c.cid}: unknown class {c.klass}")
        if (c.klass in fx.GOLD_SAME_CLASSES) != (c.gold is not None):
            problems.append(f"{c.cid}: gold and class disagree")
    base = attached[SHIPPED]
    for arm in ARMS:
        for c in cands:
            if base[c.cid] is not None and attached[arm][c.cid] != base[c.cid]:
                problems.append(f"{arm} changes a shipped attach: {c.cid}")
            if c.type in application_ingest.ATTACH_EXCLUDED_TYPES and (
                attached[arm][c.cid] is not None
            ):
                problems.append(f"{arm} attached an excluded type: {c.cid}")
    return problems


def decide(
    summary: Mapping[str, Any], problems: Sequence[str]
) -> tuple[str, list[str]]:
    """The registered verdict and its reasons, from `score` output."""
    if problems:
        return INCONCLUSIVE, list(problems)
    pop, arms = summary["population"], summary["arms"]
    short: list[str] = []
    if pop["gold_distinct"] < BARS["NEGATIVES_MIN"]:
        short.append(
            f"{pop['gold_distinct']} negatives, minimum {BARS['NEGATIVES_MIN']}"
        )
    if pop["excluded"] < BARS["EXCLUDED_MIN"]:
        short.append(
            f"{pop['excluded']} Event/Person candidates, minimum {BARS['EXCLUDED_MIN']}"
        )
    if pop["exposed"] < BARS["EXPOSED_MIN"]:
        short.append(
            f"{pop['exposed']} exposed negatives, minimum {BARS['EXPOSED_MIN']}"
        )
    if pop["exposed_classes"] < BARS["EXPOSED_CLASSES_MIN"]:
        short.append(
            f"{pop['exposed_classes']} negative classes exposed "
            f"(>= {BARS['EXPOSED_PER_CLASS_MIN']} each), minimum {BARS['EXPOSED_CLASSES_MIN']}"
        )
    if short:
        return NO_EXPOSURE, short
    if pop["gap"] < BARS["GAP_MIN"]:
        return NO_HEADROOM, [
            f"{pop['gap']} same-concept candidates missed, minimum {BARS['GAP_MIN']}"
        ]
    arm = arms[CANDIDATE_ARM]
    failed: list[str] = []
    if arm["wrong_added"] > BARS["WRONG_ADDED_MAX"]:
        failed.append(
            f"W1 wrong attachments added {arm['wrong_added']} > {BARS['WRONG_ADDED_MAX']} "
            f"({', '.join(arm['wrong_added_ids'])})"
        )
    for name in ARMS:
        if arms[name]["excluded_attached"] > BARS["EXCLUDED_ATTACHED_MAX"]:
            failed.append(
                f"W2 {name} attached {arms[name]['excluded_attached']} Event/Person"
            )
    need = max(BARS["RIGHT_ADDED_MIN"], math.ceil(BARS["BENEFIT_MIN"] * pop["gap"]))
    if arm["right_added"] < need:
        failed.append(f"B1 siblings avoided {arm['right_added']} < {need}")
    return (REJECTED, failed) if failed else (SUPPORTED, [])


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def _no_network(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError("this harness never contacts a model server")


def _stamp() -> dict[str, Any]:
    """The identity every stored result carries: harness commit and the rule
    text. No model is named because none is used; the opener refuses any
    connection, so the stamp itself cannot reach Ollama."""
    return build_stamp(
        model="none (model-free)",
        prompts={"attach/rule": RULE_TEXT},
        opener=_no_network,
    )


def run_arms() -> tuple[
    dict[str, dict[str, str | None]], list[fx.LibDoc], list[fx.Candidate]
]:
    docs, cands = fx.library(), fx.candidates()
    with tempfile.TemporaryDirectory() as tmp:
        library = Library(pathlib.Path(tmp), docs)
        attached = {arm: evaluate(arm, library, cands) for arm in ARMS}
    return attached, docs, cands


def _store_path(out: pathlib.Path) -> pathlib.Path:
    return out / "attach-outcomes.json"


def cmd_run(args: argparse.Namespace) -> int:
    attached, _docs, cands = run_arms()
    again, _, _ = run_arms()
    store = {
        "stamp": _stamp(),
        "digest": fx.digest(),
        "deterministic": attached == again,
        "attached": attached,
        "candidates": len(cands),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    _store_path(args.out).write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {_store_path(args.out)} ({len(cands)} candidates, {len(ARMS)} arms)")
    return 0


def _load(out: pathlib.Path) -> tuple[dict[str, Any], list[str]]:
    store = json.loads(_store_path(out).read_text(encoding="utf-8"))
    problems = invariants(
        fx.candidates(), store["attached"], fx.library(), digest=store["digest"]
    )
    if not store.get("deterministic"):
        problems.append("two runs of the same arms disagree")
    return store, problems


def _print_score(summary: Mapping[str, Any]) -> None:
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def cmd_score(args: argparse.Namespace) -> int:
    store, problems = _load(args.out)
    _print_score(score(fx.candidates(), store["attached"]))
    for line in problems:
        print(f"invariant: {line}")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    store, problems = _load(args.out)
    summary = score(fx.candidates(), store["attached"])
    verdict, reasons = decide(summary, problems)
    print(f"verdict: {verdict}")
    for line in reasons:
        print(f"  {line}")
    return 0


# --------------------------------------------------------------------------- #
# Self-test (model-free)                                                       #
# --------------------------------------------------------------------------- #


class _Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, ok: bool, label: str) -> None:
        self.total += 1
        if not ok:
            self.failures.append(label)
            print(f"FAIL: {label}")


def _doc(doc_id: str, doc_type: str, title: str, **extra: object) -> fx.LibDoc:
    return fx.LibDoc(doc_id, doc_type, title, f"# {title}\n", tuple(extra.items()))


def _t_rules(c: _Checks) -> None:
    docs = [
        _doc("concepts/remote-control-design", "Concept", "Remote Control Design"),
        _doc("concepts/phase-2-plan", "Concept", "Phase 2 Rollout Plan"),
        _doc("concepts/policy-a", "Concept", "Access Policy for Data"),
        _doc("concepts/policy-b", "Concept", "Access Policy for Reports"),
        _doc("concepts/old", "Concept", "Panel antiguo", status="deprecated"),
        _doc("imports/x/concepts/vendor-plan", "Concept", "Vendor Plan"),
        _doc("concepts/stoic-philosophy", "Concept", "Stoic Philosophy"),
        _doc(
            "decisions/retain-30", "Decision", "Conservar las lecturas durante 30 días"
        ),
        _doc("concepts/lucia", "Person", "Lucía Prieto"),
        _doc("concepts/consejo", "Event", "Consejo de Producto: 10 de marzo"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        lib = Library(pathlib.Path(tmp), docs)
        same = "concepts/remote-control-design"
        for arm in ARMS:
            c.check(
                lib.attach(arm, "Concept", "remote control design.") == same,
                f"{arm}: the shipped exact key attaches",
            )
            c.check(
                lib.attach(arm, "Event", "Consejo de Producto: 10 de marzo") is None,
                f"{arm}: an Event never attaches, even to an identical title",
            )
            c.check(
                lib.attach(arm, "Person", "Lucía Prieto") is None,
                f"{arm}: a Person never attaches",
            )
            c.check(
                lib.attach(arm, "Concept", "Panel antiguo") is None,
                f"{arm}: a deprecated concept is never a target",
            )
            c.check(
                lib.attach(arm, "Concept", "Vendor Plan") is None,
                f"{arm}: an imported concept is never a target",
            )
        c.check(
            lib.attach(SHIPPED, "Concept", "Design of a Remote Control") is None,
            "shipped: a reworded title does not attach",
        )
        for arm in (NEAR_GUARDED, NEAR_LOOSE, NEAR):
            c.check(
                lib.attach(arm, "Concept", "Design of a Remote Control") == same,
                f"{arm}: a reordered title attaches",
            )
            c.check(
                lib.attach(
                    arm, "Decision", "Conservar las lecturas durante 30 días en frío"
                )
                == (None if arm == NEAR_GUARDED else "decisions/retain-30"),
                f"{arm}: a one-word qualifier is refused only by the strict arm",
            )
            c.check(
                lib.attach(arm, "Concept", "Access Policy") is None,
                f"{arm}: two different near targets are an ambiguity",
            )
            c.check(
                lib.attach(arm, "Procedure", "Remote Control Design") is None,
                f"{arm}: another type is never a target",
            )
        c.check(
            lib.attach(NEAR, "Concept", "Phase 3 Rollout Plan")
            == "concepts/phase-2-plan",
            "near: the ablation attaches a different period",
        )
        c.check(
            lib.attach(NEAR_GUARDED, "Concept", "Phase 3 Rollout Plan") is None,
            "near_guarded: the numeral guard refuses a different period",
        )
        c.check(
            lib.attach(NEAR, "Concept", "Remote Control") == same
            and lib.attach(NEAR_GUARDED, "Concept", "Remote Control") is None,
            "the coverage guard refuses a part of the title",
        )
        c.check(
            lib.attach(NEAR_GUARDED, "Concept", "Stoicism") is None,
            "a one-word containment is refused by coverage (a deliberate miss)",
        )
        c.check(
            lib.attach(
                NEAR_GUARDED, "Decision", "Conservar las lecturas durante 90 días"
            )
            is None
            and lib.attach(NEAR, "Decision", "Conservar las lecturas durante 90 días")
            is not None,
            "a changed number is the guard's job, not the subset rule's",
        )
    c.check(one_family(["a/x", "a/x-2"]) == "a/x", "a suffix family is one target")
    c.check(one_family(["a/x", "a/y"]) is None, "two unrelated targets are refused")
    c.check(one_family([]) is None, "no targets attach nothing")
    c.check(
        numeral_tokens("q3 roadmap 2027") == frozenset({"q3", "2027"}),
        "numeral tokens are the digit-bearing ones",
    )
    c.check(
        coverage("remote control", "remote control design") < 0.75,
        "coverage is symmetric",
    )
    c.check(coverage("a b", "") == 0.0, "an empty title covers nothing")


def _t_fixture(c: _Checks) -> None:
    docs, cands = fx.library(), fx.candidates()
    c.check(fx.digest() == FIXTURE_DIGEST, "the fixture is the registered one")
    c.check(len({c_.cid for c_ in cands}) == len(cands), "candidate ids are unique")
    c.check(len({d.id for d in docs}) == len(docs), "library ids are unique")
    c.check(
        sum(1 for d in docs if d.type == "Decision") == 24,
        "the library holds three positions of each of the 8 chains",
    )
    attached, docs2, cands2 = run_arms()
    problems = invariants(cands2, attached, docs2, digest=fx.digest())
    c.check(not problems, f"the invariants hold on the registered fixture: {problems}")
    c.check(
        attached == run_arms()[0],
        "two runs agree (the rules are deterministic)",
    )
    pop = score(cands2, attached)["population"]
    c.check(pop["quoted"] == 3, "the issue's three quoted titles are descriptive only")
    for name in ("NEGATIVES_MIN", "EXCLUDED_MIN"):
        c.check(
            (pop["gold_distinct"] if name == "NEGATIVES_MIN" else pop["excluded"])
            >= BARS[name],
            f"the fixture meets {name}",
        )
    classes = {c_.klass for c_ in cands2}
    c.check(
        classes - {"quoted"}
        == fx.GOLD_SAME_CLASSES | fx.DISTINCT_CLASSES | fx.EXCLUDED_CLASSES,
        "every class has a candidate and every candidate a known class",
    )
    c.check(
        all(c_.klass != "quoted" or c_.gold is None for c_ in cands2),
        "a quoted title has no gold",
    )


def _synthetic(**over: Any) -> dict[str, Any]:
    pop = {
        "counted": 100,
        "gold_same": 40,
        "gold_distinct": 48,
        "excluded": 12,
        "gap": 30,
        "exposed": 14,
        "exposed_classes": 5,
        "quoted": 3,
        "exposed_by_class": {},
    }
    arm = {
        "right": 20,
        "right_added": 12,
        "wrong": 0,
        "wrong_added": 0,
        "wrong_added_ids": [],
        "excluded_attached": 0,
        "by_class": {},
    }
    arms = {name: dict(arm) for name in ARMS}
    for key, value in over.items():
        if key in pop:
            pop[key] = value
        else:
            arms[CANDIDATE_ARM][key] = value
    return {"population": pop, "arms": arms}


def _t_decide(c: _Checks) -> None:
    c.check(decide(_synthetic(), [])[0] == SUPPORTED, "all bars met is SUPPORTED")
    c.check(
        decide(_synthetic(), ["x"])[0] == INCONCLUSIVE, "an invariant is INCONCLUSIVE"
    )
    for over, name in (
        ({"gold_distinct": 39}, "NEGATIVES_MIN"),
        ({"excluded": 11}, "EXCLUDED_MIN"),
        ({"exposed": 11}, "EXPOSED_MIN"),
        ({"exposed_classes": 3}, "EXPOSED_CLASSES_MIN"),
    ):
        c.check(
            decide(_synthetic(**over), [])[0] == NO_EXPOSURE,
            f"{name} floor is NO-EXPOSURE",
        )
    c.check(
        decide(_synthetic(gap=11), [])[0] == NO_HEADROOM, "a small gap is NO-HEADROOM"
    )
    v, why = decide(_synthetic(wrong_added=1, wrong_added_ids=["c001"]), [])
    c.check(
        v == REJECTED and why[0].startswith("W1"),
        "one wrong attachment is REJECTED on W1",
    )
    c.check(
        decide(_synthetic(right_added=7), [])[0] == REJECTED,
        "fewer than RIGHT_ADDED_MIN siblings avoided is REJECTED",
    )
    c.check(
        decide(_synthetic(right_added=8), [])[0] == SUPPORTED,
        "exactly RIGHT_ADDED_MIN with a 25% share of a 30 gap clears B1",
    )
    c.check(
        decide(_synthetic(gap=40, right_added=9), [])[0] == REJECTED
        and decide(_synthetic(gap=40, right_added=10), [])[0] == SUPPORTED,
        "B1 is the larger of RIGHT_ADDED_MIN and 25% of the gap",
    )
    s = _synthetic()
    s["arms"][NEAR]["excluded_attached"] = 1
    c.check(
        decide(s, [])[0] == REJECTED, "an Event attached by ANY arm is REJECTED on W2"
    )
    s = _synthetic()
    s["arms"][NEAR]["wrong_added"] = 9
    c.check(
        decide(s, [])[0] == SUPPORTED, "the ablation's wrong attachments never decide"
    )


def _t_score(c: _Checks) -> None:
    cands = [
        fx.Candidate("c1", "Concept", "a", "x", "concept-reworded"),
        fx.Candidate("c2", "Concept", "b", "y", "concept-reworded"),
        fx.Candidate("c3", "Concept", "c", None, "part-whole"),
        fx.Candidate("c4", "Concept", "d", None, "part-whole"),
        fx.Candidate("c5", "Event", "e", None, "event"),
        fx.Candidate("c6", "Concept", "f", None, "quoted", quoted=True),
    ]
    shipped = {"c1": "x", "c2": None, "c3": "z", "c4": None, "c5": None, "c6": None}
    arm = {"c1": "x", "c2": "w", "c3": "z", "c4": "q", "c5": None, "c6": "q"}
    attached = {SHIPPED: shipped, NEAR_GUARDED: arm, NEAR_LOOSE: arm, NEAR: arm}
    got = score(cands, attached)["arms"][NEAR_GUARDED]
    c.check(
        got["right"] == 1 and got["right_added"] == 0,
        "a shipped attach is not an addition",
    )
    c.check(
        got["wrong_added"] == 2,
        "a wrong target and a wrong attach are both added wrong",
    )
    c.check(
        got["wrong_added_ids"] == ["c2", "c4"], "the added wrong attachments are named"
    )
    c.check("c6" not in got["wrong_added_ids"], "a quoted title is never counted")
    c.check(
        score(cands, attached)["arms"][SHIPPED]["wrong"] == 1,
        "the shipped rule's own wrong attach (c3) is reported",
    )
    c.check(outcome(cands[0], None) == "none", "no attach is none")


def _t_mutation(c: _Checks) -> None:
    """The guards must bind: removing either one must change an outcome the
    registered bars read."""
    saved = dict(BARS)
    try:
        attached, _docs, cands = run_arms()
        base = score(cands, attached)["arms"][CANDIDATE_ARM]
        BARS["COVERAGE_MIN"] = 0.0
        loose, _, _ = run_arms()
        c.check(
            score(cands, loose)["arms"][CANDIDATE_ARM]["wrong_added"]
            >= base["wrong_added"],
            "a loosened coverage guard cannot reduce the wrong attachments",
        )
        c.check(
            loose[CANDIDATE_ARM] != attached[CANDIDATE_ARM],
            "the coverage guard binds on the registered fixture",
        )
    finally:
        BARS.update(saved)
    c.check(
        run_arms()[0][CANDIDATE_ARM] == attached[CANDIDATE_ARM],
        "the constants are restored after the mutation",
    )
    c.check(
        attached[CANDIDATE_ARM] != attached[NEAR],
        "the guards change what the candidate arm attaches (the ablation differs)",
    )
    saved_numeral = numeral_tokens
    try:
        globals()["numeral_tokens"] = lambda _key: frozenset()
        no_numeral, _, _ = run_arms()
        c.check(
            no_numeral[CANDIDATE_ARM] != attached[CANDIDATE_ARM],
            "the numeral guard binds on the registered fixture",
        )
    finally:
        globals()["numeral_tokens"] = saved_numeral


def _t_prereg(c: _Checks) -> None:
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    c.check("(proposed)" in text, "the registration marks the proposed bars")
    c.check(
        "becomes REGISTERED when the owner has confirmed" in text,
        "the registration states when it becomes registered",
    )
    for name, value in BARS.items():
        c.check(
            f"{name} = {value}" in text, f"the registration states {name} = {value}"
        )
    c.check(
        f"FIXTURE_DIGEST = {FIXTURE_DIGEST}" in text,
        "the registration states the digest",
    )
    for arm in ARMS:
        c.check(f"`{arm}`" in text, f"the registration names arm {arm}")
    for klass in sorted(
        fx.GOLD_SAME_CLASSES | fx.DISTINCT_CLASSES | fx.EXCLUDED_CLASSES
    ):
        c.check(f"`{klass}`" in text, f"the registration names class {klass}")
    for bar in ("W1", "W2", "B1"):
        c.check(f"**{bar}" in text, f"the registration defines {bar}")
    for verdict in (SUPPORTED, REJECTED, INCONCLUSIVE, NO_HEADROOM, NO_EXPOSURE):
        c.check(verdict in text, f"the registration names the verdict {verdict}")
    c.check(
        BARS["WRONG_ADDED_MAX"] == 0 and BARS["EXCLUDED_ATTACHED_MAX"] == 0,
        "the safety ceilings are strict",
    )


def _t_stamp(c: _Checks) -> None:
    stamp = _stamp()
    c.check(stamp["model"]["name"].startswith("none"), "the stamp names no model")
    c.check(bool(stamp["model"].get("error")), "the stamp did not reach a server")
    c.check(
        any(p["id"] == "attach/rule" for p in stamp["prompts"]),
        "the stamp pins the rule text",
    )
    c.check(stamp["harness"]["commit"] is not None, "the stamp pins the harness commit")


def self_test() -> int:
    c = _Checks()
    for fn in (
        _t_rules,
        _t_fixture,
        _t_score,
        _t_decide,
        _t_mutation,
        _t_stamp,
        _t_prereg,
    ):
        fn(c)
    if c.failures:
        print(f"{len(c.failures)} of {c.total} checks failed")
        return 1
    print(f"self-test ok: {c.total} checks")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name, fn, help_text in (
        ("run", cmd_run, "model-free: the outcome of every arm"),
        ("score", cmd_score, "model-free: every metric, no bar applied"),
        ("decide", cmd_decide, "model-free: the registered bars"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
        sp.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not getattr(args, "fn", None):
        parser.error("choose a subcommand or --self-test")
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
