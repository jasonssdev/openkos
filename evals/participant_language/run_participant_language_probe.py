"""Does the participant pass translate the source? (#713)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Scores the
LANGUAGE of the `description` and `body` the participant-capture pass returns,
on the UNCHUNKED path, against a 100% Spanish source.

## Why a new probe and not `evals/language_leak`

That harness answers a different question. It scores TITLES, on the CHUNKED
path, because that is where #563 measured the leak and where the shipped gate
(`_drop_wrong_language_titles`) runs. #713 is about `description` and `body`,
from a 5.4 KB source that never chunks, produced by a call that gate never
sees.

The two classifiers are IMPORTED from it rather than rewritten, so the repo
keeps one definition of "what language is this string": `classify_title` (which
is generic marker voting, despite its name) and `quoted_verbatim` (the #618
class split that keeps a verbatim proper name from counting as a leak).

## Why a leaked BODY is not a leaked title

A leaked title is a wrong permanent slug -- serious, and already measured. A
leaked body is the stored CONTENT of the object being something the source
never said, in a language the user did not write. It is what `query` cites
back, and for a `Person` object it is personal data restated by a model rather
than quoted.

## The mechanism, and what shipped

`_build_participant_capture_messages` WAS the only extraction call in the
pipeline that omitted `_LANGUAGE_ANCHOR` -- the instruction that says,
verbatim, 'Write every "title", "description" and "body" in the same language
as the SOURCE TEXT below.' On meeting-shaped sources the general pass
(`_build_messages`) sends it; this pass sent the source TITLE instead, and its
docstring justified the omission on the grounds that "the source text itself
still carries the source's language".

That assumption is measurably false. First sweep, qwen3:8b, 3 runs per arm:
the harmful field share was **0.75** without the anchor and **0.00** with it,
over 48 scored fields, with MORE candidates retained and no latency cost. #522
had measured the same shape from the other side -- removing the only
source-language text from a user turn produced English output in 28 of 30 runs.

The anchor therefore SHIPPED, and the arms are now built by ABLATION:
`anchored` is the shipped builder untouched, `baseline` strips the anchor back
out. Adding it to a builder that already carries it would send the instruction
twice and compare duplication against itself.

This is deliberately not the prompt-instruction direction that lost in #563 and
#613. It adds no new rule -- it restores an instruction the same module already
ships on every other extraction call.

Usage:

    uv run python -u evals/participant_language/run_participant_language_probe.py --self-test
    uv run python -u evals/participant_language/run_participant_language_probe.py --runs 3
    uv run python -u evals/participant_language/run_participant_language_probe.py --arm both --runs 3
    uv run python -u evals/participant_language/run_participant_language_probe.py --rescore results/<file>.jsonl

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks hung.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import ExtractionResult, _capture_further_participants
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import OllamaClient, OllamaGenerationCapped

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

_ANCHOR_PROBE = HERE.parent / "participant_anchor" / "run_participant_anchor_probe.py"
_LEAK_PROBE = HERE.parent / "language_leak" / "run_language_leak_probe.py"

_MAX_GENERATION_TOKENS: Final = 8_192
"""The ceiling `openkos.yaml.template` ships. Never run an eval client
uncapped."""


def _load_module(path: Path, name: str) -> Any:
    """Import a sibling probe module for reuse.

    Imported rather than copied, on the precedent `evals/stage_attrition` set:
    a second copy of a fixture or a classifier drifts silently, leaving two
    things with one name and no way to say which produced which number."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fixture:
    name: str
    text: str
    title: str
    role: str


def build_fixtures() -> list[Fixture]:
    """`es-anchored` and `es-bare`, taken from `evals/participant_anchor`.

    #713's evidence was produced on those exact transcripts, and the contrast
    between them is the finding: `es-anchored` leaked on all 3 runs while
    `es-bare` stayed in Spanish on all 3, same pipeline and same model. Both
    are 100% Spanish and both sit below the chunk threshold, so any English in
    a returned field is a leak rather than a language the source carries."""
    module = _load_module(_ANCHOR_PROBE, "_participant_anchor_probe")
    arms = [arm for arm in module.build_arms() if arm.name.startswith("es-")]
    if not arms:  # pragma: no cover - defensive
        raise SystemExit("no Spanish fixtures found in evals/participant_anchor")
    return [
        Fixture(name=arm.name, text=arm.text, title=arm.title, role=arm.role)
        for arm in arms
    ]


