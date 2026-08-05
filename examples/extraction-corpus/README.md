# `extraction-corpus`

A **measurement fixture** for extraction quality on real-world material. It is
not a workspace, not a reference, and not conformance-tested.

Keep it clearly separate from [`../good-life-demo/`](../good-life-demo/), which
is the opposite thing: a small, hand-written **reference workspace** showing
what a correct bundle looks like, doubling as the OKF conformance fixture. If a
17 KB tutorial were dropped in there, every conformance test would start
depending on it.

## Why it exists

[#404](https://github.com/jasonssdev/openkos/issues/404) measured extraction
against real documents and found two distinct behaviours the demo fixtures
cannot show:

- **The cap of 5 is too low for large sources.** 13–17 KB documents routinely
  contain 7–10 genuinely distinct subjects. `Agent Security` and
  `Agent Observability` were real subjects, discarded.
- **Enumeration decay past that point.** Once the model runs out of genuine
  subjects it keeps going, emitting facets of the last one:
  `Skill Modifiability`, `Skill Reusability`, `Skill Customization`,
  `Skill Collaboration` — properties, not knowledge objects.

The issue's own conclusion is why this corpus has to exist before any fix:

> Cap and decay have to be addressed together, or the fix makes the bundle
> worse.

And `good-life-demo` structurally cannot host that measurement:

> The `good-life-demo` fixtures are 700–800 B and never approach the cap, so
> the gate as designed cannot observe this failure mode at all.

## Why the sources are real, not generated

A document written to contain exactly one Person, one Decision, one
Organization and one Event is **clean**: every object distinct by construction,
evenly spaced, no facets, no near-duplicates, no rambling. That is precisely the
material where decay does *not* happen. A synthetic corpus would measure zero
and report the defect fixed.

Real course notes stress the extractor because they were not written to be
fixtures. They have sections that are facets of the main topic, near-duplicate
headings, and tangents. That messiness *is* the test.

## Layout

```
extraction-corpus/
├── corpus.py         # survey candidates, then add one
├── sources/          # the chosen raw markdown, verbatim
└── ground-truth/     # one hand-written expectation per source
```

## Adding a source

Survey first — the point is choosing well from a large pile, not copying:

```bash
uv run python examples/extraction-corpus/corpus.py survey ~/path/to/notes
```

It ranks every markdown file by how likely it is to *exhibit* the defect. Two
columns matter:

- **BAND** — a size bucket. Pick one source per band. #404 measured 6, 13 and
  17 KB; a spread gives a curve, three files of one size give an anecdote.
- **FACETS** — headings clustering around a shared leading word. This is the
  strongest automatable predictor of the decayed tail, because it is literally
  the shape that produced it.

The ranking deliberately does **not** measure writing quality. A well-written
single-topic essay scores low, and that is correct: it cannot exhibit the bug.

Then add the chosen file:

```bash
uv run python examples/extraction-corpus/corpus.py add ~/path/to/notes/skills.md
```

That copies it into `sources/` under a band-prefixed name and scaffolds its
ground-truth stub. It refuses to overwrite an existing source.

## The ground truth is the measurement

Each source needs a hand-written expectation naming three things:

1. **Genuinely distinct subjects** — what a reader would expect its own
   document for. This is the number a run is scored against.
2. **Facets, not subjects** — headings that exist only to explain a subject
   above. An extractor emitting these is decaying, not enumerating. Naming them
   is what lets a run be *scored* rather than eyeballed.
3. **Near-duplicates** — pairs where two plausible objects name the same thing
   (`ADK Evaluation Framework` against `Agent Evaluation`). Distinct from
   facets: both look like real subjects, only one belongs.

**Write it before running any extraction.** Writing it afterwards records what
the model said rather than what is true, and the measurement stops meaning
anything.

**It cannot be model-generated.** Ground truth produced by an LLM measures one
model against another model's opinion, which is circular. This is the slow part
and there is no shortcut.

## The fixtures are not all independent

`small-04-pre-build-skills` is the **same lesson** as
`large-03-skills-vs-tools`, in Spanish and condensed to about 45% of its
length. That was discovered while writing its ground truth, not planned.

It is kept deliberately, and its ground truth says so at the top. Two
consequences bind anyone scoring a run:

- **Never average them.** A result on one is not independent confirmation of a
  result on the other. Report them as a pair.
- **It is not a clean size control.** Holding content constant while varying
  size is exactly how the "defect scales with size" claim would be isolated,
  but size and *language* vary together here, so a difference cannot be
  attributed to length.

It also surfaces a gap nothing in this project has measured: the classification
rubric, the tie-break chain, the anti-enumeration paragraph and the
multiplicity test are all English, and this is the first non-English source in
any fixture set.

## Relationship to `evals/model_spike/`

`evals/model_spike/run_spike.py` already carries a `Fixture` shape with ground
truth, but its `target_types` is a **multiset of types** — good for type
accuracy, insufficient here. `("Concept",) * 8` cannot distinguish
`Agent Security` (a genuine subject) from `Skill Collaboration` (a facet), and
that distinction is exactly what #404 turns on. The ground truth here carries
titles and classifications, so a harness can score decay rather than count.

## Licensing

This repository is public and Apache-2.0. Anything committed under `sources/`
is published under that license. Only add material you hold the rights to and
intend to publish.
