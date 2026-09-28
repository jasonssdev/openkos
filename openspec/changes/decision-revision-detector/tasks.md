# Tasks: decision-revision-detector — find decisions that were later reversed, refined or reaffirmed

Refs #1014 piece (a), sub-change 2 of 3. Design: `design.md`. Proposal:
`proposal.md`. ADR-0025 (`docs/adr/0025-llm-derived-attributes-live-in-a-cache.md`)
is already written, status `Proposed`, with its `docs/adr/README.md` index
row already present — per design.md's own "Migration / Rollout" section, it
**does not ship with Phase A**. It records a decision (LLM-derived per-concept
attributes live in a derived cache, keyed by digest+prompt version, never in
frontmatter) that only Phase B's subject cache (S2) actually realizes; Phase A
ships no cache and no table. It ships with S2's commit, the first slice that
implements the precedent it records. No task below touches the ADR file or
its README index row.

**Delivery order, decided by the owner (design.md, "Delivery order decided by
the owner (2026-09-25): measure first")**: the eight design slices ship in two
phases with a measurement checkpoint between them.

- **Phase A** (this task list applies now): S1 (subject leaf), S3 (direction
  + candidates, pure), S4 (judge) — three pure leaves, no persistence, no CLI
  wiring, no user-visible change. Each ships as its own PR, stacked to `main`
  in slice order.
- **Checkpoint**: sub-change 3 (harness) measures the Phase A leaves directly.
  Phase B does not start until the owner has read those numbers.
- **Phase B** (listed below for completeness — **NOT applied by this task
  list**): S2, S5, S6, S7, S8. A future tasks pass covers Phase B once the
  harness reports.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task, observed RED with the reason it is RED today, with the
`[IMPL]` task that turns it GREEN — in that order. `[IMPL]` tasks introduce
only what their paired `[TEST]` already pins. Revert every mutation with the
inverse edit (never `git checkout --`), and purge `__pycache__` before
trusting a verdict, per design.md's Testing Strategy preamble. Every
LLM-touching test uses a model-free stub (`_ScriptedLLM`/`_RaisingLLM`
`LLMBackend` doubles, module-local to the test file) — no test reaches
Ollama.

**Threat Matrix**: N/A, per design.md — this change adds no routing, shell,
subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Phase A adds no threat-matrix surface at all
(pure functions only); Phase B's only bundle-write path reuses the existing
`_reconcile_pair` transaction unchanged.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines (Phase A only) | ~1,080-1,100, across 3 PRs (S1 ~320, S3 ~380, S4 ~380 — design.md "Migration / Rollout") |
| 400-line budget risk | Low — each PR is independently well under 400 |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (S1 subject leaf) → PR 2 (S3 direction + candidates) → PR 3 (S4 judge) → **CHECKPOINT** (sub-change 3 harness) → Phase B (not in this list) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

The 3-PR split is not a budget-overage split — each Phase A slice is
individually well under 400 lines. It is the owner's explicit delivery
decision (design.md's "measure first" section): three small, independently
green leaves land before any plumbing, so the harness checkpoint measures
exactly the code that ships, and Phase B does not start until the owner has
read the numbers. `auto-chain` means the orchestrator proceeds with this
slice order and `stacked-to-main` with no further decision gate.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|-----------------------|------------------|--------------------|
| 1 (S1) | Subject-pass leaf: prompt, digest, verbatim check, fail-closed parse, `derive_subjects` batch | PR 1 → `main` | `uv run pytest tests/unit/resolution/test_decision_subject.py` | N/A — a pure leaf with no CLI/state wiring; nothing runs end-to-end until Phase B's S7 wires the `revisions` verb | Revert `src/openkos/resolution/decision_subject.py` and its test file; no other module imports it yet |
| 2 (S3) | Direction rule (`pair_direction`), subject overlap, candidate generation (`plan_revision_candidates`), truncation notice — pure | PR 2 → `main` | `uv run pytest tests/unit/resolution/test_decision_revision.py -k "direction or overlap or candidates"` | N/A — same reason as Unit 1 | Revert the direction/overlap/candidate additions to `src/openkos/resolution/decision_revision.py` and their tests; since PR 3 has not landed, this reverts the whole file |
| 3 (S4) | Judge: prompt, parse, verdicts, actionability, batch, verdict→relation map | PR 3 → `main` | `uv run pytest tests/unit/resolution/test_decision_revision.py` (full file) | N/A — same reason as Unit 1; the harness checkpoint (sub-change 3) is the first consumer, and it is read-only against these leaves | Revert the judge additions this PR made on top of PR 2 (`build_judge_messages`, `parse_judge_reply`, `RevisionVerdict*`, `judge_pairs`, `is_actionable_revision`, `is_reportable_revision`, `RELATION_FOR_VERDICT`) and their tests; PR 2's direction/candidates code stays intact |

## Scenario → Task Coverage

Every scenario in the three delta specs, mapped to a Phase A task or marked
deferred with the Phase B slice that will cover it (design.md's own S1-S8
numbering).

| Spec | Scenario | Coverage |
|---|---|---|
| decision-revision-detection | Subject pass runs only over Decisions | Deferred to Phase B (S6 — the service scopes calls to Decisions only) |
| decision-revision-detection | A well-formed reply yields subject, value and evidence | Phase A: 1.3, 1.5, 1.10 |
| decision-revision-detection | A verbatim quote is kept | Phase A: 1.9, 1.10 |
| decision-revision-detection | A non-verbatim quote is dropped without dropping the subject | Phase A: 1.9, 1.10 |
| decision-revision-detection | A cache hit serves the stored subject with no LLM call | Deferred to Phase B (S2/S6) |
| decision-revision-detection | An edited body invalidates the cache entry | Deferred to Phase B (S2/S6) |
| decision-revision-detection | ingest never touches the subject cache | Deferred to Phase B (S7) |
| decision-revision-detection | One Decision's malformed reply does not block the others | Phase A (leaf/batch level): 1.17, 1.18; Deferred to Phase B (full-run level, S6) |
| decision-revision-detection | A single-source Decision resolves one date | Deferred to Phase B (S6 — `provenance_source_ancestors`/`okf.read_event_date` resolution) |
| decision-revision-detection | A Source with no event_date resolves as missing | Deferred to Phase B (S6) |
| decision-revision-detection | Provenance reaching multiple distinct dates resolves as multiple | Deferred to Phase B (S6) |
| decision-revision-detection | No reachable Source resolves as none-reached | Deferred to Phase B (S6) |
| decision-revision-detection | Decisions sharing a Source form no candidate | Phase A: 3.5 |
| decision-revision-detection | A resolved pair is excluded before the count | Phase A: 3.6 |
| decision-revision-detection | A deprecated Decision is excluded unconditionally | Deferred to Phase B (S6 — concept-level exclusion before building `DecisionInput`) |
| decision-revision-detection | A confidential Decision is excluded by default and included with the flag | Deferred to Phase B (S6) |
| decision-revision-detection | Per-Decision ranking keeps the top 5 | Phase A: 3.9 |
| decision-revision-detection | The global cap truncates with a notice | Phase A: 3.11 |
| decision-revision-detection | Under the cap, no truncation notice appears | Phase A: 3.11 |
| decision-revision-detection | A well-formed reply yields one of the four verdicts | Phase A: 4.4, 4.8 |
| decision-revision-detection | An extra field claiming a direction is ignored | Phase A: 4.4 |
| decision-revision-detection | A dated pair is presented earlier-first with dates | Phase A: 4.1 |
| decision-revision-detection | An undated pair is presented in id order, labelled unknown | Phase A: 4.2 |
| decision-revision-detection | Two distinct resolved dates yield a known direction | Phase A: 3.1 |
| decision-revision-detection | Equal resolved dates yield an unknown direction | Phase A: 3.1 |
| decision-revision-detection | A missing, multiple, or none-reached date yields an unknown direction | Phase A: 3.1 |
| decision-revision-detection | One malformed reply degrades without aborting the batch | Phase A: 4.16 |
| decision-revision-detection | An undirected REVERSES is an untyped change | T6 (post-checkpoint, #1014 Plan 2) — `RevisionVerdict.is_untyped_change`/`relation_for`, `test_is_untyped_change_and_relation_for_truth_table` |
| decision-revision-detection | An undirected REFINES is an untyped change | T6 (post-checkpoint, #1014 Plan 2) — same coverage |
| decision-revision-detection | A directed REVERSES or REFINES is unaffected | T6 (post-checkpoint, #1014 Plan 2) — same truth table, directed cells |
| decision-revision-detection | An untyped change remains actionable | T6 (post-checkpoint, #1014 Plan 2) — `test_is_actionable_revision_unaffected_by_undirected_change` |
| decision-revision-detection | --help labels the verb experimental | Deferred to Phase B (S7) |
| decision-revision-detection | Every run states unmeasured quality on stderr | Deferred to Phase B (S7) |
| decision-revision-detection | The subject-pass count is exact and LLM-free | Deferred to Phase B (S6/S7) |
| decision-revision-detection | The pair-judgment count is exact and LLM-free | Deferred to Phase B (S6/S7) |
| decision-revision-detection | Declining the subject-pass gate makes no subject-pass call | Deferred to Phase B (S7) |
| decision-revision-detection | Declining the pair-judgment gate makes no judge call | Deferred to Phase B (S7) |
| decision-revision-detection | A full run changes no bundle file | Deferred to Phase B (S7) |
| decision-revision-detection | REAFFIRMS and UNRELATED cause no bundle write | Deferred to Phase B (S7/S8) |
| decision-revision-detection | Contradiction serving is unaffected by revision findings | Deferred to Phase B (S5/S6) |
| decision-revision-detection | status/next/pending surfaces are unaffected | Deferred to Phase B (S5/S6) |
| decision-revision-detection | An unchanged bundle makes zero LLM calls on re-run | Deferred to Phase B (S6) |
| decision-revision-detection | Editing a Decision's body re-judges only pairs containing it | Deferred to Phase B (S6) |
| decision-revision-detection | Editing a Source's event_date re-judges only affected pairs | Deferred to Phase B (S6) |
| decision-revision-detection | A REAFFIRMS finding appears under its Decision's group | Deferred to Phase B (S7) |
| decision-revision-detection | Re-running after serving still renders REAFFIRMS from persisted findings | Deferred to Phase B (S6/S7) |
| reconcile-command | A REVERSES finding is offered as supersedes held by the later Decision | Deferred to Phase B (S8) |
| reconcile-command | A REFINES finding is offered as revises held by the later Decision | Deferred to Phase B (S8) |
| reconcile-command | Unknown direction asks once for both the later Decision and the relation type | Deferred to Phase B (S8, combined prompt per #1014 Plan 2 — design.md Decision 9 step 7) |
| reconcile-command | Skipping an unknown-direction item writes nothing | Deferred to Phase B (S8) |
| reconcile-command | REAFFIRMS and UNRELATED are never offered | Deferred to Phase B (S8) |
| reconcile-command | A resolved pair's finding is not offered again | Deferred to Phase B (S8) |
| reconcile-command | The walk cannot override a directed finding's detected kind | Deferred to Phase B (S8) |
| reconcile-command | Contradiction findings walk is unaffected | Deferred to Phase B (S8) |
| forget-command | Forgetting a concept scrubs its persisted finding claims | Pre-existing behavior (unaffected by this change; no new task) |
| forget-command | An unrelated finding is preserved | Pre-existing behavior (unaffected) |
| forget-command | A corrupt findings store warns instead of aborting | Pre-existing behavior; wording widens to name the new stores — Deferred to Phase B (S2/S5, test extension) |
| forget-command | Forgetting a concept scrubs its persisted revision finding | Deferred to Phase B (S5) |
| forget-command | Forgetting a concept scrubs its subject-cache row | Deferred to Phase B (S2) |
| forget-command | An unrelated revision finding and subject-cache row are preserved | Deferred to Phase B (S2/S5) |

---

## Slice 1 (PR 1 → `main`): the subject-pass leaf, `resolution/decision_subject.py`

### `resolution/decision_subject.py` — subject field parsing (Decision 5)

- [x] **1.1** [TEST] `tests/unit/resolution/test_decision_subject.py` (new
  file) — add `test_parse_subject_reply_rejects_malformed_subject_field`,
  parametrized over: a non-object reply (`"not json"`); `None`; an object
  with no `subject` key; `subject` as a non-string (e.g. `123`); `subject`
  blank or whitespace-only; `subject` longer than 200 characters — every
  case yields `parse_subject_reply(raw, body) is None`. **RED today**:
  `ModuleNotFoundError` — `src/openkos/resolution/decision_subject.py` does
  not exist. Kills a `subject` field accepted with no type/blankness/length
  check.
- [x] **1.2** [IMPL] `src/openkos/resolution/decision_subject.py`: create the
  module; add `DecisionSubject(subject, value, evidence)` (frozen
  dataclass), `_MAX_SUBJECT_CHARS = 200`, and `parse_subject_reply(raw,
  body)` covering only the `subject` field so far (`value`/`evidence`
  default to `None`), through `openkos.llm.parsing.extract_json_object`.
  Makes 1.1 GREEN.
- [x] **1.3** [TEST] Same file — add
  `test_parse_subject_reply_yields_subject_value_and_evidence_on_a_well_formed_reply`:
  a reply `{"subject": "billing tool", "value": "Postgres", "evidence":
  "<verbatim body sentence>"}` with a matching `body` yields
  `DecisionSubject(subject="billing tool", value="Postgres",
  evidence="<that sentence>")`. Covers decision-revision-detection scenario
  "A well-formed reply yields subject, value and evidence". **RED today**:
  `value`/`evidence` are hardcoded `None` — `AssertionError`.
- [x] **1.4** [TEST] Same file — add `test_parse_subject_reply_value_field`,
  parametrized: `value` missing/blank/whitespace-only -> `None`; `value`
  `"  Postgres  "` -> `"Postgres"` (stripped). **RED today**: `value`
  parsing does not exist — `AssertionError`.
- [x] **1.5** [IMPL] Same module: add `value` parsing (a stripped non-empty
  string, else `None`). Makes 1.3 (value half) and 1.4 GREEN.

### `resolution/decision_subject.py` — `quoted_verbatim` and the evidence field (Decision 5)

- [x] **1.6** [TEST] Same file — add `test_quoted_verbatim_normalization`, a
  parametrized table: identical strings -> `True`; a case difference ->
  `True`; extra internal whitespace/newlines -> `True`; a trailing
  `.`/`,`/`;`/`:`/`!`/`?` on the quote -> `True` (trimmed before the
  substring test); a genuinely different sentence -> `False`; an empty
  string after normalization -> `False`. **RED today**: `AttributeError` —
  `quoted_verbatim` does not exist.
- [x] **1.7** [TEST] Same file — add
  `test_quoted_verbatim_parity_with_extraction_evidence_normalize`: over a
  shared table of strings (mixed case, whitespace runs, trailing
  punctuation), `quoted_verbatim` agrees with a same-shape check built from
  `openkos.extraction.evidence._normalize` (imported for this test only,
  per design.md Decision 5 — no cross-import in production code). **RED
  today**: same `AttributeError`. Kills a second, silently different
  normalization — exactly the drift `extraction/evidence.py`'s own
  docstring warns against.
- [x] **1.8** [IMPL] Same module: add `quoted_verbatim(quote, text)` —
  casefold + whitespace-collapse both sides (byte-identical to
  `extraction.evidence._normalize`'s `" ".join(value.casefold().split())`),
  trim `.,;:!?` from the quote's end before the substring test, `False` on
  an empty normalized quote. Makes 1.6 and 1.7 GREEN.
- [x] **1.9** [TEST] Same file — add
  `test_parse_subject_reply_evidence_field`: a verbatim `evidence` is kept;
  a paraphrased (non-verbatim) `evidence` is dropped to `None` **while
  `subject` and `value` still populate** ("the subject stands"). Covers
  decision-revision-detection scenarios "A verbatim quote is kept" and "A
  non-verbatim quote is dropped without dropping the subject". **RED
  today**: `evidence` parsing does not exist — `AssertionError`. Kills
  rejecting the whole `DecisionSubject` when evidence fails verification.
- [x] **1.10** [IMPL] Same module: wire `evidence` parsing through
  `quoted_verbatim(evidence, body)`; keep the field only when it verifies,
  else `None`, and never reject `subject`/`value` on evidence failure.
  Makes 1.3 (evidence half) and 1.9 GREEN.

### `resolution/decision_subject.py` — digest, prompt version, message shape (Decision 5)

- [x] **1.11** [TEST] Same file — add
  `test_subject_input_digest_changes_with_title_or_body_only`:
  `subject_input_digest(title, body)` changes when `title` changes (`body`
  fixed); changes when `body` changes (`title` fixed); is stable across two
  calls with an identical `(title, body)`. **RED today**: `AttributeError`
  — `subject_input_digest` does not exist. Kills a digest that ignores one
  of the two inputs.
- [x] **1.12** [IMPL] Same module: add `subject_input_digest(title, body) =
  hashlib.sha256((title + "\x00" + body).encode()).hexdigest()`. Makes 1.11
  GREEN.
- [x] **1.13** [TEST] Same file — add
  `test_subject_prompt_version_is_derived_from_the_system_prompt`:
  `SUBJECT_PROMPT_VERSION ==
  hashlib.sha256(_SUBJECT_SYSTEM_PROMPT.encode()).hexdigest()[:16]`
  (imports the private prompt constant for this pin only). **RED today**:
  neither name exists — `AttributeError`. Kills a hand-typed version string
  a future prompt edit could forget to bump.
- [x] **1.14** [IMPL] Same module: write `_SUBJECT_SYSTEM_PROMPT` (the
  subject/value/evidence rubric, JSON-only reply, per design.md Decision 5)
  and derive `SUBJECT_PROMPT_VERSION` from it via
  `hashlib.sha256(...).hexdigest()[:16]`. Makes 1.13 GREEN.
- [x] **1.15** [TEST] Same file — add `test_build_subject_messages_shape`:
  `build_subject_messages(concept_id, title, body)` returns exactly
  `[system, user]`; the system message content equals
  `_SUBJECT_SYSTEM_PROMPT`; the user message content equals `f"[{concept_id}
  — {title}]\n{body}"`. **RED today**: `AttributeError` —
  `build_subject_messages` does not exist.
- [x] **1.16** [IMPL] Same module: add `build_subject_messages`. Makes 1.15
  GREEN.

### `resolution/decision_subject.py` — `derive_subjects` batch (Decision 5)

- [x] **1.17** [TEST] Same file — add
  `test_derive_subjects_partial_batch_on_llm_failure`: three
  `SubjectRequest`s and a `_RaisingLLM` (module-local test double) that
  raises `OllamaUnavailable` on its 2nd `.chat()` call; `derive_subjects(...)`
  returns a `SubjectBatch` with exactly 1 completed result, `failure` set to
  that exception instance, `failed_index == 2`, and `chat` called exactly
  twice (never a 3rd time). Covers decision-revision-detection scenario
  "One Decision's malformed reply does not block the others" at the
  LLM-failure edge this leaf owns (the malformed-reply-only case is 1.18).
  **RED today**: `AttributeError` — `derive_subjects`/`SubjectRequest`/
  `SubjectBatch` do not exist. Kills a guard widened past `llm.chat` and a
  `failed_index` off by one.
- [x] **1.18** [TEST] Same file — add
  `test_derive_subjects_persists_only_well_formed_results`: a `_ScriptedLLM`
  returning one malformed reply and one well-formed reply across two
  requests; `derive_subjects(...).results` holds `(concept_id, None)` for
  the malformed one and `(concept_id, DecisionSubject(...))` for the
  well-formed one, and the batch completes with `failure is None`. Covers
  the same scenario at the leaf's own batch level. **RED today**: same
  `AttributeError` as 1.17.
- [x] **1.19** [IMPL] Same module: add `SubjectRequest(concept_id, title,
  body)`, `SubjectBatch(results, failure=None, failed_index=None)`, and
  `derive_subjects(requests, *, llm, on_progress=None) -> SubjectBatch` — a
  loop copied from `find_contradictions`'s shape
  (`resolution/contradiction.py:1175-1231`): only `llm.chat(...)` sits
  inside a `try/except OllamaError`, `on_progress` is called per completed
  result, and a raised error returns the completed prefix with
  `failure`/`failed_index` set. Makes 1.17 and 1.18 GREEN.

### Slice 1 verification

- [x] **1.20** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.21** Run `uv run pytest tests/unit/resolution/test_decision_subject.py`
  focused, then `uv run pytest` (unpiped) full suite — must be green.
- [x] **1.22** Commit as one or more work-unit commits, scope `resolution`
  (confirm against a sibling leaf module such as `resolution/similarity.py`
  or `resolution/contradiction.py` before finalizing if a closer scope
  exists — e.g. `feat(resolution): add the decision-subject pass leaf`).
  Tests land in the same commit(s) as their behavior. Open PR 1 (Slice 1:
  subject leaf) targeting `main`.

---

## Slice 3 (PR 2 → `main`, after PR 1 merges): direction and candidates, `resolution/decision_revision.py`

This slice creates `src/openkos/resolution/decision_revision.py`. It does
NOT depend on `provenance_source_ancestors_many` (that helper belongs to S5,
Phase B) — every function here takes already-resolved `DecisionDate`/
`DecisionInput` values as plain arguments.

### `resolution/decision_revision.py` — `pair_direction` (Decision 3)

- [x] **3.1** [TEST] `tests/unit/resolution/test_decision_revision.py` (new
  file) — add `test_pair_direction_full_table`, parametrized over the
  exhaustive table in design.md Decision 3: every combination of
  `DateState` (`dated`/`missing`/`multiple`/`none-reached`) on each side,
  plus dated-vs-dated equal, `a<b`, and `a>b` — asserting `holder`/`earlier`/
  `reason` for every cell (fixed id order `a < b`). Includes the
  "first non-dated side reported in id order" case: when BOTH sides are
  non-dated with DIFFERENT states (e.g. `a` is `missing`, `b` is
  `multiple`), `reason` matches `a`'s state, not `b`'s. Covers
  decision-revision-detection scenarios "Two distinct resolved dates yield
  a known direction", "Equal resolved dates yield an unknown direction",
  and "A missing, multiple, or none-reached date yields an unknown
  direction". **RED today**: `ModuleNotFoundError` — the module does not
  exist. Kills `<` swapped for `>`, equal dates treated as ordered, and the
  b-side check silently dropped.
- [x] **3.2** [IMPL] `src/openkos/resolution/decision_revision.py`: create
  the module; add `DateState = Literal["dated", "missing", "multiple",
  "none-reached"]`, `DirectionReason = Literal["dated", "missing",
  "multiple", "none-reached", "equal"]`, `DecisionDate(value: date | None,
  state: DateState)`, `Direction(holder: str | None, earlier: str | None,
  reason: DirectionReason)`, and `pair_direction(id_a, date_a, id_b,
  date_b) -> Direction` implementing design.md Decision 3's table exactly,
  checked in id order. Makes 3.1 GREEN.

### `resolution/decision_revision.py` — `subject_overlap` (Decision 4)

- [x] **3.3** [TEST] Same file — add `test_subject_overlap`, parametrized:
  identical subjects -> `1.0`; wholly disjoint subjects -> `0.0`;
  `"billing database"` vs `"database for billing"` -> `1.0` (the function
  word `"for"` dropped, per `similarity.MATCH_FUNCTION_WORDS`); a one-token
  subject vs a multi-token subject (smaller-set-direction containment); an
  empty subject on either side -> `0.0`. **RED today**: `AttributeError` —
  `subject_overlap` does not exist. Kills the smaller/larger token-set
  direction swapped.
- [x] **3.4** [IMPL] Same module: add `subject_overlap(subject_a,
  subject_b)` — `normalize_key` both sides, `similarity.tokenize`, drop
  `similarity.MATCH_FUNCTION_WORDS` members with the "retain if it would
  leave fewer than two tokens" guard `similarity._content_tokens` applies
  (design.md Decision 4, through the PUBLIC names `similarity.tokenize`/
  `similarity.MATCH_FUNCTION_WORDS` only — no import of the private
  `_content_tokens`); score = the fraction of the smaller token set with an
  equivalent (`SequenceMatcher.ratio() >= similarity.SIMILARITY_THRESHOLD`)
  in the larger set; either set empty -> `0.0`. Makes 3.3 GREEN.

### `resolution/decision_revision.py` — `plan_revision_candidates` (Decision 4)

- [x] **3.5** [TEST] Same file — add
  `test_plan_revision_candidates_excludes_shared_source_pairs`: two
  `DecisionInput`s with overlapping `source_ids` and a high `subject_overlap`
  score never form a candidate; a third, disjoint-source pair with the same
  subject score does. Covers decision-revision-detection scenario
  "Decisions sharing a Source form no candidate". **RED today**:
  `AttributeError` — `plan_revision_candidates` does not exist.
- [x] **3.6** [TEST] Same file — add
  `test_plan_revision_candidates_excludes_resolved_pairs`, parametrized over
  each of the three `RESOLUTION_RELATION_TYPES` members and both
  `resolved_with` directions (`b in resolved_with(a)`, `a in
  resolved_with(b)`): the pair is excluded in every case. Covers
  decision-revision-detection scenario "A resolved pair is excluded before
  the count" at the pure-function level (the service builds `resolved_with`
  from frontmatter in Phase B; this slice takes it as a given input). **RED
  today**: same `AttributeError`. Kills `and` used instead of `or` between
  the two directions.
- [x] **3.7** [TEST] Same file — add
  `test_plan_revision_candidates_drops_pairs_below_the_overlap_threshold`: a
  pair scoring just under `SUBJECT_OVERLAP_THRESHOLD` is dropped; a pair
  scoring exactly at the threshold is kept. **RED today**: same
  `AttributeError`.
- [x] **3.8** [TEST] Same file — add
  `test_plan_revision_candidates_counts_decisions_without_a_subject`: a
  `DecisionInput` with `subject=None` is excluded from pairing and counted
  in `RevisionCandidatePlan.without_subject`. **RED today**: same
  `AttributeError`.
- [x] **3.9** [TEST] Same file — add
  `test_plan_revision_candidates_top_k_is_a_union`: a hub `DecisionInput`
  with 7 eligible partners keeps only its top 5 by `(-score, partner_id)`,
  but a partner that independently ranks the hub inside its OWN top 5 still
  yields that pair (per-Decision top-k is a union across both sides,
  mirroring `graph/proximity.py`'s pair-nomination shape). Covers
  decision-revision-detection scenario "Per-Decision ranking keeps the top
  5". **RED today**: same `AttributeError`. Kills intersection substituted
  for union.
- [x] **3.10** [TEST] Same file — add
  `test_plan_revision_candidates_ordering`: the returned `candidates` tuple
  is ordered by `(-score, pair)` — a higher-scoring pair sorts first, and
  equal-score pairs sort by pair-id tuple. **RED today**: same
  `AttributeError`.
- [x] **3.11** [TEST] Same file — add
  `test_plan_revision_candidates_cap_and_truncation_notice`: 205 eligible
  pairs surviving every prior filter -> `len(plan.candidates) == 200`,
  `plan.total == 205`; `revision_truncation_notice(plan)` reads exactly
  `"200 of 205 candidate pair(s) shown (cap reached); dropped: 5"`; a plan
  at or under the cap -> `revision_truncation_notice(plan) is None`. Covers
  decision-revision-detection scenarios "The global cap truncates with a
  notice" and "Under the cap, no truncation notice appears". **RED today**:
  same `AttributeError`. Kills the cap applied before top-k ranking and a
  truncation notice with an off-by-one count.
- [x] **3.12** [TEST] Same file — add
  `test_plan_revision_candidates_exclusions_applied_before_the_cap`: a
  resolved pair plus enough unrelated eligible pairs to exceed the cap on
  their own — the resolved pair never consumes a cap slot, and `plan.total`
  counts only pairs that survived every eligibility exclusion (design.md
  Decision 4: "exclusions come before the count, which come before the
  cap"). **RED today**: same `AttributeError`. Kills post-cap filtering
  (dropping the resolved pair from `candidates` but still counting it in
  `total`).
- [x] **3.13** [IMPL] Same module: add `DecisionInput(concept_id, subject:
  str | None, source_ids: frozenset[str], resolved_with: frozenset[str])`,
  `RevisionCandidate(pair_ids: tuple[str, str], score: float)`,
  `RevisionCandidatePlan(candidates: tuple[RevisionCandidate, ...], total:
  int, without_subject: int)`, module constants `SUBJECT_OVERLAP_THRESHOLD:
  Final = 0.5`, `TOP_K: Final = 5`, `MAX_PAIRS: Final = 200` (each
  docstring marked `UNMEASURED: placeholder until sub-change 3's harness`,
  per design.md), and `plan_revision_candidates(decisions, *, top_k=TOP_K,
  cap=MAX_PAIRS)` implementing design.md Decision 4's algorithm (subject
  filter and count -> pairwise source/resolution/overlap filters ->
  per-Decision top-k union -> order by `(-score, pair)` -> `total =
  len(all)` -> `candidates = all[:cap]`), plus `revision_truncation_notice(plan)`
  returning the exact string shape or `None` under the cap. This function
  takes `resolved_with` as an already-computed `frozenset[str]` input and
  does NOT import `openkos.model.relations` — the service (Phase B, S6) is
  what reads `RESOLUTION_RELATION_TYPES` to build it. Makes 3.5-3.12 GREEN.

### Slice 3 verification

- [x] **3.14** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.15** Run `uv run pytest tests/unit/resolution/test_decision_revision.py`
  focused (direction/overlap/candidates only — S4's judge tests do not
  exist yet), then `uv run pytest` (unpiped) full suite — must be green.
- [x] **3.16** Commit as one or more work-unit commits, scope `resolution`
  (e.g. `feat(resolution): add decision-revision direction rule and
  candidate generation`). Tests land alongside their behavior. Open PR 2
  (Slice 3: direction + candidates) targeting `main`, branched from `main`
  after PR 1 merges.

---

## Slice 4 (PR 3 → `main`, after PR 2 merges): the judge, `resolution/decision_revision.py`

Extends the same module PR 2 created. Depends only on `pair_direction`
(3.2) and `quoted_verbatim` (1.8, imported from `decision_subject`).

### `resolution/decision_revision.py` — `build_judge_messages` (Decision 6)

- [x] **4.1** [TEST] `tests/unit/resolution/test_decision_revision.py` —
  add `test_build_judge_messages_known_direction_orders_earlier_first`: two
  `JudgeSide`s whose dates resolve a known direction (via `pair_direction`)
  — the earlier side's body appears first in the user message, under a
  header naming `"EARLIER DECISION"` and its resolved date; the later side
  follows under `"LATER DECISION"` and its date. Covers
  decision-revision-detection scenario "A dated pair is presented
  earlier-first with dates". **RED today**: `AttributeError` —
  `build_judge_messages`/`JudgeSide` do not exist.
- [x] **4.2** [TEST] Same file — add
  `test_build_judge_messages_unknown_direction_orders_by_id`: two
  `JudgeSide`s with an unknown direction (e.g. both `missing`) — the two
  bodies are presented in concept-id order under `"DECISION 1"`/
  `"DECISION 2"` headers, preceded by the line `"ORDER UNKNOWN: the dates
  of these decisions do not establish which came first."`. Covers
  decision-revision-detection scenario "An undated pair is presented in id
  order, labelled unknown". **RED today**: same `AttributeError`.
- [x] **4.3** [IMPL] Same module: add `JudgeSide(concept_id, title, body,
  date: DecisionDate)`, `_JUDGE_SYSTEM_PROMPT` (the
  reverses/refines/reaffirms/unrelated rubric, symmetric wording for
  unknown order, one verbatim quote per side, JSON-only reply per
  design.md Decision 6), and `build_judge_messages(a, b) -> list[Message]`
  — calls `pair_direction` internally to choose presentation order and
  header text. Makes 4.1 and 4.2 GREEN.

### `resolution/decision_revision.py` — `parse_judge_reply` (Decision 6)

- [x] **4.4** [TEST] Same file — add
  `test_parse_judge_reply_direction_is_structurally_unreadable`: a
  well-formed reply JSON that additionally includes `{"later": "<the
  earlier-dated id>"}` and whose `quote_first`/`quote_second` are ordered
  to imply the opposite of the recorded dates — building the resulting
  `RevisionVerdict` still leaves `revision_verdict.direction.holder` equal
  to the date-later id, because `direction` is a `@property` computed by
  `pair_direction` from the stored dates and the parser reads only the
  five schema keys. This is design.md's stated success criterion for
  Decision 6. Covers decision-revision-detection scenario "An extra field
  claiming a direction is ignored". **RED today**: `AttributeError` —
  `RevisionVerdict`/`parse_judge_reply` do not exist. Kills a parser that
  reads an extra `"later"`/order-shaped key from the reply.
- [x] **4.5** [TEST] Same file — add
  `test_parse_judge_reply_unknown_verdict_maps_to_unrelated_keeping_confidence`:
  a reply with `"verdict": "supersedes"` (unrecognized, not one of the
  four) and `"confidence": 0.6` parses to `RevisionVerdictValue.UNRELATED`
  with `confidence == 0.6` (the `contradiction._map_verdict` precedent —
  unrecognized, not malformed). **RED today**: same `AttributeError`.
- [x] **4.6** [TEST] Same file — add
  `test_parse_judge_reply_confidence_coercion`, parametrized: `confidence`
  as `float("nan")`, `True` (bool), a string, and missing -> each coerces
  to `0.0`; a valid float like `0.85` passes through; a value outside
  `[0, 1]` is clamped. **RED today**: same `AttributeError`.
- [x] **4.7** [TEST] Same file — add
  `test_parse_judge_reply_rationale_non_string_becomes_empty`: `rationale`
  as a non-string (e.g. `42`) -> `""`. **RED today**: same
  `AttributeError`.
- [x] **4.8** [TEST] Same file — add
  `test_parse_judge_reply_quotes_verified_and_mapped_by_presentation_order`,
  including a swap test: build a pair with a KNOWN direction where the
  earlier id sorts alphabetically AFTER the later id (so presentation
  order and sorted-id order disagree), and assert `quote_first` maps to the
  presented-FIRST (earlier) pair id and `quote_second` to the
  presented-SECOND (later) pair id, never to sorted-id order; a quote that
  fails `quoted_verbatim` against its own side's body becomes `None` on
  that side only. Covers decision-revision-detection scenario "A
  well-formed reply yields one of the four verdicts" (quote-mapping half).
  **RED today**: same `AttributeError`. Kills quotes mapped by sorted-id
  order instead of by the leaf's own chosen presentation order.
- [x] **4.9** [IMPL] Same module: add `RevisionVerdictValue(Enum)` with the
  four values (`REVERSES`, `REFINES`, `REAFFIRMS`, `UNRELATED`),
  `RevisionVerdict(pair_ids, verdict, confidence, rationale, quotes, dates,
  malformed=False)` with a `direction` `@property` delegating to
  `pair_direction` over the stored `dates`/`pair_ids`, `RevisionBatch`,
  `_MALFORMED_REPLY_RATIONALE`, `_ACTIONABLE_CONFIDENCE: Final = 0.7`
  (marked unmeasured), a module-local `_coerce_confidence` (copied from
  `contradiction.py`'s), and `parse_judge_reply(raw, first_body,
  second_body)` implementing Decision 6's parse rules — a non-object reply
  returns `None`; otherwise reads exactly the five schema keys, maps an
  unknown `verdict` string to `UNRELATED` while keeping the coerced
  confidence, coerces `rationale`, and verifies each quote against its own
  side's body via `quoted_verbatim` (imported from `decision_subject`).
  Makes 4.4-4.8 GREEN.

### `resolution/decision_revision.py` — actionability and the verdict→relation map (Decision 6)

- [x] **4.10** [TEST] Same file — add
  `test_is_actionable_revision_truth_table`, parametrized over all 4
  verdicts x confidence `{0.69, 0.70}` x quotes `{both present, one
  missing, both missing}`: `is_actionable_revision(...)` is `True` only for
  `REVERSES`/`REFINES` at confidence `>= 0.70` with BOTH quotes verified;
  every `REAFFIRMS`/`UNRELATED` cell is `False`; confidence exactly `0.69`
  is `False` for every verdict. **RED today**: `AttributeError` —
  `is_actionable_revision` does not exist. Kills `>=` weakened to `>` at
  the threshold and a `REAFFIRMS` cell marked actionable.
- [x] **4.11** [TEST] Same file — add
  `test_is_reportable_revision_includes_reaffirms_but_not_unrelated`:
  `REAFFIRMS` at confidence `>= 0.70` with both quotes verified is
  reportable (`is_reportable_revision(...) is True`) but never actionable
  (confirmed against 4.10's table); `UNRELATED` is never reportable
  regardless of confidence or quotes. **RED today**: `AttributeError` —
  `is_reportable_revision` does not exist.
- [x] **4.12** [IMPL] Same module: add `is_actionable_revision(verdict,
  confidence, quote_0, quote_1)` and `is_reportable_revision(verdict,
  confidence, quote_0, quote_1)` per Decision 6's rules. Makes 4.10 and
  4.11 GREEN.
- [x] **4.13** [TEST] Same file — add
  `test_relation_for_verdict_matches_resolution_relation_types`:
  `set(RELATION_FOR_VERDICT.values()) == RESOLUTION_RELATION_TYPES -
  {"reconciled_with"}` (`RESOLUTION_RELATION_TYPES` imported from
  `openkos.model.relations` for this test-time parity check only). **RED
  today**: `AttributeError` — `RELATION_FOR_VERDICT` does not exist. Kills
  a fourth resolution type silently added to one side but not mapped in
  the other.
- [x] **4.14** [IMPL] Same module: add `RELATION_FOR_VERDICT: Final =
  {RevisionVerdictValue.REVERSES: "supersedes", RevisionVerdictValue.REFINES:
  "revises"}`. Makes 4.13 GREEN.
- [x] **4.14a** [TEST+IMPL, post-checkpoint, #1014 Plan 2 T6 — already
  implemented] Same file/module — add
  `test_is_untyped_change_and_relation_for_truth_table` and
  `test_relation_for_never_types_an_undirected_reverses_or_refines`
  (`tests/unit/resolution/test_decision_revision.py`): `RevisionVerdict
  .is_untyped_change` (`@property`) and `relation_for(verdict:
  RevisionVerdict) -> str | None` (`src/openkos/resolution/
  decision_revision.py`). `RELATION_FOR_VERDICT` itself (4.14) is
  UNCHANGED — `relation_for` wraps it with the direction check
  sub-change 3's harness (#1014 Plan 2) motivated: an undirected pair's
  REFINES verdict is never answered by the judge (0 of 30), so this leaf
  must not infer a relation type for an undirected REVERSES/REFINES.
  `is_actionable_revision`/`is_reportable_revision` (4.12) keep their
  exact contracts — confirmed by
  `test_is_actionable_revision_unaffected_by_undirected_change`. This
  landed OUTSIDE the original Slice 4 PR sequence, after the sub-change 3
  checkpoint, as its own work unit
  (`feat(resolution): treat an undirected revision verdict as an untyped
  change`) — recorded here so the S4 task list stays the accurate record
  of what `decision_revision.py` actually contains.

### `resolution/decision_revision.py` — `judge_pairs` batch (Decision 6)

- [x] **4.15** [TEST] Same file — add
  `test_judge_pairs_partial_batch_on_llm_failure`: three `(JudgeSide,
  JudgeSide)` pairs and a `_RaisingLLM` that raises `OllamaUnavailable` on
  its 2nd call — `judge_pairs(...)` returns a `RevisionBatch` with exactly
  1 completed `RevisionVerdict`, `failure` set, `failed_index == 2`, and no
  3rd `chat()` call. Covers decision-revision-detection scenario "One
  malformed reply degrades without aborting the batch" at its
  LLM-failure edge (the malformed-JSON-reply case is 4.16). **RED today**:
  `AttributeError` — `judge_pairs` does not exist.
- [x] **4.16** [TEST] Same file — add
  `test_judge_pairs_malformed_reply_degrades_without_aborting`: a
  `_ScriptedLLM` returning one non-JSON reply and one well-formed reply
  across two pairs — the malformed pair's result has `malformed=True`,
  `verdict=UNRELATED`, `confidence=0.0`,
  `rationale=_MALFORMED_REPLY_RATIONALE`; the well-formed pair judges
  normally; the batch completes with `failure is None`. Covers
  decision-revision-detection scenario "One malformed reply degrades
  without aborting the batch". **RED today**: same `AttributeError`.
- [x] **4.17** [IMPL] Same module: add `judge_pairs(pairs, *, llm,
  on_progress=None) -> RevisionBatch` — a loop copied from
  `find_contradictions`'s shape: build messages via
  `build_judge_messages`, call `llm.chat` inside the `OllamaError` guard,
  parse via `parse_judge_reply`; a `None` parse result becomes the
  malformed `RevisionVerdict` described in 4.9; a raised `OllamaError`
  returns the completed prefix with `failure`/`failed_index`. Makes 4.15
  and 4.16 GREEN.

### Slice 4 verification

- [x] **4.18** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **4.19** Run `uv run pytest tests/unit/resolution/test_decision_revision.py`
  (full file: direction, overlap, candidates, judge), then `uv run pytest`
  (unpiped) full suite — must be green.
- [x] **4.20** Commit as one or more work-unit commits, scope `resolution`
  (e.g. `feat(resolution): add the decision-revision judge`). Open PR 3
  (Slice 4: judge) targeting `main`, branched from `main` after PR 2
  merges.

---

## CHECKPOINT — STOP

**Sub-change 3 (harness) runs here.** It builds an adjudicated fixture and
measures, using the Phase A leaves directly: the subject pass, candidate-stage
recall, REVERSES-vs-REFINES precision and recall, and direction accuracy. The
harness must be able to come back "no". **Phase B does not start until the
owner has read those numbers** (design.md, "Delivery order decided by the
owner"). No tasks are listed under this checkpoint — the harness is its own
sub-change, out of scope for this task list.

---

## Phase B (listed for completeness — NOT applied by this task list)

The following slices depend on the harness checkpoint above and are
deliberately **not** broken into tasks here. A future `sdd-tasks` pass covers
them once the owner accepts the harness numbers. Recorded now so the whole
design's shape is visible in one place, and so the orchestrator correction
and the design/spec reconciliation below travel with the slices they affect.

- **S2 — Subject cache + privacy sweep.** `src/openkos/state/decision_subjects.py`
  (tenant table, `record_subjects`/`open_subjects`/`prune_missing_subjects`/
  `delete_decision_subjects_referencing`), the `_sweep_findings_for_ids` join
  in `cli/main.py`, and forget/purge tests. **Cache key correction (owner,
  2026-09-25 — see design.md "Orchestrator correction"): the subject-cache
  key MUST include the chat model tag (`resolve_task_model` result), in
  addition to concept id, body digest, and prompt version.** This changes
  S2's table schema (an added `model_tag` column or equivalent) and its
  freshness check (`open_subjects` must also match the current model tag);
  it does not affect Phase A. **ADR-0025 ships with this slice** — flip
  design.md's own instruction into a task: stage the already-written
  `docs/adr/0025-llm-derived-attributes-live-in-a-cache.md` and its
  `docs/adr/README.md` index row into S2's commit (no edit needed; both
  already exist in the worktree). Implements forget-command's "Forgetting a
  concept scrubs its subject-cache row" and the corrupt-store warning-text
  widening.
- **S5 — Revision findings store + sweep + provenance helper.**
  `src/openkos/state/revision_findings.py` (tenant tables, REPLACE-per-pair,
  the four sweep predicates over `pair_id_0`/`pair_id_1`/`input_ref`/
  `sources-of:<id>`), the `_sweep_findings_for_ids` join, and
  `provenance_source_ancestors_many` in `src/openkos/bundle/provenance.py`
  (the single-id function delegates to a shared private walk; behavior
  unchanged). Implements forget-command's "Forgetting a concept scrubs its
  persisted revision finding" and "An unrelated revision finding and
  subject-cache row are preserved" (jointly with S2).
- **S6 — Service.** `src/openkos/application/revisions.py`:
  `load_decisions` (concept-level exclusions: deprecated, confidential,
  bad-relations, each counted), Decision event-date resolution (via
  `provenance_source_ancestors_many` + `okf.read_event_date`, building the
  `DecisionDate`s S3's `pair_direction` consumes), `plan_subjects`,
  `derive_subjects` (persists successes), `plan_revisions` (serving from
  S5's store via the strict-equality freshness rule), `judge_revisions`,
  `revision_input_digests`, `is_fresh`, `actionable_revision_findings`.
  Implements every decision-revision-detection scenario about dates, cache
  serving, and re-judging-on-edit.
- **S7 — Verb.** `openkos revisions` in `cli/main.py` (experimental
  `--help`/stderr notice, both cost gates, zero-LLM probes, the report
  grouped per Decision including REAFFIRMS lines, `--all`, bundle-write
  guard), `docs/cli.md`'s `revisions` section. Implements the experimental
  label, cost-gate, bundle-write-guard, and report scenarios.
- **S8 — Reconcile walk.** The second walk inside
  `_run_reconcile_from_findings` (`cli/main.py`), `_ask_later_decision_and_type`
  (design.md Decision 9 step 7 — combined "which is later, which relation
  type" prompt, per #1014 Plan 2; supersedes the earlier
  `_ask_later_decision` two-question sketch), the `--from-findings` help
  text, and `docs/cli.md`'s `reconcile` section. Implements every
  `reconcile-command` scenario: REVERSES/REFINES offered as
  `supersedes`/`revises` held by the later Decision for a DIRECTED finding,
  the combined unknown-direction prompt for an UNDIRECTED (untyped-change)
  finding (`[1] <b> replaces <a>  [2] <b> adjusts <a>  [3] <a> replaces <b>
  [4] <a> adjusts <b>  [s] skip`, one keystroke naming both holder and
  edge_type, no separate y/N confirm), the never-offered REAFFIRMS/UNRELATED
  rule, the resolved-pair re-offer guard, and the directed-only no-override
  rule. Reuses `_reconcile_pair`'s `--winner`/`--revision` modes unchanged
  (shipped by `revises-relation`, archived). Reads
  `RevisionVerdict.is_untyped_change`/`relation_for` (T6, already shipped)
  to decide which of the two prompts an item gets.

### Design/spec reconciliation for Decisions 6-8 (checked now, for Phase B)

Design.md Decisions 6 (judge), 7 (cost gates), and 8 (report) were checked
against the three delta specs for conflicting literal strings (verdict
wording, gate text, notice text). **No conflict was found.** The specs
describe behavior only (Given/When/Then) and pin no literal gate or notice
wording, so design.md's exact strings (the two gate lines in Decision 7, the
line shapes in Decision 8) stand unchanged for S6/S7. The one literal phrase
a spec DOES pin — decision-revision-detection's "the report MUST include a
line reading `reaffirmed by <id> on <date>`" — matches design.md Decision 8's
own example (`"reaffirmed by decisions/billing-review on 2026-04-01"`)
verbatim. The verdict vocabulary (`REVERSES`/`REFINES`/`REAFFIRMS`/
`UNRELATED`) is identical between design.md Decision 6 and the spec's "Judge
Verdict Vocabulary And Reply Shape" requirement. **Rule for a future
conflict, stated for completeness though none applies now: the spec wins.**
Should a Phase B tasks pass discover a divergence design.md did not catch
(for example if the delta specs are edited after this tasks pass), the spec's
wording is authoritative and the affected S6/S7/S8 task must be written to
match the spec, with a note on that task explaining what design.md said
instead.
