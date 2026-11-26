# Archive Report: adopt-gemma4-sufficiency-default

**Archived**: 2026-11-26
**Archive folder**: `openspec/changes/archive/2026-11-26-adopt-gemma4-sufficiency-default/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1560. Implemented and merged in PR #1585 (2026-10-20); archived late.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| query-answer | Updated | 1 ADDED requirement appended: The Sufficiency Check May Run On Its Own Client (5 scenarios). |
| query-command | Updated | 3 ADDED requirements appended: The Sufficiency Check Runs On The `sufficiency` Task Model (3 scenarios), A Missing Sufficiency Model Degrades The Check, Not The Query (1 scenario) and `--json` Names The Sufficiency Model (1 scenario). |
| doctor-command | Updated | 1 MODIFIED requirement replaced in place: Task-Models-Installed Check. Only the scenarios changed (the packaged judge model is also the default for `sufficiency`; the opt-out names all three tasks). No ADDED, REMOVED or RENAMED headings. |

None of the ADDED requirements existed in the living specs, and the living Task-Models-Installed text was the pre-change version, so nothing was regressed or duplicated. No conflicts found.

## Requirement-vs-Code Check

Checked against shipped code: `config.DEFAULT_TASK_MODELS["sufficiency"] = DEFAULT_JUDGE_MODEL` (`gemma4:26b-a4b`) with `sufficiency` in `TASK_MODEL_KEYS`; `sufficiency_model_missing` in `retrieval/answer.py`; `sufficiency_model` in `application/query_report.py`; the missing-model notice with the `models: {sufficiency: null}` opt-out in `cli/main.py`.

## ADR

ADR-0076 already read `Accepted` in its frontmatter, its `**Status:**` line and the `docs/adr/README.md` index; no status edit was needed.