# --------------------------------------------------------------------------- #
# Arms
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    name: str
    strip_language_anchor: bool
    """Whether this arm REMOVES `_LANGUAGE_ANCHOR` from the user turn.

    The anchor SHIPPED once this probe's first sweep measured it, so the pair
    is now built by ablation: `anchored` is the shipped builder untouched, and
    `baseline` strips the anchor back out. Adding it to a builder that already
    carries it would send the instruction TWICE and compare duplication
    against itself while the table still said anchor-against-no-anchor -- the
    same inversion `evals/stage_attrition` needed for #715's clause."""


def _assert_shipped_carries_the_anchor() -> None:
    """Fail loudly if the shipped builder no longer sends the anchor.

    Without this the ablation is a silent no-op: `baseline` would equal
    `anchored`, every field would score identically, and the probe would report
    'the anchor does nothing' for an anchor that was never there to remove."""
    user = concept_mod._build_participant_capture_messages("texto", "Título")[1]
    content = str(user["content"])
    if content.count(concept_mod._LANGUAGE_ANCHOR) != 1:
        raise SystemExit(
            "the shipped participant-capture user turn does not carry "
            "_LANGUAGE_ANCHOR exactly once, so the baseline arm has nothing to "
            "ablate and both arms would be one prompt. Re-sync this probe with "
            "`_build_participant_capture_messages` before trusting any number."
        )


def _install_arm(arm: Arm) -> Any:
    """Patch `_build_participant_capture_messages` for `arm`, returning the
    original so the caller can restore it.

    The `baseline` builder is the shipped one with `_LANGUAGE_ANCHOR` removed
    and nothing else changed -- same system prompt, same title line, same
    source text -- so the arms differ by exactly the one instruction under
    measurement."""
    _assert_shipped_carries_the_anchor()
    original = concept_mod._build_participant_capture_messages
    if not arm.strip_language_anchor:
        return original

    def _stripped(source_text: str, source_title: str) -> list[Any]:
        messages = original(source_text, source_title)
        user = dict(messages[1])
        user["content"] = str(user["content"]).replace(
            f"{concept_mod._LANGUAGE_ANCHOR}\n\n", "", 1
        )
        return [messages[0], user]

    concept_mod._build_participant_capture_messages = _stripped
    return original


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldScore:
    field: str
    value: str
    language: str
    verbatim: bool
    harmful: bool


def score_result(
    result: ExtractionResult, source_text: str, leak: Any
) -> list[FieldScore]:
    """Language-score this candidate's `description` and `body`.

    HARMFUL is `en` AND not verbatim in the source, mirroring #618's class
    split exactly:

    - `mixed` is NOT harmful. On a Spanish source a mixed string is Spanish
      prose quoting an English technical term, which is the model doing the
      right thing -- counting it inflated the first #563 analysis roughly 2x.
    - a VERBATIM match is not harmful either: a proper name or a quoted phrase
      that appears in the prose is the source's own words, not a translation.
    - `neutral` is not harmful. It is unlabelled, and this probe reports it
      separately rather than folding uncertainty into either column.

    `title` is deliberately NOT scored here: it is `evals/language_leak`'s
    subject and the shipped gate's, and mixing the two would make this probe's
    numbers uncomparable with that harness's."""
    scores: list[FieldScore] = []
    for field in ("description", "body"):
        value = getattr(result, field, "") or ""
        if not value.strip():
            continue
        language = leak.classify_title(value)
        verbatim = leak.quoted_verbatim(value, source_text)
        scores.append(
            FieldScore(
                field=field,
                value=value,
                language=language,
                verbatim=verbatim,
                harmful=language == "en" and not verbatim,
            )
        )
    return scores


