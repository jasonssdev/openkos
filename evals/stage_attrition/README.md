# `stage_attrition` — which pipeline stage kills the subjects? (#715)

#715 established that meeting-shaped sources retain people and no subjects,
across two harnesses and 21 runs. It named three suspects and proved none,
because all three predict the same final state. This probe records the
candidate set entering and leaving **every** transforming stage, so the answer
is read off a ledger instead of argued from an outcome.

```bash
uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --self-test
uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --runs 3
uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --fixture es-anchored
uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --arm both --runs 3
uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --rescore results/<file>.jsonl
```

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks
hung.

## How it observes

Each stage is wrapped with a recorder that delegates to the real function.
**No production file is modified** and the pipeline runs exactly as shipped —
the technique `evals/participant_anchor` used on one seam, widened to the
chain: `_extract_once`, `_strip_ungrounded_expansions`,
`_drop_framing_objects`, `_merge_union`, `_dedup_merged`,
`_drop_source_title_twins`, `_drop_wrong_language_titles`,
`_add_reask_subjects`, `_add_participant_capture`, and the judge.

`install()` asserts every target attribute exists first. A renamed stage would
otherwise be patched into nothing, read as a no-op in the ledger, and exonerate
itself.

## Reading the ledger

Every row shows `subject in→out` and `participant in→out` side by side, with
the titles dropped. Both lanes always appear together: a view showing one class
hides its own complement, which is exactly why #715 sat unnoticed inside
`evals/participant_anchor`'s stored data for a day.

`_merge_union` and `_dedup_merged` are labelled as deduplication. The union
path runs extraction twice, so every genuine subject enters `_merge_union`
twice and its count legitimately halves; counting that as a loss would put a
merge step at the top of a table headed "where subjects die". The tally names
the **eliminating** stages separately.

## Fixtures

`es-anchored` and `es-bare` are imported from
`evals/participant_anchor/run_participant_anchor_probe.py` rather than copied.
#715's evidence was produced on those exact transcripts, and a second copy
would drift silently, leaving two fixtures with one name. `ami-ts3005a` is the
real AMI transcript, present only after `decision_extraction`'s
`build_sources.py` has run.

Each fixture carries a hand-written `known_subjects` list — what the source
demonstrably discusses. It is **not** a recall target; it exists so a reader
can judge whether the candidates a stage killed were worth keeping, which a
bare count cannot say.

## Arms and the gate

`--arm both` measures the shipped prompt against the shipped prompt plus one
ADDITIVE clause, spliced at the exact position the clause would ship so the
measurement is of shippable text and not a paraphrase. The splice anchor is
asserted: an unspliced arm would measure the baseline twice and read as "the
treatment does nothing".

The gate's five conditions decide whether the treatment may ship. Four score
COMPLETED runs; the fifth counts the runs each arm could not complete, per
fixture, and exists because the other four scored the first real sweep as
shippable while the treatment failed every run on the largest fixture.
Averages over completed runs get *better* when a hard run crashes out of them.

Condition 5 counts runs rather than asking whether a fixture failed outright:
the failure it guards (#714) is intermittent, so an all-or-nothing test is
clear on exactly the regime that issue reports.

`--rescore` re-reads a stored sweep through the current gate with no model
calls. Add a gate condition and re-score the existing evidence with it —
re-running the models would change the sample and the gate at the same time.

## What it measured

See `report.md` for the diagnosis and `report-treatment.md` for the fix
attempt.

Diagnosis: nothing kills the subjects — generation never produces them. On
meeting-shaped sources `_extract_once` returns exactly one subject candidate
per call, the Event named after the meeting, which `_drop_framing_objects`
then correctly deletes.

Fix attempt: REJECT, on condition 5. The clause lifted subject retention from
0/9 to 1/6 with zero fabrications, and made a 16 KB transcript blow the 8192
generation ceiling on 3 of 3 runs (#714). Blocked, not refuted — re-measure
after #714 lands.

`report-treatment.md` also records what the review of that measurement caught:
condition 5's first version could not see an intermittent break.
