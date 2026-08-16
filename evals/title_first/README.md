# `title_first` — costing #728's option 2 before production pays for it

One question: **does moving the framing gate ahead of body generation pay for
itself?**

#692 measured that extraction discards 53–93% of what it generates. #728
narrowed it: 81–100% of that discarded tail dies in gates that read `type` and
`title` and nothing else, the largest being `_drop_framing_objects`. A
candidate it kills had its `description` and `body` written for nothing.

## The answer

**No — see `report.md`.** Wall clock nearly halves (165.7s → 87.0s) and quality
leaves the #694 oracle band on both axes (recall 0.82 → 0.62, precision
0.95 → 0.62).

The reason is the finding: **writing the body is itself the brake on
enumeration.** Phase 1 proposes ~2.3x more candidates once proposing one costs
a title instead of a paragraph, and the extras are conjunctive, over-broad
decay titles. Part of #728's "waste" was the price of restraint.

Production is unchanged by this directory.

## How the arm is built, and why it is one function

`extract_concept_union` calls `_extract_once` once per window. The treatment
patches **that one function** — survey for `type` + `title`, drop the framing
objects, hydrate the survivors in one call — and nothing else. Chunking, dedup,
the twin rule, the language gate, the re-ask, participant capture, the judge,
the backstop and the report all run production's own code, byte-identical in
both arms.

A probe that reimplemented the union would make every difference ambiguous
between the lever and the copy. This one cannot.

## Three properties worth reusing

**The survey prompt is derived, not restated.** It is
`concept._SYSTEM_PROMPT` with only its final reply-shape clause replaced, so
the nine-type rubric, the pinned anti-enumeration paragraph (#380) and the
transcript-subjects clause (#715) are carried byte-identical. If production
ever moves that clause, `_survey_system_prompt` **raises** rather than silently
sending the full-shape instruction and measuring an arm that asks for exactly
what the baseline asks for — the inert-arm defect a reviewer caught in the #714
probe.

**The confound is measured, not assumed.** Changing a reply shape can change
what the model proposes; this repo has five prior cases. The report prints
title-set overlap between arms, and at 0.29 it is what turned "1.9x faster"
into "did different work". A latency comparison without it would have read as a
win.

**The treatment's own loss channel is counted.** A survivor the hydration call
does not return is dropped and recorded as `hydration_lost`, never back-filled
from its survey title. A candidate with an invented description is worse than
an absent one, and a silent fallback would hide the treatment's failure mode
inside its own quality score.

## Running it

```bash
uv run python -u evals/title_first/run_title_first_probe.py --runs 6   # needs Ollama
uv run python evals/title_first/run_title_first_probe.py --self-test   # no model
```

`--fixture` selects any `examples/extraction-corpus/ground-truth/` entry, but
the quality bar needs adjudicated titles, so in practice that means
`medium-10-reunion-plataforma`. The sweep refuses to run when that fixture has
drifted off the chunked path, reusing #726's own `## Path invariant` check —
the lever only exists where `_extract_once` runs per window.

Each sweep writes its raw runs and its rendered table into `results/`. It never
writes `report.md`: that file carries a human's reading of a sweep, and a probe
that overwrote it would turn every re-run into silent loss of the analysis the
numbers were published with.
