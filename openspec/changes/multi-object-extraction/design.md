# Design: extraction decides multiplicity per object, not per source

Slice 1 measured. The title anchor causes the **twin**, not the **count collapse**. The collapse is
the rubric's per-source framing, so slice 2 edits `_SYSTEM_PROMPT` on three axes one at a time, and
the title is **reframed as metadata inside `_build_messages`** — not withheld, so the ADR gate is
not crossed.

## Slice 1 evidence base (the D1 verdict)

Decisive run: `evals/model_spike/results/title-ab-20260804T151148Z-qwen3-8b.md` (immutable),
`qwen3:8b`, arms `h1`/`stem`, 5 runs × 18 sources = 180 calls, `_SYSTEM_PROMPT` byte-identical,
2 `stem` runs lost to Ollama timeouts (88/90).

| Finding | Measurement | Reading |
|---|---|---|
| Anchor changes **what** | `twin_rate` h1 **0.34** vs stem **0.13** | The H1-derived title makes objects restate the source heading — #377's twin. D1 **implicated** |
| Anchor does not change **how many** | `multi_obj_rate` h1 **0.09** vs stem **0.12** (means 1.19/1.32) | Both arms collapse to ~1. D1 **exonerated** as the cause of the collapse |
| Per-type "ONE" wording is a hard cap, not the cause | named-entity side n=56, **every run exactly 1**, zero variance; Concept/Entity n=118, mean 1.42, max 5, 19 enumerated | Explains the capped runs only — the exempt side still mostly yields 1. Consistent with D2 |
| Fixture reproduces | `call-with-maria` (declared 3) → **1 object in 10/10** runs, both arms | The acceptance target is genuinely unmet, not a scoring artifact |

Residual cause, per D2: the shared framing at `concept.py:37` — "Classify by what **the source** is
fundamentally about" — is a question with exactly one answer by construction.

## Architecture decisions

### DD1 — Title framing: reframe as metadata; do not withhold

| Option | Trade-off | Decision |
|---|---|---|
| Keep `SOURCE TITLE:` as-is | Twin persists at 0.34; D4 alone must fight an anchor still framed as the answer | Rejected |
| **Reframe the label as non-authoritative metadata in `_build_messages`** | Prompt-text-only; keeps the ingest value; measurable against the same h1 baseline | **Chosen** |
| Withhold the title entirely | Would be a real ingest↔extraction interface change (extraction stops consuming `derive_source_title`), and the `none` arm was **not** run in the decisive run — zero evidence supports it | Rejected (unevidenced) |

**ADR gate**: not crossed. The chosen edit changes one f-string label inside `concept.py`; the
value, its producer, and `main.py:2688` are untouched, so nothing about the ingest↔extraction
interface moves. The proposal's "no ADR" stance stands (proposal lines 121–124). Had we withheld
the title, an ADR would have been required — that is the recorded reason we did not.

### DD2 — Three prompt axes, one at a time, each with its own measurement gate

| Axis | Exact lever | Edit shape (wording deferred to slice 2 TDD) | Gate |
|---|---|---|---|
| D2 | `concept.py:37`, the framing line above the nine bullets | Re-point the rubric from the document to the candidate: the model first identifies candidate objects, then applies the rubric to **each** one. The nine bullets and `CLASSIFIABLE_TYPES` stay | `multi_obj_rate` rises above the 0.09 h1 baseline; `call-with-maria` produces >1 |
| D3 | New paragraph adjacent to the anti-enumeration block (`concept.py:122–134`), before the positive default | A stated test separating single-topic from multi-topic documents. Positive rule added; nothing deleted | `call-with-maria` reaches 3 (`Person` + `Concept` + `Decision`); instructional sources still yield ≥1 |
| D4 | New clause after the anti-enumeration paragraph | An object whose title merely restates the SOURCE heading and scope is not distinct | `twin_rate` falls below 0.34 without `multi_obj_rate` regressing |

