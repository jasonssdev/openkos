# Pre-registration: is what the selector judge cuts worth a page? (#1579, step 3)

**Status: REGISTERED 2026-10-26, before any label call was sent for this
study and before any cut or kept object was labelled.** A change after the
commit that adds this file is a new registration, not an edit to this one.

## Question

Steps 1 and 2 found that the cut objects are about as source-supported
(`CUT_PRECISION_RESULTS.md`) and about as non-redundant
(`CUT_REDUNDANCY_RESULTS.md`) as the kept ones, while the cut costs window
lift (`STAGE_LOSS_RESULTS.md`, `JUDGE_STUDY_RESULTS.md`). Neither step asked
whether a person curating the bundle wants the cut objects as pages. This step
asks that, on the same objects: would a curator keep the object as its own page
(`keep`), fold it into another object (`merge`), or drop it (`drop`)? `NEXT_STEP.md`
section 4 is the draft this registration replaces.

It measures; it changes no production code and no default. Any change to the
selection needs its own ADR afterwards (epic #1556: no default is adopted in a
results PR).

## Owner decisions (recorded verbatim)

1. An LLM-judge label is accepted as evidence for this question (no
   human-labelled sample is required first).
2. The threshold stays at 10 points: rule 1 adopts "the cut buys clutter
   reduction" iff D's 95% lower bound > +0.10; rule 2 ("the cut removes
   keepable objects") iff D's upper bound < +0.10 and >= 70% of cut objects are
   labelled keep; otherwise inconclusive. Same bootstrap as steps 1 and 2.

**Out of scope here, and open until adoption:** whether the Project and
Decision cut rates (Project 43 of 61 and 55 of 61, Decision 57 of 129 and 41 of
129, `self` and `fixed`) are acceptable defaults; whether an opt-in switch is
acceptable product surface; whether the downstream stages (edge typing,
contradictions) are priced on the two bundles first. This study's verdict does
not answer them and no outcome below presupposes an answer.

## No generation by candidate models

- Only the #1571 replay records in
  `~/Dev/Projects/openkos-eval-runs/judge-study/records/` and the #1557 corpus
  under `~/Dev/Projects/openkos-eval-runs/concept-screen/` are read. Neither is
  written.
- The only model called is `gpt-oss:20b`, the #1557 guardrail judge, with the
  prompt below. No extraction, no selector-judge call, no embedding.
- Raw replies, labels and scores go to
  `~/Dev/Projects/openkos-eval-runs/label-study/` (outside the repo).

## Population

Pinned by count and by a sha256 over the sorted object keys (one key per line;
a key is the JSON array `[model, meeting, run, ceiling index, window, type,
title, description]`). `label_study.py population` recomputes both and
`run` and `score` refuse any other population.

- **Records.** Round 1, run 1, pool DA (ES2015a, IS1004b, TS3009c, ES2015d,
  IS1004d), all five extractors (`qwen3:8b`, `qwen3.6:35b-a3b`,
  `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`): 25 meeting-runs, as step 1's
  primary population. Secondary runs are not part of this study.
