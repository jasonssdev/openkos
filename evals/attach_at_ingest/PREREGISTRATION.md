# Pre-registration: attaching a later meeting's subject to an existing concept (#1701, part 2)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms", the held-out fixture exists with its digest pinned below, and this
file has merged to `main`, before the first model call.**
Nothing here calls a model, so "the first model call" never comes; read it as "before the
first counted run". A change after that point is a new registration, not an edit to this
one. It generates no number itself: it fixes what will be run, how it will be read, and
what each outcome means. Production code, prompts and defaults are unchanged by this file
and by the run. Every bar marked **(proposed)** is the author's proposal until the owner
confirms it.

**The rule and every constant below are FROZEN, before any held-out data exists.** The
author built the rule against a development fixture and saw its numbers while doing so
(see "Development set"). That fixture is evidence of nothing. The verdict is decided
**only** on a held-out fixture, written by a separate author who never sees the rule, the
harness, the development set or its results.

It is modelled on `evals/source_demotion/` and `evals/series_boost/` (the treatment lives
in the harness only; a self-test checks this file against the code) and on the auto-merge
registration `evals/auto_merge/PREREGISTRATION-1298.md` (a wrong merge is bounded by a
zero-tolerance bar on a fixture with named hard negatives, and the bar is refused when the
negatives were never exposed to the rule).

## Question

Part 1 of #1701 shipped (`ea917049`, PR #1715: near-match pairs of one subject are
clustered into one group question; `resolution/candidates.py` `_cluster_low_pairs`).
This is part 2: when a **later meeting's** extracted Decision or Concept names the same
subject as an existing concept under other words, would attaching it to that concept
(a new `provenance` entry and body, as the shipped attach does) instead of creating a
sibling **avoid siblings without wrong attachments**? A wrong attachment is costly: it
folds two things into one page and the model's next answer cites a fused one.

It must respect the recurring-series rule (`docs/cli.md`, `adjudicate`: an identical title
usually names occurrences, not duplicates). So the rule keys on the **type and title of a
Decision or a Concept** and never on an Event title: `Event` and `Person` stay out through
the shipped constant `ATTACH_EXCLUDED_TYPES`, which every arm reads.

A `SUPPORTED` verdict licenses **one** thing: a confirmation on real extractions
(a separate registration, with its own judges), never a production change. See "What a
`SUPPORTED` verdict does not establish".

### What the shipped code does today (verified)

