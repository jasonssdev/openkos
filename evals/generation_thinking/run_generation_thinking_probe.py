"""Where a runaway generation actually goes, and whether #830's detector sees it.

#830 proposes streaming `chat()` and aborting on an n-gram repetition loop in
the reply text, on the premise that "length is a proxy for it; repetition is
the thing itself". This probe measures that premise before anything is built
on it, on the same public fixtures `evals/generation_runaway/` uses, and adds
the one arm the premise never considered: `think`.

    uv run python -u evals/generation_thinking/run_generation_thinking_probe.py --self-test
    uv run python -u evals/generation_thinking/run_generation_thinking_probe.py --runs 10
    uv run python -u evals/generation_thinking/run_generation_thinking_probe.py \
        --rescore evals/generation_thinking/results/<file>.json

`--self-test` and `--rescore` make no model calls and need no Ollama.

Nothing here is shipped and no production file is touched: this is a
measurement of a proposal, and the README records what it came back with.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import statistics
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Final

_HERE: Final = pathlib.Path(__file__).resolve().parent
_REPO: Final = _HERE.parent.parent
_FIXTURES: Final = _REPO / "evals" / "section_coverage" / "section_fixtures.py"
RESULTS_DIR: Final = _HERE / "results"

sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_HERE))

from detector import repetition_share  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_fixtures = _load(_FIXTURES, "_section_fixtures_thinking")

CEILING: Final = 2048
"""Generation ceiling for this probe, well below the shipped 8192.

