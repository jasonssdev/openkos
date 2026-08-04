# Proposal: extraction decides multiplicity per object, not per source

**Issue**: [#377](https://github.com/jasonssdev/openkos/issues/377) (P0).
**Baseline**: `main` @ `cbc601e` (v0.2.1). **Mode**: hybrid.

## Intent

A complete end-to-end run over 15 sources produced **15 derived objects — exactly one each,
without exception**. The repository's own acceptance fixture contradicts that:
`examples/good-life-demo/` declares that `raw/call-with-maria-2026-07-14.txt` yields three objects
(`people/maria-salazar.md`, `concepts/stoicism.md`, and
`decisions/frame-the-essay-on-the-dichotomy-of-control.md`); today it yields one Person.

At 1:1 the product's central claim fails. Every source produces a **twin carrying the source's own
title** — `sources/05-workflow` "Workflow" sitting beside `concepts/workflow` "Workflow", fifteen
times over — so the bundle is a mirror of the sources directory rather than a compilation of it.
Nothing synthesises across sources, which is the whole justification for compiling instead of
retrieving. It also starves every floor built on top: entity resolution has nothing to resolve
beyond exact-title collisions, the typed graph has no synthesis to represent, and contradiction
detection mostly compares a source against its own reflection.

This change restores multiplicity **without reopening the `[]` defect that #129 fixed**. The fix is
not the removal of restraint; it is giving the model a decision procedure for *when a source carries
several distinct objects*, which the prompt currently never states.

## Decisions settled here

| # | Decision | Rationale |
|---|---|---|
| D1 | **There are two candidate levers, and the issue names only one. Both are in scope; which one carries the regression is settled by experiment before any prompt is rewritten.** | `_SYSTEM_PROMPT`'s last change is `e2669c6` (#129), and `git tag --contains e2669c6` returns **v0.1.2, v0.2.0 and v0.2.1**. Stronger still: `git diff v0.2.0 v0.2.1 -- src/openkos/extraction/` is **empty** — not merely the prompt but the whole extraction module is byte-identical across the two releases being compared, so *it cannot by itself explain a 3→1 regression between them*. What did change is `7f29cdd` (#248, in v0.2.1 only): the `SOURCE TITLE:` value handed to the user turn moved from `_titleize(src.stem)` (`"01 What Is Claude Code"`) to the document's own H1 (`"Claude Code"`), and that commit's own message names `_stage_derived_objects`' LLM prompt among the consumers the single assignment feeds. The system prompt's framing line (`concept.py:37`) instructs the model to "Classify by what **the source** is fundamentally about"; the user turn now hands it a pre-computed, authoritative answer to exactly that question. The twin phenomenon — a derived object echoing the Source's title verbatim — is what that anchor predicts. |
| D2 | **The rubric is re-pointed from the source to the candidate object. The lever is the shared framing line, not the per-type wording.** | The per-source framing enters **once, above the list**: `concept.py:37` says "Classify by what the **source** is fundamentally about", and it governs all nine types. Seven of the nine then repeat it verbatim — *"the **source** is fundamentally about ONE specific X"* (Person, Organization, Place, Event, Procedure, Decision, Project); the code's own comment at `concept.py:68` records the same count. **`Concept` and `Entity` do not carry that phrasing at all** ("the source describes an idea, topic, theory…" and "a fallback for a concrete tool, product, or artifact"). That exemption matters: `concepts/stoicism.md`, one of the three objects the fixture declares and does not get, is a **Concept** — so the per-type "ONE" wording is demonstrably *not* what suppresses it, and editing those seven bullets alone would not move the fixture. What must be re-pointed is line 37. Even under the preamble's "apply the rubric to EACH object independently", the question the model actually answers is "what is this document about?", which has exactly one answer by construction. A rubric phrased over a candidate object can be applied N times; a rubric phrased over the document cannot. |
| D3 | **Restraint is preserved and re-expressed, never removed. The prompt gains a positive multiplicity rule, not a weaker suppression one.** | #129's forces are still live: three stacked suppression levers made `qwen3:8b` return a bare `[]` for any source without a named subject. The current text sets a floor ("AT LEAST ONE object") and gives the canonical example of one ("A document explaining one topic usually yields exactly ONE object"), but **never describes when a document should yield several**. The correction is a stated test for distinguishing single-topic from multi-topic documents — not deleting the anti-enumeration paragraph, which remains load-bearing against the opposite failure. |
| D4 | **A derived object that merely restates its Source's title and scope is not a distinct object.** | This is the twin, stated as a rule the model can apply. It is the one new suppression clause this change adds, and it is precisely orthogonal to multiplicity: it removes the useless object, never a genuine second one. |
| D5 | **Synthesis is unblocked here, not built here.** | The fixture's `concepts/stoicism.md` carries provenance from **both** raw sources. That shape is already reachable with shipped machinery: two sources each extract a "Stoicism" candidate → the foreign-source collision rule writes `stoicism-2` (Bounded, Deduplicated Derived-Object Staging) → `adjudicate`/`merge` unifies them into one object with two provenance entries (ADR-0005/ADR-0011). Multi-source provenance is therefore **emergent from multiplicity plus entity resolution**, and this change adds no merge, staging, or provenance machinery. It is the precondition for #379's criterion 1, not its implementation. |
| D6 | **The fixture is the acceptance test in both directions, and the live proof runs in `evals/`, not in pytest.** | `evals/model_spike/` already drives the real pipeline over the two `good-life-demo` raws with known-correct target objects and already scores `avg_object_count` and `anti_enumeration_score`. The deterministic suite pins prompt *text* and fake-backend *parsing*; a non-deterministic quality claim is measured in the spike harness, per AGENTS.md's spike-then-test rule. Both directions gate: three objects for `call-with-maria`, and still `[]` for blank/boilerplate. |

## Scope

### In scope

- **A decisive A/B on the D1 anchor** before any prompt edit: same corpus, same model, same
  `_SYSTEM_PROMPT`, one variable — the `SOURCE TITLE:` value (H1-derived vs. `_titleize(stem)` vs.
  omitted). Run in `evals/model_spike/`. The result is recorded in `design.md` and decides whether
  `_build_messages`' framing of the title changes.
- **`_SYSTEM_PROMPT` revision** on three axes: the rubric re-pointed at each candidate object (D2);
  an explicit multiplicity test distinguishing single-topic from multi-topic documents (D3); the
  anti-twin rule (D4).
- **`ingestion` spec deltas** — the "Type Classification Prefers Specific Types Over the Entity
  Fallback" requirement gains per-object framing, a multiplicity requirement, and the anti-twin
  rule; the `[]`-only-for-empty-content contract is restated so it cannot be traded away.
- **Fixture-backed acceptance**: `call-with-maria-2026-07-14.txt` yields the three declared objects,
  proven in the spike harness and reflected as a spec scenario.
- **Offline prompt-text regression tests** for every new clause, in the shape
  `tests/unit/extraction/test_concept.py` already uses (14 such tests exist), plus fake-backend
  tests for multi-object replies. The existing `[]`-escape-hatch guard
  (`test_prompt_does_not_reinstate_the_empty_array_escape_hatch`) must pass **unedited** — it is the
  #129 regression alarm, and this change is exactly the kind that would be tempted to relax it.
- **A recorded run protocol** in the change directory: corpus, model tag, object counts before and
  after, so #379's gate can be re-run against a stated baseline rather than a memory.

### Out of scope (non-goals)

- **The sibling issues.** Candidate-edge fan-out (#378), the `derived_from` vocabulary collision
  (#380), post-curation index staleness (#381), and the `curate` call budget (#382) each get their
  own change. This one moves only the ground floor.
- **The nine-type vocabulary** (`CLASSIFIABLE_TYPES`) — unchanged, in content and in count.
- **`_MAX_OBJECTS_PER_SOURCE = 5`** — unchanged. It is a safety ceiling applied after validation,
  not a target, and 5 is not what is producing 1.
- **The staging, slug-disambiguation, and `<slug>-N` collision rules** — unchanged (D5).
- **Merge, adjudication, or any write that produces multi-source provenance** (D5).
- **Chunking or splitting a source into passages.** Multiplicity is a classification decision over
  one whole source text, not a segmentation strategy.
- **The default model** (ADR-0001) and any change to `LLMBackend`, `Message`, or the 2-message
  prompt structure.
- **Backfilling already-ingested sources.** Sources ingested under the 1:1 behavior keep their
  single derived object until re-ingested; a backfill verb is a separate change if wanted.

## Capabilities

### New capabilities

- None. The behavior belongs to an existing capability.

### Modified capabilities

- `ingestion`: the type-classification requirement is re-framed per candidate object and gains an
  explicit multiplicity contract plus the anti-twin rule; the fail-closed `[]` contract is restated
  unchanged in substance.

## Approach

Order matters, because one step can invalidate the other's premise:

1. **Measure first.** Reproduce 1:1 in `evals/model_spike/` against the current build, then run the
   D1 anchor A/B. This costs one afternoon of local inference and either identifies the regression's
   real cause or eliminates the hypothesis. Rewriting the prompt first would confound both.
2. **Then edit the prompt**, one axis at a time (D2, then D3, then D4), re-measuring between axes.
   #129 is the precedent for why: it swung a stack of three levers at once and the pendulum landed
   on the opposite defect. Single-axis edits with a measurement between them are what keep this from
   being the third swing.
3. **Then write the spec**, from what was measured. The deltas describe behavior that has been
   observed, not behavior that is hoped for.

`extract_concept`'s signature, return contract, validation, and cap are untouched: this is a prompt
and specification change, with at most a small edit to how `_build_messages` frames the title.

## Delivery

Forecast **well under the 400-line review budget** for the code itself — the change is concentrated
in one string constant, its docstring, and its tests. Two PRs, because the measurement is a genuine
deliverable and not a preamble:

| Slice | Content | Standalone value |
|---|---|---|
| 1 | Spike-harness reproduction + the D1 anchor A/B; results recorded in `design.md` | Settles the cause on evidence. Independently valuable even if the answer is "the anchor is innocent" — #379's gate needs a stated baseline either way |
| 2 | Prompt revision (D2–D4), `ingestion` spec deltas, offline regression tests, fixture acceptance | The fix |

Strict TDD applies to slice 2 (`rules.apply.tdd`): each prompt clause lands as a RED prompt-text
assertion before the constant is edited.

No ADR is proposed. Prompt wording is the most reversible artifact in the repository and decides no
technology, interface, or hard-to-reverse trade-off. If the D1 experiment concludes that the Source
title must be *withheld* from the extraction prompt, that is a genuine interface decision between
ingest and extraction and `sdd-design` re-evaluates it against the ADR gate.

## Affected areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modified | `_SYSTEM_PROMPT` (D2–D4) and its docstring; possibly `_build_messages`' title framing pending D1 |
| `src/openkos/cli/main.py` | Possibly modified | Only if D1 concludes the title passed at `main.py:2688` should change; no other ingest behavior moves |
| `openspec/specs/ingestion/spec.md` | Modified (delta) | Type-classification requirement re-framed per object; multiplicity and anti-twin requirements added |
| `tests/unit/extraction/test_concept.py` | Modified | New prompt-text and multi-object fake-backend tests; the `[]`-guard test passes unedited |
| `evals/model_spike/` | Modified | The A/B harness run; `report.md` regenerated |
| `examples/good-life-demo/` | Unchanged | It is the fixture under test — this change adapts to it, never the reverse |
| `docs/` | Unchanged | No user-facing surface changes |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The pendulum swings back to shallow enumeration — a Person stub per name mentioned | High | The anti-enumeration paragraph stays verbatim (its regression test is unedited); D4 adds a suppression clause rather than only removing one; `anti_enumeration_score` on the `call-with-maria` fixture is a gate, not a note |
| `[]` returns for instructional sources — #129 reopened | Med | `test_prompt_does_not_reinstate_the_empty_array_escape_hatch` and `test_prompt_states_the_positive_extraction_default` must pass **unedited**; the five instructional files #129 validated against are re-run in the spike |
| The D1 anchor turns out innocent and the regression has a third cause | Med | Slice 1 is scoped to *find* the cause, not to confirm a guess. If the A/B is flat, slice 1 still delivers a measured baseline and the search widens inside slice 1 rather than shipping a speculative prompt edit |
| Prompt-quality claims cannot be pinned in CI, so the fix erodes silently | High (structural) | Accepted and mitigated the way the repo already does it: text-level clauses are pinned deterministically in pytest, behavior is measured in `evals/`. A clause that no test pins is a clause that will be edited away |
| Multiplicity multiplies downstream cost — more objects means more candidate edges | High | Real, and it makes #378 (the O(n²) seeding fix) a hard dependency for the #379 gate rather than a parallel nicety. Called out here; resolved there |
| The prompt is already ~5 KB; more text degrades adherence at the 8B tier | Med | Every axis is measured separately, so a clause that costs more than it earns is visible and gets reverted rather than accumulated. Net token growth is a review criterion |

## Rollback plan

Slice 2 is a one-file revert: restoring the previous `_SYSTEM_PROMPT` restores byte-identical
extraction behavior, because nothing else in the pipeline changes. Bundles already compiled under
the new prompt are unaffected by the revert — derived objects are ordinary files on disk with
ordinary provenance, and every one of them is individually removable with `openkos forget`, exactly
as if the operator had produced them by hand. The spec delta reverts with it. Slice 1 writes no
production code and needs no rollback.

If the change ships and over-extraction is discovered later in the field, the intermediate position
is available without a revert: `_MAX_OBJECTS_PER_SOURCE` is untouched by this change and remains the
lever that bounds the damage.

## Dependencies

- No new runtime dependencies.
- Shipped and merged: the bounded-list extraction contract (#57), the `[]` fix (#129), foreign-source
  slug disambiguation, and the `evals/model_spike/` harness.
- A local Ollama with the configured chat model is required for slices 1 and 2's measurements — the
  pytest suite itself stays fully offline.
- **Blocks** [#379](https://github.com/jasonssdev/openkos/issues/379) (the MVP 3 gate).
  [#378](https://github.com/jasonssdev/openkos/issues/378) must land before #379's criterion 3 can
  pass, and this change makes that ordering more urgent, not less.

## Success criteria

- [ ] The 1:1 collapse is reproduced in `evals/model_spike/` against `cbc601e`, and the cause is
      stated with the measurement that supports it.
- [ ] `examples/good-life-demo/raw/call-with-maria-2026-07-14.txt` yields the three objects the
      reference bundle declares: a `Person`, a `Concept` (the *apatheia* correction), and a
      `Decision`.
- [ ] Across the 15-source corpus, the mean derived-object count per source is greater than 1, and
      at least one source that #377 measured at 1 now yields the multiple objects v0.2.0 produced.
- [ ] No source in the corpus yields a derived object whose title merely restates its Source's title
      (D4).
- [ ] Blank, boilerplate-only, and unintelligible sources still yield `[]`.
- [ ] The five instructional files #129 validated against still extract a sensible object — 5/5, the
      figure #129 recorded.
- [ ] `test_prompt_does_not_reinstate_the_empty_array_escape_hatch`,
      `test_prompt_states_the_positive_extraction_default`, and
      `test_prompt_contains_anti_enumeration_paragraph_verbatim` pass **unedited**.
- [ ] Quality gate green: `uv run pytest --cov`, ruff check + format, mypy strict.

## Proposal question round

No interactive round was run. Assumptions open to correction:

1. **The measurement is part of the change, not homework before it.** Slice 1 exists because the
   issue's stated cause is contradicted by `git tag --contains`, and a prompt rewritten against the
   wrong cause would look like a fix while the anchor kept pulling.
2. Multiplicity is decided by the model per source, with no user-facing control — no `--max-objects`
   flag, no per-source hint, no config key.
3. Already-ingested sources are not backfilled; re-ingest is the upgrade path.
4. The fixture's three declared objects are treated as **correct**, and the engine adapts to them.
   If the fixture is instead judged too demanding for the 8B tier, that is a different change and it
   must edit `examples/good-life-demo/` explicitly rather than quietly lowering the bar here.
5. Multi-source provenance is left to entity resolution (D5) and is verified in #379, not here.