ERROR_RAISED: Final = "raised"
ERROR_SWALLOWED: Final = "swallowed"
"""The two ways one participant-capture call can fail, and the ONLY two labels
`RunRecord.error` is ever written with.

One vocabulary, `"<cause>: <detail>"`, because the column is tallied and
grouped: a bare `OllamaUnavailable` beside a prefixed
`participant_capture: OllamaUnavailable` would split ONE failure across two
values and halve whichever count someone read. The two causes are kept APART
inside that one shape because they are different facts about the contract:

- `raised` -- the exception escaped `_capture_further_participants`, which by
  its own contract never raises. That is a broken contract or a broken probe,
  not a backend failure, and it must not be tallied with one.
- `swallowed` -- the call degraded to no additions and NAMED why (#828). The
  detail is the production string verbatim, `participant_capture: <Type>`, so
  the spelling this column stores is the one `OPTIONAL_CALL_PARTICIPANT_CAPTURE`
  pins rather than a second copy of it that could drift."""


@dataclass(frozen=True)
class RunRecord:
    fixture: str
    arm: str
    run: int
    model: str
    seconds: float
    error: str
    """Why this run's single capture call failed, or `""` when it did not.

    `""` is NOT comparable across the #828 boundary, and a `--rescore` over a
    mixed set of files must not read it as one value. In a file stored BEFORE
    #828 the call discarded its own cause, so `""` there means "did not RAISE"
    and silently covers every swallowed backend failure -- a runaway against
    the generation ceiling was recorded as a clean run that found nobody. In a
    file stored since, `""` means the call genuinely reported no failure.
    Nothing in the record distinguishes the two, so an old file's zero error
    rate is unmeasured, not measured-as-zero."""
    candidates: int
    scores: list[dict[str, Any]]


