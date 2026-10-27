# Results of the second framing-drop measurement (#1575)

Registration: `PREREGISTRATION-2.md`, registered at its merge (PR #1604,
commit `354fe2ad`). Run commit: `354fe2ad1f91754fe5c57f4c3e37ab9570c53e60`
(both blocks, from the main checkout). Scored with the harness at the commit
that adds this note, after `ADJUDICATION-2.md`.

## Disclosure

The classification of the 16 new non-`Event` objects was **made blind by an AI
agent on the owner's delegation, not by the owner**. The agent saw only each
object's type, title and description (no model, run, record or arm) and
committed `ADJUDICATION-2.md` before `--decide` was first run. The owner
should review `ADJUDICATION-2.md`. A re-classification after seeing the
verdict below would be a new registration, per the registered rule.

## Models and digests

Verified with `ollama list` before each block; they match the registration.

| Model | Digest |
|---|---|
| `qwen3:8b` | `500a1f067a9f` |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf` |
| `gemma4:26b-a4b` | `001e5dafc3c7` |

## Runs

| Block | Runs | Start (UTC) | End (UTC) | Wall-clock | Forecast |
|---|---|---|---|---|---|
| 1 | 1-2 | 2026-10-26 20:29:40 | 2026-10-27 00:25:11 | 3 h 55 min | 3.8 to 4.6 h |
| 2 | 3-4 | 2026-10-27 00:25:28 | 2026-10-27 03:52:57 | 3 h 27 min | 3.8 to 4.6 h |

Records attempted: 156 (13 meetings x 4 runs x 3 models). Failed: 2, both
`qwen3:8b` `TS3012a` runs 1 and 2 in block 1, `OllamaGenerationCapped`
(8192-token ceiling, whole source, see #1607); none re-run, as registered.
`--count` counts 138 records: it de-duplicates records with an identical raw
stage, and the 16 records beyond the 2 failures are inferred to be such
duplicates (not separately verified).

`N_new` (meeting-titled objects on framing-shaped records of the new
population):

| Model | Block 1 | Block 2 | Total | Records counted |
|---|---|---|---|---|
| `gemma4:26b-a4b` | 77 | 76 | 153 | 46 |
| `qwen3.6:35b-a3b` | 86 | 97 | 183 | 46 |
| `qwen3:8b` | 52 | 42 | 94 | 46 |
| **All** | **215** | **215** | **430** | **138** |

Block 1 ended at 215 (< 400), so block 2 ran; block 2 ended at 430 (>= 400),
so the stopping rule was met and no block 3 exists. Block 2 values are the
final `--count` minus the block 1 `--count`.

## Classification (blind, see the disclosure)

Counts: CONTAINER 10, SUBJECT 1, CONTESTED 5. Contested objects are stored as
CONTAINER in `ADJUDICATION` and listed in `CONTESTED`, so they are read both
ways. Full table with reasons: `ADJUDICATION-2.md`.

| Type | Title | Class |
|---|---|---|
| Decision | Meeting Environment Improvements | CONTESTED |
| Decision | Meeting Room Equipment Feedback | CONTESTED |
| Decision | Next Meeting Date Schedule | CONTAINER |
| Decision | Next Meeting Planning | CONTAINER |
| Decision | Next Meeting Schedule | CONTAINER |
| Decision | Next meeting schedule | CONTAINER |
| Decision | Postponement of Meeting Due to Schedule Conflict | CONTAINER |
| Decision | Project retrospective on financial and material constraints | SUBJECT |
| Decision | Scheduling Next Meeting for First Monday of May | CONTAINER |
| Decision | Scheduling Strategy for Next Meeting | CONTAINER |
| Decision | Scheduling of the May meeting | CONTAINER |
| Decision | Selection of film for April screening and meeting details | CONTESTED |
| Entity | Next meeting schedule | CONTAINER |
| Project | Cost and Criteria Review Meeting | CONTESTED |
| Project | Movie Review Meeting | CONTAINER |
| Project | Remote Control Design Project Retrospective | CONTESTED |

## `--decide` output (verbatim)

```text
### Stored population

- records: 201 of 228 have a framing-shaped source
- meeting-titled objects on those: 379; by type {'Event': 376, 'Decision': 2, 'Project': 1}

| arm | dropped | false positives | container stubs leaked |
|---|---|---|---|
| `before` | 379 of 379 | 1 of 379 | 0 of 379 |
| `opt1` | 377 of 379 | 0 of 379 | 1 of 379 |
| `opt2a` | 376 of 379 | 0 of 379 | 2 of 379 |
| `opt2b` | 377 of 379 | 0 of 379 | 1 of 379 |
| `shipped` (production, equals `before`) | 379 of 379 | | |

Non-Event objects the `before` arm drops:

- Decision `Assignment of Next Meeting Tasks` (subject), concept-screen/records/qwen3.6:27b/IS1004b/r1
- Project `New Product Development Meeting` (container), concept-screen/records/qwen3:14b/ES2015a/r1
- Decision `Decisions Made in Previous Meeting` (container), runtime-screen/concept-replay/records/qwen3:8b@llama.cpp/IS1004d/r2

