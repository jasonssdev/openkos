# Edge-typing screen (#1563)

A measurement harness for the `edge_typing` task key. **It has not been run, and
it is not registered:** [`PREREGISTRATION-DRAFT.md`](PREREGISTRATION-DRAFT.md) is a
draft; its labels are adjudicated (see "Second adjudication") and it is blocked on the
owner's open questions and Stage 0. No model has been called. Part of #1556; refs #1563.

It does not duplicate [`evals/edge_typing/`](../edge_typing/README.md). That
harness is a regression gate for prompt changes on one model: a constructed
fixture, many arms. This one asks the key-justification question the volatility
screen asked of `volatility_typing`: does any local model beat what needs no
model, on labels a second person has checked, before a per-task model is
recommended or adopted?

## What the task decides

`resolution/edge_typing.py` (`suggest_edge_types`, reached through
`suggest-relations`, `curate`'s Structure stage and the daemon) makes **one chat
call per untyped body-link edge**. The model sees SOURCE and TARGET as
`[concept id - title]` and the full body of each, and answers one JSON object
`{"type": ..., "rationale": ...}` with `type` one of the seven **suggestable**
relation types: `caused_by`, `depends_on`, `member_of`, `part_of`, `produced_by`,
`references`, `related_to` (`model.relations.SUGGESTABLE_RELATION_TYPES`; the
engine-owned `derived_from` is withheld from the prompt and refused if replied).
`related_to` is the prompt's honest abstention: "the two are connected, and the
documents do not support saying how". There is no separate "untyped" answer.

What happens to a reply, all of which the harness runs because it calls the
shipped function:

- an unparseable reply degrades to "no suggestion" (fail closed); a reply naming
  a type outside the seven is **not** refused by production (the vocabulary is
  open), so the screen records it as `out_of_vocab`;
- a rationale that argues the reverse direction of an asymmetric type withdraws
  the type to `related_to` (#807, English-only prose check);
- an asymmetric type whose endpoints' object types name the reverse of its
  direction (`Person -> Organization` for `member_of`; `Concept -> Project` or
  `Concept -> Person` for `produced_by`) is swapped (#991, `corrected_edge`).

Downstream, an accepted suggestion becomes a typed edge in the graph.
`resolution/contradiction.py` derives its candidate pairs from **typed** edges
only, and shows the type string to the contradiction judge, so a wrong type is a
wrong premise for a later model call. The five asymmetric types never bulk-apply
(`curate --accept structure` asks per item, #624) because a model's direction was
measured to carry no evidence (#613); `references` and `related_to` do.

## Method

Each fixture item is a two-document bundle written to a scratch directory and put
through production's own `suggest_edge_types`, with no argument other than the
edge, the bundle and a timed backend, so the shipped prompt (`--self-test` pins it
byte for byte), parser and both direction checks are what is measured. One arm,
`default`. There is no anchor to probe (the prompt shows no current type), so
direction is probed by items instead: nine reversed probes whose true answer
cannot be the asymmetric type that would be right the other way round.

Numbers (all in `edge_screen_core.summarize`):

- **accuracy**, strict (the primary label) and lenient (also the flagged
  alternative); the mean over items of each item's mean over runs. A direction-
  corrected reply describes the reverse edge, so it scores wrong here and is
  reported as `recovered` instead;
- **per-class recall and the gold-by-answered confusion matrix** (with a `none`
  column for failed calls), and accuracy per stratum;
- **specific precision**: of the answers that were one of the five structural
  types, the share that were right. It is the number behind the production
  prompt's closing guard (a wrong `part_of` asserts false structure), and it is
  reported, not decisive;
- **trap-hit rate**: on the reversed probes, the share answered with the
  asymmetric type that is only true the other way round and left uncorrected;
- **failure rate** by kind (`backend`, `timeout`, `invalid_reply`,
  `out_of_vocab`, `no_question`), latency (median, p95), answer share per type,
  stability across runs, and how often production withdrew a type (#807);
- the model-free baselines below.

## The baselines

Three predictors that need no model, scored on the same labels, and the commonest
label:

| baseline | what it answers |
| --- | --- |
| `always references` | `references` for every link: the rubric-literal reading of any body link, since SOURCE explicitly points at TARGET |
| `always related_to` | the abstention for every link; what the suggester answers most (67% of accepted edges on a real bundle before the rubric of #388, 59% of emissions on the constructed fixture after it, `evals/edge_typing/`) |
| `type signature` | the type whose declared `(subordinate, superordinate)` pair `edge_typing._DIRECTION_TYPE_SIGNATURES` holds for the two object types, else `related_to`; the only deterministic opinion the engine has about a relation type |

On the 49 items, resolved labels: **24 / 49 = 0.490 (`always references`), 7 / 49 =
0.143 (`always related_to`), 10 / 49 = 0.204 (`type signature`)**, commonest label
24 / 49 = 0.490. Lenient: 0.551, 0.531, 0.592. On the first author's labels they were
21, 9 and 12 of 49; `forecast` recomputes them.

**The fixture discriminates, by construction and by count.** 15 of 49 items are
right under none of the three baselines (16 on the first author's labels); 25 of 49
are wrong for the strongest baseline,
`always references`. A fixture where the commonest answer won would have returned
a green no model could fail; the volatility screen's lesson was the opposite
(only 6 items wrong for the floor before its adjudication).

## The fixture (`fixture.json`)

49 items over 58 documents, in three strata. Every document's type, title and body
is **exactly** the repository file it came from (`--self-test` fails if one drifts,
because a label was decided on that text):

| stratum | items | source | what it is |
| --- | --- | --- | --- |
| `constructed` | 29 | `evals/edge_typing/fixtures.py`, every edge | hand-written pairs whose bodies were written to the rubric; the 20 forward pairs and the 9 reversed probes. The labels are that fixture's, **constructed, not adjudicated** |
| `good-life` | 14 | `examples/good-life-demo/bundle` | **the whole production candidate set** of a real compiled bundle (`candidate_edges`, confidential included, which is what a local backend sends); `--self-test` fails if the set changes |
| `adr` | 6 | `docs/adr/` | ADRs that name one another, chosen for an explicit stated relationship |

Class balance (resolved labels): **3 caused_by, 4 depends_on, 4 member_of,
3 part_of, 4 produced_by, 24 references, 7 related_to** (the first author's: 5
depends_on, 21 references, 9 related_to). 27 items carry a flagged
alternative; the 9 reversed probes carry a `trap_type`. No `Place`,
`Entity`, `Insight`, `Event` or `Procedure` document appears as an endpoint, and
nothing was invented to fill them.

### How a label was decided

One rule: the **production prompt itself**, verbatim (definitions read
SOURCE -> TARGET, the three tie-breaks, and the guard that `related_to` is the
correct answer when the documents do not support a specific claim). The label
follows what the **two documents say**, in the orientation shown. Where a second
type is also defensible `alt_label` names it and the item is `ambiguous`: it is
scored strict (primary only) and lenient (either), and both are reported. The
per-item reasoning is the `how` field.

Three things about those labels you must know before reading a number:

- **`references` is the rubric-literal answer to any link, and that dominates.**
  Every candidate edge is a body link, so SOURCE "explicitly points at, cites, or
  names" TARGET by construction; `references` is wrong only when a specific type is
  also stated. 24 of 49 items are labelled `references`, 14 of them the good-life
  links. The production suggester answers `related_to` most of the time
  (`evals/edge_typing/`: 67% of accepted edges on a real bundle before the rubric,
  59% of emissions on the constructed fixture after it). Which of those two is right for an explicit link
  that states no further relationship is a product decision, not something the
  harness can settle; both are reported, and the J bar is set against the stronger.
- **The good-life items are 14 links among 7 documents.** They are the real
  population, not a sample, but they are not independent: the same bodies recur.
  The bootstrap resamples items and does not know that, so its interval is a little
  narrower than the information supports; per-stratum accuracy is reported for this
  reason.
- **The constructed stratum was written to the rubric.** Its bodies state the
  relation in the rubric's own words, which is why a model that has read the rubric
  does well on it; it measures rubric-consistency, and the older harness's result
  for `gemma2:27b` (0.82 forward) lives there. The prompt was tuned on this
  material, so it is the least independent stratum.

### The items

Labels are the first author's; `alt` is the flagged alternative; `probe` marks a
reversed-orientation item and the type that would be right the other way round.

| item | stratum | SOURCE -> TARGET | object types | label | alt | probe |
| --- | --- | --- | --- | --- | --- | --- |
| e01 | constructed | Retry Budget -> Request Scheduler | Concept -> Concept | part_of |  |  |
| e02 | constructed | Nightly Backup Job -> Scheduled Maintenance Jobs | Concept -> Concept | member_of |  |  |
| e03 | constructed | Report Renderer -> Template Cache | Concept -> Concept | depends_on |  |  |
| e04 | constructed | Checkout Outage -> Payment Gateway Migration | Concept -> Concept | caused_by |  |  |
| e05 | constructed | Quarterly Risk Report -> Risk Committee | Concept -> Concept | produced_by |  |  |
| e06 | constructed | Migration Runbook -> Rollback Policy | Concept -> Concept | references | related_to |  |
| e07 | constructed | Onboarding Checklist -> Incident Review Culture | Concept -> Concept | related_to |  |  |
| e08 | constructed | Aortic Valve -> Human Heart | Concept -> Concept | part_of |  |  |
| e09 | constructed | Soprano Line -> Voice Parts | Concept -> Concept | member_of |  |  |
| e10 | constructed | WAL Segment -> Write-Ahead Log | Concept -> Concept | part_of |  |  |
| e11 | constructed | EU West Replica -> Read Replicas | Concept -> Concept | member_of |  |  |
| e12 | constructed | Search API -> Ranking Service | Concept -> Concept | depends_on |  |  |
| e13 | constructed | Data Loss Incident -> Storage Upgrade | Concept -> Concept | caused_by |  |  |
| e14 | constructed | Architecture Decision Record -> Platform Team | Concept -> Concept | produced_by |  |  |
| e15 | constructed | Deploy Guide -> Incident Severity Matrix | Concept -> Concept | references | related_to |  |
| e16 | constructed | Hiring Loop -> Documentation Style | Concept -> Concept | related_to |  |  |
| e17 | constructed | Cost Dashboard -> On-call Rotation | Concept -> Concept | related_to |  |  |
| e18 | constructed | Scheduled Maintenance Jobs -> Nightly Backup Job | Concept -> Concept | related_to |  | reversed (member_of) |
| e19 | constructed | Read Replicas -> EU West Replica | Concept -> Concept | related_to |  | reversed (member_of) |
| e20 | constructed | Voice Parts -> Soprano Line | Concept -> Concept | related_to |  | reversed (member_of) |
| e21 | constructed | Risk Committee -> Quarterly Risk Report | Concept -> Concept | references | related_to | reversed (produced_by) |
| e22 | constructed | Platform Team -> Architecture Decision Record | Concept -> Concept | references | related_to | reversed (produced_by) |
| e23 | constructed | Payment Gateway Migration -> Checkout Outage | Concept -> Concept | references | related_to | reversed (caused_by) |
| e24 | constructed | Dana Reyes -> Platform Guild | Person -> Organization | member_of |  |  |
| e25 | constructed | Platform Guild -> Dana Reyes | Organization -> Person | related_to |  | reversed (member_of) |
| e26 | constructed | Sampling Policy -> Telemetry Rewrite | Concept -> Project | produced_by |  |  |
| e27 | constructed | Telemetry Rewrite -> Sampling Policy | Project -> Concept | references | related_to | reversed (produced_by) |
| e28 | constructed | Escalation Ladder -> Marcus Oyelaran | Concept -> Person | produced_by |  |  |
| e29 | constructed | Marcus Oyelaran -> Escalation Ladder | Person -> Concept | references | related_to | reversed (produced_by) |
| g01 | good-life | Epicureanism -> Stoicism | Concept -> Concept | references | related_to |  |
| g02 | good-life | Stoicism -> Epicureanism | Concept -> Concept | references | related_to |  |
| g03 | good-life | Stoicism -> Frame the essay (decision) | Concept -> Decision | references | related_to |  |
| g04 | good-life | Stoicism -> Maria Salazar | Concept -> Person | references | caused_by |  |
| g05 | good-life | Frame the essay (decision) -> Epicureanism | Decision -> Concept | references | related_to |  |
| g06 | good-life | Frame the essay (decision) -> Stoicism | Decision -> Concept | references | depends_on |  |
| g07 | good-life | Maria Salazar -> Stoicism | Person -> Concept | references | related_to |  |
| g08 | good-life | Maria Salazar -> Frame the essay (decision) | Person -> Decision | references | related_to |  |
| g09 | good-life | Call with Maria Salazar -> Stoicism | Source -> Concept | references | related_to |  |
| g10 | good-life | Call with Maria Salazar -> Frame the essay (decision) | Source -> Decision | references | related_to |  |
| g11 | good-life | Call with Maria Salazar -> Maria Salazar | Source -> Person | references | related_to |  |
| g12 | good-life | Reading notes -> Epicureanism | Source -> Concept | references | related_to |  |
| g13 | good-life | Reading notes -> Stoicism | Source -> Concept | references | related_to |  |
| g14 | good-life | Reading notes -> Frame the essay (decision) | Source -> Decision | references | related_to |  |
| r01 | adr | ADR-0069 -> ADR-0066 | Decision -> Decision | depends_on | references |  |
| r02 | adr | ADR-0055 -> ADR-0054 | Decision -> Decision | references | depends_on |  |
| r03 | adr | ADR-0068 -> ADR-0061 | Decision -> Decision | depends_on | references |  |
| r04 | adr | ADR-0012 -> ADR-0009 | Decision -> Decision | caused_by | references |  |
| r05 | adr | ADR-0074 -> ADR-0067 | Decision -> Decision | references | depends_on |  |
| r06 | adr | ADR-0003 -> ADR-0002 | Decision -> Decision | references | depends_on |  |

### What the fixture cannot show (read before any number)

- **The labels are adjudicated, not the owner's.** The first author labelled all 49
  items and flagged an alternative on 27; a Claude model labelled them blind (38 of 49
  agree, kappa 0.696), and a third model resolved the 11 disagreements (see "Second
  adjudication"). The owner labelled none of them. The volatility screen's
  experience is the warning: its first author's labels equalled the registry
  default in all eight disagreements, which moved the floor from 0.778 to 0.481.
  The same hazard exists here with `references`, which the first author may have
  chosen because the rubric makes it the literal reading of a link.
- **27 of 49 items are ambiguous by the first author's own flag**, so `lenient`
  is a weak statistic (the baselines score 0.53 to 0.59 on it) and `strict` decides.
- **Per-class recall is thin.** `caused_by` and `part_of` have 3 items each; with
  three seeds a class recall rests on 9 calls. The aggregate is the decision, the
  per-class table is for reading what went wrong.
- **Direction is probed on 9 items only**, all constructed. The ADR and good-life
  strata have no reversed twins because a reversed twin of a real link is not a
  link production would ever ask about. The good-life `Source -> concept` links are
  themselves reversed relative to the engine's own derivation edge (question 2).
- **Bodies are short for two strata and long for one.** Constructed documents
  run 109 to 219 characters (median 169), good-life documents 481 to 1,328 (median
  724), ADRs 2,515 to 6,388 (median 4,572);
  the real task sees whatever a user's compiled bundle holds.
- **Not covered:** `Place`, `Entity`, `Insight`, `Event` and `Procedure` as
  endpoints; non-English bodies; the rationale (never scored); a `confidential`
  document, which production would withhold from a non-local backend (every
  scratch document is written `private`, so the disclosure gate has nothing to
  drop).

### Questions the labelling surfaced (not decided here)

1. **Is `references` the right answer for an explicit link that states nothing
   more?** The rubric reads that way and the production suggester does not behave
   that way (it prefers `related_to`). Which is the intended behaviour decides
   whether the dominant stratum rewards or punishes the model, and whether the key
   is worth a call at all: `references` adds no claim beyond the link that already
   exists.
2. **Should the 14 good-life links exist as candidate edges?** The `Compiled into:`
   lines of the two Source documents produce six of them (g09 to g14) in the
   direction opposite to the engine's own `derived_from` edge, which the suggester
   cannot name. A model asked about them can only say `references` or `related_to`
   about a derivation.
3. **`gemma2:27b` is `RECOMMENDED_TASK_MODELS["edge_typing"]`** (#650) on #516's
   0.81, measured on the constructed stratum, and is not in the candidate list this
   screen inherits from the volatility screen. If the recommendation should stand
   or fall on this measurement it has to be added; that is a registration decision.

## Second adjudication

**A second set of labels is recorded, and its disagreements are resolved, but neither by
the owner.** The owner delegated the blind labelling and a Claude model filled the sheet
(it read only the sheet). The owner then delegated the adjudication: a third model (Claude
Opus) resolved the 11 disagreements blind to which annotator gave which label, and each
resolution is the item's `adjudication` record in `fixture.json`, listed in
[`ADJUDICATION.md`](ADJUDICATION.md). An adjudication may resolve to either label; the
losing one stays as `alt_label`. `import-sheet --sheet <filled> --out second_labels.json --labeller <who>` writes
those labels, keyed by fixture id, to [`second_labels.json`](second_labels.json); the
filled sheet is not committed. Agreement is measured against that labeller, and no
disagreement is resolved (see the pre-registration draft).

Not done. The commands below write the blind sheet and, once the owner has filled
it, compare it with the first author's labels. Neither command contacts a model.

```sh
# 1. write the sheet OUTSIDE the repository (a path inside it is refused)
uv run python evals/edge_typing_screen/run_edge_typing_screen.py blind-sheet --out ~/scratch/edge-typing-blind-sheet.md
# 2. the second adjudicator fills in every `Label:` line (one of the seven types)
# 3. compare with the first author's labels (`first_label`, not the resolved one)
uv run python evals/edge_typing_screen/run_edge_typing_screen.py adjudicate --sheet ~/scratch/edge-typing-blind-sheet.md
```

The sheet shows opaque item ids in a seeded shuffle (`--seed`, default 1563; use
the same seed for both commands), the **production rule verbatim** (the definitions,
tie-breaks and guard of the system prompt) and, per item, the two documents'
titles and bodies, SOURCE first. It withholds the primary label, the flagged
alternative, the reversed-probe marker, the stratum, the `how` reasoning, the
document sources, the fixture item id and the **concept ids**: an id's folder names
the object type, and three type pairs have a deterministic answer in the
`type signature` baseline, so showing it would anchor the adjudicator on the very
predictor the screen compares models against. The model does see the ids; the
adjudication question is what the documents say. The documents' own prose (a
`Related` link path, an ADR number) can still hint at neighbours; that is residual
and accepted. A self-test plants a leak in a probe item and checks that no id, type,
source path, reasoning, label or alternative reaches the sheet, and that no relation
type appears in an item block outside its quoted documents and its `Label:` line.

The sheet is about 104,000 characters (the six ADR items are most of it); the
owner's reading is the cost of a defensible second label.

`adjudicate` reports raw agreement, Cohen's kappa and each disagreeing item
(noting where the second label is the first author's flagged alternative). A sheet
with a blank, invalid, unknown or missing item is rejected and nothing is scored.
It writes nothing into `fixture.json`: resolving each disagreement (the same
`adjudication` record as the volatility fixture, with the first label kept as the
alternative) and recording the outcome is a separate, human step, and the filled
sheet is never committed.

## Usage

Always with `OLLAMA_HOST` pointing nowhere unless a run is intended:

```sh
# model-free
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/edge_typing_screen/run_edge_typing_screen.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/edge_typing_screen/run_edge_typing_screen.py forecast --runs 3
# a model (needs a local Ollama; stamped, cached per row, resumable) -- NOT before registration
uv run python -u evals/edge_typing_screen/run_edge_typing_screen.py run --model qwen3:8b --runs 3
uv run python evals/edge_typing_screen/run_edge_typing_screen.py score
```

`run --dry-run` prints the same forecast as `forecast`. Runs are stored under
`--runs-dir` (default `~/Dev/Projects/openkos-eval-runs/edge-typing-screen/`,
outside the repository), one `runs-<model>.json` each, with the identity stamp
(harness commit, model digest, prompt hash) in-band.

`edge_screen_core.py` holds everything model-free and is unit-tested
(`tests/unit/test_edge_typing_screen_core.py`); `run_edge_typing_screen.py` owns
the model client, the stub backend of `--self-test` and the report;
`edge_blind_adjudication.py` owns the sheet.