def run_fixture(
    fixture: Fixture, arm: Arm, llm: LLMBackend, runs: int, model: str, leak: Any
) -> list[RunRecord]:
    """`runs` real `_capture_further_participants` calls, scored.

    The production function, not a reimplementation: it owns the prompt, the
    validation and the `Person`/`Organization` narrowing, and a probe that
    rebuilt any of that would be measuring itself.

    `llm` is annotated as the PROTOCOL rather than `OllamaClient`, which is
    all this function ever needed: it hands the backend straight to
    `_capture_further_participants`, whose own parameter is `LLMBackend`. The
    narrower annotation was over-constrained, and it is what kept the
    self-test from driving this function with a scripted backend -- the gap
    #833 point 4 reports."""
    records: list[RunRecord] = []
    original = _install_arm(arm)
    try:
        for index in range(1, runs + 1):
            started = time.monotonic()
            error = ""
            results: list[ExtractionResult] = []
            try:
                outcome = _capture_further_participants(
                    fixture.text, fixture.title, llm
                )
            except Exception as exc:  # broad: every failure is data here
                error = f"{ERROR_RAISED}: {type(exc).__name__}"
            else:
                results = outcome.additions
                # The two causes are ALTERNATIVES, never a merge: this branch
                # runs only when the `except` above did not, so the assignment
                # cannot overwrite a raised cause and the column never has to
                # represent both at once.
                #
                # #828: the call still swallows its own backend failure, and
                # now NAMES it. Until then the `except` above could not fire
                # -- the function returned `[]` for every failure -- so a
                # runaway against the generation ceiling was scored here as a
                # clean run that found nobody, with `error` empty.
                error = (
                    f"{ERROR_SWALLOWED}: {outcome.failure}" if outcome.failure else ""
                )
            seconds = round(time.monotonic() - started, 1)
            scores = [
                asdict(score)
                for result in results
                for score in score_result(result, fixture.text, leak)
            ]
            records.append(
                RunRecord(
                    fixture=fixture.name,
                    arm=arm.name,
                    run=index,
                    model=model,
                    seconds=seconds,
                    error=error,
                    candidates=len(results),
                    scores=scores,
                )
            )
            harmful = sum(1 for s in scores if s["harmful"])
            print(
                f"      run {index}/{runs}: {len(results)} candidate(s), "
                f"{len(scores)} field(s), {harmful} harmful, "
                f"{error or 'ok'}, {seconds}s",
                flush=True,
            )
    finally:
        concept_mod._build_participant_capture_messages = original
    return records


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def render(records: list[RunRecord]) -> str:
    """Per (fixture, arm): the harmful share, and the class split beside it.

    The denominator is printed with every rate. A filtered count without its
    total cannot be read -- an arm that returned no candidates at all scores
    0 harmful and would otherwise look like the best result in the table."""
    lines: list[str] = []
    header = (
        f"{'fixture':<14} {'arm':<10} {'runs':>5} {'cands':>6} {'fields':>7} "
        f"{'harmful':>8} {'rate':>6} {'en':>4} {'es':>4} {'mixed':>6} "
        f"{'neutral':>8} {'verbatim':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    seen: list[tuple[str, str]] = []
    for r in records:
        if (r.fixture, r.arm) not in seen:
            seen.append((r.fixture, r.arm))
    for fixture, arm in seen:
        group = [r for r in records if r.fixture == fixture and r.arm == arm]
        scores = [s for r in group for s in r.scores]
        harmful = sum(1 for s in scores if s["harmful"])
        rate = f"{harmful / len(scores):.2f}" if scores else "-"
        counts = {lang: 0 for lang in ("en", "es", "mixed", "neutral")}
        for s in scores:
            counts[s["language"]] = counts.get(s["language"], 0) + 1
        lines.append(
            f"{fixture:<14} {arm:<10} {len(group):>5} "
            f"{sum(r.candidates for r in group):>6} {len(scores):>7} "
            f"{harmful:>8} {rate:>6} {counts['en']:>4} {counts['es']:>4} "
            f"{counts['mixed']:>6} {counts['neutral']:>8} "
            f"{sum(1 for s in scores if s['verbatim']):>9}"
        )
    errors = [r for r in records if r.error]
    if errors:
        lines.append("")
        lines.append("Errors:")
        for r in errors:
            lines.append(f"  {r.fixture} [{r.arm}] run {r.run}: {r.error}")
    lines.append("")
    lines.append(
        "harmful = `en` AND not verbatim in the source. `mixed` is Spanish "
        "quoting an English term and is NOT harmful (#618's class split)."
    )
    return "\n".join(lines)


def render_examples(records: list[RunRecord], limit: int = 8) -> str:
    """The harmful strings themselves, so a reader can adjudicate them.

    A rate is not evidence on its own. #713's whole claim is that the BODY is a
    translated rendering of the source's own turns rather than a summary in the
    wrong language, and only the strings can show that."""
    lines = ["Harmful fields (adjudicate each):"]
    shown = 0
    for r in records:
        for s in r.scores:
            if not s["harmful"] or shown >= limit:
                continue
            shown += 1
            value = " ".join(s["value"].split())
            lines.append(f"  [{r.arm}] {r.fixture} {s['field']}: {value[:160]}")
    if shown == 0:
        lines.append("  (none)")
    return "\n".join(lines)


def load_results(path: Path) -> list[RunRecord]:
    return [
        RunRecord(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_results(records: list[RunRecord], stamp: str, model: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"participant-language-{stamp}-{slug}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Prove the scorer separates the classes and the arm really changes the
    prompt.

    Both are silent-success failures. A scorer that counted every non-Spanish
    string harmful would report a large leak made mostly of quoted names, and
    the table would look like strong evidence. An arm that failed to install
    would measure the baseline twice and read as 'the anchor does nothing'."""
    leak = _load_module(_LEAK_PROBE, "_language_leak_probe")
    source = (
        "Ana Ríos: yo dicto el ramo de sistemas distribuidos en el "
        "Departamento de Informática.\n"
        "Gustavo Martínez: estoy a cargo del pipeline de ingesta desde marzo."
    )

    translated = ExtractionResult(
        type="Person",
        title="Gustavo Martínez",
        description="Student in charge of the ingestion pipeline",
        body="I am in charge of the ingestion pipeline since March.",
    )
    kept = ExtractionResult(
        type="Person",
        title="Ana Ríos",
        description="Dicta el ramo de sistemas distribuidos",
        body="yo dicto el ramo de sistemas distribuidos en el Departamento de Informática.",
    )

    bad = score_result(translated, source, leak)
    if not all(s.harmful for s in bad):
        print(f"FAIL: a translated description/body scored as clean: {bad}")
        return 1
    good = score_result(kept, source, leak)
    if any(s.harmful for s in good):
        print(f"FAIL: Spanish fields scored as harmful: {good}")
        return 1

    # A verbatim English quotation must NOT count: the class split is the whole
    # point, and without it the probe measures "is there English" rather than
    # "was the source translated".
    quoted = ExtractionResult(
        type="Person",
        title="Q",
        description="Model Context Protocol",
        body="",
    )
    if any(
        s.harmful
        for s in score_result(quoted, "hablamos del Model Context Protocol", leak)
    ):
        print("FAIL: a verbatim English quotation was counted as a translation")
        return 1

    original = concept_mod._build_participant_capture_messages
    shipped_user = str(original("texto", "Título")[1]["content"])
    if concept_mod._LANGUAGE_ANCHOR not in shipped_user:
        print("FAIL: the shipped builder does not carry the anchor to ablate")
        return 1
    try:
        _install_arm(Arm(name="baseline", strip_language_anchor=True))
        user = str(
            concept_mod._build_participant_capture_messages("texto", "Título")[1][
                "content"
            ]
        )
        if concept_mod._LANGUAGE_ANCHOR in user:
            print("FAIL: the baseline arm did not strip the language anchor")
            return 1
        if "SOURCE TITLE: Título" not in user or "texto" not in user:
            print("FAIL: the ablation dropped the title or the source text")
            return 1
        if len(user) >= len(shipped_user):
            print("FAIL: the ablation removed nothing measurable")
            return 1
    finally:
        concept_mod._build_participant_capture_messages = original

    if concept_mod._build_participant_capture_messages is not original:
        print("FAIL: the arm was left installed")
        return 1

    # ------------------------------------------------------------------
    # The error vocabulary (#833 point 4). `raised:` and `swallowed:`
    # arrived with #828 and nothing drove `run_fixture`, so the split that
    # tells a broken contract from a backend failure had no check at all --
    # in a column that is tallied and grouped, where the two collapsing
    # into one would halve whichever count somebody read.
    #
    # Three runs, one per outcome, against a fake backend. No model, no
    # network: `_capture_further_participants` takes any `LLMBackend`, and
    # `chat` is its whole surface.
    # ------------------------------------------------------------------
    class _Backend:
        """One scripted `chat`: raise, or answer with these bytes."""

        def __init__(self, reply: str | None = None, error: Exception | None = None):
            self._reply = reply
            self._error = error

        def chat(self, messages: Any) -> str:
            if self._error is not None:
                raise self._error
            return self._reply or ""

    vocab_fixture = build_fixtures()[0]
    vocab_arm = Arm(name="anchored", strip_language_anchor=False)

    # SWALLOWED -- the backend fails and `_capture_further_participants`
    # degrades by contract, naming its cause. This is the live case #828
    # created and the one a real runaway takes.
    capped = OllamaGenerationCapped("generation hit the ceiling")
    swallowed = run_fixture(
        vocab_fixture, vocab_arm, _Backend(error=capped), 1, "fake", leak
    )[0]
    expected_swallowed = (
        f"{ERROR_SWALLOWED}: {concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE}: "
        "OllamaGenerationCapped"
    )
    if swallowed.error != expected_swallowed:
        print(
            f"FAIL: a swallowed backend failure must be recorded as "
            f"{expected_swallowed!r}, got {swallowed.error!r}"
        )
        return 1
    if swallowed.candidates != 0:
        print(f"FAIL: a failed capture added candidates: {swallowed.candidates}")
        return 1

    # CLEAN -- a call that ran leaves the column EMPTY. Without this the two
    # labels above could both be produced by a probe that stamped an error
    # on every run.
    reply = json.dumps(
        [
            {
                "type": "Person",
                "title": "Ana Ríos",
                "description": "Dicta el ramo de sistemas distribuidos",
                "body": "yo dicto el ramo de sistemas distribuidos.",
            }
        ]
    )
    clean = run_fixture(
        vocab_fixture, vocab_arm, _Backend(reply=reply), 1, "fake", leak
    )[0]
    if clean.error != "":
        print(f"FAIL: a capture that ran must leave `error` empty, got {clean.error!r}")
        return 1
    # The POSITIVE CONTROL, without which the swallowed arm's
    # `candidates != 0` proves nothing: if this scripted reply did not parse
    # into a candidate either, both arms would report zero and the check
    # above would be measuring a backend that never works rather than one
    # that failed.
    if clean.candidates != 1:
        print(
            "FAIL: the scripted clean reply must produce exactly one candidate, "
            f"or the swallowed arm's zero-candidate check is vacuous: got "
            f"{clean.candidates}"
        )
        return 1

    # RAISED -- reserved for a BROKEN CONTRACT: `_capture_further_participants`
    # never raises, so this label can only be produced by rebinding it. Left
    # untested it would be a branch nobody could tell from the swallowed one.
    # Rebinding the MODULE-LEVEL name is the target that matters: `run_fixture`
    # calls the bare name, so patching anywhere else would leave the real
    # function running and this check passing while asserting nothing.
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("contract broken")

    original_capture = globals()["_capture_further_participants"]
    globals()["_capture_further_participants"] = _explode
    try:
        raised = run_fixture(
            vocab_fixture, vocab_arm, _Backend(reply=reply), 1, "fake", leak
        )[0]
    finally:
        globals()["_capture_further_participants"] = original_capture
    if raised.error != f"{ERROR_RAISED}: RuntimeError":
        print(
            f"FAIL: an escaped exception must be recorded as "
            f"{ERROR_RAISED}: RuntimeError, got {raised.error!r}"
        )
        return 1
    # No "was the stub uninstalled?" check follows. The `finally` above
    # performs that exact assignment, so comparing against it immediately
    # afterwards is unconditionally true -- a check that cannot fail, which
    # is the whole class of defect #833 exists to remove. That the rebinding
    # took effect at all is already proved by the assertion above: the real
    # function does not raise `RuntimeError`.

    print(f"self-test OK ({len(build_fixtures())} fixture(s))")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--fixture", action="append")
    parser.add_argument(
        "--arm", default="baseline", choices=("baseline", "anchored", "both")
    )
    parser.add_argument("--rescore")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    leak = _load_module(_LEAK_PROBE, "_language_leak_probe")

    if args.rescore:
        stored = load_results(Path(args.rescore))
        print(render(stored))
        print()
        print(render_examples(stored))
        return 0

    fixtures = build_fixtures()
    if args.fixture:
        wanted = set(args.fixture)
        unknown = wanted - {f.name for f in fixtures}
        if unknown:
            raise SystemExit(f"unknown fixture(s): {', '.join(sorted(unknown))}")
        fixtures = [f for f in fixtures if f.name in wanted]

    arm_names = ["baseline", "anchored"] if args.arm == "both" else [str(args.arm)]
    arms = {
        "baseline": Arm(name="baseline", strip_language_anchor=True),
        "anchored": Arm(name="anchored", strip_language_anchor=False),
    }

    llm = OllamaClient(model=args.model, max_generation_tokens=_MAX_GENERATION_TOKENS)
    print(f"model {args.model}, {args.runs} run(s) per fixture/arm\n")

    records: list[RunRecord] = []
    for arm_name in arm_names:
        for fixture in fixtures:
            print(f"  [{arm_name}] {fixture.name} ({len(fixture.text)} chars)")
            records.extend(
                run_fixture(fixture, arms[arm_name], llm, args.runs, args.model, leak)
            )

    print()
    print(render(records))
    print()
    print(render_examples(records))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"stored {write_results(records, stamp, args.model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