### New population

- records: 138 of 138 have a framing-shaped source
- meeting-titled objects on those: 430; by type {'Event': 413, 'Decision': 12, 'Project': 3, 'Entity': 1, 'Procedure': 1}

| arm | dropped | false positives | container stubs leaked |
|---|---|---|---|
| `before` | 429 of 430 | 1 of 430 | 0 of 430 |
| `opt1` | 417 of 430 | 0 of 430 | 11 of 430 |
| `opt2a` | 413 of 430 | 0 of 430 | 15 of 430 |
| `opt2b` | 416 of 430 | 0 of 430 | 12 of 430 |
| `shipped` (production, equals `before`) | 429 of 430 | | |

Non-Event objects the `before` arm drops:

- Decision `Next Meeting Planning` (container), concept-screen/records/gemma4:26b-a4b/IB4010/r4
- Decision `Next meeting schedule` (container), concept-screen/records/gemma4:26b-a4b/IB4011/r1
- Decision `Next Meeting Schedule` (container), concept-screen/records/gemma4:26b-a4b/IB4011/r3
- Decision `Meeting Environment Improvements` (container), concept-screen/records/gemma4:26b-a4b/IS1002d/r4
- Project `Remote Control Design Project Retrospective` (container), concept-screen/records/gemma4:26b-a4b/TS3012d/r2
- Entity `Next meeting schedule` (container), concept-screen/records/qwen3.6:35b-a3b/IB4003/r1
- Procedure `Use of Meeting Rooms vs. Lounges` (subject), concept-screen/records/qwen3.6:35b-a3b/IB4003/r2
- Decision `Next Meeting Date Schedule` (container), concept-screen/records/qwen3.6:35b-a3b/IB4010/r3
- Decision `Scheduling Strategy for Next Meeting` (container), concept-screen/records/qwen3.6:35b-a3b/IB4010/r4
- Project `Movie Review Meeting` (container), concept-screen/records/qwen3.6:35b-a3b/IB4011/r1
- Decision `Selection of film for April screening and meeting details` (container), concept-screen/records/qwen3.6:35b-a3b/IB4011/r1
- Decision `Scheduling Next Meeting for First Monday of May` (container), concept-screen/records/qwen3.6:35b-a3b/IB4011/r3
- Decision `Scheduling of the May meeting` (container), concept-screen/records/qwen3.6:35b-a3b/IB4011/r4
- Project `Cost and Criteria Review Meeting` (container), concept-screen/records/qwen3.6:35b-a3b/IS1002d/r1
- Decision `Meeting Room Equipment Feedback` (container), concept-screen/records/qwen3.6:35b-a3b/IS1002d/r3
- Decision `Postponement of Meeting Due to Schedule Conflict` (container), concept-screen/records/qwen3.6:35b-a3b/IS1005b/r4
- Decision `Project retrospective on financial and material constraints` (subject), concept-screen/records/qwen3.6:35b-a3b/TS3012d/r3

{
  "verdict": "INCONCLUSIVE",
  "n_new_objects": 430,
  "arms": {
    "opt1": {
      "removed_new": 1,
      "removed_pooled": 2,
      "distinct_titles": 2,
      "leaks_pooled": 12,
      "passes": false
    },
    "opt2b": {
      "removed_new": 1,
      "removed_pooled": 2,
      "distinct_titles": 2,
      "leaks_pooled": 13,
      "passes": false
    },
    "opt2a": {
      "removed_new": 1,
      "removed_pooled": 2,
      "distinct_titles": 2,
      "leaks_pooled": 17,
      "passes": false
    }
  },
  "fp_new_subject_reading": 6
}
```

(The "Non-Event objects the `before` arm drops" lists are printed as the
harness emits them, including its `Procedure` line.)

## Verdict: INCONCLUSIVE

Applying the registered rule mechanically:

- ADOPT (rule 1), judged under the container reading, fails for every arm.
  The new population holds one false positive that each arm removes
  (`Project retrospective on financial and material constraints`, SUBJECT) and
  the pooled removed count is 2 with 2 distinct titles, so the first two
  conditions hold. The third fails: pooled `leaks` are 12 (`opt1`), 13
  (`opt2b`) and 17 (`opt2a`), each above the 2 removed.
- KEEP (rule 2), judged under the subject reading, fails: the old rule
  deleted 6 subjects from the new population under that reading (the 1
  SUBJECT object plus the 5 CONTESTED ones), not none.
- Hence INCONCLUSIVE ("a new false positive appeared but the pooled counts
  do not satisfy rule 1: the leaks outweigh"). #1575 stays open, and any
  further generation is a new registration.

Neither outcome depends on the contested objects: with all 5 read as
containers there is still exactly one new false positive and the leaks still
outweigh the removals, so ADOPT fails; with them read as subjects KEEP fails.
No production change is adopted by this PR. Had the verdict been ADOPT, a
production change would have to be opened separately with its own ADR.

## Limits

The classification is by an AI agent, not the owner. Distinct
(source, type, title) triples are not reproduced beyond what `--decide`
printed. Nothing AMI-derived beyond titles and counts is committed.
