# Results: the Source fallback on long AMI Sources (#1741, second registration)

Registration: `PREREGISTRATION-long.md` in this directory. Harness:
`measure_source_fallback_long.py`. Core fixture hash `7d799c74462ebaefb7ae5284700d0c8c0711646fc16497a0193b89c5192a8209` (needs only the gold CSVs and the transcripts); full fixture hash `22ff6ef42eb7438ec547e505bc407f1220a5aee05ffab04695fc2b2d7bb3ec7f` (adds the glosses).

## Verdict

**NO-REACH-PRE. No model was called; nothing was run on GPU.** The registered model-free
reach gate (`ORACLE_REACH_MIN = 0.5`) failed on the real fixture, so `run` and `fallback`
refuse to start. The result comes from `exposure` alone, which reads only the gold CSVs and the
transcripts.

## Readout (model-free)

| | Value |
|---|---|
| Prompt budget | 8719 chars (window 12288, reply reserve 8192, overhead rule) |
| Fallback share (3 Sources) | 2906 chars |
| Shipped share (4 Sources) | 2179 chars |
| Sources exceeding their share | 12 of 12 (smallest 2.9 times, median 11.9 times) |
| Source sizes (chars) | 8289 to 42686, median 34448 |
| **Oracle reach, fallback share** | **0.467 (14 of 30)** |
| **Oracle reach, shipped share** | **0.333 (10 of 30)** |

Oracle reach is the share of questions whose query-aware excerpt of their own Source (the
right Source chosen, retrieval aside) holds the gold evidence act.

**The Spanish readout and why it was replaced.** The first draft derived a Spanish question
from the gold object over the English transcripts. Its oracle reach was 0.133 at the fallback
share and 0.033 at the shipped share (4 and 1 of 30). The BM25 excerpt has almost no lexical
signal across languages, so that measured the mismatch, not the failure #1741 reports. The owner
replaced it with 30 hand-authored English questions (2026-11-10) and added the reach gate. The
gate was therefore written after the Spanish readout. The English readout was taken once,
before any revision, and the questions were not changed afterwards.

## Per-question reach

Selection: the registered SHA-256 selection of three eligible gold rows per meeting.

| Row | Question | Fallback share | Shipped share |
|---|---|---|---|
| ES2015a.d01 | In ES2015a, what kind of product is the team being asked to develop? | miss | miss |
| ES2015a.d02 | In ES2015a, what selling price does the project manager want the team to aim for? | hit | hit |
| ES2015a.d05 | In ES2015a, what upper limit does the project manager set for the cost of making it? | hit | hit |
| ES2015b.d02 | In ES2015b, what position has the management taken on putting teletext in the remote? | hit | miss |
| ES2015b.d03 | In ES2015b, what branding element does the project manager require on every remote? | miss | miss |
| ES2015b.d05 | In ES2015b, what does the project manager conclude about the alarm idea for finding a lost remote? | miss | miss |
| ES2015c.d03 | In ES2015c, which feature from the earlier discussion does the marketing expert want to stick to? | miss | miss |
| ES2015c.d04 | In ES2015c, how will the remote be powered, according to what the team decided? | miss | miss |
| ES2015c.d08 | In ES2015c, which functions does the team agree the remote should offer? | hit | hit |
| ES2015d.d03 | In ES2015d, what does the project manager suggest about how many different shades the product should come in? | miss | miss |
| ES2015d.d05 | In ES2015d, what shape does the project manager describe for the remote? | miss | miss |
| ES2015d.d06 | In ES2015d, what is the remote going to be made of? | miss | miss |
| IS1004a.d01 | In IS1004a, what is the stated goal of the project? | hit | hit |
| IS1004a.d02 | In IS1004a, what selling price does the project manager say the team should target? | hit | hit |
| IS1004a.d03 | In IS1004a, in which part of the world should the team aim to sell? | hit | hit |
| IS1004b.d01 | In IS1004b, what does the head office want the remote to be restricted to? | hit | hit |
| IS1004b.d05 | In IS1004b, did the team decide to add technology that lets users operate the remote by talking? | miss | miss |
| IS1004b.d06 | In IS1004b, what elements should the product visibly carry to show which company made it? | hit | hit |
| IS1004d.d02 | In IS1004d, what does each of the two wheels on the prototype do, and on which side is it? | hit | hit |
| IS1004d.d04 | In IS1004d, which communication component does the prototype include for talking to the television? | miss | miss |
| IS1004d.d10 | In IS1004d, which grade of chip did the industrial designer say was chosen? | miss | miss |
| TS3009a.d01 | In TS3009a, what did the project manager ask each team member to do before the next meeting? | miss | miss |
| TS3009b.d02 | In TS3009b, which idea for the casing did the user interface designer want to pursue further? | hit | miss |
| TS3009b.d03 | In TS3009b, which design philosophy did the participants prefer? | miss | miss |
| TS3009b.d06 | In TS3009b, did the team include a dedicated control for entering two-digit channel numbers? | miss | miss |
| TS3009c.d04 | In TS3009c, where on the remote did the user interface designer say the screen should go? | hit | miss |
| TS3009c.d06 | In TS3009c, what extra feature did the project manager mention while reviewing the function list? | miss | miss |
| TS3009c.d08 | In TS3009c, what finishing touch was suggested for the front of the remote? | hit | hit |
| TS3009d.d01 | In TS3009d, where did the team decide to leave the display, given the prototype they looked at? | miss | miss |
| TS3009d.d03 | In TS3009d, what happened to the titanium idea? | hit | miss |

## Reading

On long Sources the query-aware excerpt misses the gold evidence for most answerable
questions even with the right Source chosen: 16 of 30 at the fallback share and 20 of 30 at the
shipped share. The bounded fallback widens the per-Source share only 1.33 times (3 Sources at
2906 chars against 4 at 2179) and lifts reach by 4 questions (10 to 14 of 30), so it cannot
fix the failure mode on this fixture. The bottleneck is the construction of the excerpt, not the
re-check or the synthesis.

## What this does not establish

Model-side answer rates: no model was run, so the base refusal rate, the fallback's recovery,
B0 to B6 and false answers on the unanswerable questions are all unmeasured. It does not
establish reach for other questions: the 30 questions were written by the harness author, who
knew the excerpt rule, and the glosses are the author's paraphrase. It is one model-free
measurement on three development series, one window and one share rule.

## Reproducibility

The scoring glosses paraphrase private gold annotations, so they live in a gitignored local
file (`evals/source_fallback/local/glosses.json`), not in the repository. Without it,
`exposure` reproduces the Source sizes, the oracle reach (0.467 and 0.333), the per-question
table and the core hash, and reports the leak check and the full hash as `skipped: no glosses
file`. The leak check, the scoring and the full hash cannot be reproduced or audited without
the file. The verdict does not depend on the glosses: the reach gate reads questions, Sources
and the gold evidence only.

## Next

A separate issue on the excerpt strategy for long Sources (how the query-aware excerpt chooses
windows when the Source is many times its share). The fallback is worth re-registering only after
the excerpt reaches the evidence for most questions.
