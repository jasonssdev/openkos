# Results: scope of the framing-object drop (#1575)

**Recommendation, not adopted: the measured counts favor option 1 (exempt
`Decision`, like `Procedure`), but production is unchanged.** On every stored
run that records the stage, the old rule removed one non-container object, and
the exemption would let back one container stub. A deleted subject is silent
data loss; a kept stub is visible and curatable (#459's asymmetry). But the
evidence is one false positive and one contested container leak, and the
#522 census (273 runs) is not stored in the repository. That does not meet the
bar #1575 sets for changing the rule on an observation, so the rule stays as
it is pending more framing-shaped runs. `Person` would stay dropped under
every option: a `Person` titled after the meeting is the stub #522/#533
measured.

Harness: `measure_framing_scope.py` (`--self-test`, or no flag to score). No
model is called; it reads recorded stages only. Run with `OLLAMA_HOST` set to
a closed port.

## Stores

- 453 `concept-screen`-format record files (the #1557 screen, the #1558
  window screen, the #1571 judge study, the runtime replay) under
  `openkos-eval-runs/`. 1 failed run, 303 duplicates of another record's raw
  stage, leaving 149 distinct usable records over 6 models, AMI meetings and
  the extraction-corpus prose fixtures. 122 of 149 have a framing-shaped
  source.
- 79 `_drop_framing_objects` stage executions in the tracked
  `evals/stage_attrition/results/*.jsonl` (Spanish notes and AMI TS3005a). All
  79 sat on a framing-shaped source.

The scoring unit is a meeting-titled object (the only kind any arm can
drop) on a framing-shaped source. The #522 census (273 runs) is not stored
in the repository; this is the largest stored population available.

## Counts

| arm | dropped | false positives | container stubs leaked |
|---|---|---|---|
| `before` (old rule) | 379 of 379 | 1 of 379 | 0 of 379 |
| `opt1` exempt `Decision` | 377 of 379 | 0 of 379 | 1 of 379 |
| `opt2a` drop only `Event` | 376 of 379 | 0 of 379 | 2 of 379 |
| `opt2b` drop `Event` and `Project` | 377 of 379 | 0 of 379 | 1 of 379 |
| `shipped` (production, unchanged; equals `before`) | 379 of 379 | | |

By type: 376 `Event`, 2 `Decision`, 1 `Project`. The 85 objects of the
stage-attrition store are all `Event`s; the three non-`Event`s are all in the
record store (294 objects, `before` false positives 1 of 294).

## Every non-Event object the old rule removed

| type | title | run | class |
|---|---|---|---|
| `Decision` | Assignment of Next Meeting Tasks | `qwen3.6:27b`, IS1004b, r1 (concept-screen) | subject: the work due before the next meeting |
| `Project` | New Product Development Meeting | `qwen3:14b`, ES2015a, r1 (concept-screen) | container: describes the meeting |
| `Decision` | Decisions Made in Previous Meeting | `qwen3:8b@llama.cpp`, IS1004d, r2 (runtime replay) | container: a heading over unnamed decisions. Contested: read as a subject it is a second false positive and `opt1` leaks nothing |

Every `Event` is a container by definition. A read of the distinct `Event` titles in the
record store found none that names a subject.

## Reading

- `opt2a` is dominated: it leaks the `Project` stub as well and fixes nothing
  more than `opt1`.
- `opt1` and `opt2b` are identical on the stored data. `opt2b` would also
  exempt `Person`, `Concept` and every other type, with no measured example in
  its favour and the #522/#533 `Person` stub against it. `opt1` is the smaller
  change and the one the evidence names.

## Limits

- The judge (L10) and the stages after it are not replayed; a leaked stub may
  still be removed downstream.
- Types and titles are what the models emitted on these 6 models and these
  sources; a model that types container stubs as `Decision` more often would
  leak more.
- The class of a non-`Event` object is a hand reading, recorded in
  `ADJUDICATION` in the harness.
