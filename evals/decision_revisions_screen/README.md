# Decision-revisions screen (#1563)

A measurement harness for the decision-revision judge's task key. **It has not been
run, and it is not registered:** [`PREREGISTRATION-DRAFT.md`](PREREGISTRATION-DRAFT.md)
is a draft; its labels are adjudicated (see "Second adjudication") and it is blocked
on the owner's open questions and Stage 0. No model has been called. Part of #1556; refs #1563.

It does not duplicate [`evals/decision_revisions/`](../decision_revisions/README.md).
That harness gated #1014's Phase B and tuned the judge prompt against its fixture,
one model (`qwen3:8b`, plus one `qwen3:14b` run), many bars. This one asks the
key-justification question the volatility screen asked of `volatility_typing`:
does any local model beat what needs no model, and does the key need a different
model than the global `qwen3:8b`, on labels a second person has checked, before a
per-task model is recommended or adopted? It reuses that harness's fixture
(through its own loader) and its structural integrity check rather than copying
either.

## What the task decides

`resolution/decision_revision.py` (`judge_pairs`, reached through `revisions` and
the daemon) makes **one chat call per candidate pair of Decisions**. The model sees
the two Decisions' titles and bodies, the earlier one first under its resolved date
when the dates order the pair, or both under `ORDER UNKNOWN` when they do not, and
answers one JSON object with a `verdict` of `reverses`, `refines`, `reaffirms` or
`unrelated`, a confidence, and one sentence quoted verbatim from each body.

What happens to a reply, all of which the harness runs because it calls the shipped
function:

- an unparseable reply degrades that pair to a malformed `unrelated` with
  confidence 0 (fail closed). The screen scores it wrong and counts it as a
  failure, so a model that cannot answer is not credited for an unrelated pair. A
  parseable reply whose verdict is outside the four words is also read as
  `unrelated` by production and cannot be told apart from a real one;
- a `reverses`/`refines` verdict with confidence >= 0.7 and **both quotes found
  verbatim in their bodies** is *actionable* and goes to the review queue
  (`is_actionable_revision`); `reaffirms` and `unrelated` never do;
- on a **directed** pair `reverses` becomes `supersedes` and `refines` becomes
  `revises` once a person accepts the finding; on an **undirected** pair the
  engine never types the change (`relation_for` returns `None`), the person names
  the later side and the relation together. Direction comes from the dates, never
  from the reply.

So a wrong verdict costs a person's attention (a false finding in the queue) or a
hidden Decision (a false `reverses` accepted writes `supersedes`), never a silent
write.

## Method

Each fixture item is a pair put through production's own `judge_pairs` with no
argument other than the two `JudgeSide`s and a timed backend, so the shipped prompt
(`--self-test` pins it byte for byte, and the user message against production's own
`build_judge_messages`), parser and quote check are what is measured. One arm. The
candidate stage (embedding similarity, which needs an embedder) is not measured: the
pair is handed to the judge directly, as stage (b) of `evals/decision_revisions/`
did.

Numbers (all in `revisions_screen_core.summarize`):

- **strict accuracy** (the deciding statistic): the verdict equals the label, with
  one equivalence production itself holds. On an undirected pair `reverses` and
  `refines` are one untyped change, so either counts for either label. A failed
  call scores wrong. The mean over items of each item's mean over runs;
- **sensitivity**, never deciding: `strict (1st)` scores the same answers against
  the first author's labels (it differs only after an adjudication is recorded) and
  `strict (clean)` restricts to the 42 pairs the drafter did not flag contested;
- accuracy per split (`original` 46, `confirmation` 12), per-class recall and the
  gold-by-answered confusion matrix (with a `none` column for failed calls);
- **false-supersede rate** (veto V3): of the calls on directed pairs that are not a
  reversal, the share answered `reverses`. It is the error that would write
  `supersedes` over a Decision still in force, the one `evals/decision_revisions/`
  named costliest;
- **queue recall and queue noise**, reported not deciding: the share of calls on
  `reverses`/`refines` pairs that production would queue, and the share of calls on
  `reaffirms`/`unrelated` pairs that it would queue;
- failure rate by kind (`backend`, `timeout`, `malformed`), latency (median, p95),
  stability across runs;
- the model-free baselines below.

## The labels: provenance and audit

Read this before any number.

