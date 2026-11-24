"""Are the extra objects of the V4-vetoed hosted arms useful? (#1783)

The hosted screen (#1767, `HOSTED_RESULTS.md`) vetoed three arms on V4 volume:
they wrote 2 to 3 times the reference's objects per meeting-run. V4 is
anchored to the reference (`qwen3:8b`), so it cannot say whether the extra
objects are noise or recall. This harness measures it on the twelve thesis
development meetings (pool DEV12): pool DA from the cached #1767 records, the
seven others from new runs made by `run_concept_screen.py run --pool DEV12`.
It decides nothing `EXTRA_OBJECT_PREREGISTRATION.md` does not.

Subcommands:

- `population`  model-free: each vetoed arm's final objects with no
                counterpart in the `qwen3:8b` output of the same meeting-run,
                plus the `qwen3:8b` control, with their bodies recovered by a
                cache-only replay. Checks every record, the pool-DA pins in this
                file, and pins DEV12 on its first clean build. A cache or
                embedding miss stops it; nothing is generated and nothing under
                the screen's runs directory is written.
- `budget`      model-free: each hosted arm's registered `run_concept_screen.py
                run` line for the seven new meetings, with its cumulative
                spend ceilings, refused if its ledger moved since registration.
- `embed`       bge-m3 vectors of every DEV12 final object, into the screen's
                embedding cache (an embedding model call, no generation).
- `run`         the two registered judges over the pinned population, one call
                per object per judge, cached and resumable. The only judge
                calls, and it refuses an unpinned population or a
                judge whose digest is not the registered one.
- `report`      kappa, agreed shares, the registered V4 replacement rule and
                the re-read #1767 verdicts, applied mechanically.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import re
import sqlite3
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import hosted_screen as hs  # noqa: E402
import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm import parsing  # noqa: E402

Obj = dict[str, Any]
Send = Callable[[str, Sequence[Any], int, float], tuple[str | None, dict[str, Any]]]

REGISTRATION: Final = HERE / "EXTRA_OBJECT_PREREGISTRATION.md"
OUT_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/extra-object-value"
)
"""Where this study writes. The screen's runs directory is only ever read."""

REFERENCE: Final = rcs.BASELINE
ARMS: Final = ("openai-gpt-5-mini", "anthropic-claude-haiku-5-5", "openai-gpt-6-luna")
"""The three arms #1767 vetoed on V4."""
CONTROL: Final = "control"
RUNS: Final = (1, 2)
MEETINGS: Final = data.POOL_DEV12
"""All twelve development meetings (owner decision, 2026-11-23). Pool F stays
reserved as the confirmation pool."""
NEW_MEETINGS: Final = data.POOL_DEV_NEW
"""Need new extraction runs; pool DA's records are reused from cache."""
REFERENCE_DIGEST: Final = (
    "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
)
"""`qwen3:8b` as its #1557 stamp and records name it. Every control record,
old or new, must carry it."""
DA_DATE: Final = "2026-11-23"
"""The date the #1767 hosted runs carry in their digest."""

EMBED_DIGEST: Final = "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
"""`bge-m3` as the local Ollama lists it; the screen's embedding cache is keyed
by it. A vector missing under this digest stops `population`."""
COUNTERPART_THRESHOLD: Final = 0.85
"""An arm object has a counterpart when its `title: description` reaches this
bge-m3 cosine against any `qwen3:8b` final object of the same meeting-run.
The screen's registered same-subject cosine (`cut_redundancy.py`,
`hosted_screen.NEAR_DUP_THRESHOLD`)."""
SENSITIVITY: Final = (0.80, 0.90)
"""Population counts at these thresholds are reported, never decided on."""

JUDGES: Final = (
    ("gpt-oss:20b", "f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe"),
    (
        "gemma4:26b-a4b",
        "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c",
    ),
)
"""(tag, Ollama digest). `run` refuses a judge whose listed digest differs."""
JUDGE_MODELS: Final = tuple(tag for tag, _digest in JUDGES)
JUDGE_TIMEOUT_S: Final = 600.0
ORDER_SEED: Final = 1783
"""Seeds the one presentation order both judges use."""

LABELS: Final = ("useful", "redundant", "trivial")
KAPPA_FLOOR: Final = 0.6
USEFUL_FLOOR: Final = 0.70
"""The 95% lower bound of the arm's agreed useful share must reach this
(owner decision, 2026-11-23: an absolute bar, because the 41-object control
made a relative bound fail by power alone)."""
REDUNDANT_CEILING: Final = 0.20
"""The 95% upper bound of the arm's agreed redundant share."""
MIN_AGREED: Final = 20
"""Fewer agreed items than this on the arm or the control: no verdict."""
MAX_UNLABELLED_SHARE: Final = 0.10
"""More items than this share without a label from either judge: degraded."""

EXPECTED_DA: Final = {
    "openai-gpt-5-mini": (
        72,
        "68973bcfaf90d58f86e4cfde87fc6c1b5a3fb4b88dcd3047bf068f09f39bf779",
    ),
    "anthropic-claude-haiku-5-5": (
        130,
        "2717f18d5ad4d65f93034210f311220387253c82d95da940101644a0798a65a9",
    ),
    "openai-gpt-6-luna": (
        88,
        "4536054f90f31766172e6b1ffd00c1dc0b7e8f503cf1030ca8335859b90d9a27",
    ),
    CONTROL: (
        41,
        "6c4e564f055809b6b5f5e12cb6deb0a0716225bcfea31d0767d485545415919e",
    ),
}
"""Per side: (objects, sha256 of the sorted keys) over the pool-DA meetings,
as registered. The DEV12 population is pinned at run time (`population-pin.json`)."""

G1_FLOOR: Final = -0.05

BUDGET_USD: Final = 3.00
"""The owner's total ceiling for the new hosted runs."""
OVERSHOOT_PROMPT_TOKENS: Final = 2 * sp.CONTEXT_WINDOW
"""A bound on one call's provider-counted prompt (twice the context window,
for a tokenizer that counts more than the local one)."""


@dataclass(frozen=True)
class HostedBudget:
    digest_prefix: str
    """The `<provider>:<served-model>` part of the arm's `--digest`."""
    prior_calls: int
    prior_usd: float
    """The arm's ledger after #1767, read on 2026-11-23. The ledger is
    cumulative, so the run's flags add the new ceilings to these."""
    ceiling_usd: float
    ceiling_calls: int


HOSTED: Final = {
    "openai-gpt-5-mini": HostedBudget("openai:gpt-5-mini", 112, 0.479312, 1.68, 250),
    "anthropic-claude-haiku-5-5": HostedBudget(
        "anthropic:claude-haiku-5-5", 112, 0.1386496, 0.68, 250
    ),
    "openai-gpt-6-luna": HostedBudget(
        "openai:gpt-6-luna", 112, 0.10268819999999998, 0.60, 250
    ),
}


def hosted_arm(label: str) -> hs.Arm:
    return next(a for a in hs.ARMS if a.label == label)


def overshoot_usd(budget: HostedBudget) -> float:
    """The most one call past the ceiling can cost: the guard checks before a
    call, so a run stops at most one call over."""
    arm = hosted_arm(next(k for k, v in HOSTED.items() if v is budget))
    return (
        OVERSHOOT_PROMPT_TOKENS * arm.price_in
        + sp.MAX_GENERATION_TOKENS * arm.price_out
    ) / 1_000_000