- **Objects.** The `ceiling` stage (the selector judge's input) of each record's
  `self` seed-index-0 replay, restricted to objects that carry a window (the
  excerpt is that window): **303 objects in 154 windows** (of 352 ceiling
  objects; 49 carry no window and are excluded and counted).
  sha256 `76a9d42c3d70445ae4899d9b21c4a1c08299a4030f4c3d3867d8462a052aef84`.
- **Arms and seed.** Both judge arms of step 1, `self` (the extractor judges)
  and `fixed` (`gemma4:26b-a4b`), at seed index 0. Per arm each ceiling object is
  KEPT (present in that arm's `judge` stage) or CUT (matched by type, title,
  description and window, with multiplicity). For `gemma4:26b-a4b` as extractor
  the two arms are one sample, as in step 1. Seed indices 1 and 2 are not run.
- **Labelled once.** Every record window gets one call listing all its windowed
  ceiling objects, cut and kept together, so one label per object serves both
  arms, with the same companions.

## Judge

`gpt-oss:20b`, digest
`f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe` (the step-1
stamp; `ollama list` shows id `f38aa0c53da5`). `run` refuses another digest.
Production `OllamaClient` through the harness sender: `think` off,
`num_ctx` 12288, `num_predict` 8192, **seed 0**, one retry at **seed 1000** on
an unusable reply. Temperature is not set, so it is the model's default, as in
steps 1 and 2. The reply is parsed fail-closed by `screen_judge.parse_labels`
(every entry labelled exactly once with an allowed label). A window unusable
twice is **counted as a failure, never dropped silently**: it is reported as
`windows unlabelled k of N`, its objects are excluded from D, and if more than
10% of windows fail the run is `degraded` and decides nothing.

### The prompt (committed in `label_study.py` as `LABEL_SYSTEM`)

System message, verbatim:

```
You curate a personal knowledge base built from meeting notes. Below is a source excerpt and a numbered list of candidate entries extracted from it. Each entry would become a page of its own.

For EACH numbered entry, decide whether a person curating the knowledge base would keep it as its own page:
- "keep": it deserves a page of its own. It says something specific and useful that no other entry in the list already says.
- "merge": it is worth keeping, but not as a separate page. It repeats, overlaps or is a small part of another entry in the list, and would be folded into that entry.
- "drop": it is not worth keeping. It is vague, generic or trivial, or it is only a label or container with little content of its own.

Judge each entry by its own content, against the excerpt and the other entries. Do not use outside knowledge.

Reply with ONLY a JSON array, one object per entry, in entry order:
[{"id": 1, "label": "keep"}, {"id": 2, "label": "merge"}]
```

User message: `SOURCE EXCERPT:\n<window text>\n\nENTRIES:\n` then one line per
entry, `<n>. <title>: <description>`, in ceiling order. The prompt does not say
which objects the selector judge cut, and it does not mention a selector.

## Validity control (can come back NO)

If the rubric cannot recognise objects that are known to be duplicates, the
study is vacuous. Control population: the windowed objects the deterministic
dedup stage removed (`raw` minus `dedup`, multiset difference on type, title,
description and window) over the 35 unique records of the #1571 plan (primary
plus the two secondary extractors' run 2): **11 objects in 8 windows**. sha256
`2cfad2ef0510e8c95b9b1d9846ab26c83bfe8a41b7f7d962669d00aac3bd6f73`.

Each group is one call with the same prompt: the window's dedup-stage objects,
then the surviving twin of each removed object (same type and normalised title)
when it lives in another window, then the removed objects, which are the only
ones scored. A twin is always present in the call.

**Void bar, fixed now:** the control passes only if **at least 70% of all 11
control objects (that is 8 of 11) are labelled `merge` or `drop`**. An object in
a group whose reply was unusable counts in the 11 and is not flagged. If the
control does not pass, the study is **void**: no verdict, whatever D is, and the
rubric is recorded as unable to see known duplicates.

Limits of the control, stated in advance: 11 objects is a coarse bar (7 of 11
fails, 8 of 11 passes); 4 of the 11 are Person duplicates from one record
(`qwen3.6:35b-a3b`, IS1004b); an exact-title duplicate is the easiest case, so
passing shows the rubric is not blind, not that it is accurate on subtle clutter.

## Measure and decision rule

- A ceiling object is *clutter* if labelled `merge` or `drop`.
- **D** = share(clutter) among CUT objects minus share(clutter) among KEPT
  objects, pooled over windows, per arm.
- **Interval:** the two-stage cluster bootstrap of steps 1 and 2
  (`screen_metrics.two_stage_bootstrap`): meetings with replacement, then
  windows within a drawn meeting, **10,000 resamples, seed 42**, percentile 95%,
  on per-window (kept clutter, kept labelled, cut clutter, cut labelled).
- Counts print as `k of N`: objects labelled of windowed, windows labelled of
  all, cut and kept sizes, the control, the cut objects labelled `keep`.

Per arm, in this order:

0. **Degraded** (more than 10% of windows unlabelled) and **void** (the control
   fails) decide nothing, in that precedence. They apply to both arms, because
   the labels are shared.
1. **Insufficient** if fewer than 30 CUT objects are labelled in the arm.
2. **The cut buys clutter reduction** if the lower bound of D is **above +0.10**.
3. **The cut removes keepable objects** if the upper bound of D is **below
   +0.10** and **at least 70%** of the labelled cut objects are labelled `keep`.
4. **Inconclusive** otherwise.

Overall: the common verdict if both arms say rule 2 or both say rule 3;
otherwise **mixed or inconclusive**, with the pattern described, not decided.
Per type (Decision, Project, Concept, Event, the rest) the label counts for cut
and kept are reported as **descriptive only**.

Consequences, stated before the data:

- Buys clutter reduction: the lift cost is recorded as the price of the
  reduction, with an ADR for the record. No default changes.
- Removes keepable objects: a drop-only judge arm (option C in `NEXT_STEP.md`)
  is filed for its own pre-registration. Nothing is adopted here.
- Mixed or inconclusive, degraded or void: no change. The next question is the
  downstream cost, which needs a human or a pipeline run, not this labeller.

**Limits stated in advance.** One LLM labeller with its own noise, accepted as
evidence by the owner (decision 1); one label per object, no repeat labelling;
the labeller sees the whole window's candidates, which is not the bundle a
curator sees; a window-only source; the 303 objects are not independent (25
meeting-runs, 5 meetings). Expected precision (an estimate from step 1, not a
measurement): step 1's intervals on a 0.93 share were about +-0.07 wide each
side; the standard error of a share near 0.3 to 0.5 is about twice as large at
this n, so D's interval will probably be +-0.10 or wider, and rule 2 or rule 3
may well not be reachable. An inconclusive result is a real possibility and is
the outcome that changes nothing.

## Forecast and budget

- Calls: 154 label calls (one per window) plus 8 control calls = **162 calls**,
  plus one retry per unusable reply.
- Step 1 measured 216 calls in 2137 s of judge time, 9.9 s per call, 36 min of
  wall time (`CUT_PRECISION_RESULTS.md`). This prompt has the same shape (a
  window plus a numbered list, a short JSON array back), so the forecast reuses
  9.9 s: 162 x 9.9 s = 1604 s, about **27 min**, plus one model load of about
  1 min: **about 28 min**.
- Upper estimate at 16 s per call plus 10% retries: 178 x 16 s = 2848 s, about
  **48 min**. Budget: **1 h** of judge time. This is an assumption about timing,
  not a measurement of the new prompt.
- If the first 20 calls average more than 25 s, stop and report; no partial
  verdict is read.
- Scoring is model-free.

## Execution

**Ollama must be exclusive to this study during the run**: no other process may
use the server, and the run starts only when it is idle. Raw replies are kept in
the cache outside the repo, `run` is resumable (a stored window file is skipped,
a cached reply is not re-asked).

```
uv run python evals/concept_model_screen/label_study.py population
mkdir -p ~/Dev/Projects/openkos-eval-runs/label-study
uv run python evals/concept_model_screen/label_study.py run
uv run python evals/concept_model_screen/label_study.py score
```

`population` is model-free and must print 303 objects, 154 windows and the two
hashes above. The results go in a separate PR that adopts nothing.