**Who labelled, by what rule.** One drafter wrote each scenario and labelled it as
it was written (`revision_fixture_library.py`'s docstring). The rule is the
production judge prompt's four definitions, read against the sources' dates. Where a
careful reader could pick another verdict the drafter set `contested` with a
one-line note: **16 of 58 pairs**. On 2026-09-27 the owner went through those 16 and
**accepted every proposed label** (one was discussed and left UNRELATED; the file
says so). The other **42 pairs were never labelled a second time**, and the 12
`confirmation` pairs were written to be unambiguous (they carry `contested=False`
by construction). So the record is: one author, and one owner review of the doubtful
quarter, who agreed with the author on all 16. That is not a blind second
adjudication and carries no measured agreement; the volatility screen's second
adjudication found its first author had anchored on the production default in all
eight disagreements, and no number here can rule out the equivalent hazard.

**Balance** (adjudicated labels, `forecast`): REVERSES 14, REFINES 13, REAFFIRMS 8,
UNRELATED 23 (the drafter's: REFINES 14, REAFFIRMS 6, UNRELATED 24). The breakdowns
that follow are the drafter's labels. By split: `original` 46 (11, 8, 5, 22) and `confirmation` 12 (3, 6, 1, 2). 49
pairs are directed and 9 undirected (3 REVERSES, 2 REFINES, 4 UNRELATED). The 16
contested pairs are 6 REVERSES, 8 REFINES, 2 UNRELATED, all `original`. Hard-case
tags: 11 same-subject-unrelated, 9 shared-cue-unrelated, 7
partial-refine-reads-as-reversal, 5 reversal-reads-as-refine, 5
reaffirm-different-words, and smaller groups.

**Baselines, strict, k of 58, adjudicated labels** (`forecast` recomputes them; the
drafter's labels gave 24, 17, 16, 6 and 35):

| baseline | what it answers | k / 58 |
| --- | --- | --- |
| `always unrelated` | the commonest label | **23** (0.397) |
| `always refines` | `refines` for every pair | 16 (0.276) |
| `always reverses` | `reverses` for every pair | 16 (0.276) |
| `always reaffirms` | `reaffirms` for every pair | 8 (0.138) |
| `lexical overlap (oracle cut)` | one word-overlap cut, below it `unrelated`, at or above it `refines`, chosen to maximise accuracy **on these labels** | **34** (0.586) |

Production holds no deterministic opinion about a verdict (its direction rule orders
a pair, its candidate stage needs an embedder), so there is no shipped heuristic to
score; the lexical cut is the strongest model-free predictor the screen could build
and it is fitted to the answer key, an optimistic ceiling for any word-overlap
heuristic, not something that ships. It is the reference of rule J.

**Does the fixture discriminate?** Yes, against the baselines: the reference is
wrong on **24 of 58** items and the two strongest baselines are both wrong on 21 (23
and 19 on the drafter's labels).
(Four constant answers cover every label between them, so "right under no trivial
predictor" is empty by construction; the measure that means something is the room
left under the strongest one.) The sharper finding is on the other side.

**The stored `qwen3:8b` run leaves little room above the baseline.** Read into this
screen's format (`import-stored`, no model) and scored by its rule on the adjudicated
labels, the three stored 15-run `qwen3:8b` runs under the current judge prompt
(`d8238af6410ac31d`, `evals/decision_revisions/results/runs-20260928T173355Z...`,
`...204537Z...`, `...20260929T011322Z...`) score **0.928, 0.922 and 0.916**, against
the reference's 0.586 (on the drafter's labels the same answers score 0.893, 0.887 and
0.882, which the import reproduces). On the latest, 52 of 58 items are answered correctly
in every run and only 6 fall below 1.0: three are never right (`r14`, `r27`, `r30`) and
three are right in a fraction of runs (`r13` 0.20, `r28` 0.20, `r04` 0.73). The prompt was
tuned against the 46 `original` pairs (0.894 to 0.909 there), and the 12 `confirmation`
pairs are at ceiling (0.994 to 1.000), so they cannot separate any
two models. Consequences, stated before any run:

- the question "does the key beat no model" is already answered by the stored run
  for `qwen3:8b`, **if the labels hold**; rule J will be cleared by every competent
  model and discriminates little;
- the live question is selection against `qwen3:8b`, where the headroom is 6 items.
  Replaying the stored per-item scores (`score` prints it), a candidate that fixes 3 of
  those 6 with no loss has a paired-gain lower bound of 0.0 (not cleared) and one that
  fixes 4 clears it (+0.0138), 5 gives +0.0172. The bootstrap does not know the hard
  items are not independent, so treat that as optimistic;
- **2 of the 3 never-right items are ones the drafter flagged contested** (`r14`,
  `r27`), and the third (`r30`) is an undirected pair the drafter did not flag. These are
  the first places a labeller can be overturned, and a model "failing" on them may be a
  label error. The adjudication already moved the other two former never-right items
  (`r43`, `r53`) to the model's answer.

## The fixture

58 pairs over 66 Decisions in 8 sources, the library fixture of
`evals/decision_revisions/`: hand-written notes of a small community library's
volunteer committee, deliberately not AMI so tuning against it cannot contaminate
the thesis evaluation. Item ids `r01`..`r58` follow the sorted pair key, so adding a
pair renumbers the items (and the blind sheet's shuffle).

### What the fixture cannot show (read before any number)

- **The labels are adjudicated, not the owner's.** A Claude model labelled the 58 pairs
  blind (55 agree, kappa 0.927) and a third model resolved the 3 disagreements (see
  "Second adjudication"); the owner reviewed only the drafter's 16 contested pairs.
- **The prompt was tuned on it**, for `qwen3:8b`: the baseline model's score is
  optimistic for generalisation and any other model is, if anything, disadvantaged
  by a prompt it was not tuned with. A win for a candidate is therefore stronger
  evidence than a loss.
- **Eight stored-run items carry all the headroom** (above); per-class recall is
  thin (REAFFIRMS has 8 items) and a class recall rests on 18 calls at three seeds.
- **One domain, one voice, English only.** Short bodies; no non-English Decisions;
  no real compiled bundle. The ingest-produced Decision text a user's bundle holds is
  not covered, and `plan_revision_candidates` is not measured.
- The 9 undirected pairs cannot test `reverses` versus `refines` by design.

## Second adjudication

**A second set of labels is recorded, and its three disagreements are resolved, but
neither by the owner.** The owner delegated the blind labelling and a Claude model filled
the sheet (it read only the sheet). The owner then delegated the adjudication to a third
model (Claude Opus), blind to which annotator gave which label; the resolutions are in
[`adjudication.json`](adjudication.json) and listed in [`ADJUDICATION.md`](ADJUDICATION.md).
A record may resolve to either label. `import-sheet --sheet <filled> --out second_labels.json --labeller <who>` writes
those labels, keyed by fixture id, to [`second_labels.json`](second_labels.json); the
filled sheet is not committed. Agreement is measured against that labeller, and no
disagreement is resolved (see the pre-registration draft).

Not done. The commands below write the blind sheet and, once the owner has filled
it, compare it with the first author's labels. Neither command contacts a model.

```sh
# 1. write the sheet OUTSIDE the repository (a path inside it is refused)
uv run python evals/decision_revisions_screen/run_revisions_screen.py blind-sheet --out ~/scratch/revisions-blind-sheet.md
# 2. the second adjudicator fills in every `Label:` line (one of the four verdicts)
# 3. compare with the first author's labels (`first_label`, not the resolved one)
uv run python evals/decision_revisions_screen/run_revisions_screen.py adjudicate --sheet ~/scratch/revisions-blind-sheet.md
```

The sheet shows opaque item ids in a seeded shuffle (`--seed`, default 1563; use the
same seed for both commands), the **production judge's definitions verbatim** and,
per pair, the two Decisions exactly as the judge is shown them (earlier first with
its date, or both under `ORDER UNKNOWN`). It withholds the label, the drafter's
note, the contested flag, the hard-case tag (`reversal-reads-as-refine` names the
answer), the split, the source ids and the **concept ids** (a slug such as
`first-saturday-book-swap-opening` is part of the story; the model sees the ids, the
adjudication question is what the Decisions say). A self-test plants a leak in a
probe item and checks that no id, tag, note, split or fixture id reaches the sheet,
and that no verdict word appears in an item block outside its quoted Decisions and
its `Label:` line. The sheet is about 24,000 characters.

`adjudicate` reports raw agreement, agreement on the scored classes, Cohen's kappa
and each disagreeing item, noting where the drafter had flagged the pair contested
and where the two labels score alike (an undirected `reverses`/`refines` difference
cannot move any score and needs no resolution). A sheet with a blank, invalid,
unknown or missing item is rejected and nothing is scored. It writes nothing:
resolving each disagreement and recording it is a separate, human step. Each record
goes into `adjudication.json` beside this file, keyed `idA|idB` (sorted), with
`first`, `second`, `resolved` and `reason`; the loader checks `first` against the
fixture and the first label stays recoverable as `strict (1st)`. The filled sheet is never
committed.

## Usage

Always with `OLLAMA_HOST` pointing nowhere unless a run is intended:

```sh
# model-free
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py forecast --runs 3
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py j-table
# read a stored run of the older harness into this screen's format, then score it
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py import-stored --runs-dir <dir> --stored evals/decision_revisions/results/runs-20260929T011322Z-qwen3-8b.json
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py score --models qwen3:8b --runs-dir <dir>
# a model (needs a local Ollama; stamped, cached per row, resumable) -- NOT before registration
uv run python -u evals/decision_revisions_screen/run_revisions_screen.py run --model qwen3:8b --runs 3
uv run python evals/decision_revisions_screen/run_revisions_screen.py score
```

`run --dry-run` prints the same forecast as `forecast`. Runs are stored under
`--runs-dir` (default `~/Dev/Projects/openkos-eval-runs/decision-revisions-screen/`,
outside the repository), one `runs-<model>.json` each, with the identity stamp
(harness commit, model digest, judge-prompt hash) in-band.

`revisions_screen_core.py` holds everything model-free and is unit-tested
(`tests/unit/test_revisions_screen_core.py`); `run_revisions_screen.py` owns the
model client, the stub backend of `--self-test` and the report;
`revisions_blind_adjudication.py` owns the sheet. The bootstrap, selection and
justification helpers are the edge-typing screen's, loaded from its module.