A runaway against 8192 costs 222 seconds (#828) and this probe needs many
of them. At 2048 the same failure reproduces in about 50 -- and the lower
ceiling is not a distortion of the question, because #830's detector is
supposed to fire EARLY. A loop invisible in the first 2048 tokens is not
one a stream-abort could have caught in time to matter.
"""

CONTEXT_WINDOW: Final = 12288


@dataclass(frozen=True)
class ReplyRecord:
    """One extraction call, with where its tokens went.

    `content_chars` and `thinking_chars` are the two halves the runaway
    question turns on, and they are recorded separately because a reply can
    spend its whole budget in one and none in the other -- which is the
    finding. Both fixtures are COMMITTED and public, so the reply text is
    safe to score here; it is still not stored, because a score is what a
    later reader needs and 8 KB of model deliberation per row is not.
    """

    fixture: str
    arm: str
    run: int
    model: str
    done_reason: str
    ceiling: int
    """`num_predict` this row was produced under.

    Stored per row rather than left as a module constant: a committed sweep
    outlives the constant, and a reader comparing two files has no other way
    to know whether they were measured under the same bound."""
    context_window: int
    """`num_ctx`, for the same reason."""
    eval_count: int
    content_chars: int
    thinking_chars: int
    repetition_content: float
    repetition_thinking: float
    seconds: float

    @property
    def cut_off(self) -> bool:
        return self.done_reason == "length"

    @property
    def total_loss(self) -> bool:
        """Cut off AND carrying no content at all -- the paid call that
        produced nothing, which is the failure #828 measured at 222s."""
        return self.cut_off and self.content_chars == 0


def _done_reason(data: dict[str, Any]) -> str:
    """The response's `done_reason`, or a loud sentinel.

    `str(data.get("done_reason"))` renders a missing key as the string
    `"None"`, which then compares unequal to `"length"` and is COUNTED AS A
    LEGITIMATE REPLY -- silently corrupting the one axis every verdict here
    rests on. The sentinel is not `"length"` either, because inventing a
    cut-off is the mirror error; it is its own value, so a report carrying
    any of them is visibly wrong rather than quietly skewed.
    """
    reason = data.get("done_reason")
    if not isinstance(reason, str) or not reason:
        return "(absent)"
    return reason


def run_once(fixture: Any, *, arm: str, run: int, model: str, host: str) -> ReplyRecord:
    """One real extraction call, built by production's own message builder.

    `think` is sent only on the `no-think` arm: omitting the key is what
    every shipped call does today, so the `think` arm has to be the
    untouched request or the comparison is against something nobody runs.
    """
    messages = concept_mod._build_messages(fixture.text, fixture.title)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": CONTEXT_WINDOW, "num_predict": CEILING},
    }
    if arm == "no-think":
        body["think"] = False
    # The host is this probe's own `--host`, defaulting to loopback, never
    # anything read out of a document -- the same trusted-host rationale
    # `OllamaClient`'s own call site records.
    request = urllib.request.Request(  # noqa: S310
        f"{host}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=900) as response:  # noqa: S310
        data = json.loads(response.read())
    seconds = round(time.monotonic() - started, 1)
    message = data.get("message") or {}
    content = message.get("content") or ""
    thinking = message.get("thinking") or ""
    return ReplyRecord(
        fixture=fixture.name,
        arm=arm,
        run=run,
        model=model,
        done_reason=_done_reason(data),
        ceiling=CEILING,
        context_window=CONTEXT_WINDOW,
        eval_count=int(data.get("eval_count") or 0),
        content_chars=len(content),
        thinking_chars=len(thinking),
        repetition_content=round(repetition_share(content), 4),
        repetition_thinking=round(repetition_share(thinking), 4),
        seconds=seconds,
    )


def _rows(
    records: list[dict[str, Any]], arm: str, fixture: str
) -> list[dict[str, Any]]:
    return [r for r in records if r["arm"] == arm and r["fixture"] == fixture]


def render(records: list[dict[str, Any]]) -> str:
    """The two questions, side by side: where the tokens went, and whether
    the proposed detector could have told."""
    lines: list[str] = ["", "=" * 78, "WHERE A RUNAWAY GOES (#830)", "=" * 78, ""]
    if not records:
        lines.append("NO DATA -- no run to report.")
        return "\n".join(lines)
    arms = sorted({r["arm"] for r in records})
    fixtures = sorted({r["fixture"] for r in records})
    lines.append(
        f"   {'arm':<10}{'fixture':<18}{'n':>3}{'cut':>5}{'TOTAL LOSS':>12}"
        f"{'median s':>10}{'med think':>11}"
    )
    for arm in arms:
        for fixture in fixtures:
            rows = _rows(records, arm, fixture)
            if not rows:
                continue
            cut = sum(1 for r in rows if r["done_reason"] == "length")
            loss = sum(
                1
                for r in rows
                if r["done_reason"] == "length" and r["content_chars"] == 0
            )
            lines.append(
                f"   {arm:<10}{fixture:<18}{len(rows):>3}{cut:>5}{loss:>12}"
                f"{statistics.median(r['seconds'] for r in rows):>10.1f}"
                f"{statistics.median(r['thinking_chars'] for r in rows):>11.0f}"
            )
    lines.append("")
    lines.append(
        "   TOTAL LOSS is a cut-off carrying NO content -- the paid call that "
        "produced nothing. `cut` alone does not separate it from a cut-off "
        "that still returned usable objects."
    )
    lines.append("")
    lines.append("=" * 78)
    lines.append("COULD THE PROPOSED DETECTOR HAVE TOLD? (#830)")
    lines.append("=" * 78)
    lines.append("")
    # PER ARM, never pooled. The two arms are different regimes -- one
    # produces cut-offs with no content at all -- and pooling them let a
    # `no-think` cut-off's high score sit beside a `think` cut-off's zero
    # and read as one separating distribution. The first draft of this
    # report did exactly that and printed SEPARATES over an arm where the
    # detector is inverted.
    # Per arm AND per FIXTURE. Pooling arms let an inverted arm read as
    # separating; pooling fixtures inside an arm is the same error one level
    # down, since the lowest-scoring cut-off and the highest-scoring
    # legitimate reply can then come from two different sources whose
    # replies have nothing to do with each other.
    for arm in arms:
        for fixture in fixtures:
            cut_rows = [
                r
                for r in records
                if r["arm"] == arm
                and r["fixture"] == fixture
                and r["done_reason"] == "length"
            ]
            ok_rows = [
                r
                for r in records
                if r["arm"] == arm
                and r["fixture"] == fixture
                and r["done_reason"] != "length"
            ]
            lines.extend(_detector_block(arm, fixture, cut_rows, ok_rows))
    return "\n".join(lines)


def _detector_block(
    arm: str,
    fixture: str,
    cut_rows: list[dict[str, Any]],
    ok_rows: list[dict[str, Any]],
) -> list[str]:
    """One arm/fixture cell of the detector table, verdict included."""
    lines = [f"   -- arm: {arm} / {fixture}"]
    for label, rows in (("cut off", cut_rows), ("legitimate", ok_rows)):
        if not rows:
            lines.append(f"      {label:<12} (none recorded)")
            continue
        scores = sorted(r["repetition_content"] for r in rows)
        empty = sum(1 for r in rows if r["content_chars"] == 0)
        lines.append(
            f"      {label:<12} n={len(rows):<4} content repetition: "
            f"min {scores[0]:.3f}  max {scores[-1]:.3f}"
            f"   (no content at all: {empty})"
        )
    if cut_rows and ok_rows:
        worst_cut = min(r["repetition_content"] for r in cut_rows)
        best_ok = max(r["repetition_content"] for r in ok_rows)
        # The bar is ZERO false cuts (#830's own kill criterion), so the
        # comparison is the LOWEST-scoring cut-off against the
        # HIGHEST-scoring legitimate reply. Comparing maxima instead would
        # bless a threshold that still cuts a good reply.
        verdict = "SEPARATES" if worst_cut > best_ok else "OVERLAPS"
        lines.append(
            f"      VERDICT: {verdict}. Lowest-scoring cut-off "
            f"{worst_cut:.3f} against highest-scoring legitimate reply "
            f"{best_ok:.3f}; a threshold catching every cut-off also cuts "
            "every legitimate reply above it, and #830 names the false-cut "
            "count as what kills a candidate."
        )
    else:
        lines.append(
            "      no verdict: a cell needs BOTH classes, and inventing one "
            "from a single class is how an unmeasured cell reads as a clean "
            "one."
        )
    lines.append("")
    return lines


def _self_test() -> int:
    """Prove the detector and the tallies with no model running."""
    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    check(
        repetition_share("one two three four five six seven eight nine") == 0.0,
        "distinct text must score 0.0",
    )
    looped = "alpha beta gamma delta epsilon zeta eta theta " * 6
    check(
        repetition_share(looped) > 0.5,
        f"a phrase repeated six times must score high (got {repetition_share(looped)})",
    )
    check(
        repetition_share("short text") == 0.0,
        "text shorter than the window must score 0.0 rather than an invented value",
    )
    # ONE EXACT VALUE, not only a direction. Every number this directory
    # publishes is a `repetition_share`, and a scaled variant of the formula
    # passes every threshold check above while moving the whole ladder.
    #
    # `x y x y x y` at window 2 holds 5 n-grams -- (x,y), (y,x), (x,y),
    # (y,x), (x,y) -- of which 2 are distinct: 1 - 2/5 = 0.6. Counted by
    # hand here rather than read back from the function it pins.
    exact = repetition_share("x y x y x y", window=2)
    check(
        exact == 0.6,
        f"`x y x y x y` at window 2 must score exactly 0.6 (got {exact})",
    )
    # BOTH SIDES of the short-text boundary, and the upper side must be
    # NON-ZERO or it cannot tell the guard from the arithmetic. At window 2,
    # `a a` is exactly `window` words and holds one n-gram, which cannot
    # repeat; `a a a` is one word more, holds two identical n-grams, and
    # scores 1 - 1/2 = 0.5. An off-by-one either way changes one of these.
    check(
        repetition_share("a a", window=2) == 0.0,
        f"exactly `window` words hold one n-gram and must score 0.0 (got "
        f"{repetition_share('a a', window=2)})",
    )
    check(
        repetition_share("a a a", window=2) == 0.5,
        f"exactly `window + 1` words must be SCORED, not skipped (got "
        f"{repetition_share('a a a', window=2)})",
    )
    try:
        repetition_share("a b c", window=0)
    except ValueError:
        pass
    else:
        check(False, "a window below 1 must raise rather than score everything")
    # The window must REACH the score, or a swept value would be mislabelled.
    check(
        repetition_share("a b c a b c", window=3) > 0.0
        and repetition_share("a b c a b c", window=6) == 0.0,
        "the window argument must change the score it produces",
    )

    # `total_loss` is the column the finding rests on, and it is NOT `cut_off`:
    # a cut-off that still returned content is a different fact.
    def _rec(**kw: Any) -> ReplyRecord:
        base = dict(
            fixture="f",
            arm="think",
            run=1,
            model="m",
            done_reason="stop",
            ceiling=CEILING,
            context_window=CONTEXT_WINDOW,
            eval_count=10,
            content_chars=100,
            thinking_chars=0,
            repetition_content=0.0,
            repetition_thinking=0.0,
            seconds=1.0,
        )
        base.update(kw)
        return ReplyRecord(**base)  # type: ignore[arg-type]

    check(
        _rec(done_reason="length", content_chars=0).total_loss
        and not _rec(done_reason="length", content_chars=500).total_loss
        and not _rec(done_reason="stop", content_chars=0).total_loss,
        "total_loss must require BOTH a cut-off and empty content",
    )

    overlapping = [
        asdict(_rec(done_reason="length", content_chars=0, repetition_content=0.0)),
        asdict(_rec(done_reason="stop", repetition_content=0.064)),
    ]
    check(
        "OVERLAPS" in render(overlapping),
        f"a cut-off scoring BELOW a legitimate reply must read as OVERLAPS "
        f"(got {render(overlapping)!r})",
    )
    separating = [
        asdict(_rec(done_reason="length", content_chars=10, repetition_content=0.9)),
        asdict(_rec(done_reason="stop", repetition_content=0.064)),
    ]
    check(
        "SEPARATES" in render(separating),
        "a cut-off scoring ABOVE every legitimate reply must read as SEPARATES",
    )
    # A cut-off BARELY under a legitimate reply must still read as OVERLAPS:
    # the bar is zero false cuts, so the lowest cut-off is what a threshold
    # has to clear. Comparing maxima would call this pair separating.
    narrow = [
        asdict(_rec(done_reason="length", content_chars=10, repetition_content=0.294)),
        asdict(_rec(done_reason="length", content_chars=10, repetition_content=0.633)),
        asdict(_rec(done_reason="stop", repetition_content=0.300)),
    ]
    check(
        "OVERLAPS" in render(narrow),
        "a cut-off at 0.294 beneath a legitimate reply at 0.300 must read as "
        f"OVERLAPS even though another cut-off reaches 0.633 (got "
        f"{render(narrow)!r})",
    )
    # And the arms must be reported APART: pooling them is what printed
    # SEPARATES over an inverted arm.
    two_arms = render(
        [
            asdict(
                _rec(
                    arm="think",
                    done_reason="length",
                    content_chars=0,
                    repetition_content=0.0,
                )
            ),
            asdict(_rec(arm="think", done_reason="stop", repetition_content=0.041)),
            asdict(
                _rec(
                    arm="no-think",
                    done_reason="length",
                    content_chars=10,
                    repetition_content=0.633,
                )
            ),
            asdict(_rec(arm="no-think", done_reason="stop", repetition_content=0.100)),
        ]
    )
    check(
        two_arms.count("VERDICT:") == 2,
        f"each arm must get its OWN verdict, never one pooled over both (got "
        f"{two_arms.count('VERDICT:')})",
    )
    check("NO DATA" in render([]), "an empty report must say NO DATA rather than raise")

    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK (no model calls)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        print(render(json.loads(args.rescore.read_text())))
        return 0

    fixtures = [_fixtures.KICKOFF, _fixtures.HELIOS_OVERVIEW]
    records: list[ReplyRecord] = []
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = RESULTS_DIR / f"runs-{stamp}-{args.model.replace(':', '-')}.json"
    print(
        f"model {args.model}, {args.runs} run(s) per fixture per arm, "
        f"num_predict {CEILING}, num_ctx {CONTEXT_WINDOW}\n",
        flush=True,
    )
    # INTERLEAVED, run by run. Running every `think` call before every
    # `no-think` call confounds the arm with anything that drifts over a
    # twenty-minute sweep -- and the headline this probe produces is a
    # LATENCY comparison, which is the measurement most exposed to that.
    # Alternating costs nothing and removes it.
    for fixture in fixtures:
        for run in range(1, args.runs + 1):
            for arm in ("think", "no-think"):
                record = run_once(
                    fixture, arm=arm, run=run, model=args.model, host=args.host
                )
                records.append(record)
                print(
                    f"   {fixture.name} {arm} run {run}/{args.runs}: "
                    f"{record.done_reason} {record.eval_count} tok, content "
                    f"{record.content_chars}, thinking {record.thinking_chars}, "
                    f"{record.seconds}s",
                    flush=True,
                )
                # Written after EVERY call. A sweep that persists only at the
                # end discards twenty minutes of GPU on one transport
                # failure, and `run_once` has no retry by design -- a failed
                # call is data this probe does not want to paper over.
                path.write_text(
                    json.dumps(
                        [asdict(r) for r in records], indent=2, ensure_ascii=False
                    )
                )

    print(render([asdict(r) for r in records]))
    print(f"\nstored {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