Ordering is load-bearing (#129 swung three levers at once and landed on the opposite defect). Each
axis is committed and measured before the next.

**Measurement mechanism, no harness change**: `run_title_ab.py` imports the real
`concept._SYSTEM_PROMPT`, so re-running `--arms h1` over the same corpus/model/seed count after each
edit is a before/after against the frozen snapshot above. The h1 column of
`title-ab-20260804T151148Z-qwen3-8b.md` **is** the pre-edit baseline.

### DD3 — Restraint is structurally pinned, not trusted

These three tests must pass **unedited** and are the structural mitigation for the pendulum risk
(#129 reopening, shallow enumeration):

- `test_prompt_does_not_reinstate_the_empty_array_escape_hatch`
- `test_prompt_states_the_positive_extraction_default`
- `test_prompt_contains_anti_enumeration_paragraph_verbatim`

Because the third pins the anti-enumeration paragraph **verbatim**, D3 and D4 must be *additive*
paragraphs placed around it, never edits inside it. That constraint is what makes "restraint
preserved" checkable rather than aspirational.

**Prompt-size risk (8B adherence)**: net added text is a review criterion. Each axis is measured
alone, so a clause that costs more adherence than it earns is visible and reverted rather than
accumulated. Budget: the three axes together add no more than ~15% to the constant (6,573 bytes
as measured on the pre-edit baseline).

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Edit the seven per-type "ONE specific, named X" bullets | The probe shows a real hard cap there (n=56, zero variance), but `concepts/stoicism.md` is a **Concept** — an exempt type — so this alone cannot move the fixture. May be revisited after D2 if the named-entity side stays capped |
| Chunk/segment the source | Out of scope per proposal; multiplicity is a classification decision over one whole text |
| Lower/raise `_MAX_OBJECTS_PER_SOURCE` | 5 is not what produces 1; it is the untouched post-hoc safety ceiling and the field rollback lever |
| Rewrite all three axes at once | Exactly the #129 failure mode |

## Data flow

    ingest (main.py:2688) ──SOURCE TITLE(H1)──┐
                                              ▼
    raw text ─────────────────────► _build_messages ──► [system: _SYSTEM_PROMPT, user]
                                    (DD1: label reframed)          │
                                                                   ▼
                                                           LLMBackend.chat
                                                                   │
                                    _validate (per item) ◄── parsing.extract_json_items
                                                                   │
                                                     results[:_MAX_OBJECTS_PER_SOURCE]

Unchanged: `extract_concept`'s signature, return contract, per-item fail-closed validation, the cap,
staging, slug disambiguation, merge/provenance (D5).

## File changes

| File | Action | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modify | `_SYSTEM_PROMPT` (D2–D4), `_build_messages` title label (DD1), module/constant docstrings |
| `openspec/specs/ingestion/spec.md` | Modify (delta) | Per-object framing, multiplicity requirement, anti-twin requirement; `[]`-only-for-empty restated |
| `tests/unit/extraction/test_concept.py` | Modify | RED prompt-text assertions per clause + multi-object fake-backend tests; the three alarms unedited |
| `evals/model_spike/report-title-ab.md`, `results/` | Modify/Add | Per-axis re-runs; the timestamped snapshot is immutable |
| `src/openkos/cli/main.py` | **Unchanged** | DD1 keeps the title value and its producer |

## Testing strategy

| Layer | What | How |
|---|---|---|
| Unit (offline) | Every new prompt clause exists; multi-object replies parse to N results | Prompt-text assertions + fake `LLMBackend`, strict TDD (RED first) |
| Regression | #129 not reopened | The three named tests, unedited |
| Behavioral (evals) | Multiplicity, twin suppression, `[]` for empty | `run_title_ab.py --arms h1` before/after each axis, vs the frozen baseline |
| Acceptance | `call-with-maria` → Person + Concept + Decision | Labeled fixture in the harness, reflected as a spec scenario |

## Threat matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. This change is one string constant, its tests, and a spec delta.

## Migration / rollout

No migration. Already-ingested sources keep their single derived object until re-ingested.
Rollback is a one-file revert of `concept.py`; `_MAX_OBJECTS_PER_SOURCE` remains the field lever.

## Open questions

- [x] If D2 lifts the exempt side but the named-entity side stays capped at exactly 1, do the seven
      per-type bullets come into scope as a fourth axis, or into a follow-up change? **Answered
      (maintainer decision, Jason, 2026-08-04): fourth axis, in this change (Phase 4b), not a
      follow-up.** Evidence: gate run `20260804T170255Z` — D3 failed on its central criterion
      (`call-with-maria` still 1,1,1; empties still 3 of 24) while showing every multi-object run
      of the whole campaign was Concept/Procedure only and every named-entity-typed source was
      pinned at exactly 1 with zero variance. Mechanism: the seven per-type bullets still phrased
      per-source aboutness ("the source is fundamentally about ONE specific, named X"), inconsistent
      with D2's per-candidate framing — explaining both the cap and the empties in one mechanism, so
      deferring it would leave D3's own gate unexplained.
- [ ] Is a `none`-arm run worth commissioning as a control after D4, purely to confirm the reframed
      label captured the anchor's benefit without losing the title's information?