def budget_flags(label: str, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """`--max-usd` and `--max-calls` for the new runs of `label`. Refuses a
    ledger that moved since registration: the new ceiling would then not be
    the registered one."""
    b = HOSTED[label]
    if int(ledger.get("calls", 0)) != b.prior_calls or not math.isclose(
        float(ledger.get("usd", 0.0)), b.prior_usd, abs_tol=1e-9
    ):
        raise ValueError(
            f"{label}: ledger at {ledger.get('calls')} calls, ${ledger.get('usd')}; "
            f"registered {b.prior_calls}, ${b.prior_usd}"
        )
    return {
        "max_usd": b.prior_usd + b.ceiling_usd,
        "max_calls": b.prior_calls + b.ceiling_calls,
    }


@dataclass(frozen=True)
class Screen1767:
    """What #1767 recorded for an arm, beside V4 (`HOSTED_RESULTS.md`)."""

    v1_ok: bool
    v5_ok: bool
    g1_low: float
    lift: tuple[float, float, float]
    """(point, low, high) of the window lift delta against `qwen3:8b`."""


RESULTS_1767: Final = {
    "openai-gpt-5-mini": Screen1767(True, True, -0.1167, (0.0151, -0.0230, 0.0479)),
    "anthropic-claude-haiku-5-5": Screen1767(
        True, True, -0.0833, (0.0387, -0.0096, 0.0772)
    ),
    "openai-gpt-6-luna": Screen1767(True, True, -0.0538, (0.0178, -0.0210, 0.0488)),
}

JUDGE_SYSTEM: Final = """You review one candidate entry for a personal knowledge base built from meeting transcripts. If kept, the entry becomes a page of its own. You are given the source excerpt the entry was extracted from, the titles of the other entries extracted from the same meeting, and the entry itself.

Give the entry exactly one label, checking them in this order:
1. "redundant": the entry fragments or restates one of the other entries listed. Its subject is a part of, a rewording of, or the same as another entry's subject, so it would be folded into that entry rather than kept as its own page.
2. "useful": otherwise, if the entry is a distinct concept that the excerpt supports, and a reader of the knowledge base would want it as a page of its own.
3. "trivial": otherwise. The entry is not worth a page of its own: it is generic, obvious or too small to stand alone, or the excerpt does not support it.

Judge only from the excerpt and the listed entries. Do not use outside knowledge. Do not reward length or polish: a short entry can be useful and a long one trivial.

Reply with ONLY a JSON object with the label and one short sentence giving the reason:
{"label": "useful", "reason": "..."}"""

JUDGE_PROMPT_SHA256: Final = (
    "0981f896c894c34c4c64a7bd2f3cc98c46af815bb48508322c3acfb41b5c7bc3"
)


# --- pure ----------------------------------------------------------------------


def unit_text(obj: Mapping[str, Any]) -> str:
    return rcs.unit_text(obj)


def counterpart_flags(
    arm_vecs: Sequence[Sequence[float]],
    ref_vecs: Sequence[Sequence[float]],
    threshold: float,
) -> tuple[list[bool], list[float | None]]:
    """Per arm object (unit vectors in): whether any reference object reaches
    `threshold`, and the best cosine (None with no reference object)."""
    flags: list[bool] = []
    best: list[float | None] = []
    for vec in arm_vecs:
        top = max((sm.dot(vec, ref) for ref in ref_vecs), default=None)
        best.append(top)
        flags.append(top is not None and top >= threshold)
    return flags, best


def item_key(
    model: str, meeting: str, run: int, index: int, obj: Mapping[str, Any]
) -> str:
    """One stable string per population object."""
    return json.dumps(
        [model, meeting, run, index, obj.get("window"), obj["type"], obj["title"],
         obj["description"]],
        ensure_ascii=False,
    )  # fmt: skip


def population_hash(keys: Sequence[str]) -> str:
    """sha256 over the sorted keys, one per line."""
    return hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()


def population_pin(items: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Per side: [objects, sha256 of the sorted keys]."""
    keys = side_keys(items)
    return {side: [len(k), population_hash(k)] for side, k in keys.items()}


def pin_mismatch(
    saved: Mapping[str, Sequence[Any]], current: Mapping[str, Sequence[Any]]
) -> list[str]:
    """Empty when `current` is the pinned population."""
    return [
        f"{side}: {list(current.get(side, []))}, pinned {list(saved[side])}"
        for side in saved
        if list(current.get(side, [])) != list(saved[side])
    ]


def da_pin_problems(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Empty when the pool-DA part of the population is the registered one."""
    da = population_pin([i for i in items if i["meeting"] in data.POOL_DA])
    return pin_mismatch({k: list(v) for k, v in EXPECTED_DA.items()}, da)


def judge_messages(
    window: str, others: Sequence[str], obj: Mapping[str, Any]
) -> list[Any]:
    """The registered user message: excerpt, the other titles of the same
    extraction, then the entry. Nothing names the arm."""
    listed = "\n".join(f"- {t}" for t in others) or "- (none)"
    user = (
        f"SOURCE EXCERPT:\n{window}\n\n"
        f"OTHER ENTRIES FROM THE SAME MEETING (titles):\n{listed}\n\n"
        f"ENTRY:\nType: {obj['type']}\nTitle: {obj['title']}\n"
        f"Description: {obj['description']}\nBody:\n{obj.get('body') or ''}"
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_verdict(reply: object) -> tuple[str, str] | None:
    """(label, reason), or None unless the reply holds one JSON object with an
    allowed label and a non-empty string reason (fail-closed)."""
    obj = parsing.extract_json_object(reply)
    if obj is None:
        return None
    label = obj.get("label")
    reason = obj.get("reason")
    if not isinstance(label, str) or not isinstance(reason, str):
        return None
    label = label.strip().casefold()
    if label not in LABELS or not reason.strip():
        return None
    return label, reason.strip()


def presentation_order(keys: Sequence[str], seed: int = ORDER_SEED) -> list[str]:
    """The registered order: the sorted keys shuffled once with `seed`."""
    out = sorted(keys)
    random.Random(seed).shuffle(out)  # noqa: S311 -- an order, not security
    return out


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa over (judge A, judge B) labels; None when undefined (no
    pairs, or chance agreement is 1)."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    expected = sum(
        (sum(a == lab for a, _ in pairs) / n) * (sum(b == lab for _, b in pairs) / n)
        for lab in LABELS
    )
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1 - expected)


def share(labels: Sequence[str], label: str) -> float:
    return sum(x == label for x in labels) / len(labels) if labels else math.nan


def paired_share_delta(
    arm: Mapping[str, Sequence[str]],
    control: Mapping[str, Sequence[str]],
    label: str,
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> tuple[sm.Interval, int]:
    """Pooled `label` share of the arm minus the control's. Two-stage
    bootstrap: meetings with replacement (one draw shared by both sides), then
    each side's items within each drawn meeting with replacement. Returns the
    interval and the number of draws dropped because a side was empty."""
    keys = sorted(k for k in set(arm) | set(control) if arm.get(k) or control.get(k))
    if not keys:
        raise ValueError("no items to bootstrap")

    def pooled(side: Mapping[str, Sequence[str]], ks: Sequence[str]) -> list[str]:
        return [x for k in ks for x in side.get(k, ())]

    point = share(pooled(arm, keys), label) - share(pooled(control, keys), label)
    rng = random.Random(seed)  # noqa: S311 -- statistical resampling, not security
    draws: list[float] = []
    dropped = 0
    for _ in range(resamples):
        a_s: list[str] = []
        c_s: list[str] = []
        for _m in range(len(keys)):
            k = keys[rng.randrange(len(keys))]
            for side, out in ((arm, a_s), (control, c_s)):
                items = side.get(k, ())
                out.extend(items[rng.randrange(len(items))] for _ in range(len(items)))
        if not a_s or not c_s:
            dropped += 1
            continue
        draws.append(share(a_s, label) - share(c_s, label))
    if not draws:
        raise ValueError("every draw had an empty side")
    return sm.Interval(
        point, sm._percentile(draws, 0.025), sm._percentile(draws, 0.975)
    ), dropped


def share_interval(
    arm: Mapping[str, Sequence[str]],
    label: str,
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> sm.Interval:
    """The arm's pooled `label` share, same two-stage bootstrap."""
    return sm.two_stage_bootstrap(
        arm, lambda xs: share(xs, label), resamples=resamples, seed=seed
    )


PASS: Final = "PASS"  # noqa: S105 -- a verdict label, not a secret
FAIL: Final = "FAIL"
INSUFFICIENT: Final = "INSUFFICIENT"
INCONCLUSIVE: Final = "INCONCLUSIVE"
DEGRADED: Final = "DEGRADED"
INCOMPLETE: Final = "INCOMPLETE"


def arm_rule(useful: sm.Interval, redundant: sm.Interval, n_arm: int) -> str:
    """The registered V4 replacement for one arm: absolute bounds on its own
    agreed shares. The control does not decide."""
    if n_arm < MIN_AGREED:
        return INSUFFICIENT
    if useful.low >= USEFUL_FLOOR and redundant.high <= REDUNDANT_CEILING:
        return PASS
    return FAIL


def fail_reading(useful: sm.Interval, redundant: sm.Interval) -> str:
    """How a FAIL is stated. `inferior`: the interval excludes the bar (the
    useful share's upper bound is below the floor, or the redundant share's
    lower bound is above the ceiling). `unresolved`: the bar sits inside the
    interval."""
    if useful.high < USEFUL_FLOOR or redundant.low > REDUNDANT_CEILING:
        return "inferior"
    return "unresolved"


def study_verdict(kappa: float | None, unlabelled: int, total: int) -> str | None:
    """None when per-arm verdicts may be read; otherwise why not."""
    if total and unlabelled / total > MAX_UNLABELLED_SHARE:
        return DEGRADED
    if kappa is None or kappa < KAPPA_FLOOR:
        return INCONCLUSIVE
    return None


def reread_1767(facts: Screen1767, rule: str) -> str:
    """The #1767 verdict with V4 replaced by `rule`, every other veto and G1
    unchanged. A rule that did not decide (INSUFFICIENT, INCONCLUSIVE,
    DEGRADED) leaves the registered V4 veto standing."""
    vetoes = [
        name
        for name, failed in (
            ("V1", not facts.v1_ok),
            ("V5", not facts.v5_ok),
            ("G1", facts.g1_low < G1_FLOOR),
            ("V4-replacement", rule == FAIL),
            ("V4 (replacement undecided)", rule not in (PASS, FAIL)),
        )
        if failed
    ]
    if vetoes:
        return "VETOED (" + ", ".join(vetoes) + ")"
    _point, low, high = facts.lift
    if low > 0:
        return "BETTER than local"
    if high < 0:
        return "WORSE than local"
    return "NOT DISTINGUISHABLE"


# --- population ------------------------------------------------------------------


class CacheMiss(RuntimeError):
    """A replayed call is not in the screen's chat cache. Sending it would
    regenerate an extraction, which this study never does. Not a
    `BackendError`: production retries and skips a window on one of those."""


def align_bodies(
    final: Sequence[Mapping[str, Any]], objects: Sequence[Any]
) -> list[str]:
    """Bodies of the replayed `objects`, after checking they are the recorded
    `final` list (type, title, description and body length, in order)."""
    got = [(o.type, o.title, o.description, len(o.body or "")) for o in objects]
    want = [(o["type"], o["title"], o["description"], o["body_chars"]) for o in final]
    if got != want:
        raise ValueError("the replay does not reproduce the recorded final list")
    return [o.body or "" for o in objects]


def replay_bodies(
    corpus: rcs.Corpus,
    cache: sp.ChatCache,
    timeout_s: float,
    meeting: str,
    run: int,
    rec: Mapping[str, Any],
    unrecorded: list[str] | None = None,
) -> list[str]:
    """Final-object bodies of one record from a cache-only replay of the
    production pipeline (the record keeps only their length).

    Every call the record made must be served from the cache, and the replayed
    final list must be the recorded one. A miss on a call the record never
    made is allowed and appended to `unrecorded`: the current pipeline added an
    optional call after the record was made (the meeting Event, #1689), it
    fails closed without a model, and the alignment check proves the output
    did not move."""
    transcript, wins, _g = corpus.meeting(meeting)
    misses: list[str] = []

    def no_send(model: str, messages: Sequence[Any], seed: int, timeout: float) -> Any:
        # Recorded before raising: production catches broad exceptions around
        # its optional calls, so the raise alone can be lost.
        misses.append(f"{model} seed {seed}")
        raise CacheMiss(f"{model}: call not in the cache (seed {seed})")

    backend = sp.ScreenBackend(
        model=str(rec["model"]),
        digest=rec["digest"],
        run=run,
        window_timeout_s=timeout_s,
        whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE * timeout_s,
        cache=cache,
        send=no_send,
        windows=[w.text for w in wins],
    )
    outcome = concept_mod.extract_concept_union(
        transcript.body,
        source_title=meeting,
        llm=backend,
        transcript_decisions=False,
        selector_judge=True,
    )
    where = f"{rec['model']} {meeting} r{run}"
    served = {r.key for r in backend.records if r.cache_hit}
    recorded = {
        str(c["key"]) for c in rec["calls"] if c["outcome"] != "timeout_not_resent"
    }
    if recorded - served:
        raise SystemExit(f"{where}: {len(recorded - served)} recorded calls not served")
    if unrecorded is not None:
        unrecorded.extend(f"{where}: {m}" for m in misses)
    try:
        return align_bodies(rec["stages"]["final"] or [], outcome.objects)
    except ValueError as exc:
        raise SystemExit(f"{where}: {exc}") from exc


class ReadOnlyCache(sp.ChatCache):
    """The screen's chat cache, which this study must never write."""

    def put(self, key: str, entry: dict[str, Any]) -> None:
        raise CacheMiss(f"refusing to write the screen's cache ({key[:12]})")


class ReadOnlyEmbeddings:
    """The screen's bge-m3 cache, opened read-only. A miss raises."""

    def __init__(self, path: pathlib.Path, digest: str = EMBED_DIGEST) -> None:
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.digest = digest

    def vector(self, text: str) -> list[float]:
        key = hashlib.sha256(f"{self.digest}\x00{text}".encode()).hexdigest()
        row = self.db.execute("SELECT vec FROM emb WHERE key=?", (key,)).fetchone()
        if row is None:
            raise SystemExit(f"no cached bge-m3 vector for {text[:60]!r}")
        blob = row[0]
        return list(struct.unpack(f"{len(blob) // 4}f", blob))


_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def record_problems(model: str, meeting: str, rec: Mapping[str, Any]) -> list[str]:
    """Why a record is not one this study may use: the control's digest, a
    hosted arm's provider, model and date, and no seed-0 call (seed 0 is the
    stage-0 calibration's; a scored run never sends it)."""
    where = f"{model} {meeting} r{rec.get('run', '?')}"
    out: list[str] = []
    digest = str(rec.get("digest"))
    if model == REFERENCE:
        if digest != REFERENCE_DIGEST:
            out.append(f"{where}: digest {digest}, registered {REFERENCE_DIGEST}")
    else:
        prefix, _sep, date = digest.rpartition(":")
        if prefix != HOSTED[model].digest_prefix or not _DATE.match(date):
            out.append(
                f"{where}: digest {digest} is not {HOSTED[model].digest_prefix}:<date>"
            )
        elif meeting in data.POOL_DA and date != DA_DATE:
            out.append(f"{where}: a pool-DA record must be the #1767 run of {DA_DATE}")
        elif meeting not in data.POOL_DA and date < DA_DATE:
            out.append(f"{where}: dated {date}, before the #1767 runs")
    if any(int(c.get("seed", -1)) == 0 for c in rec.get("calls", [])):
        out.append(f"{where}: holds a seed-0 call")
    return out


def provider_failures(rec: Mapping[str, Any]) -> int:
    """Windows that ended in a provider error (#1767: never a model result)."""
    return hs.provider_error_count({"windows": [], **rec})


def build_population(
    load: Callable[[str, str, int], Mapping[str, Any] | None],
    vector: Callable[[str], list[float]],
    bodies: Callable[[str, int, Mapping[str, Any]], list[str]],
) -> dict[str, Any]:
    """Items per side, counts, record problems, and per side the reasons it is
    INCOMPLETE (a missing hosted record, a provider-failure window).
    `load(model, meeting, run)` reads a record, `bodies(meeting, run, rec)`
    recovers its final bodies. A missing control record stops the build: the
    counterpart rule needs it."""
    items: list[Obj] = []
    counts: dict[str, Any] = {}
    problems: list[str] = []
    incomplete: dict[str, list[str]] = {}
    for model in (*ARMS, REFERENCE):
        side = CONTROL if model == REFERENCE else model
        row: dict[str, Any] = {
            "final": 0,
            "no_window": 0,
            "with_counterpart": 0,
            "population": 0,
            "meeting_runs": 0,
            "dates": [],
        }
        row["sensitivity"] = {str(t): 0 for t in SENSITIVITY}
        for meeting in MEETINGS:
            for run in RUNS:
                ref = load(REFERENCE, meeting, run)
                if ref is None:
                    raise SystemExit(f"no {REFERENCE} record for {meeting} r{run}")
                rec = load(model, meeting, run)
                if rec is None:
                    incomplete.setdefault(side, []).append(
                        f"{meeting} r{run}: no record"
                    )
                    continue
                problems += record_problems(model, meeting, rec)
                failed = provider_failures(rec)
                if failed:
                    incomplete.setdefault(side, []).append(
                        f"{meeting} r{run}: {failed} provider-failure windows"
                    )
                row["meeting_runs"] += 1
                date = str(rec.get("digest")).rpartition(":")[2]
                if model != REFERENCE and date not in row["dates"]:
                    row["dates"].append(date)
                final = rec["stages"]["final"] or []
                ref_final = ref["stages"]["final"] or []
                body = bodies(meeting, run, rec)
                row["final"] += len(final)
                if model == REFERENCE:
                    flags = [False] * len(final)
                    best: list[float | None] = [None] * len(final)
                else:
                    flags, best = counterpart_flags(
                        [vector(unit_text(o)) for o in final],
                        [vector(unit_text(o)) for o in ref_final],
                        COUNTERPART_THRESHOLD,
                    )
                titles = [str(o["title"]) for o in final]
                for i, obj in enumerate(final):
                    if model != REFERENCE:
                        for t in SENSITIVITY:
                            row["sensitivity"][str(t)] += obj.get(
                                "window"
                            ) is not None and (
                                best[i] is None or float(best[i] or 0) < t
                            )
                    if obj.get("window") is None:
                        row["no_window"] += 1
                        continue
                    if flags[i]:
                        row["with_counterpart"] += 1
                        continue
                    row["population"] += 1
                    items.append(
                        {
                            "key": item_key(model, meeting, run, i, obj),
                            "side": side,
                            "model": model,
                            "meeting": meeting,
                            "run": run,
                            "index": i,
                            "window": int(obj["window"]),
                            "type": obj["type"],
                            "title": obj["title"],
                            "description": obj["description"],
                            "body": body[i],
                            "others": titles[:i] + titles[i + 1 :],
                            "best_reference_cosine": best[i],
                        }
                    )
        counts[side] = row
    return {
        "items": items,
        "counts": counts,
        "problems": problems,
        "incomplete": incomplete,
    }


def side_keys(items: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {side: [] for side in (*ARMS, CONTROL)}
    for item in items:
        out[str(item["side"])].append(str(item["key"]))
    return out


PIN_FILE: Final = "population-pin.json"


def cmd_population(args: argparse.Namespace) -> int:
    corpus = rcs.Corpus(args.runs_dir)
    cache = ReadOnlyCache(args.runs_dir / "cache" / "chat")
    # Every arm ran with `--timeout-from qwen3:8b` (#1767).
    timeout_s = rcs.load_timeouts(args.runs_dir)[REFERENCE]
    embeddings = ReadOnlyEmbeddings(args.runs_dir / "cache" / "embed.sqlite")
    unrecorded: list[str] = []
    pop = build_population(
        lambda m, s, r: rcs.load_record(args.runs_dir, m, s, r),
        embeddings.vector,
        lambda s, r, rec: replay_bodies(
            corpus, cache, timeout_s, s, r, rec, unrecorded
        ),
    )
    pop["unrecorded_misses"] = unrecorded
    pin = population_pin(pop["items"])
    summary = {
        side: {**pop["counts"][side], "objects": pin[side][0], "sha256": pin[side][1]}
        for side in pin
    }
    print(json.dumps(summary, indent=2))
    print(f"unrecorded optional calls that missed the cache: {len(unrecorded)}")
    failed = False
    for p in pop["problems"]:
        print(f"RECORD NOT USABLE: {p}")
        failed = True
    for p in da_pin_problems(pop["items"]):
        print(f"NOT THE REGISTERED POOL-DA POPULATION: {p}")
        failed = True
    for side, reasons in pop["incomplete"].items():
        print(f"INCOMPLETE {side}: {'; '.join(reasons)}")
    if pop["incomplete"] and not args.final:
        print(
            "re-run the affected meeting-runs (at most two re-runs per arm), or pass "
            "--final once they are spent or a ceiling stopped the run"
        )
        failed = True
    if failed:
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "population.json").write_text(
        json.dumps(pop, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    pin_path = args.out_dir / PIN_FILE
    if not pin_path.is_file():
        pin_path.write_text(json.dumps(pin, indent=1) + "\n", encoding="utf-8")
        print(f"pinned the DEV12 population in {pin_path}")
        return 0
    mismatch = pin_mismatch(json.loads(pin_path.read_text(encoding="utf-8")), pin)
    for p in mismatch:
        print(f"NOT THE PINNED DEV12 POPULATION: {p}")
    return 1 if mismatch else 0


def cmd_embed(args: argparse.Namespace) -> int:
    """Embed every DEV12 final object on the local bge-m3 into the screen's
    own embedding cache, so `population` can stay model-free. Refuses a bge-m3
    whose digest is not the registered one."""
    from harness_stamp import model_identity

    got = model_identity(rcs.EMBED_MODEL, args.host).get("digest")
    if got != EMBED_DIGEST:
        raise SystemExit(f"refusing: bge-m3 digest {got}, registered {EMBED_DIGEST}")
    store = rcs.make_store(args)
    texts: list[str] = []
    for model in (*ARMS, REFERENCE):
        for meeting in MEETINGS:
            for run in RUNS:
                rec = rcs.load_record(args.runs_dir, model, meeting, run)
                if rec is not None:
                    texts += [unit_text(o) for o in rec["stages"]["final"] or []]
    store.vectors(texts)
    print(f"{len(set(texts))} texts embedded or already cached")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    """Model-free: the registered `run` command line of each hosted arm, with
    its cumulative `--max-usd` and `--max-calls` read against its ledger."""
    for label in ARMS:
        path = args.runs_dir / "hosted" / f"{rcs.safe(label)}-ledger.json"
        ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        flags = budget_flags(label, ledger)
        arm = hosted_arm(label)
        print(
            "uv run python evals/concept_model_screen/run_concept_screen.py run "
            f"--models {label} --pool DEV12 --runs 1 2 "
            f"--backend openai-compatible --base-url {arm.base_url} "
            f"--served-model {arm.served_model} "
            f"--digest {HOSTED[label].digest_prefix}:<date of run> "
            "--timeout-from qwen3:8b --no-seed "
            f"--max-calls {flags['max_calls']} --max-usd {math.floor(flags['max_usd'] * 1e6) / 1e6:.6f} "
            f"--price-in {arm.price_in:.2f} --price-out {arm.price_out:.2f}"
        )
    return 0


def _load_pinned(out_dir: pathlib.Path) -> tuple[list[Obj], dict[str, list[str]]]:
    """The population, refused unless its DA part is the registered one and
    the whole is the one pinned when it was first built."""
    path = out_dir / "population.json"
    pin_path = out_dir / PIN_FILE
    if not path.is_file() or not pin_path.is_file():
        raise SystemExit("population.json or its pin missing: run `population` first")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    items = list(loaded["items"])
    problems = da_pin_problems(items) + pin_mismatch(
        json.loads(pin_path.read_text(encoding="utf-8")), population_pin(items)
    )
    if problems:
        raise SystemExit("not the registered population: " + "; ".join(problems))
    return items, dict(loaded.get("incomplete") or {})


# --- run -------------------------------------------------------------------------


def judge_one(
    cache: sp.ChatCache,
    send: Send,
    model: str,
    digest: str,
    messages: list[Any],
) -> dict[str, Any]:
    """One judged object: seed 0, one retry (seed 1000) on an unusable reply,
    then no label (counted)."""
    total = 0.0
    for attempt in range(2):
        seed = attempt * sp.RETRY_SEED_STRIDE
        key = sp.call_key(model=model, digest=digest, messages=messages, seed=seed)
        entry = cache.get(key)
        if entry is None:
            _c, entry = send(model, messages, seed, JUDGE_TIMEOUT_S)
            entry["timeout_s"] = JUDGE_TIMEOUT_S
            cache.put(key, entry)
        total += float(entry.get("wall_s", 0.0))
        verdict = parse_verdict(entry.get("content"))
        if verdict is not None:
            return {
                "label": verdict[0],
                "reason": verdict[1],
                "attempts": attempt + 1,
                "wall_s": total,
            }
    return {"label": None, "reason": None, "attempts": 2, "wall_s": total}


def run_judges(
    items: Sequence[Mapping[str, Any]],
    window_text: Callable[[str, int], str],
    send_for: Callable[[str], Send],
    out_dir: pathlib.Path,
    judges: Sequence[tuple[str, str]] = JUDGES,
) -> dict[str, dict[str, Any]]:
    """Every item through every judge, one judge at a time, in the registered
    order. Writes `labels.json`; cached, so a re-run resumes."""
    by_key = {str(i["key"]): i for i in items}
    order = presentation_order(list(by_key))
    labels: dict[str, dict[str, Any]] = {}
    for model, digest in judges:
        cache = sp.ChatCache(out_dir / "cache" / rcs.safe(model))
        send = send_for(model)
        labels[model] = {}
        for done, key in enumerate(order, start=1):
            item = by_key[key]
            messages = judge_messages(
                window_text(str(item["meeting"]), int(item["window"])),
                item["others"],
                item,
            )
            labels[model][key] = judge_one(cache, send, model, digest, messages)
            if done % 25 == 0:
                print(f"{model}: {done} of {len(order)}", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return labels


def cmd_run(args: argparse.Namespace) -> int:
    from harness_stamp import build_stamp, model_identity

    items, _incomplete = _load_pinned(args.out_dir)
    for model, digest in JUDGES:
        got = model_identity(model, args.host).get("digest")
        if got != digest:
            raise SystemExit(
                f"refusing to run: {model} digest {got}, registered {digest}"
            )
    stamp = args.out_dir / "stamp.json"
    if not stamp.is_file():
        stamp.write_text(
            json.dumps(
                [
                    build_stamp(
                        model=model,
                        prompts={"extra-object-value/judge": JUDGE_SYSTEM},
                        host=args.host,
                    )
                    for model, _d in JUDGES
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    corpus = rcs.Corpus(args.runs_dir)

    def window_text(meeting: str, index: int) -> str:
        _t, wins, _g = corpus.meeting(meeting)
        return wins[index].text

    def send_for(model: str) -> Send:
        print(f"warm-up {model}: {rcs.warm_up(model, args.host):.1f} s", flush=True)
        return sp.ollama_sender(args.host)

    run_judges(items, window_text, send_for, args.out_dir)
    return 0


# --- report ----------------------------------------------------------------------


def report(
    items: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Mapping[str, Any]]],
    judges: Sequence[str] = JUDGE_MODELS,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    incomplete: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """The registered analysis, mechanically. An arm in `incomplete` is
    reported with its numbers and decides nothing."""
    a, b = judges
    pairs: list[tuple[str, str]] = []
    unlabelled = 0
    agreed: dict[str, dict[str, list[str]]] = {s: {} for s in (*ARMS, CONTROL)}
    disagreed = {s: 0 for s in (*ARMS, CONTROL)}
    labelled = {s: 0 for s in (*ARMS, CONTROL)}
    for item in items:
        key, side = str(item["key"]), str(item["side"])
        la = labels[a].get(key, {}).get("label")
        lb = labels[b].get(key, {}).get("label")
        if la is None or lb is None:
            unlabelled += 1
            continue
        labelled[side] += 1
        pairs.append((la, lb))
        if la == lb:
            agreed[side].setdefault(str(item["meeting"]), []).append(la)
        else:
            disagreed[side] += 1
    kappa = cohen_kappa(pairs)
    blocked = study_verdict(kappa, unlabelled, len(items))
    out: dict[str, Any] = {
        "items": len(items),
        "unlabelled": unlabelled,
        "kappa": kappa,
        "study": blocked or "DECIDED",
        "arms": {},
    }
    control = agreed[CONTROL]
    n_control = sum(len(v) for v in control.values())
    out["control"] = {
        "labelled": labelled[CONTROL],
        "agreed": n_control,
        "disagreed": disagreed[CONTROL],
        "decides": False,
        "shares": {
            lab: share([x for v in control.values() for x in v], lab) for lab in LABELS
        },
    }
    if n_control:
        # Descriptive reference only (owner decision): never enters a rule.
        out["control"]["useful_interval"] = share_interval(
            control, "useful", resamples=resamples
        ).fmt()
        out["control"]["redundant_interval"] = share_interval(
            control, "redundant", resamples=resamples
        ).fmt()
    for arm in ARMS:
        own = agreed[arm]
        n_arm = sum(len(v) for v in own.values())
        row: dict[str, Any] = {
            "labelled": labelled[arm],
            "agreed": n_arm,
            "disagreed": disagreed[arm],
            "shares": {
                lab: share([x for v in own.values() for x in v], lab) for lab in LABELS
            },
        }
        rule = blocked
        if arm in (incomplete or {}):
            row["incomplete"] = list((incomplete or {})[arm])
            rule = rule or INCOMPLETE
        if rule is None and n_arm < MIN_AGREED:
            rule = INSUFFICIENT
        if rule is None:
            useful = share_interval(own, "useful", resamples=resamples)
            red = share_interval(own, "redundant", resamples=resamples)
            row["useful"] = useful.fmt()
            row["useful_low"] = useful.low
            row["redundant"] = red.fmt()
            row["redundant_high"] = red.high
            if n_control:
                # Reported, not decided on.
                delta, dropped = paired_share_delta(
                    own, control, "useful", resamples=resamples
                )
                row["useful_minus_control"] = delta.fmt()
                row["dropped_draws"] = dropped
            rule = arm_rule(useful, red, n_arm)
            if rule == FAIL:
                row["fail_reading"] = fail_reading(useful, red)
        row["rule"] = rule
        row["reread_1767"] = reread_1767(RESULTS_1767[arm], rule)
        out["arms"][arm] = row
    return out


def cmd_report(args: argparse.Namespace) -> int:
    items, incomplete = _load_pinned(args.out_dir)
    path = args.out_dir / "labels.json"
    if not path.is_file():
        raise SystemExit("labels.json missing: run `run` first")
    labels = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(report(items, labels, incomplete=incomplete), indent=2))
    return 0


# --- self-test -------------------------------------------------------------------


class _Fail(AssertionError):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def _t_counterpart() -> None:
    a, b, c = [1.0, 0.0], [0.0, 1.0], [0.8, 0.6]
    flags, best = counterpart_flags([a, b], [c], 0.85)
    check(flags == [False, False], f"0.8 and 0.6 are below 0.85: {flags}")
    check(abs((best[0] or 0) - 0.8) < 1e-9, f"best cosine {best}")
    flags, _ = counterpart_flags([a, b], [c, [0.0, 1.0]], 0.85)
    check(flags == [False, True], f"an exact twin is a counterpart: {flags}")
    flags, best = counterpart_flags([a], [], 0.85)
    check(flags == [False] and best == [None], "no reference: no counterpart")
    flags, _ = counterpart_flags([[0.85, math.sqrt(1 - 0.85**2)]], [a], 0.85)
    check(flags == [True], "the threshold is inclusive")


def _t_prompt_pin() -> None:
    got = hashlib.sha256(JUDGE_SYSTEM.encode("utf-8")).hexdigest()
    check(got == JUDGE_PROMPT_SHA256, f"prompt sha {got}")
    text = REGISTRATION.read_text(encoding="utf-8") if REGISTRATION.is_file() else ""
    check(JUDGE_SYSTEM in text, "the registration holds the prompt verbatim")
    check(JUDGE_PROMPT_SHA256 in text, "the registration states the prompt sha256")


def _t_messages() -> None:
    obj = {"type": "Concept", "title": "T", "description": "D", "body": "B body"}
    msgs = judge_messages("the window", ["Other one", "Other two"], obj)
    user = msgs[1]["content"]
    check(msgs[0]["content"] == JUDGE_SYSTEM, "system is the registered prompt")
    for part in (
        "the window",
        "- Other one\n- Other two",
        "Title: T",
        "Description: D",
        "B body",
    ):
        check(part in user, f"user message lacks {part!r}")
    check(all(arm not in user for arm in (*ARMS, REFERENCE)), "blind to arm")
    check("- (none)" in judge_messages("w", [], obj)[1]["content"], "no others")


def _t_parse() -> None:
    check(
        parse_verdict('{"label": "Useful", "reason": " r "}') == ("useful", "r"),
        "plain",
    )
    check(
        parse_verdict('```json\n{"label": "trivial", "reason": "x"}\n```')
        == ("trivial", "x"),
        "fenced",
    )
    for bad in (
        '{"label": "keep", "reason": "x"}',
        '{"label": "useful"}',
        '{"label": "useful", "reason": "  "}',
        '{"label": 1, "reason": "x"}',
        "no json at all",
        None,
    ):
        check(parse_verdict(bad) is None, f"fail-closed on {bad!r}")


def _t_order() -> None:
    keys = [f"k{i}" for i in range(20)]
    first = presentation_order(keys)
    check(
        first == presentation_order(list(reversed(keys))), "input order is irrelevant"
    )
    check(sorted(first) == sorted(keys), "a permutation")
    check(first != sorted(keys), "shuffled")
    check(first != presentation_order(keys, seed=1), "seed-dependent")


def _t_kappa() -> None:
    check(cohen_kappa([("useful", "useful"), ("trivial", "trivial")]) == 1.0, "perfect")
    check(cohen_kappa([]) is None, "empty")
    check(cohen_kappa([("useful", "useful")] * 3) is None, "chance agreement 1")
    # 10 pairs: 4 uu, 1 ut, 1 tu, 4 tt -> po 0.8, pe 0.5, kappa 0.6.
    pairs = [("useful", "useful")] * 4 + [("useful", "trivial"), ("trivial", "useful")]
    pairs += [("trivial", "trivial")] * 4
    got = cohen_kappa(pairs)
    check(got is not None and abs(got - 0.6) < 1e-9, f"kappa {got}")


def _t_bootstrap() -> None:
    arm = {"m1": ["useful", "trivial"], "m2": ["useful", "useful"]}
    ctrl = {"m1": ["useful", "useful"], "m2": ["trivial", "useful"]}
    ci, dropped = paired_share_delta(arm, ctrl, "useful", resamples=300)
    check(abs(ci.point - 0.0) < 1e-9, f"point {ci}")
    check(ci.low <= ci.point <= ci.high and dropped == 0, f"brackets {ci}, {dropped}")
    lone = {"m1": ["useful"], "m2": []}
    ci2, dropped2 = paired_share_delta(
        lone, {"m1": [], "m2": ["useful"]}, "useful", resamples=300
    )
    check(dropped2 > 0, "draws with an empty side are dropped and counted")
    check(ci2.point == 0.0, f"point {ci2}")
    red = share_interval(
        {"m1": ["redundant", "useful"], "m2": ["useful"]}, "redundant", resamples=300
    )
    check(abs(red.point - 1 / 3) < 1e-9 and red.high <= 1.0, f"redundant {red}")


def _t_rule() -> None:
    iv = sm.Interval
    ok_red = iv(0.1, 0.05, REDUNDANT_CEILING)
    check(USEFUL_FLOOR == 0.70, "the owner's absolute floor")
    check(arm_rule(iv(0.8, USEFUL_FLOOR, 0.9), ok_red, 30) == PASS, "inclusive bounds")
    check(arm_rule(iv(0.8, 0.6999, 0.9), ok_red, 30) == FAIL, "useful too low")
    check(arm_rule(iv(0.8, 0.75, 0.9), iv(0.1, 0.0, 0.2001), 30) == FAIL, "redundant")
    check(arm_rule(iv(0.8, 0.75, 0.9), ok_red, MIN_AGREED - 1) == INSUFFICIENT, "n arm")
    check(arm_rule(iv(0.8, 0.75, 0.9), ok_red, MIN_AGREED) == PASS, "n arm floor")
    check(fail_reading(iv(0.6, 0.5, 0.6999), ok_red) == "inferior", "useful excluded")
    check(fail_reading(iv(0.8, 0.75, 0.9), iv(0.3, 0.2001, 0.4)) == "inferior", "red")
    check(fail_reading(iv(0.7, 0.6, 0.8), iv(0.1, 0.0, 0.25)) == "unresolved", "spans")
    check(study_verdict(0.59, 0, 100) == INCONCLUSIVE, "kappa below the floor")
    check(study_verdict(None, 0, 100) == INCONCLUSIVE, "undefined kappa")
    check(study_verdict(0.6, 10, 100) is None, "kappa 0.6 and 10% unlabelled decide")
    check(study_verdict(0.9, 11, 100) == DEGRADED, "over 10% unlabelled")


def _t_reread() -> None:
    for arm, facts in RESULTS_1767.items():
        check(
            reread_1767(facts, PASS) == "VETOED (G1)", f"{arm}: G1 alone still vetoes"
        )
        check(
            reread_1767(facts, FAIL) == "VETOED (G1, V4-replacement)",
            f"{arm}: {reread_1767(facts, FAIL)}",
        )
        check("V4 (replacement undecided)" in reread_1767(facts, INCONCLUSIVE), arm)
    clean = Screen1767(True, True, -0.01, (0.02, -0.01, 0.05))
    check(
        reread_1767(clean, PASS) == "NOT DISTINGUISHABLE", "no veto, interval spans 0"
    )
    check(
        reread_1767(Screen1767(True, True, 0.0, (0.02, 0.01, 0.05)), PASS)
        == "BETTER than local",
        "better",
    )
    check(
        reread_1767(Screen1767(True, True, 0.0, (-0.02, -0.05, -0.01)), PASS)
        == "WORSE than local",
        "worse",
    )


def _t_judge_one() -> None:
    import tempfile

    replies = iter(["prose", '{"label": "redundant", "reason": "same as A"}'])
    sent: list[int] = []

    def send(
        model: str, messages: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        sent.append(seed)
        body = next(replies)
        return body, {"outcome": "ok", "content": body, "wall_s": 1.0}

    with tempfile.TemporaryDirectory() as tmp:
        cache = sp.ChatCache(pathlib.Path(tmp))
        msgs = judge_messages(
            "w", [], {"type": "Concept", "title": "T", "description": "D"}
        )
        got = judge_one(cache, send, "j", "d", msgs)
        check(got["label"] == "redundant" and got["attempts"] == 2, f"retry {got}")
        check(sent == [0, sp.RETRY_SEED_STRIDE], f"seeds {sent}")
        again = judge_one(cache, send, "j", "d", msgs)
        check(again["label"] == "redundant" and len(sent) == 2, "served from the cache")

        def prose(
            model: str, messages: Sequence[Any], seed: int, timeout: float
        ) -> tuple[str | None, dict[str, Any]]:
            return "x", {"outcome": "ok", "content": "x", "wall_s": 0.5}

        none = judge_one(cache, prose, "j2", "d", msgs)
        check(none["label"] is None and none["attempts"] == 2, f"no label {none}")


def _fixture_digest(model: str, meeting: str) -> str:
    if model == REFERENCE:
        return REFERENCE_DIGEST
    date = DA_DATE if meeting in data.POOL_DA else "2026-11-24"
    return f"{HOSTED[model].digest_prefix}:{date}"


def _fixture_records() -> dict[tuple[str, str, int], Obj]:
    recs: dict[tuple[str, str, int], Obj] = {}
    for model in (*ARMS, REFERENCE):
        for meeting in MEETINGS:
            for run in RUNS:
                final = [
                    {
                        "type": "Concept",
                        "title": "Shared",
                        "description": "s",
                        "body_chars": 1,
                        "window": 0,
                    },
                    {
                        "type": "Concept",
                        "title": f"{model} extra",
                        "description": "e",
                        "body_chars": 1,
                        "window": 1,
                    },
                    {
                        "type": "Concept",
                        "title": "Loose",
                        "description": "l",
                        "body_chars": 1,
                        "window": None,
                    },
                ]
                recs[(model, meeting, run)] = {
                    "model": model,
                    "digest": _fixture_digest(model, meeting),
                    "calls": [{"seed": run}, {"seed": run + sp.RETRY_SEED_STRIDE}],
                    "windows": [{"outcome": "ok"}, {"outcome": "ok"}],
                    "stages": {"final": final},
                }
    return recs


def _fixture_vector(text: str) -> list[float]:
    return (
        [1.0, 0.0]
        if text.startswith("Shared") or text.startswith("Loose")
        else [0.0, 1.0]
        if "extra" in text and REFERENCE not in text
        else [0.6, 0.8]
    )


def _fixture_bodies(meeting: str, run: int, rec: Mapping[str, Any]) -> list[str]:
    return [f"body {i}" for i in range(len(rec["stages"]["final"]))]


def _build(recs: Mapping[tuple[str, str, int], Obj]) -> dict[str, Any]:
    return build_population(
        lambda m, s, r: recs.get((m, s, r)), _fixture_vector, _fixture_bodies
    )


def _t_pools() -> None:
    check(MEETINGS == data.POOL_DEV12, f"the population is DEV12: {MEETINGS}")
    check(len(MEETINGS) == 12 and set(data.POOL_DA) <= set(MEETINGS), "DA inside")
    reserved = set(data.POOL_F_WINDOWED) | set(data.POOL_F_MEETING_ONLY)
    check(not set(MEETINGS) & reserved, "pool F stays reserved")
    check(set(HOSTED) == set(ARMS), "every arm has a registered budget")


def _t_population() -> None:
    pop = _build(_fixture_records())
    check(not pop["problems"] and not pop["incomplete"], f"clean fixture {pop}")
    per_run = len(MEETINGS) * len(RUNS)
    keys = side_keys(pop["items"])
    for arm in ARMS:
        row = pop["counts"][arm]
        check(
            row["final"] == 3 * per_run and row["no_window"] == per_run, f"{arm} {row}"
        )
        check(
            row["with_counterpart"] == per_run and row["population"] == per_run,
            f"{arm} {row}",
        )
        check(len(keys[arm]) == per_run, f"{arm} keys")
    check(
        pop["counts"][CONTROL]["population"] == 2 * per_run,
        "control keeps every windowed object",
    )
    item = next(i for i in pop["items"] if i["side"] == ARMS[0])
    check(
        item["body"] == "body 1" and item["others"] == ["Shared", "Loose"],
        f"item {item}",
    )
    check(len({i["key"] for i in pop["items"]}) == len(pop["items"]), "unique keys")
    check(da_pin_problems(pop["items"]) != [], "fixture is not the registered DA")


def _t_incomplete() -> None:
    recs = _fixture_records()
    del recs[(ARMS[0], "TS3009d", 2)]
    recs[(ARMS[1], "IS1004a", 1)]["windows"][0]["outcome"] = "error"
    recs[(ARMS[2], "ES2015c", 1)]["windows"][1]["outcome"] = "not_called"
    pop = _build(recs)
    check(set(pop["incomplete"]) == set(ARMS), f"incomplete {pop['incomplete']}")
    whole = _fixture_records()
    for key in (r for r in whole if r[1] == "IS1004a"):
        whole[key]["chunked"] = False
        for w in whole[key]["windows"]:
            w["outcome"] = "not_called"
    check(not _build(whole)["incomplete"], "whole-source not_called is no failure")
    check(
        pop["counts"][ARMS[0]]["population"] == len(MEETINGS) * len(RUNS) - 1,
        "a missing meeting-run contributes nothing",
    )
    gone = _fixture_records()
    del gone[(REFERENCE, "ES2015b", 1)]
    try:
        _build(gone)
    except SystemExit:
        pass
    else:
        raise _Fail("a missing control record must stop the build")
    bad = _fixture_records()
    bad[(REFERENCE, "ES2015c", 1)]["digest"] = "500a1f067a9f"
    bad[(ARMS[0], "ES2015a", 1)]["digest"] = "openai:gpt-5-mini:2026-11-24"
    bad[(ARMS[1], "TS3009a", 2)]["digest"] = "openai:gpt-5-mini:2026-11-24"
    bad[(ARMS[2], "IS1004c", 2)]["calls"][0]["seed"] = 0
    probs = _build(bad)["problems"]
    check(len(probs) == 4, f"digest, DA date, provider and seed-0 problems: {probs}")
    earlier = _fixture_records()
    earlier[(ARMS[0], "ES2015c", 1)]["digest"] = "openai:gpt-5-mini:2026-11-22"
    check(_build(earlier)["problems"] != [], "a new-meeting run predating DA")


def _t_pins() -> None:
    pop = _build(_fixture_records())
    pin = population_pin(pop["items"])
    check(set(pin) == {*ARMS, CONTROL}, f"one pin per side: {pin}")
    check(pin_mismatch(pin, pin) == [], "a population matches its own pin")
    fewer = [i for i in pop["items"] if i["key"] != pop["items"][0]["key"]]
    check(pin_mismatch(pin, population_pin(fewer)) != [], "one object less")
    da_only = [i for i in pop["items"] if i["meeting"] in data.POOL_DA]
    check(
        da_pin_problems(da_only) == da_pin_problems(pop["items"]),
        "the DA pin reads only DA meetings",
    )


def _t_budget() -> None:
    total = sum(h.ceiling_usd + overshoot_usd(h) for h in HOSTED.values())
    check(total <= BUDGET_USD, f"ceilings plus one-call overshoot {total:.4f}")
    check(BUDGET_USD == 3.00, "the owner's ceiling")
    for arm, h in HOSTED.items():
        ledger = {"calls": h.prior_calls, "usd": h.prior_usd}
        flags = budget_flags(arm, ledger)
        check(
            abs(flags["max_usd"] - (h.prior_usd + h.ceiling_usd)) < 1e-9
            and flags["max_calls"] == h.prior_calls + h.ceiling_calls,
            f"{arm}: the ledger is cumulative, so the flags add the prior spend",
        )
        try:
            budget_flags(arm, {"calls": h.prior_calls + 1, "usd": h.prior_usd})
        except ValueError:
            pass
        else:
            raise _Fail(f"{arm}: a moved ledger must refuse")


def _t_align() -> None:
    @dataclass
    class Replayed:
        type: str
        title: str
        description: str
        body: str

    final = [{"type": "Concept", "title": "A", "description": "a", "body_chars": 2}]
    check(
        align_bodies(final, [Replayed("Concept", "A", "a", "xy")]) == ["xy"], "aligned"
    )
    try:
        align_bodies(final, [Replayed("Concept", "A", "a", "xyz")])
    except ValueError:
        pass
    else:
        raise _Fail("a body of another length is not the recorded object")


def _t_report() -> None:
    items: list[Obj] = []
    labels: dict[str, dict[str, Any]] = {"ja": {}, "jb": {}}
    for side in (*ARMS, CONTROL):
        for meeting in MEETINGS:
            for n in range(6):
                key = f"{side}|{meeting}|{n}"
                items.append({"key": key, "side": side, "meeting": meeting})
                # The arms label every object useful, the control five of six.
                lab = "useful" if side != CONTROL or n < 5 else "trivial"
                labels["ja"][key] = {"label": lab}
                labels["jb"][key] = {"label": lab}
    out = report(items, labels, ("ja", "jb"), resamples=200)
    check(out["study"] == "DECIDED" and out["kappa"] == 1.0, f"study {out['study']}")
    partial = report(
        items, labels, ("ja", "jb"), resamples=200, incomplete={ARMS[0]: ["x"]}
    )
    check(partial["arms"][ARMS[0]]["rule"] == INCOMPLETE, "an incomplete arm")
    check(
        "V4 (replacement undecided)" in partial["arms"][ARMS[0]]["reread_1767"],
        "an incomplete arm leaves V4 standing",
    )
    check(partial["arms"][ARMS[1]]["rule"] == PASS, "the others still decide")
    check(
        "useful_interval" in out["control"], "the control is reported with its interval"
    )
    check(
        "decides" not in out["control"] or not out["control"]["decides"], "descriptive"
    )
    for arm in ARMS:
        check(out["arms"][arm]["rule"] == PASS, f"{arm}: {out['arms'][arm]}")
        check(out["arms"][arm]["reread_1767"] == "VETOED (G1)", arm)
    labels["jb"] = {
        k: {"label": "trivial" if v["label"] == "useful" else "useful"}
        for k, v in labels["ja"].items()
    }
    out = report(items, labels, ("ja", "jb"), resamples=200)
    check(out["study"] == INCONCLUSIVE, f"disagreeing judges: {out['study']}")
    check(
        all(r["rule"] == INCONCLUSIVE for r in out["arms"].values()), "no arm verdict"
    )


def self_test() -> int:
    tests = (
        _t_counterpart,
        _t_prompt_pin,
        _t_messages,
        _t_parse,
        _t_order,
        _t_kappa,
        _t_bootstrap,
        _t_rule,
        _t_reread,
        _t_judge_one,
        _t_pools,
        _t_population,
        _t_incomplete,
        _t_pins,
        _t_budget,
        _t_align,
        _t_report,
    )
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    parser.add_argument("--runs-dir", type=pathlib.Path, default=rcs.DEFAULT_RUNS_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--host", default=None)
    sub = parser.add_subparsers(dest="command")
    pop = sub.add_parser("population")
    pop.add_argument(
        "--final",
        action="store_true",
        help="extraction is over: record missing or failed meeting-runs as INCOMPLETE",
    )
    sub.add_parser("budget")
    sub.add_parser("embed")
    sub.add_parser("run")
    sub.add_parser("report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {
        "population": cmd_population,
        "budget": cmd_budget,
        "embed": cmd_embed,
        "run": cmd_run,
        "report": cmd_report,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
