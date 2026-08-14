# Design: First-class Person/Organization participants

## Technical Approach

Phase 1a adds a **direction-scoped, anchor-gated, transcript-scoped re-admission** of `Person`/`Organization` at the judge seam in `extract_concept_union`, reusing the proven D5 path. Phase 1b extends `run_type_coverage.py` into the measured participant gate. Phase 2 (a scoped capture pass) is designed but opens only on phase-1b evidence. Measure-first (#613/#622): no `_SYSTEM_PROMPT` edit ships in phase 1.

## Architecture Decisions

### D1: Do NOT generalize `_TWIN_EXEMPT_TYPE` — add a separate additive set

**Discovery**: `_TWIN_EXEMPT_TYPE` has **three** call sites, not the one the exploration named:

| Line | Site | Direction |
|---|---|---|
| 821 | `_is_droppable_source_title_twin` | **deletion** exemption |
| 1010 | `_drop_framing_objects` | **deletion** exemption |
| 2234 | judge re-admission | **additive** |

**Choice**: leave `_TWIN_EXEMPT_TYPE = "Procedure"` byte-identical; introduce `_JUDGE_READMIT_TYPES: Final = frozenset({"Procedure", "Person", "Organization"})` used **only** at line 2234.
**Alternatives**: (a) widen the constant to a frozenset everywhere (proposal's literal wording); (b) one set, per-site subtraction.
**Rationale**: (a) is a silent regression — exempting `Person` from `_drop_framing_objects` would retain a Person titled `"Team Meeting"` as a framing stub, the exact defect #522/#533 measured, and exempting it from the twin rule re-opens title-twin deletion. The module's standing rule is explicit (`concept.py:786`): *the deletion keeps its own, stricter predicate*. Additive and deletion consumers must not share this predicate.

### D2: judge.py unchanged

Type-blind by construction; re-admission stays in `concept.py`. No change.

### D3: Transcript-shape detection = existing `_MEETING_SHAPED_TITLE_RE`

**Choice**: gate re-admission on `_MEETING_SHAPED_TITLE_RE.search(source_title)`. `source_title` is already in scope at line 2234.
**Alternatives**: content-shape heuristic (speaker-turn density); a new source-kind field.
**Rationale**: reuses the single, measured, tight-by-design gate that already scopes `_build_messages` and `_drop_framing_objects` — one definition of "meeting-shaped", no drift. Enforces the SCOPE RULE: a `Person` from a non-transcript source is **never** re-admitted. Its known English/Spanish-only lexicon (#522) is the accepted, greppable limit.

### D4: Stub predicate = additional conjunct on re-admission only

**Choice**: `_has_participant_anchor(result) -> bool` in `concept.py`, reading `description`+`body` for a role / affiliation / relation cue via a tight `_PARTICIPANT_ANCHOR_RE`. Composition at line 2234:

```python
or (
    c.type in _JUDGE_READMIT_TYPES
    and (c.type == _TWIN_EXEMPT_TYPE or (meeting_shaped and _has_participant_anchor(c)))
)
```

**Alternatives**: a global post-filter over all retained objects; a judge-side floor.
**Rationale**: a global filter would **delete** a Person the judge itself selected — a deletion, which per D1 needs its own stricter predicate and its own measurement. Re-admission is additive, so it carries the tighter test. Anchor-less name-only candidates are simply not re-admitted (discarded, per the owner STUB RULE), and `Procedure`'s existing behavior is provably unchanged.

### D5: Probe reports recall **and** precision-side counts

Extend `run_type_coverage.py`: per-run `Person`/`Organization` emitted counts, per-meeting unexplained-absence verdict (already present), plus new **stub-flooding guard** rows — re-admitted-vs-judge-selected counts and anchor-less discard counts, sourced from new `ExtractionReport` fields. `--participants` flag; `_self_test()` gains a two-meeting fixture asserting the flooding guard fires. Baseline recorded in `evals/decision_extraction/report.md`.

### D6: Phase 2 — conditional, trigger-gated

**Trigger**: phase-1b baseline still shows zero raw `Person`/`Organization` on ≥2 AMI meetings across ≥3 runs. **Architecture**: a scoped second call shaped like `_reask_for_further_subjects`/`_add_reask_subjects` (#584), gated on the same `_MEETING_SHAPED_TITLE_RE` source predicate, joining candidates **before** the judge. `_SYSTEM_PROMPT` untouched. Does not open unless the trigger fires.

### D7: #602 / #645 trace finding (closes proposal risk 3)

Traced this pass; **no gap found**:

| Surface | Finding |
|---|---|
| `_scrub_entry_snapshots` (`cli/main.py:638`) | Type-blind. Keys on purge ids and NFC-normalized filenames; excises `## Merged content (<id>)` sections structurally. Composes with Person/Organization unchanged. |
| `_reconcile_merged_survivor` (`cli/main.py:8168`) → `reconcile_merged_body` | Type-blind. Splits on the same structural separator, rebuilds via `okf.dump_frontmatter(metadata, ...)` — frontmatter `type: Person` is preserved verbatim. |

Neither reads `ObjectType`. Reconciliation only fires on merge, and person-merge is deferred, so no participant-specific reconciliation work exists in this change.

### D8: Identity seam (deferred, named only)

Person-name identity would plug in at `resolution/similarity.py` as a **companion** predicate, surfaced through `suggest-relations --apply` (#560/#483). Not designed here.

## Data Flow

    extract_once ─→ strip/framing/twin filters ─→ _add_reask_subjects ─→ judge.select
                                                                            │
                            _JUDGE_READMIT_TYPES × meeting_shaped × anchor ─┘
                                                                            ↓
                                                        _UNION_BACKSTOP ─→ ExtractionOutcome

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modify | `_JUDGE_READMIT_TYPES`, `_PARTICIPANT_ANCHOR_RE`, `_has_participant_anchor`, line-2234 conjunct, `ExtractionReport` counters |
| `tests/unit/extraction/test_concept.py` | Modify | RED tests per D1/D3/D4 |
| `evals/.../run_type_coverage.py` | Modify | Participant scoring + flooding guard + self-test |
| `evals/decision_extraction/report.md` | Modify | Recorded baseline |
| `src/openkos/extraction/judge.py` | Unchanged | D2 |

## Testing Strategy (Strict TDD — RED first, mutate before trusting)

| Layer | What | Approach |
|---|---|---|
| Unit | Person re-admitted after judge drop, meeting-shaped source | Mirror `test_union_procedure_survives_judge_rejection_via_deterministic_readmission` |
| Unit | Person **not** re-admitted from a non-transcript source | Scope-rule guard |
| Unit | Anchor-less Person stub **not** re-admitted; anchored Person is | Stub-rule pair |
| Unit | `_drop_framing_objects` still drops a meeting-titled Person | **D1 regression guard** — the twin-rule floor needs two guards |
| Unit | `_JUDGE_READMIT_TYPES ⊆ CLASSIFIABLE_TYPES` | Extends `test_twin_exempt_type_is_in_the_vocabulary` |
| Unit | `Procedure` behavior byte-identical at all three sites | Non-regression |
| Eval | Participant recall + flooding counts | `--self-test` (no model), then measured run |

Every new test must be mutated against its exact target line (purge `__pycache__`) before it is trusted.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

## Migration / Rollout

No migration. Phase 1a reverts by deleting the added conjunct. Phase 1b is eval-only. Phase 2 reverts by removing its trigger.

## Delivery Slicing (auto-chain, 2500-line budget)

| PR | Scope | Est. lines |
|---|---|---|
| 1 | D1/D3/D4 + tests | ~300 |
| 2 | D5 probe + recorded baseline | ~250 |
| 3 | D6 pass — **conditional on PR2** | ~350 |

Each slice is independently revertible and measurable. Total well under budget.

## Open Questions

- [ ] `_PARTICIPANT_ANCHOR_RE` lexicon scope: English+Spanish at launch (mirroring #522's fix) vs English-only. Resolve in tasks; either way the limit is documented at the constant.