- Attach-at-ingest already exists (#1268, ADR-0045): `application/ingest.py`
  `build_attach_lookup` + `stage_derived_objects`. A candidate attaches when an existing,
  non-deprecated, local concept has the **same OKF type and the same normalized title key**
  (`normalize_key`, the key `duplicates` uses). No model is called. `Event` and `Person` are
  excluded; an imported concept is never a target; `attach_at_ingest: false` turns it off.
- So part 2 is not "add attach". It is **widen the match** from identical keys to near
  titles, which is exactly where a person is today asked the pairwise questions of part 1
  (`near_match_score`, the LOW tier).
- The near-match score is title-only and containment-based
  (`resolution/similarity.py`): every content token of the shorter title must have a
  similar token (ratio >= 0.75) in the longer. Its own docstring says LOW is high-recall,
  not high-precision, and must not be tightened to fix a false positive. That is why the
  rule below adds guards outside it.
- A Decision's later position of the same subject is **not** an occurrence of the same
  concept. In the committed bundle `evals/decision_evolution/` each position is its own
  Decision, linked by `supersedes` / `revises`, and the older one is deprecated. An attach
  rule that folds the new value into the old page destroys that history. The fixture
  therefore labels a changed value **distinct** (`value-changed`), and that label is the
  first judgment call below.

## What this does not touch

- Production code, prompts, defaults, `attach_at_ingest`, `ATTACH_EXCLUDED_TYPES`.
- Part 1 (the cluster questions) and the review queue.
- Embeddings and every model. **Neither the chat model nor the embedder is called**, in the
  run or in the self-test: the GPU is busy with another measurement, and the earlier
  nomination probe found the embedding margin negative on labelled pairs
  (`evals/pair_nomination/README.md`), so there is no measured vector signal to put in an
  arm. A model-judged attach (an adjudication call at ingest) would break the shipped "no
  model is called" property and is a different registration.
- Auto-merge of existing duplicates (#1298) and the `-N` families it covers.

## Arms

| Arm | Rule | Role |
|---|---|---|
| `shipped` | `build_attach_lookup(...).find` as shipped: same type, same normalized key | baseline |
| `near_guarded` | the shipped lookup first; when it finds nothing, the existing concept of the same type whose title is a near-match, with the same digit-bearing tokens and the same number of content tokens | candidate |
| `near_loose` | as `near_guarded` with token-count coverage 0.75 instead of 1.0 | ablation, never decides |
| `near` | the shipped lookup, then the near-match with no guard at all | ablation, never decides |

The rule, exactly (`Library.attach`, `near_targets`, `one_family`):

1. The candidate's type must not be in `ATTACH_EXCLUDED_TYPES` (Event, Person).
2. The shipped exact lookup runs first. If it finds a match the arm attaches there, on the
   canonical family member, exactly as shipped. **No arm ever changes a shipped attach**
   (an invariant, below).
3. Otherwise the targets are the existing documents of the **same OKF type** that
   `resolution.candidates.keyed_documents` yields (readable, typed, titled, not a Source,
   not deprecated) minus imported concepts (`imports/...`), whose key has
   `near_match_score(candidate_key, key) >= NEAR_SCORE_MIN`.
4. `near_guarded` and `near_loose` keep a target only when (a) its digit-bearing tokens
   (`q3`, `2027`, `180`) equal the candidate's, and (b) the **coverage** reaches the arm's
   minimum. Coverage is the content-token count of the shorter title over that of the
   longer, content tokens being the shipped near-match tokens minus the shipped function
   words. `near_guarded` requires 1.0 (`COVERAGE_MIN`), `near_loose` 0.75
   (`LOOSE_COVERAGE_MIN`). `near` applies neither.
5. It attaches only when the kept targets are **one** `-N` suffix family, to its canonical
   member (`canonical_family_member`, ingest's own rule). Two unrelated targets are an
   ambiguity and attach nothing.

Why the guards: the shipped near-match accepts a shorter title contained in a longer one
(`Remote Control` inside `Remote Control Design`), and drops tokens shorter than three
characters, so `Q3` and `Q4`, or `45` and `21`, look identical to it. The numeral guard and
the coverage guard exist for those two shapes. Nothing about them was fitted to a model.

The runs are deterministic: one pass per arm, repeated once to check it.

## Data

Two fixtures with one schema and one vocabulary of classes. Both are synthetic and
committed; nothing private is read.

| Fixture | Module | Author | Role |
|---|---|---|---|
| development | `attach_fixtures_dev.py` | the rule's author | observed while tuning; **decides nothing** |
| held-out | `attach_fixtures_heldout.py` | a separate author | the **only** fixture a verdict is read from |

Each is one library of existing concepts and a list of later-meeting candidates. A candidate
is `(type, title)` as one extraction would produce it, with a gold label: the existing
concept it names (a **right** attach), or none (any attach is **wrong**, including to a
different concept than the gold).

### Classes

| Class | Gold | What it is |
|---|---|---|
| `exact` | a concept | same normalized title: the shipped attach |
| `decision-restated` | a concept | the current position of a decision chain, reworded, same value |
| `concept-reworded` | a concept | reworded, reordered or inflected |
| `concept-expanded` | a concept | a shorter or longer title for the same thing |
| `unreachable` | a concept | the same thing under words that share no token |
| `value-changed` | none | the next position of a chain: same subject, new value |
| `value-added` | none | the current position plus a qualifier: a new decision |
| `numeral-differs` | none | same frame, another number, version or period |
| `part-whole` | none | a shorter or longer title naming the whole or a part, not the thing |
| `sibling-aspect` | none | two aspects of one subject, different pages |
| `ambiguous` | none | near to two different concepts |
| `cross-series` | none | identical title, another working group's decision |
| `exact-homonym` | none | identical title, another referent |
| `cross-type` | none | the same title as a concept of another OKF type |
| `deprecated-target` | none | near only a deprecated or superseded concept |
| `imported-target` | none | near only an imported concept |
| `unrelated` | none | shares nothing with the library |
| `event` | excluded | recurring-series Event titles: same, reordered, undated |
| `person` | excluded | the same name, with and without accent or a surname |

A changed value is a **new Decision**: in the committed bundle `evals/decision_evolution/`
each position is its own page linked by `supersedes` / `revises`, and the older one is
deprecated. Labels are CONSTRUCTED, not adjudicated, by one hand per fixture. A wrong
verdict is a rubric-consistency failure, not disagreement with a person who read a real
library.

### Development set

117 candidates (114 counted; 3 titles quoted from #1701 are descriptive only) over a library
of 72 documents (the first three positions of the 8 chains of `decision_evolution`, 36
Concepts, 2 Procedures, 6 Events, 4 Persons, 2 imported, 2 deprecated). 39 gold-same, 63
gold-distinct, 12 Event/Person; the gap (gold-same the shipped rule misses) is 33.

`DEV_DIGEST = 7196c2e17211a58cbe044929711c50d58e5428aae36e9ce23b4cd329608be43b`

### Held-out fixture

Written by a separate author from `HELDOUT_GUIDE.md` and `heldout_schema.py` alone. The
author never sees the rule, its constants, the harness, the development set or any result.
`heldout_schema.py` checks the schema and these minimums, which are set above the floors
below so that a fixture that meets them can meet the floors:

- library: 60 documents (20 `Decision`, 6 superseded, 4 `Event`, 4 `Person`);
- 45 candidates naming an existing page, 30 of them not `exact`;
- 70 candidates naming no existing page;
- 16 `event` and `person` candidates together;
- per class: `exact` 5, `decision-restated` 8, `concept-reworded` 10, `concept-expanded` 4,
  `unreachable` 4, `value-changed` 8, `value-added` 8, `numeral-differs` 8, `part-whole` 8,
  `sibling-aspect` 8, `ambiguous` 3, `cross-series` 2, `exact-homonym` 2, `cross-type` 2,
  `deprecated-target` 3, `imported-target` 2, `unrelated` 6, `event` 8, `person` 8.

It is read through the harness (`--fixture heldout`); a file that breaks the schema or a
minimum is refused, not repaired. The author is not told the floors that depend on the
rule (`EXPOSED_*`): they are the rule's business, and a fixture that does not expose it ends
in `NO-EXPOSURE`, not in a rewrite.

**Pinning.** Before this file merges, the held-out fixture's sha256 is computed by the
harness (`digest --fixture heldout`, the layout `digest_of` fixes) and written on the line
below and into `HELDOUT_DIGEST` in `measure_attach_at_ingest.py`, in one commit that changes
nothing else. Until then `decide` refuses. A fixture whose digest differs is `INCONCLUSIVE`.

`HELDOUT_DIGEST = PENDING`  <!-- PLACEHOLDER: replace PENDING with the sha256 of attach_fixtures_heldout.py's converted fixture before merge; HELDOUT_DIGEST in the harness changes in the same commit -->

## Development set

**Observed while tuning; decides nothing.** The arms cost milliseconds and were run while the
development fixture was built, so these numbers were seen by the rule's author and shaped the
rule. They are recorded so a reader can see what was seen:

| Arm | Right attaches | Siblings avoided (added over `shipped`) | Wrong attachments added |
|---|---|---|---|
| `shipped` | 6 | 0 | 0 (its own wrong attaches: 4, the `cross-series` and `exact-homonym` cases) |
| `near_guarded` | 27 | 21 of a gap of 33 | **0 of 63** |
| `near_loose` | 29 | 23 | 8 |
| `near` | 27 | 21 | 20 |

The history that makes them untrustworthy (`CHANGE LOG`, in order):

1. The first draft's candidate used coverage 0.75. On the draft it attached wrongly a
   qualifier decision (`... por Slack y por SMS`) and aspect siblings (`Remote Control
   Design Review`, `Remote Control Battery Life`). The author **tightened the candidate to
   coverage 1.0** and kept the 0.75 rule as the ablation `near_loose`. `COVERAGE_MIN = 1.0`
   is therefore a tuned constant, not a blind one.
2. Three labels were corrected after a run showed the label's claim false: the two
   deprecated concepts had live near neighbours (renamed so they have none), and one
   `ambiguous` candidate was in fact the same thing as a Spanish concept in the library.
3. Ten negatives were added when the first run exposed 11 negatives against the floor of 12.

The rule is **frozen** as of the commit that carries this section. No constant, arm, guard,
bar or floor changes after a held-out fixture exists; a change is a new registration with a
new held-out fixture.

## Metrics

Per arm, over counted candidates (the three `quoted` titles never count):

- **Right attach**: attached to the gold concept.
- **Siblings avoided** (`right_added`): right attaches the `shipped` arm does not make.
  Each is one sibling page that would otherwise exist.
- **Wrong attach added** (`wrong_added`): an attach to anything other than the gold (any
  attach when the gold is none) that the `shipped` arm does not already make wrongly. A
  candidate with a gold that the arm attaches to a *different* concept is wrong.
- **Event/Person attached**: attaches of an `event` or `person` candidate, by any arm.
- **Exposure**: the gold-distinct candidates the `shipped` arm leaves alone and the
  unguarded `near` ablation attaches wrongly, and in how many classes (at least
  `EXPOSED_PER_CLASS_MIN` each). It says how many hard negatives the guards actually
  faced; a zero wrong-attach count with no exposure would be vacuous.
- Reported, never deciding: the share of the gap closed, the per-class right/wrong/none
  table, the rule-of-three upper bound on the wrong-attach rate (3 over the gold-distinct
  count when none is wrong), and each arm's misses.

## Bars

The constants below are the ones in `measure_attach_at_ingest.py::BARS` (and the digests
above); the self-test fails if this file and the code disagree. They are frozen.

```
NEAR_SCORE_MIN = 0.85
COVERAGE_MIN = 1.0
LOOSE_COVERAGE_MIN = 0.75
WRONG_ADDED_MAX = 0
EXCLUDED_ATTACHED_MAX = 0
NEGATIVES_MIN = 40
EXCLUDED_MIN = 12
EXPOSED_MIN = 12
EXPOSED_CLASSES_MIN = 4
EXPOSED_PER_CLASS_MIN = 2
GAP_MIN = 12
BENEFIT_MIN = 0.25
RIGHT_ADDED_MIN = 8
```

Judged on `near_guarded` against `shipped`, **on the held-out fixture only** (all
(proposed)). The floors below apply to the held-out fixture:

- **W1, wrong attachments (primary safety bar).** `wrong_added` is at most
  `WRONG_ADDED_MAX` (**zero**) over the gold-distinct candidates. One wrong attachment
  makes the verdict `REJECTED`, whatever the benefit. A wrong target for a gold-same
  candidate counts.
- **W2, the recurring-series rule.** No `event` or `person` candidate is attached by **any**
  arm, ablations included (at most `EXCLUDED_ATTACHED_MAX`, zero). This also checks that the
  harness reads the shipped exclusion.
- **B1, siblings avoided.** `right_added` is at least the larger of `RIGHT_ADDED_MIN` and
  `ceil(BENEFIT_MIN * gap)`. On a gap of 33 that would be 9. The bar is deliberately low next
  to W1: safety is the point, and a rule that only avoids a quarter of the missed siblings
  while adding none wrongly is still worth a confirmation.

`near_loose` and `near` are reported in full and **never decide** (a verdict that rested on
an ablation would let the guards be dropped).

**Verdicts, in this order.** `INCONCLUSIVE` when an invariant fails (below). `NO-EXPOSURE`
when the exposure floors are not met. `NO-HEADROOM` when the gap is under `GAP_MIN`. Then
`SUPPORTED` when W1, W2 and B1 all hold, otherwise `REJECTED` naming the failed bars.

## n and the stopping rules

- **n = 1 deterministic pass per arm on the held-out fixture**, repeated once to confirm
  determinism. There is no sampling and no seed: the rules are pure functions of two titles
  and a library, so more runs would repeat the same numbers. The binding n is the held-out
  fixture: with at least 70 gold-distinct candidates and none wrong, the 95% upper bound on
  the wrong-attach rate is about 4.3% (3/70). That is a statement about that fixture, not
  about a real library.
- **One shot.** The held-out fixture is run once. A failed bar, an unmet floor or a
  surprising miss is never answered by editing the rule, the constants or the fixture and
  running again; that is a new registration with a new held-out fixture.
- **Invariants** (a violation is `INCONCLUSIVE`, nothing else is read): the fixture digest
  matches the pinned one; every gold id exists in the library with the candidate's type and
  is neither deprecated, superseded nor imported; a candidate has a gold exactly when its
  class is gold-same; no arm changes an attach the `shipped` arm makes; no arm attaches an
  Event or a Person; two runs agree; the stored outcomes are from the held-out fixture.
- **Exposure futility stop, before any bar is read** (ends only this axis, ends in
  `NO-EXPOSURE`): fewer than `NEGATIVES_MIN` gold-distinct candidates, or fewer than
  `EXCLUDED_MIN` Event/Person candidates, or fewer than `EXPOSED_MIN` negatives exposed to
  the unguarded ablation, or fewer than `EXPOSED_CLASSES_MIN` negative classes with at least
  `EXPOSED_PER_CLASS_MIN` exposed. Then W1 would be passing for want of anything to refuse.
  The held-out author is not asked to reach this floor and is not told the rule, so the stop
  is a real outcome, not a formality.
- No harm-futility stop: there is no spend to save, so a rule that fails W1 is simply
  `REJECTED`.
- `decide` refuses (exit 2, nothing printed as a verdict) while the held-out fixture is
  missing or invalid, while `HELDOUT_DIGEST` is `PENDING`, or before `run --fixture heldout`.

## Judging

None. The labels are constructed and the rules are deterministic, so there is no model
answer to grade and no blind judge to run. The blind-judge protocol of
`evals/decision_evolution/` (opaque codes, the reference text beside the answer, nothing
about arm, checks or verdict) belongs to the confirmation stage on real extractions, whose
sheet would carry the **source passages** both titles were extracted from, since a title
alone cannot tell a judge whether two pages are the same thing.

## Cost forecast

| Step | Model | Low | Typical | High |
|---|---|---|---|---|
| held-out fixture written by a separate author (an agent session, no GPU) | none | 0.5 h | 1 h | 2 h |
| `run --fixture heldout` (4 arms, twice; **measured** on the development set: 0.14 s) | none | 0.00 h | 0.00 h | 0.01 h |
| `score`, `decide`, `--self-test` | none | 0.00 h | 0.00 h | 0.01 h |
| **GPU** | none | **0 h** | **0 h** | **0 h** |

The held-out writing time is an estimate. The confirmation stage, if licensed, is not
forecast here: it needs extraction runs whose size is unmeasured, and it gets its own
registration and GPU approval.

## Exact commands

```sh
M=evals/attach_at_ingest/measure_attach_at_ingest.py

# model-free, anywhere; passes whether or not the held-out module exists
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# the development set: informational only (default OUT:
# ~/Dev/Projects/openkos-eval-runs/attach-at-ingest)
OLLAMA_HOST=127.0.0.1:1 uv run python $M run --fixture dev
OLLAMA_HOST=127.0.0.1:1 uv run python $M score --fixture dev

# the held-out author checks the file against the schema (no harness, no rule)
uv run python evals/attach_at_ingest/heldout_schema.py

# pinning, once the held-out file exists and before merge
uv run python $M digest --fixture heldout

# the registered run, after merge, once
OLLAMA_HOST=127.0.0.1:1 uv run python $M run --fixture heldout
OLLAMA_HOST=127.0.0.1:1 uv run python $M score --fixture heldout
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide
```

`decide` takes no `--fixture`: it reads the held-out fixture only. The poisoned `OLLAMA_HOST`
is a check: the harness never reaches for a server, and the identity stamp
(`harness_stamp.build_stamp`) is built with an opener that refuses every connection, so the
stamp records the harness commit and the rule text and names no model.

## Outputs

`RESULTS.md` in this directory: the commit and both digests, the held-out population counts,
the table of every bar, the per-class right/wrong/none table for all four arms, every miss of
`near_guarded` and every wrong attach of the ablations, the verdict, and the limits below.
The development numbers appear only as a labelled appendix. The verdict updates #1701 and
nothing else.

## Disclosed before scoring

- **The rule's author has seen numbers, on the development set only.** See "Development
  set" for what was seen and how it shaped the rule. Nothing about the held-out fixture is
  known to the rule's author at the time of this registration; it does not exist yet.
- **Who sees the held-out fixture.** Its author sees only the guide and the schema checker,
  neither of which names the rule, its constants or the near-match idea. Whoever pins the
  digest and merges sees the file but changes no code except the one `HELDOUT_DIGEST` line.
  The guide names the classes and their meanings, including the shapes a hasty reader could
  mistake; those are the labelling vocabulary, shared with the development set because the
  harness scores by class.
- **Labels are one hand's per fixture.** The held-out labels are not reviewed by the rule's
  author. A mislabel in either direction moves a bar; it is not corrected after the run.
- **Titles are fixed strings.** In a real ingest the model words each title, and a title for
  the same subject varies from run to run. The fixture cannot show how often that produces a
  near-miss the guards let through.
- **Part-whole by containment is a judgment call** that the held-out author makes on their
  own examples; the strict coverage refuses both a part and a restated whole.
- **Recall is limited by design.** `unreachable` and `concept-expanded` candidates cannot be
  attached by a title rule that stays safe. B1's bar is low for that reason.
- **Known unmodeled hazard.** Antonym pairs whose tokens are lexically close (`enable` and
  `disable` score 0.77) fall below `NEAR_SCORE_MIN` and are not a class. A different
  `NEAR_SCORE_MIN` would need them.
- Bars with an analogue are reused (`WRONG_ADDED_MAX = 0` follows the 1298 R2 and S2 bars;
  the exposure idea follows the 1298 R1 floor). The numeric floors and `NEAR_SCORE_MIN =
  0.85` are the author's picks.

## What a `SUPPORTED` verdict does not establish

That the rule is safe on real extractions, or that a person would agree with its
attachments. It licenses a confirmation (extract a real library's meeting series with the
shipped pipeline, apply the rule at the attach seam, and have blind judges rate every
**added** attachment against the two source passages) and a later OpenSpec change with its
own ADR. Ingest would also gain a kind of attach a person cannot tell from the exact kind;
the shipped attach is reversible per commit (`git revert`), and that is not credited here.
A `REJECTED` verdict closes the title-only route and leaves part 1's clustered questions as
the way the duplicate is resolved.

## Limits (assumptions, labelled)

- Synthetic text, two languages, one product. Real titles are noisier.
- One hand's labels, no second labeller, no agreement figure.
- Title is the only signal. Body text and `description` are not read, so two pages that
  agree only in their body are invisible, and two that agree in title and disagree in body
  are attached.
- The Decision half assumes a changed value is a new Decision (the engine's model today).
  If the owner wants a restated-with-new-value decision folded into the old page, the
  `value-changed` and `value-added` labels flip and this registration is void.

## The owner confirms

1. The question and its scope: widening the shipped exact-title attach to near titles for
   Decision and Concept only, keyed on type and title, with `Event` and `Person` excluded by
   the shipped constant.
2. The label for a Decision whose value changed (`value-changed`, `value-added`): a **new**
   Decision, never folded into the earlier one (the decision-evolution model).
3. The frozen rule and constants: `NEAR_SCORE_MIN = 0.85`, same digit-bearing tokens,
   coverage `1.0`, a single target family; and that `COVERAGE_MIN` was tuned on the
   development set (disclosed above), with `near_loose` kept as an ablation.
4. That the development set decides nothing and the verdict is read only from the held-out
   fixture, once.
5. `HELDOUT_GUIDE.md` and `heldout_schema.py` as the only things the held-out author is
   given, including the per-class minimums and the changed-value label.
6. The bars: W1 with a ceiling of **zero** wrong attachments added, W2 at zero Event or
   Person, B1 at `max(8, ceil(0.25 x gap))`, and the exposure and headroom floors, all on the
   held-out fixture.
7. That `near_loose` and `near` never decide.
8. What a `SUPPORTED` verdict licenses: a confirmation on real extractions only, with its
   own registration and GPU approval, and no production change.
9. That the run needs no GPU and no model, and is run only after the held-out digest is
   pinned here and this file has merged.

