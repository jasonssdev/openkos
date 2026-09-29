# Tasks: decision-revision-detector — find decisions that were later reversed, refined or reaffirmed

Refs #1014 piece (a), sub-change 2 of 3. Design: `design.md`. Proposal:
`proposal.md`. ADR-0025 (`docs/adr/0025-temporal-direction-never-comes-from-the-model.md`) (renamed from `0025-llm-derived-attributes-live-in-a-cache.md` by Phase B P3)
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
- **Phase B**: the checkpoint passed (sub-change 3's harness, live run
  `runs-20260928T204537Z`, all bars B1-B8) and the owner accepted the
  2026-09-28 re-plan (design.md, "Phase B re-plan"). Phase B is now broken
  into tasks in the **"Phase B: tasks (2026-09-28 re-plan)" section near the
  end of this file** — eleven slices, chain order P1 → P2 → P3 → P4 → P5a →
  P5b → P6 → P7a → P7b → P8a → P8b. The S2/S5-S8 mapping in the "Phase B
  (listed for completeness)" section further down is the ORIGINAL plan,
  superseded by the re-plan; see the banner there.

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

> **Superseded by the 2026-09-28 re-plan.** The harness checkpoint passed
> (all bars B1-B8; design.md "Phase B re-plan") and the owner accepted
> Decisions B1-B7, including dropping the production subject pass (B3) and
> reading candidate vectors from `vectors.db` instead of embedding at
> detection time (B1). The slices below (S2, S5-S8) are the ORIGINAL Phase B
> plan and are kept here only as history — S2 (subject cache) is DROPPED
> entirely, and S5-S8 are re-cut into P1-P8b. **The plan that actually ships
> is the "Phase B: tasks (2026-09-28 re-plan)" section near the end of this
> file. Do not apply the slices below.**

The following slices depend on the harness checkpoint above and were
deliberately **not** broken into tasks in the original pass. Recorded here so
the whole design's shape is visible in one place, and so the orchestrator
correction and the design/spec reconciliation below travel with the slices
they affect.

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

---

# Phase B: tasks (2026-09-28 re-plan)

Refs #1014 piece (a), sub-change 2 of 3. Basis: design.md's "Phase B re-plan
(2026-09-28)" section (Decisions B1-B7), written after sub-change 3's harness
passed all bars B1-B8 on live run `runs-20260928T204537Z` and the owner
accepted the re-plan the same day. The two delta specs
(`specs/decision-revision-detection/spec.md`,
`specs/forget-command/spec.md`) are ALREADY updated for Phase B — every task
below was checked against the spec text actually on disk, not against
design.md's paraphrase of it (see "Design/spec reconciliation for Phase B"
near the end of this section).

**Owner decisions, final as of 2026-09-28** (design.md, Decisions B1-B7):
drop the production subject pass entirely (B3) — no `state/decision_subjects.py`,
no subject-pass cost gate, no subject-pass service calls; `--include-confidential`
releases the judge send only, never an embed (B2); candidate vectors come
ONLY from `.openkos/vectors.db`'s `doc_vectors` table as written by
`openkos reindex` — `revisions` makes zero embedding calls, ever (B1);
`EMBEDDING_SIMILARITY_THRESHOLD` stays `0.65`, its docstring cites the
production-shape measurement instead of the stale `graph/proximity.py`
pointer (B5); no new ADR — ADR-0025 is narrowed while still `Proposed` (B6).
The Phase A leaf `resolution/decision_subject.py` (Slice 1, already shipped)
stays in `src/` unchanged: the judge imports `quoted_verbatim` from it and
the harness runs `derive_subjects` as its own diagnostic, but it now has NO
production caller — a docstring sentence records this (P2).

Strict TDD is ON, runner `uv run pytest`, same conventions as Phase A: every
behavioral task pairs a `[TEST]` (observed RED, with the reason it is RED
today) with the `[IMPL]` that turns it GREEN, in that order; revert a
mutation with the inverse edit, never `git checkout --`; purge `__pycache__`
before trusting a verdict; every LLM-touching test uses a model-free stub
(`_ScriptedLLM`/`_RaisingLLM`, module-local to the test file) — no test
reaches Ollama. A small number of tasks are marked `[DOC]` (prose-only, no
behavior, so no RED/GREEN pairing applies) or `[TEST, no paired IMPL]` (a
pure architectural-invariant regression pin whose GREEN state follows
automatically from an adjacent task's IMPL, explained inline).

**Threat Matrix**: still N/A, unchanged from Phase A's determination — this
change adds no routing, shell, subprocess, VCS/PR automation,
executable-file-classification, or process-integration boundary. Phase B's
only bundle-write path is the existing `_reconcile_pair` transaction
(confirm gate, drift guard, atomic writes, autocommit), reused unchanged
(design.md Decision 9, step 8; Phase B's own Threat Matrix section repeats
this).

**Chain order** (auto-chain, chain strategy `stacked-to-main`, chosen by the
owner on 2026-09-28: each slice's PR merges to `main` in order): P1 → P2 → P3 → P4 → P5a → P5b →
P6 → P7a → P7b → P8a → P8b. Per design.md, P3, P4 and P8a have no upstream
dependency inside Phase B and MAY be reordered earlier if the orchestrator
prefers; every other edge is a real dependency (a slice imports or tests
against a symbol the prior slice ships).

## Review Workload Forecast (Phase B)

| Field | Value |
|-------|-------|
| Estimated changed lines (Phase B, 11 slices) | ~3,750 total (re-derived from Phase A's own ~1.95x forecast-to-actual ratio, design.md "Phase B re-plan", Slices) |
| 400-line budget risk | High — P3 (~550) is over budget on its own and is a documented `size:exception` candidate; P6/P7b/P8b (~400-450 each) sit at or just over the line even before Phase A's ~1.95x actual multiplier is applied |
| Chained PRs recommended | Yes |
| Suggested split | 11 PRs, one per slice, in chain order (below) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main (owner, 2026-09-28) |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

`auto-chain` means the orchestrator proceeds with slice P1 first, in the
chain order above, once the chain strategy (`stacked-to-main` or
`feature-branch-chain`) is recorded — this task list does not gate on that
choice, per the skill's own decision table for `auto-chain`. **P3 cannot be
split further without breaking design.md Decision 2's own rule** ("every
table ships with its sweep join and sweep tests in the same slice") — if
P3's actual size lands as far over budget as Phase A's slices did relative to
their forecasts, it is the one slice this task list recommends requesting
`size:exception` for, rather than splitting the table from its sweep.

### Suggested Work Units (Phase B)

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|-----------------------|------------------|--------------------|
| P1 | Vector read seam: `VectorStoreDB.document_vectors`, public `embedding_tag` | PR 4 (chain, after Phase A's PR 3) | `uv run pytest tests/unit/state/test_vectorstore.py tests/unit/state/test_reindex.py` | N/A — a store-level read method with no CLI wiring yet | Revert the new method/function and their tests; `reindex`'s existing write path is untouched |
| P2 | Harness production-shape arm, one committed live run, threshold/`subject` docstrings | PR 5 | `uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test` | Harness self-test is model-free (fake embedder); the ONE live measurement run (design.md Decision B5) needs a running Ollama and is a manual/operator step, not part of `--self-test` | Revert the new `--vector-source` arm, the committed results file, and the docstring edits; production code is untouched |
| P3 | Revision findings store, sweep join, forget tests, ADR-0025 narrowing | PR 6 | `uv run pytest tests/unit/state/test_revision_findings.py tests/unit/cli/test_forget.py` | N/A — no CLI verb reads this table yet | Revert `state/revision_findings.py`, its sweep call in `main.py`, and the ADR rename; `findings.db`'s existing tenants are untouched |
| P4 | `provenance_source_ancestors_many` (shared-walk refactor) | PR 7 | `uv run pytest tests/unit/bundle/test_provenance.py` | N/A — pure parity refactor | Revert the new function and the extracted helper; `provenance_source_ancestors` keeps its own inline walk if reverted whole |
| P5a | Service: `load_decisions` (+ `resolved_with`), date resolution, `read_decision_vectors` | PR 8 | `uv run pytest tests/unit/application/test_revisions_service.py -k "load_decisions or dates or vectors"` | N/A — service functions, no verb yet | Revert the new functions in `application/revisions.py` and their tests; depends on P1/P3/P4 already having merged |
| P5b | Service: `revision_input_digests`, `is_fresh`, `plan_revisions` | PR 9 | `uv run pytest tests/unit/application/test_revisions_service.py -k "digest or is_fresh or plan_revisions"` | N/A — still no verb | Revert the new functions on top of P5a; P5a's code stays intact |
| P6 | Service: `judge_revisions`, `actionable_revision_findings` | PR 10 | `uv run pytest tests/unit/application/test_revisions_service.py` (full file) | N/A — the verb (P7b) is the first consumer | Revert the judging half; P5a/P5b stay intact |
| P7a | Pure report renderer (`application/revisions_report.py`) | PR 11 | `uv run pytest tests/unit/application/test_revisions_report.py` | N/A — pure function, not wired into the verb yet | Revert the new module and its tests; nothing else imports it yet |
| P7b | `openkos revisions` verb, one gate, `docs/cli.md` | PR 12 | `uv run pytest tests/unit/cli/test_revisions.py` | `openkos revisions --self-test`-equivalent: none exists; exercise with `uv run openkos revisions --auto` against a small local bundle with Ollama running, on a workspace with `.openkos/vectors.db` already populated by `openkos reindex` | Revert the CLI command and its `docs/cli.md` section; `application/revisions.py` (P5a/P5b/P6) stays usable by a future caller |
| P8a | `_ask_later_decision_and_type` (pure CLI helper) | PR 13 | `uv run pytest tests/unit/cli/test_reconcile.py -k ask_later_decision_and_type` | N/A — a prompt-loop helper with no caller yet | Revert the new function; nothing calls it until P8b |
| P8b | `reconcile --from-findings` revision walk, help text, `docs/cli.md` | PR 14 (final) | `uv run pytest tests/unit/cli/test_reconcile.py` (full file) | `uv run openkos reconcile --from-findings` on a workspace with at least one actionable revision finding persisted by a real `openkos revisions --auto` run | Revert the second walk inside `_run_reconcile_from_findings`, `_ask_later_decision_and_type`'s call site, and the `docs/cli.md`/help-text additions; the contradiction walk (unchanged) keeps working |

## Phase B Scenario → Task Coverage

Every requirement/scenario in the two Phase B delta specs, as they read ON
DISK today (already updated for the re-plan), mapped to a task. A scenario
already satisfied by Phase A or by a production commit that shipped ahead of
this tasks pass is marked "Already shipped" with its PR, per
`prove-a-branch-landed-by-test-names` (grep the tests before assuming it is
new work).

| Spec | Requirement / Scenario | Coverage |
|---|---|---|
| decision-revision-detection | Decision Event Date Resolution — all 4 scenarios | P5a.5, P5a.6 |
| decision-revision-detection | Candidate Eligibility Exclusions — Decisions sharing a Source form no candidate | Already shipped: Phase A leaf test (`test_plan_revision_candidates_excludes_shared_source_pairs`, Slice 3, task 3.5); wired to real Decisions by P5b.10 |
| decision-revision-detection | Candidate Eligibility Exclusions — A resolved pair is excluded before the count | Already shipped: Phase A leaf test (task 3.6); `resolved_with` is built from frontmatter by P5a.3/P5a.4, wired by P5b.10 |
| decision-revision-detection | Candidate Eligibility Exclusions — A deprecated Decision is excluded unconditionally | P5a.1, P5a.2 |
| decision-revision-detection | Candidate Eligibility Exclusions — A confidential Decision is excluded by default and included with the flag | P5a.1, P5a.2; verb-level in P7b.12 |
| decision-revision-detection | An included confidential Decision without a stored vector is counted, not embedded | P5a.11, P5a.12; verb-level in P7b.4, P7b.12 |
| decision-revision-detection | Candidate Ranking And Caps — Per-Decision top 5, global cap + notice, under-cap no notice | Already shipped: Phase A leaf tests (tasks 3.9, 3.11) |
| decision-revision-detection | A Decision without an embedding forms no candidate and is counted | Already shipped, PR #1050 (`main` @ `9cbedc8`, `feat(resolution): block revision candidates by embedding similarity`) — the leaf's `vectors`/`without_vector` shape landed ahead of this tasks pass; test lives in `tests/unit/resolution/test_decision_revision.py` |
| decision-revision-detection | Candidate Vectors Come From The Reindexed Vector Store — all 4 scenarios | P1.1-P1.7 (read seam); P5a.7-P5a.12 (service coverage); P7b.4 (verb-level remedy messages) |
| decision-revision-detection | Judge Verdict Vocabulary And Reply Shape — both scenarios | Already shipped: Phase A leaf tests (tasks 4.4, 4.8) |
| decision-revision-detection | Candidate Presentation Order Given To The Judge — both scenarios | Already shipped: Phase A leaf tests (tasks 4.1, 4.2) |
| decision-revision-detection | Direction Comes Only From Resolved Source event_date — all 3 scenarios | Already shipped: Phase A leaf test (task 3.1) |
| decision-revision-detection | An Undirected Change Verdict Is Untyped — all 4 scenarios | Already shipped, PR #1048/#1049 (`main` @ `9cbedc8` lineage, task 4.14a — `is_untyped_change`/`relation_for` and their truth-table tests) |
| decision-revision-detection | Fail-Closed Judge Parsing With Partial Batch On Failure | Already shipped at leaf level (tasks 4.15, 4.16); full-run level in P6.2 |
| decision-revision-detection | `openkos revisions` Is Labelled Experimental — both scenarios | P7b.1, P7b.2 |
| decision-revision-detection | Zero-LLM Probe Precedes The Cost Gate | P7b.5, P7b.6 |
| decision-revision-detection | One Exact Cost Gate Before Pair Judgment — declining makes no judge call | P7b.6, P7b.8 |
| decision-revision-detection | `revisions` Writes Only Derived State — full run changes nothing else | P7b.10 |
| decision-revision-detection | `revisions` Writes Only Derived State — REAFFIRMS/UNRELATED cause no bundle write | P7b.10 (the verb never writes bundle at all); P8b.4 (never offered by the walk either) |
| decision-revision-detection | `revisions` Writes Only Derived State — the verb makes no embedding call | P7b.4, P7b.10 |
| decision-revision-detection | Revision Findings Persist In Sibling Tables — both scenarios | P3.14 |
| decision-revision-detection | Revision Findings Are Served From Cache Keyed By Input Digests — all 3 scenarios | P5b.1-P5b.9 |
| decision-revision-detection | Revisions Report Groups Findings Per Decision, Including REAFFIRMS — both scenarios | P7a.5, P7a.9 |
| forget-command | Forgetting a concept scrubs its persisted finding claims / An unrelated finding is preserved | Pre-existing behavior, unaffected by this change |
| forget-command | A corrupt findings store warns instead of aborting (wording widens) | P3.12, P3.13 |
| forget-command | Forgetting a concept scrubs its persisted revision finding | P3.11 |
| forget-command | An unrelated revision finding is preserved | P3.11 |

No requirement or scenario in either updated spec is left without a task or
an "Already shipped" citation.

---

## Slice P1 (PR 4 → after Phase A's PR 3): the vector read seam

Depends on nothing new — the vector store and reindex modules already exist.
Files: `src/openkos/state/vectorstore.py`, `src/openkos/state/reindex.py`.
Tests: `tests/unit/state/test_vectorstore.py`, `tests/unit/state/test_reindex.py`.

### `state/vectorstore.py` — `document_vectors` (design.md Decision B1, Interfaces)

- [x] **P1.1** [TEST] `tests/unit/state/test_vectorstore.py` — add
  `test_document_vectors_returns_the_derived_document_vector_not_a_chunk_row`:
  `upsert_many` a multi-chunk document, then `document_vectors([concept_id])`
  returns `{concept_id: StoredDocVector(vector=<the doc_vectors row>,
  content_hash=<the vector_meta row's hash>)}` — never one of the per-chunk
  `vectors` rows. **RED today**: `AttributeError` — `document_vectors`/
  `StoredDocVector` do not exist. Kills reading from `vectors` instead of
  `doc_vectors`.
- [x] **P1.2** [TEST] Same file — add
  `test_document_vectors_omits_ids_with_no_stored_row`:
  `document_vectors(["a", "b"])` where only `"a"` was ever upserted returns
  `{"a": ...}` only — no `KeyError`, no exception, no entry for `"b"`. **RED
  today**: same `AttributeError`.
- [x] **P1.3** [TEST] Same file — add
  `test_document_vectors_hash_equals_the_upserted_content_hash`: after
  `upsert_many` with `content_hash="h1"`, `document_vectors([concept_id])[
  concept_id].content_hash == "h1"`. **RED today**: same `AttributeError`.
  Kills the hash read from the wrong table (a per-chunk `vectors.content_hash`
  that a future schema change could desynchronize from `vector_meta`).
- [x] **P1.4** [TEST] Same file — add
  `test_document_vectors_empty_input_returns_empty_dict`:
  `document_vectors([]) == {}`. **RED today**: same `AttributeError`.
- [x] **P1.5** [IMPL] `src/openkos/state/vectorstore.py`: add
  `StoredDocVector(vector: tuple[float, ...], content_hash: str)` (frozen
  dataclass) and `VectorStoreDB.document_vectors(self, concept_ids:
  Collection[str]) -> dict[str, StoredDocVector]` — a `JOIN` of `doc_vectors`
  and `vector_meta` on `concept_id`, decoding each row's float32 blob (mirrors
  `neighbors`' `_SELECT_DOC_VECTOR_BLOB_SQL` blob handling), NOT declared on
  the `VectorStore` Protocol (the same "concrete-store-only capability"
  reasoning `neighbors` already documents). Makes P1.1-P1.4 GREEN.

### `state/reindex.py` — public `embedding_tag` (design.md File changes, P1)

- [x] **P1.6** [TEST] `tests/unit/state/test_reindex.py` — add
  `test_embedding_tag_composes_model_and_the_chunk_composition_tag`:
  `embedding_tag("bge-m3") == "bge-m3#chunk-v1"` (`EMBED_COMPOSITION_TAG`).
  **RED today**: `AttributeError` — `embedding_tag` is not a public name
  (only the private `_effective_model_tag` exists).
- [x] **P1.7** [TEST] Same file — add
  `test_effective_model_tag_delegates_to_embedding_tag_and_keeps_none_passthrough`:
  `_effective_model_tag(None) is None` (unchanged); `_effective_model_tag(
  "bge-m3") == embedding_tag("bge-m3")` (parity, not a duplicated
  computation). **RED today**: same `AttributeError` on `embedding_tag`.
- [x] **P1.8** [IMPL] `src/openkos/state/reindex.py`: promote `embedding_tag(
  model: str) -> str` to a public function (`f"{model}#{EMBED_COMPOSITION_TAG}"`);
  `_effective_model_tag(model_tag: str | None)` keeps its `None` passthrough
  and otherwise delegates to `embedding_tag(model_tag)` — behavior unchanged
  for every existing caller. Makes P1.6 and P1.7 GREEN.

### Slice P1 verification

- [x] **P1.9** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **P1.10** Run `uv run pytest tests/unit/state/test_vectorstore.py
  tests/unit/state/test_reindex.py` focused, then `uv run pytest` (unpiped)
  full suite — must be green.
- [x] **P1.11** Commit as one or more work-unit commits. The project scope
  list (`AGENTS.md`) has no bare `state` entry; confirm against the most
  recent commit touching `state/vectorstore.py`/`state/reindex.py` before
  finalizing — likely `graph` (vector-store-adjacent work) or `ingest`
  (reindex-adjacent work), e.g. `feat(graph): add the document-vector read
  seam for revision candidate blocking`. Tests land alongside their
  behavior. Open PR 4 (Slice P1) targeting `main` (or the tracker branch,
  per the chosen chain strategy), branched after Phase A's PR 3 merges.

---

## Slice P2 (PR 5): harness production-shape arm, committed run, docstring updates

Depends on P1 (`document_vectors`). Files: `evals/decision_revisions/run_decision_revisions_eval.py`,
`evals/decision_revisions/README.md`, `evals/decision_revisions/results/` (new
directory); `src/openkos/resolution/decision_revision.py` (docstring only);
`src/openkos/resolution/decision_subject.py` (docstring only).

This harness lives under `evals/`, outside the pytest suite: its own
`--self-test` flag is the "runner", discovered by `evals/run_self_tests.py`'s
existing sweep (it already runs this harness's `--self-test`, per
`harness-without-self-test-is-invisible` — no new discovery wiring is
needed, only new assertions inside the existing self-test path). "RED" here
means the self-test assertion raises/fails when run with `--self-test`
BEFORE the arm exists, exactly as pytest RED does elsewhere in this task
list.

### `evals/decision_revisions/run_decision_revisions_eval.py` — the `--vector-source reindex` arm

- [x] **P2.1** [TEST] Add a self-test case (in the harness's existing
  `--self-test` assertion block) asserting that running the `reindex` arm
  over the harness's own tiny synthetic fixture (`revision_fixtures.load_fixture()`)
  writes N fixture Decisions into a temporary OKF bundle, runs
  `state.reindex.reindex` with a deterministic FAKE `Embedder` (never
  Ollama — keeps the poisoned-`OLLAMA_HOST` self-test sweep honest), and
  reads back exactly N vectors keyed by concept id via
  `VectorStoreDB.document_vectors` (P1.5). **RED today**: `argparse` has no
  `--vector-source` option and no such arm function exists — the assertion
  cannot even be written against real code yet (`AttributeError`/`SystemExit`
  on the unknown flag).
- [x] **P2.2** [TEST] Same file — add a self-test asserting the arm RAISES
  (never silently returns fewer vectors, never scores a real 0/24 read-back
  loss as a quiet "no") when the read-back vector count differs from the
  written Decision count — inject a fake vector store / embedder that "loses"
  one document's chunks. Kills a read-back that silently returns zero and is
  scored as a real recall failure (`unworked-queue-fakes-a-recall-failure`-
  style hazard, generalized to this harness). **RED today**: same as P2.1 —
  the arm does not exist.
- [x] **P2.3** [IMPL] `evals/decision_revisions/run_decision_revisions_eval.py`:
  add a `--vector-source {text,reindex}` CLI option (default `text` — the
  existing title+body cosine measurement, behavior unchanged); implement the
  `reindex` arm per design.md Decision B5 (write fixture Decisions as real
  OKF concepts into a temporary bundle, run `state.reindex.reindex` with the
  deterministic fake `Embedder`, read vectors back via `document_vectors`);
  raise with an actionable message (not a silent empty result) when the
  read-back count does not match the written count; wire both self-test
  assertions (P2.1, P2.2) into the existing `--self-test` block. Makes P2.1
  and P2.2 GREEN.
- [x] **P2.4** `evals/decision_revisions/README.md`: document the
  `--vector-source {text,reindex}` flag and what each arm measures.

### One committed live measurement (design.md Decision B5 — operator step, not TDD)

- [x] **P2.5** **Operator step, requires a running local Ollama** (per
  project memory: Ollama is startable locally, models already pulled). Run
  `uv run python evals/decision_revisions/run_decision_revisions_eval.py
  --vector-source reindex --runs 15` (flags per the harness's own `--help`;
  confirm the exact invocation against `README.md`'s usage section, updated
  in P2.4) against the real
  `revision_fixture_library.load_library_fixture()` fixture. Commit the
  output as a new file under `evals/decision_revisions/results/` (the
  directory does not exist yet — create it in this commit). **This is the
  measurement design.md Decision B5 requires before the threshold can be
  cited as reproducible** — do not skip it and do not fabricate a result.
  - Done 2026-09-29: `results/decision-revisions-20260929T011322Z-qwen3-8b.md` (+ `runs-...json`), `--vector-source reindex --runs 15`. B1-B8 all pass.
- [x] **P2.6** Compare the committed run's recall against 18/24 at
  `EMBEDDING_SIMILARITY_THRESHOLD = 0.65` (the bar design.md Decision B5
  sets). If it clears the bar, no threshold change is needed (P2.7 cites the
  committed run as-is). If it falls below 18/24, change
  `EMBEDDING_SIMILARITY_THRESHOLD` in THIS slice, before P5 (service) ships,
  per Decision B5's own instruction — do not defer a threshold change past
  this slice. **This step gates P2.7's exact numbers and may gate whether
  P5/P6/P7b need a different constant than the one currently in `src/`.**
  - Done: B2 19 of 24 at 0.65 (confirmation 10 of 10), 46 pre-cap candidates (not the 55 the offline probe predicted; that probe synthesized a `description` the harness does not write). No threshold change.
- [x] **P2.7** [DOC] `src/openkos/resolution/decision_revision.py`: rewrite
  `EMBEDDING_SIMILARITY_THRESHOLD`'s docstring to cite BOTH measurements
  (title+body shape: 19/24 at 46 candidates; reindex `doc_vectors` shape:
  the committed run's numbers from P2.5/P2.6, expected 19/24, 10/10
  confirmation, 55 candidates unless P2.6 changed the threshold), state the
  thin margin (0.66-0.68 is the tightest value still clearing 18/24, 0-1
  pairs of headroom, no clean separation in any shape), keep the overfitting
  caveat, cite the committed results file from P2.5 by name, and REPLACE the
  stale pointer to `graph/proximity.py`'s docstring with a pointer to
  `state/reindex.py`'s `_compose_header`/`EMBED_COMPOSITION_TAG` (per
  Decision B7 — this change only stops CITING the stale docstring; fixing it
  is a separate follow-up, not a task here).
  - Done: docstring cites both shapes, the committed run, the 0-1 pair margin and `_compose_header`/`EMBED_COMPOSITION_TAG`; the `graph/proximity.py` pointer is removed (stale docstring filed as #1052).
- [x] **P2.8** [DOC] `src/openkos/resolution/decision_revision.py`:
  `DecisionInput.subject`'s docstring currently says the field is "kept for
  the service layer's own use (e.g. a future report)" — correct it to say
  production `load_decisions`/`plan_revisions` always pass `None`; only the
  harness sets it (as its own subject-pass diagnostic, per Decision B3).
- [x] **P2.9** [DOC] `src/openkos/resolution/decision_subject.py`: add one
  module-docstring sentence stating the subject pass has no production
  caller as of the Phase B re-plan (Decision B3) — the judge imports only
  `quoted_verbatim` from this module, and the harness runs `derive_subjects`
  as its own diagnostic.

### Slice P2 verification

- [x] **P2.10** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **P2.11** Run `uv run python evals/decision_revisions/run_decision_revisions_eval.py
  --self-test`, then `uv run python evals/run_self_tests.py` (confirms this
  harness's self-test is still discovered), then `uv run pytest` (unpiped)
  full suite — must be green. (This slice adds no `tests/unit/**` file, so
  the pytest suite itself is unaffected except by the docstring edits.)
- [x] **P2.12** Commit as one or more work-unit commits, scope `sdd` (evals
  harnesses are documented under the `sdd` scope precedent in this project's
  commit history for cross-cutting measurement work — confirm against the
  most recent `evals/decision_revisions/` commit before finalizing) or
  `resolution` for the docstring-only commits, e.g. `test(sdd): add a
  production-shape vector arm to the decision-revisions harness` +
  `docs(resolution): cite the reindex-shape measurement in the embedding
  threshold docstring`. Open PR 5 (Slice P2) targeting PR 4's branch.
  **Correction (apply, 2026-09-28): confirming against actual git history**
  **(the P2.12 instruction's own directive) showed the real precedent for**
  **`evals/decision_revisions/` commits is scope `eval`** (e.g. `b9f382b`,
  `eval(decision-revisions): measure-first harness...`), **not `sdd`** — no
  `sdd`-scoped commit exists in this project's history for eval-harness
  work. Landed as two commits instead: `7b793e0`
  `eval(decision-revisions): add a production-shape reindex vector arm
  (#1014)` (P2.1-P2.4) and `0be85dd` `docs(resolution): correct the
  subject-pass docstrings for Phase B (#1014)` (P2.8-P2.9). PR 5 was NOT
  opened by this apply batch (no push, per the executor's own scope) — the
  commits are ready on `feat/1014-phase-b-p2-harness-shape`.

---

## Slice P3 (PR 6): revision findings store, sweep join, forget tests, ADR-0025 narrowing

Depends on nothing new (no upstream Phase B dependency — may be reordered
earlier per design.md). Files: `src/openkos/state/revision_findings.py`
(new), `src/openkos/cli/main.py` (modify `_sweep_findings_for_ids`),
`docs/adr/0025-llm-derived-attributes-live-in-a-cache.md` (rename + rewrite),
`docs/adr/README.md`. Tests: `tests/unit/state/test_revision_findings.py`
(new), `tests/unit/cli/test_forget.py` (modify), one regression test in
`tests/unit/cli/test_contradictions.py` (or `test_status.py`/`test_next.py`
— pick the file whose existing fixtures make seeding both a `findings` row
and a `revision_findings` row easiest; confirm before finalizing).

**Size note**: design.md forecasts this slice at ~550 authored lines
(High risk) and explicitly warns it may not be split further without
breaking its own Decision 2 rule ("every table ships with its sweep join
and sweep tests in the same slice"). If the actual diff lands far over 400
after Phase A's ~1.95x actual-vs-forecast pattern, request `size:exception`
for this PR rather than splitting the table from its sweep.

### `state/revision_findings.py` — schema and REPLACE-per-pair (design.md Decision 2)

- [x] **P3.1** [TEST] `tests/unit/state/test_revision_findings.py` (new
  file) — add `test_record_revision_findings_replaces_per_sorted_pair_key`:
  recording two `RevisionFinding`s for the SAME sorted pair keeps only the
  latest (REPLACE semantics); recording findings for two DIFFERENT pairs
  keeps both; `open_revision_findings` returns rows in insertion order.
  **RED today**: `ModuleNotFoundError` — the module does not exist.
- [x] **P3.2** [TEST] Same file — add
  `test_record_revision_findings_round_trips_every_column_including_nulls`:
  a finding with `quotes=(None, "verbatim text")` and `dates=(None,
  "2026-03-04")` round-trips through `record_revision_findings`/
  `open_revision_findings` with every column preserved, NULLs included.
  **RED today**: same `ModuleNotFoundError`.
- [x] **P3.3** [TEST] Same file — add
  `test_open_revision_findings_on_a_fresh_connection_returns_empty_tuple`:
  a connection with no prior write returns `()`, not an error (the `CREATE
  TABLE IF NOT EXISTS`-on-every-write pattern from `state/edge_suggestions.py`).
  **RED today**: same `ModuleNotFoundError`.
- [x] **P3.4** [IMPL] `src/openkos/state/revision_findings.py` (new module):
  `InputDigest(input_ref, digest)` and `RevisionFinding(pair_ids, verdict,
  confidence, rationale, quotes, dates, date_states, include_confidential,
  prompt_version, input_digests)` (frozen dataclasses, per design.md
  Interfaces); `record_revision_findings(conn, batch)` (REPLACE per sorted
  `(pair_id_0, pair_id_1)`, `CREATE TABLE IF NOT EXISTS` on every write, per
  the `state/edge_suggestions.py` tenant pattern); `open_revision_findings(
  conn) -> tuple[RevisionFinding, ...]` (insertion order, `()` on an absent
  table). Makes P3.1-P3.3 GREEN.

### `state/revision_findings.py` — checked-erasure sweep (design.md Decision 2, privacy sweep)

- [x] **P3.5** [TEST] Same file — add
  `test_delete_revision_findings_referencing_matches_pair_id_0`: a finding
  whose `pair_id_0` is a purge-set member is deleted, along with its
  `revision_finding_input_digests` child rows; an unrelated finding
  survives. **RED today**: `AttributeError` —
  `delete_revision_findings_referencing` does not exist.
- [x] **P3.6** [TEST] Same file — add
  `test_delete_revision_findings_referencing_matches_pair_id_1`: same,
  keyed on `pair_id_1` alone (kills an `OR` narrowed to only `pair_id_0`).
  **RED today**: same.
- [x] **P3.7** [TEST] Same file — add
  `test_delete_revision_findings_referencing_matches_an_input_ref_source_id`:
  a finding whose `revision_finding_input_digests.input_ref` names a
  purge-set member Source id (a `sources/...` id, neither Decision in the
  pair) is deleted. Covers forget-command's "Forgetting a concept scrubs
  its persisted revision finding" at the Source-provenance level. **RED
  today**: same.
- [x] **P3.8** [TEST] Same file — add
  `test_delete_revision_findings_referencing_matches_sources_of_prefix_suffix`:
  a finding whose `input_ref` reads `"sources-of:<purge-id>"` is deleted
  when `<purge-id>` is in the purge set, matched on the SUFFIX (kills an
  exact-match-only comparison that misses the `sources-of:` prefix form).
  **RED today**: same.
- [x] **P3.9** [TEST] Same file — add
  `test_delete_revision_findings_referencing_runs_vacuum_and_checked_checkpoint`:
  after a deletion, VACUUM ran and a subsequent `wal_checkpoint(TRUNCATE)`
  returned a checked non-`busy` row; a stubbed `busy` checkpoint result
  RAISES instead of returning silently (the `edge_suggestions.py:237-299`
  checked-erasure precedent). **RED today**: same `AttributeError`.
- [x] **P3.10** [IMPL] Same module: add `delete_revision_findings_referencing(
  conn, purge_ids) -> int` — the four-arm `OR` delete (`pair_id_0`,
  `pair_id_1`, exact `input_ref` match, `input_ref` `sources-of:<id>` suffix
  match) plus its child `revision_finding_input_digests` rows, then the
  checked-erasure sequence (VACUUM, `wal_checkpoint(TRUNCATE)`, raise on
  `busy`) copied from `state/edge_suggestions.py`. Makes P3.5-P3.9 GREEN.

### `cli/main.py` — sweep join and forget integration

- [x] **P3.11** [TEST] `tests/unit/cli/test_forget.py` — add
  `test_forget_scrubs_a_revision_finding_referencing_the_purged_concept_and_preserves_an_unrelated_one`:
  seed one revision finding whose pair (or Source input digest) names the
  concept `forget` is about to purge, with a verbatim quote from its body,
  and a SECOND, unrelated revision finding; after `openkos forget <id>`
  completes, the first finding is gone and its quoted text is not
  recoverable from `findings.db`'s raw bytes (mirrors the existing
  ledger/decisions-sweep integration tests), and the second finding is
  unchanged. Covers forget-command's "Forgetting a concept scrubs its
  persisted revision finding" and "An unrelated revision finding is
  preserved". **RED today**: `_sweep_findings_for_ids` does not call
  `delete_revision_findings_referencing` yet — the targeted row survives.
- [x] **P3.12** [TEST] Same file — add (or extend the existing corrupt-store
  warning test with) an assertion that the stderr warning text names
  "revision finding(s)" as one of the residue stores, alongside the
  existing findings/adjudications/edge-suggestions wording. Covers
  forget-command's "A corrupt findings store warns instead of aborting"
  (pre-existing behavior; wording widens). **RED today**: the current
  warning text lists only findings/adjudications/edge suggestions.
- [x] **P3.13** [IMPL] `src/openkos/cli/main.py`: `_sweep_findings_for_ids`
  gains a call to `revision_findings_store.delete_revision_findings_referencing(
  conn, set(purge_ids))` on the SAME connection, after the existing
  `delete_edge_suggestions_referencing` call; its stderr warning text widens
  to name "revision findings" as the fourth swept tenant. Makes P3.11 and
  P3.12 GREEN.

### Sibling-table regression pin (proposal's "verified hazard", spec: Revision Findings Persist In Sibling Tables)

- [x] **P3.14** [TEST, no paired IMPL] Add
  `test_revision_findings_do_not_affect_contradiction_status_or_next`
  (in `tests/unit/cli/test_contradictions.py`, or split across
  `test_status.py`/`test_next.py` if that fixture shape is easier — decide
  when writing this task): seed a `revision_findings` row for the SAME
  concept pair a persisted `findings` (contradiction) row already covers;
  `openkos contradictions`, `openkos status`, and `openkos next` each
  produce output byte-identical to a run with no revision-finding row
  seeded. Covers decision-revision-detection's "Contradiction serving is
  unaffected by revision findings" and "status/next/pending surfaces are
  unaffected". **No separate `[IMPL]` task**: this is a pure
  architectural-invariant regression pin — nothing in `contradictions`,
  `status`, `next`, or pending-work code reads `revision_findings` at all
  (it is a sibling table with its own module), so this test goes GREEN as
  soon as P3.4 lands, with zero additional production code. **RED before
  P3.4**: `ModuleNotFoundError` on `state.revision_findings` when the test
  tries to seed a row — write and confirm this RED, then confirm GREEN
  immediately after P3.4, before moving on to P3.5.

### ADR-0025 narrowing (design.md Decision B6, ships with P3 — owner accepted B3 2026-09-28)

- [x] **P3.15** [DOC] Rename `docs/adr/0025-llm-derived-attributes-live-in-a-cache.md`
  to `docs/adr/0025-temporal-direction-never-comes-from-the-model.md`.
  Retitle it "Temporal direction between two concepts never comes from a
  model"; move the per-concept-attribute cache rule (subject/value/evidence
  living in a derived cache keyed by digest + prompt version) to
  "Alternatives considered", marked deferred until it has a real consumer
  (per Decision B3's recommendation to drop the subject pass); keep status
  `Proposed` (archive still flips it to `Accepted` when this change
  archives, since P3 is the slice that realizes the ADR's now-narrower
  claim — the schema stores dates and no holder).
- [x] **P3.16** [DOC] `docs/adr/README.md`: update the ADR-0025 index row's
  title and filename to match P3.15's rename.

### Slice P3 verification

- [x] **P3.17** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **P3.18** Run `uv run pytest tests/unit/state/test_revision_findings.py
  tests/unit/cli/test_forget.py` focused, then the sibling-table regression
  file from P3.14, then `uv run pytest` (unpiped) full suite — must be
  green.
- [x] **P3.19** Commit as one or more work-unit commits, scope `state` if
  it exists as a project scope, else `lint`/`cli` for the sweep-wiring
  half and a bare doc-only commit for the ADR rename (confirm against
  `AGENTS.md`'s scope list and the most recent commit touching
  `state/edge_suggestions.py` before finalizing — e.g. `feat(cli): add the
  revision-findings store, sweep join, and ADR-0025 narrowing`). Tests land
  alongside their behavior. Open PR 6 (Slice P3) targeting PR 5's branch
  (or `main`/tracker directly, since P3 has no upstream Phase B
  dependency, per the chosen chain strategy).

---

## Slice P4 (PR 7): `provenance_source_ancestors_many`

Depends on nothing new — may be reordered earlier per design.md. Files:
`src/openkos/bundle/provenance.py`. Tests:
`tests/unit/bundle/test_provenance.py`.

- [x] **P4.1** [TEST] `tests/unit/bundle/test_provenance.py` — add
  `test_provenance_source_ancestors_many_matches_the_single_id_function`:
  on a fixture with an intermediate concept, a provenance cycle, and a
  dangling Source reference, for EVERY id in the fixture,
  `provenance_source_ancestors_many(files, object_ids=ids)[id] ==
  provenance_source_ancestors(files, object_id=id)`. **RED today**:
  `AttributeError` — `provenance_source_ancestors_many` does not exist.
- [x] **P4.2** [IMPL] `src/openkos/bundle/provenance.py`: extract the walk
  inside `provenance_source_ancestors` into a shared private helper
  operating on an already-parsed `provenance_by_id` map (from
  `_parse_provenance_by_id`); add `provenance_source_ancestors_many(files,
  *, object_ids: Collection[str]) -> dict[str, list[str]]` that parses
  `files` ONCE and calls the shared helper per id (O(1) parses instead of
  O(D)); `provenance_source_ancestors` itself now delegates to the SAME
  helper (parsing once for its own single id) — behavior unchanged for
  every existing caller. Makes P4.1 GREEN.

### Slice P4 verification

- [x] **P4.3** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **P4.4** Run `uv run pytest tests/unit/bundle/test_provenance.py`
  focused, then `uv run pytest` (unpiped) full suite — must be green.
- [x] **P4.5** Commit as one or more work-unit commits, scope `bundle`
  (e.g. `perf(bundle): parse provenance once for many-id ancestor lookups`).
  Open PR 7 (Slice P4) targeting PR 6's branch (or `main`/tracker directly,
  since P4 has no upstream Phase B dependency).

---

## Slice P5a (PR 8): service — decision loading, dates, vector coverage

Depends on P1 (`document_vectors`, `embedding_tag`), P3
(`revision_findings` module exists so the service package can be created
alongside it — no direct symbol dependency yet), P4
(`provenance_source_ancestors_many`). Files: `src/openkos/application/revisions.py`
(new). Tests: `tests/unit/application/test_revisions_service.py` (new).

### `application/revisions.py` — `load_decisions` (design.md Interfaces, Decision 4 exclusions)

- [x] **P5a.1** [TEST] `tests/unit/application/test_revisions_service.py`
  (new file) — add
  `test_load_decisions_excludes_deprecated_confidential_and_bad_relations`:
  three Decisions — one deprecated, one confidential, one with
  unparseable `relations:` frontmatter — `load_decisions(layout,
  include_confidential=False, local_exemption=False)` excludes all three
  from the returned `DecisionSet.decisions`; the deprecated one stays
  excluded even with `include_confidential=True` (there is no
  `--include-deprecated` flag for `revisions`, per design.md Decision 4);
  the confidential one is included when the flag or the local exemption
  releases it; the bad-relations one is counted in a dedicated
  `DecisionSet` field. **RED today**: `ModuleNotFoundError` —
  `application/revisions.py` does not exist.
- [x] **P5a.2** [IMPL] `src/openkos/application/revisions.py` (new module):
  `DecisionSet` dataclass and `load_decisions(layout, *,
  include_confidential, local_exemption)` — deprecated exclusion via
  `lifecycle.deprecated_concept_ids` (always), confidential exclusion via
  `sensitivity.sensitive_concept_ids(..., include_confidential,
  local_exemption)`, bad-relations exclusion + count via
  `okf.decode_relations` fail-closed. Makes P5a.1 GREEN.
- [x] **P5a.3** [TEST] Same file — add
  `test_load_decisions_builds_resolved_with_from_relation_frontmatter`,
  parametrized over each of `model.relations.RESOLUTION_RELATION_TYPES`
  (`supersedes`, `reconciled_with`, `revises`): a Decision whose
  `relations:` frontmatter names a target under that relation type
  populates the Decision's `resolved_with: frozenset[str]` with the
  target id; a relation type OUTSIDE that set does not. **RED today**:
  `AttributeError`/`TypeError` — `DecisionSet`'s per-Decision
  `resolved_with` does not exist yet.
- [x] **P5a.4** [IMPL] Same module: extend `load_decisions` to build each
  surviving Decision's `resolved_with` from `okf.decode_relations`,
  filtered to `model.relations.RESOLUTION_RELATION_TYPES` — the input the
  Phase A leaf's `plan_revision_candidates` already takes as a given
  (design.md Decision 4). Makes P5a.3 GREEN.

### `application/revisions.py` — Decision event-date resolution (design.md Decision 3)

- [x] **P5a.5** [TEST] Same file — add
  `test_resolve_decision_dates_covers_the_date_state_table`, parametrized
  over: `none-reached` (no Source reached); `missing` (an absent
  `event_date`, a malformed one, and a reached Source whose file is
  dangling — three sub-cases); `multiple` (two distinct valid dates
  reached); `dated` (a single valid date, INCLUDING through an
  intermediate concept, not just a direct Source). **RED today**:
  `AttributeError` — the date-resolution function does not exist.
- [x] **P5a.6** [IMPL] Same module: build one `files` snapshot (mirrors
  `list_service.list_provenance_sources`), resolve each Decision's
  `DecisionDate` via `bundle.provenance.provenance_source_ancestors_many`
  (P4.2) + `okf.read_event_date`, applying design.md Decision 3's
  four-case rule exactly (unreadable documents become `NotRun`/counted,
  per design.md). Makes P5a.5 GREEN.

### `application/revisions.py` — `read_decision_vectors` (design.md Decision B1, Interfaces)

- [x] **P5a.7** [TEST] Same file — add
  `test_read_decision_vectors_store_absent_yields_absent_and_creates_no_vectors_db`:
  an absent `.openkos/vectors.db` -> `VectorCoverage(store="absent", ...)`,
  AND a filesystem assertion confirms `.openkos/vectors.db` was NOT
  created as a side effect of the read (kills `open_vector_store`, which
  lazily creates the file, being called BEFORE the `vector_store_is_empty`
  probe). **RED today**: `AttributeError` — `read_decision_vectors`/
  `VectorCoverage` do not exist.
- [x] **P5a.8** [TEST] Same file — add
  `test_read_decision_vectors_sqlite_vec_unavailable_yields_absent`: a
  stubbed/unloadable `sqlite-vec` extension also yields `store="absent"`.
  **RED today**: same `AttributeError`.
- [x] **P5a.9** [TEST] Same file — add
  `test_read_decision_vectors_model_tag_mismatch_or_missing_yields_model_mismatch`,
  parametrized: a stored tag of `None`, and a stored tag that differs from
  `embedding_tag(cfg.embedding_model)` (P1.8) — both yield
  `store="model-mismatch"`. **RED today**: same. Kills the tag compared
  without its `#chunk-v1` composition suffix.
- [x] **P5a.10** [TEST] Same file — add
  `test_read_decision_vectors_per_decision_missing_and_stale`: a Decision
  with no `doc_vectors` row is in `coverage.missing`; a Decision whose
  stored `content_hash` differs from `content_hash(current file bytes)`
  is in `coverage.stale`; both are ABSENT from `coverage.vectors`. **RED
  today**: same. Kills `==` swapped to `!=` on the hash comparison.
- [x] **P5a.11** [TEST] Same file — add
  `test_read_decision_vectors_confidential_interaction`: a confidential
  Decision excluded by `load_decisions` (no flag) never reaches
  `read_decision_vectors`'s input set at all; the SAME Decision made
  eligible with `--include-confidential` and NO stored vector lands in
  `coverage.missing` (never embedded on its behalf); with the flag AND a
  CURRENT stored vector, it is present in `coverage.vectors` (eligible,
  paired). Covers decision-revision-detection's "An included confidential
  Decision without a stored vector is counted, not embedded". **RED
  today**: same (also exercises P5a.2/P5a.4).
- [x] **P5a.12** [IMPL] Same module: `VectorCoverage` dataclass (`store:
  VectorStoreState`, `vectors`, `missing`, `stale`) and
  `read_decision_vectors(layout, decision_ids, files, *, embedding_model)`
  — probe with `vector_store_is_empty` BEFORE `open_vector_store` (the
  P5a.7 ordering), degrade on an unloadable `sqlite-vec`, compare the
  stored model tag via `state.reindex.embedding_tag`, compare each id's
  hash via `document_vectors` (P1.5), partitioning `missing`/`stale`/
  `vectors`. Makes P5a.7-P5a.11 GREEN.

### Slice P5a verification

- [x] **P5a.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **P5a.14** Run `uv run pytest tests/unit/application/test_revisions_service.py
  -k "load_decisions or resolve_decision_dates or read_decision_vectors"`
  focused, then `uv run pytest` (unpiped) full suite — must be green.
- [x] **P5a.15** Commit as one or more work-unit commits, scope
  `application`-adjacent per this project's scope list (confirm the closest
  existing scope, likely `graph` or a bare subsystem term used for prior
  `application/*.py` work, before finalizing — e.g. `feat(cli): add the
  revisions service's decision loading, date resolution, and vector
  coverage`). Open PR 8 (Slice P5a) targeting PR 7's branch.

---

## Slice P5b (PR 9): service — input digests, freshness, candidate planning

Depends on P5a, P3 (`state.revision_findings.open_revision_findings`),
and Phase A's `plan_revision_candidates`/`revision_truncation_notice`
leaf. Same module and test file as P5a.

### `application/revisions.py` — `revision_input_digests` (design.md Decision 2)

- [ ] **P5b.1** [TEST] `tests/unit/application/test_revisions_service.py`
  — add
  `test_revision_input_digests_covers_both_decisions_and_their_reached_sources`:
  for a pair, `revision_input_digests(layout, files, pair_ids)` returns
  exactly `4 + 2 * (distinct reached Sources)` rows in the documented
  order — ordinals 1-2 are each Decision's `content_hash`; 3-4 are
  `sources-of:<id>` (sha256 of the `"\n"`-joined SORTED reached-Source id
  list for each side); 5+ are every reached Source's `content_hash`,
  sorted and deduped across both sides; a MISSING Source file contributes
  NO row for itself (so its tuple differs from a stored tuple that had
  one). **RED today**: `AttributeError` — `revision_input_digests` does
  not exist.
- [ ] **P5b.2** [IMPL] Same module: add `revision_input_digests(layout,
  files, pair_ids) -> tuple[InputDigest, ...]` per design.md Decision 2's
  table, reusing the same `content_hash`-per-file shape as
  `cli.curate.finding_input_digests`. Makes P5b.1 GREEN.

### `application/revisions.py` — `is_fresh` (design.md Decision 2, strict rule)

- [ ] **P5b.3** [TEST] Same file — add
  `test_is_fresh_applies_the_strict_equality_rule`, parametrized: the
  latest row for a pair with matching `prompt_version`, matching
  `include_confidential` (`--include-confidential OR local_exemption`),
  and an EXACTLY equal recomputed digest tuple -> fresh; a superseded
  (non-latest) row for the same pair -> not fresh; `prompt_version`
  mismatch -> not fresh; `include_confidential` mismatch -> not fresh;
  ONE FEWER current digest row than stored (an input that became
  unreadable) -> not fresh, NEVER treated as "unchanged". **RED today**:
  `AttributeError` — `is_fresh` does not exist. Kills the lenient
  `None`-means-unchanged rule `findings._is_stale` uses, which must NOT
  apply here (a revision finding can lead to a bundle write).
- [ ] **P5b.4** [IMPL] Same module: add `is_fresh(layout, finding, *,
  effective_confidential=None) -> bool` implementing design.md Decision
  2's four-condition strict rule exactly. Makes P5b.3 GREEN.

### `application/revisions.py` — `plan_revisions` (serving split, design.md Data flow)

- [ ] **P5b.5** [TEST] Same file — add
  `test_plan_revisions_serves_unchanged_findings_with_zero_llm_calls`: a
  bundle whose Decisions, dates, and vectors are unchanged since the last
  run -> every previously-judged pair appears in `RevisionPlan`'s served
  set and NONE in `to_judge`. **RED today**: `AttributeError` —
  `plan_revisions`/`RevisionPlan` do not exist.
- [ ] **P5b.6** [TEST] Same file — add
  `test_plan_revisions_edited_decision_body_rejudges_only_its_own_pairs`:
  editing one Decision's body moves ONLY pairs containing it from served
  to `to_judge`; every other persisted finding stays served. **RED
  today**: same.
- [ ] **P5b.7** [TEST] Same file — add
  `test_plan_revisions_edited_source_event_date_rejudges_only_affected_pairs`:
  editing one Source's `event_date` moves ONLY pairs whose Decisions
  reach that Source to `to_judge`. **RED today**: same. Kills a missing
  `sources-of:<id>` digest row — exactly the row that catches this edit.
- [ ] **P5b.8** [TEST] Same file — add
  `test_plan_revisions_provenance_path_change_marks_stale`: rewiring an
  intermediate concept so a Decision now reaches a DIFFERENT Source, with
  neither Decision's own body edited, moves the affected pair to
  `to_judge` (caught by the `sources-of:<id>` digest rows 3-4, not by
  rows 1-2). **RED today**: same.
- [ ] **P5b.9** [TEST] Same file — add
  `test_plan_revisions_fresh_flag_bypasses_serving`: `fresh=True` sends
  EVERY eligible candidate to `to_judge` regardless of persisted findings.
  **RED today**: same.
- [ ] **P5b.10** [IMPL] Same module: `RevisionPlan` dataclass (coverage,
  candidate plan, served findings, `to_judge`) and `plan_revisions(
  layout, decisions, *, embedding_model, effective_confidential, fresh)`
  — wires `read_decision_vectors` (P5a.12) into `DecisionInput`s (subject
  always `None`, per Decision B3), calls the Phase A leaf
  `plan_revision_candidates(decisions, vectors)`, computes
  `revision_input_digests` (P5b.2) per candidate, partitions served/
  `to_judge` via `is_fresh` (P5b.4) unless `fresh=True`, reading persisted
  rows from `state.revision_findings.open_revision_findings` (P3.4).
  Makes P5b.5-P5b.9 GREEN.

### Slice P5b verification

- [ ] **P5b.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P5b.12** Run `uv run pytest tests/unit/application/test_revisions_service.py
  -k "digest or is_fresh or plan_revisions"` focused, then the FULL
  `test_revisions_service.py` file (P5a + P5b together), then
  `uv run pytest` (unpiped) full suite — must be green.
- [ ] **P5b.13** Commit as one or more work-unit commits (e.g. `feat(cli):
  add revision input digests, freshness, and candidate planning to the
  revisions service`). Open PR 9 (Slice P5b) targeting PR 8's branch.

---

## Slice P6 (PR 10): service — judging and actionable findings

Depends on P5b (`plan_revisions`, `is_fresh`), P3
(`record_revision_findings`), and Phase A's `judge_pairs`/
`is_actionable_revision` leaf. Same module and test file.

- [ ] **P6.1** [TEST] `tests/unit/application/test_revisions_service.py`
  — add `test_judge_revisions_persists_only_non_malformed_verdicts`: a
  `_ScriptedLLM` returning one malformed reply and one well-formed reply
  across a two-pair `to_judge` list — after `judge_revisions(...)`,
  `state.revision_findings.open_revision_findings` holds EXACTLY the
  well-formed one; the malformed pair is never persisted (so it is
  re-judged next run). **RED today**: `AttributeError` —
  `judge_revisions` does not exist.
- [ ] **P6.2** [TEST] Same file — add
  `test_judge_revisions_partial_batch_persists_the_completed_prefix`: a
  `_RaisingLLM` failing on its 2nd of 3 `to_judge` pairs — the FIRST,
  already-judged pair IS persisted before the failure propagates;
  `judge_revisions` surfaces the same `failure`/`failed_index` contract
  `judge_pairs` (Phase A leaf) returns. Covers "One malformed reply
  degrades without aborting the batch" at the full-run level. **RED
  today**: same `AttributeError`.
- [ ] **P6.3** [IMPL] Same module: add `judge_revisions(layout, plan, *,
  llm, effective_confidential, on_progress=None) -> RevisionOutcome` —
  builds `JudgeSide` pairs from `plan.to_judge` (direction from each
  side's `DecisionDate`), calls `judge_pairs` (Phase A leaf), persists
  each non-malformed `RevisionVerdict` via `record_revision_findings`
  (P3.4) AS IT COMPLETES (so a partial batch keeps its already-paid-for
  prefix), never persists a `malformed=True` result. Makes P6.1 and P6.2
  GREEN.
- [ ] **P6.4** [TEST] Same file — add
  `test_actionable_revision_findings_strict_freshness_and_actionability`:
  of three persisted findings — one fresh AND actionable (REVERSES,
  confidence ≥ 0.7, both quotes verified), one fresh but REAFFIRMS (never
  actionable), one actionable-SHAPED but STALE (digest mismatch) —
  `actionable_revision_findings(layout)` returns ONLY the first. **RED
  today**: `AttributeError` — `actionable_revision_findings` does not
  exist.
- [ ] **P6.5** [IMPL] Same module: add `actionable_revision_findings(
  layout) -> tuple[RevisionFinding, ...]` — takes the LATEST row per pair
  from `open_revision_findings` (P3.4), keeps it only if `is_fresh`
  (P5b.4) and `is_actionable_revision` (Phase A leaf) both hold. Makes
  P6.4 GREEN.

### Slice P6 verification

- [ ] **P6.6** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P6.7** Run `uv run pytest tests/unit/application/test_revisions_service.py`
  (the full file: P5a + P5b + P6 together), then `uv run pytest` (unpiped)
  full suite — must be green.
- [ ] **P6.8** Commit as one or more work-unit commits (e.g. `feat(cli):
  add judging and actionable-finding selection to the revisions service`).
  Open PR 10 (Slice P6) targeting PR 9's branch.

---

## Slice P7a (PR 11): the pure report renderer

Depends on P6 (the shapes it renders — `RevisionPlan`/`RevisionOutcome`/
`RevisionFinding`). Files: `src/openkos/application/revisions_report.py`
(new — kept in `application/` rather than a private `cli/main.py`
renderer: it takes only service-layer result types as input, no CLI/typer
types, so it is unit-testable without a CLI context, the same reasoning
ADR-0018 already applies to `application/revisions.py` itself). Tests:
`tests/unit/application/test_revisions_report.py` (new).

- [ ] **P7a.1** [TEST] `tests/unit/application/test_revisions_report.py`
  (new file) — add `test_counts_line_omits_zero_valued_clauses`,
  parametrized over: `missing` alone, `stale` alone, `excluded` alone, all
  three non-zero, and all three zero (the WHOLE counts line is omitted).
  **RED today**: `ModuleNotFoundError` — the module does not exist.
- [ ] **P7a.2** [TEST] Same file — add
  `test_remedy_clause_only_when_missing_or_stale_is_nonzero`: the
  `"Run 'openkos reindex' to include them."` clause appears only when
  `missing + stale > 0`; a run with only `excluded > 0` prints the counts
  line WITHOUT the remedy clause. Kills a remedy printed with nothing to
  remedy. **RED today**: same.
- [ ] **P7a.3** [TEST] Same file — add
  `test_groups_by_earlier_decision_for_known_direction_with_verdict_ordering`:
  two findings for the same earlier Decision group under its id, ordered
  REVERSES, REFINES, REAFFIRMS (UNRELATED only under `--all`), confidence
  descending within a verdict. **RED today**: same.
- [ ] **P7a.4** [TEST] Same file — add
  `test_groups_by_pair_id_0_for_unknown_direction_with_state_wording`: an
  unknown-direction finding groups under `pair_id_0` and renders
  `[direction unknown: <id>: <state>]` with all four exact phrasings
  (`no event_date`, `2+ distinct event_dates`, `no Source reached`, `same
  event_date`). **RED today**: same.
- [ ] **P7a.5** [TEST] Same file — add
  `test_reaffirms_line_renders_under_the_reaffirmed_decisions_group`: a
  REAFFIRMS finding renders `"reaffirmed by <id> on <date>"` under the
  reaffirmed Decision's group. Covers "A REAFFIRMS finding appears under
  its Decision's group" and (combined with a second assertion re-running
  the renderer over the SAME persisted finding with zero fresh judging)
  "Re-running after serving still renders REAFFIRMS from persisted
  findings". **RED today**: same.
- [ ] **P7a.6** [TEST] Same file — add
  `test_unverified_quote_renders_placeholder_and_not_actionable_tag`: a
  REVERSES/REFINES finding with one unverified quote renders
  `"(no verbatim quote from <id>)"` for that side, is tagged
  `[not actionable: unquoted]`, and STILL appears in the default
  (non-`--all`) view. **RED today**: same.
- [ ] **P7a.7** [TEST] Same file — add
  `test_default_filter_versus_all_flag`: the default view shows
  `is_reportable_revision` results plus any unquoted REVERSES/REFINES;
  `--all` additionally shows UNRELATED, low-confidence, and malformed
  results. **RED today**: same.
- [ ] **P7a.8** [TEST] Same file — add
  `test_empty_result_messages`: zero findings judged or served ->
  `"No decision revisions found."`; zero candidate PAIRS at all ->
  `"No candidate Decision pairs found (need two Decisions from different
  Sources with similar embeddings)."` (Decision B4's revised wording, NOT
  the Phase A "similar subjects" wording). **RED today**: same.
- [ ] **P7a.9** [IMPL] `src/openkos/application/revisions_report.py` (new
  module): the pure renderer over `RevisionPlan`/`RevisionOutcome`-shaped
  inputs, implementing design.md Decision 8 as revised by Decision B4 —
  counts-line clause logic, remedy-clause gate, grouping, line shapes,
  the REAFFIRMS line, the unverified-quote placeholder, the default/
  `--all` filters, and the empty-result messages. Makes P7a.1-P7a.8
  GREEN.

### Slice P7a verification

- [ ] **P7a.10** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P7a.11** Run `uv run pytest tests/unit/application/test_revisions_report.py`
  focused, then `uv run pytest` (unpiped) full suite — must be green.
- [ ] **P7a.12** Commit as one or more work-unit commits (e.g. `feat(cli):
  add the pure revisions report renderer`). Open PR 11 (Slice P7a)
  targeting PR 10's branch.

---

## Slice P7b (PR 12): the `revisions` verb

Depends on P5a/P5b/P6 (the service) and P7a (the renderer). Files:
`src/openkos/cli/main.py` (new `revisions` command), `docs/cli.md`. Tests:
`tests/unit/cli/test_revisions.py` (new).

- [ ] **P7b.1** [TEST] `tests/unit/cli/test_revisions.py` (new file) — add
  `test_help_labels_the_verb_experimental`: `openkos revisions --help`
  output contains `[experimental]`. **RED today**: `openkos revisions`
  does not exist — Typer reports "No such command".
- [ ] **P7b.2** [TEST] Same file — add
  `test_every_run_prints_the_unmeasured_quality_notice_on_stderr`: any
  invocation's stderr includes the Decision B4 experimental-notice line
  verbatim. **RED today**: same.
- [ ] **P7b.3** [TEST] Same file — add
  `test_no_decisions_found_exits_zero_with_message`: an empty bundle ->
  `"No Decision objects found."`, exit 0, zero LLM calls. **RED today**:
  same.
- [ ] **P7b.4** [TEST] Same file — add
  `test_vector_store_absent_or_model_mismatch_exits_zero_with_remedy_and_zero_calls`,
  parametrized over BOTH Decision B1 cases (store absent/empty/`sqlite-vec`
  unavailable; stored tag mismatch or absent) — exit 0, the exact remedy
  message naming `openkos reindex`, ZERO LLM calls AND zero embedding
  calls (inject a raising `Embedder`/embed-hook stub at the one seam the
  verb could reach and assert it is never called). **RED today**: same.
- [ ] **P7b.5** [TEST] Same file — add
  `test_the_one_gate_count_matches_the_stub_llm_call_count`: a
  `_ScriptedLLM` counting calls — the printed gate count equals
  `_ScriptedLLM.calls` after `--auto`-driven completion; a truncation
  notice, when present, prints BEFORE the gate line. **RED today**: same.
  Kills a gate printing a pre-exclusion count.
- [ ] **P7b.6** [TEST] Same file — add
  `test_gate_never_fires_when_to_judge_is_zero`: a fully-served re-run
  (empty `to_judge`) prints no gate line, asks nothing, and makes zero
  LLM calls. Covers "Declining the pair-judgment gate makes no judge
  call" jointly with P7b.8. **RED today**: same. Kills a gate that fires
  at zero.
- [ ] **P7b.7** [TEST] Same file — add
  `test_non_tty_without_auto_refuses`: non-TTY stdin without `--auto` ->
  exit 1, the exact refusal text, zero LLM calls. **RED today**: same.
- [ ] **P7b.8** [TEST] Same file — add
  `test_tty_decline_exits_zero_with_no_calls`: a simulated TTY that
  declines the gate prompt -> `"Aborted -- no revisions judged."`, exit
  0, zero LLM calls, and no revision finding persisted for this run's
  candidates. **RED today**: same.
- [ ] **P7b.9** [TEST] Same file — add
  `test_auto_runs_unattended_on_non_tty`: `--auto` on non-TTY stdin
  proceeds without prompting. **RED today**: same.
- [ ] **P7b.10** [TEST] Same file — add
  `test_full_run_writes_no_bundle_file_and_no_other_derived_store_content`:
  a content snapshot of every file under `bundle/` and of
  `.openkos/vectors.db`'s ROW CONTENT (not raw file bytes — WAL side
  files may legitimately appear), taken before and after a completed
  run that accepts the gate, shows a change ONLY under
  `.openkos/findings.db`. Covers "A full run changes no bundle file and
  no other derived store", "REAFFIRMS and UNRELATED cause no bundle
  write" (the verb never writes bundle at all), and "The verb makes no
  embedding call". **RED today**: same. Kills a bundle write.
- [ ] **P7b.11** [TEST] Same file — add
  `test_partial_batch_renders_completed_then_exits_one`: a raising LLM
  stub partway through `to_judge` -> completed verdicts render, THEN the
  failure line with `"{completed} of {to_judge}"`, exit 1. **RED today**:
  same.
- [ ] **P7b.12** [TEST] Same file — add
  `test_include_confidential_reduces_excluded_count_and_releases_the_judge_send`:
  without the flag, a confidential-eligible pair is excluded from the
  printed count; with the flag AND a current stored vector, it is
  counted eligible and its body reaches the judge (the send is
  released). Covers design.md Decision B2. **RED today**: same.
- [ ] **P7b.13** [IMPL] `src/openkos/cli/main.py`: add the `revisions`
  Typer command — `@_guard_workspace_lock("revisions")`,
  `rich_help_panel="Explore"`, `--auto`/`--include-confidential`/
  `--fresh`/`--all` flags, the Decision B4 sequence (workspace gate,
  `read_config`, experimental notice, `_chat_client`,
  `_resolve_local_exemption`, `service.load_decisions`,
  `service.plan_revisions`, the one gate, `service.judge_revisions`,
  render via `revisions_report` (P7a.9)). Makes P7b.1-P7b.12 GREEN.
- [ ] **P7b.14** `docs/cli.md`: add the `revisions` section — experimental
  label, flags, the one gate, that it READS `.openkos/vectors.db` and
  makes NO embedding call, the `openkos reindex` remedy, and that it
  writes only `.openkos/findings.db`.

### Slice P7b verification

- [ ] **P7b.15** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P7b.16** Run `uv run pytest tests/unit/cli/test_revisions.py`
  focused, then `uv run pytest` (unpiped) full suite — must be green.
- [ ] **P7b.17** Commit as one or more work-unit commits (e.g. `feat(cli):
  add the openkos revisions verb`). Open PR 12 (Slice P7b) targeting PR
  11's branch.

---

## Slice P8a (PR 13): the combined "who is later, which type" prompt

Depends on nothing new inside Phase B (a pure CLI helper) — may be
reordered earlier per design.md. Files: `src/openkos/cli/main.py`. Tests:
`tests/unit/cli/test_reconcile.py`.

- [ ] **P8a.1** [TEST] `tests/unit/cli/test_reconcile.py` — add
  `test_ask_later_decision_and_type_maps_each_numbered_choice`,
  parametrized over `1`-`4`: `[1]` -> `(holder=b, target=a,
  edge_type="supersedes")`; `[2]` -> `(holder=b, target=a,
  edge_type="revises")`; `[3]` -> `(holder=a, target=b,
  edge_type="supersedes")`; `[4]` -> `(holder=a, target=b,
  edge_type="revises")`. **RED today**: `AttributeError` —
  `_ask_later_decision_and_type` does not exist.
- [ ] **P8a.2** [TEST] Same file — add
  `test_ask_later_decision_and_type_skip_and_reask`: `s` and empty input
  BOTH return a skip sentinel (writes nothing, no further consent
  prompt); an unrecognized answer (e.g. `x`) re-asks, mirroring
  `_confirm`'s own loop (`cli/curate.py:690-713`). **RED today**: same.
- [ ] **P8a.3** [IMPL] `src/openkos/cli/main.py`: add
  `_ask_later_decision_and_type(a, b) -> tuple[str, str, str] | None` —
  the combined `[1] <b> replaces <a>  [2] <b> adjusts <a>  [3] <a>
  replaces <b>  [4] <a> adjusts <b>  [s] skip (Enter = s)` prompt
  (design.md Decision 9, step 7), looping on an unrecognized answer.
  Makes P8a.1 and P8a.2 GREEN.

### Slice P8a verification

- [ ] **P8a.4** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P8a.5** Run `uv run pytest tests/unit/cli/test_reconcile.py -k
  ask_later_decision_and_type` focused, then `uv run pytest` (unpiped)
  full suite — must be green.
- [ ] **P8a.6** Commit as one or more work-unit commits (e.g. `feat(cli):
  add the combined later-decision-and-relation-type prompt`). Open PR 13
  (Slice P8a) targeting PR 12's branch (or `main`/tracker directly, since
  P8a has no upstream Phase B dependency).

---

## Slice P8b (PR 14, final): the `reconcile --from-findings` revision walk

Depends on P6 (`actionable_revision_findings`, `is_fresh`) and P8a
(`_ask_later_decision_and_type`). Files: `src/openkos/cli/main.py`
(`_run_reconcile_from_findings`), `docs/cli.md`. Tests:
`tests/unit/cli/test_reconcile.py`.

- [ ] **P8b.1** [TEST] `tests/unit/cli/test_reconcile.py` — add
  `test_reverses_known_direction_offers_supersedes_held_by_the_later_decision`:
  a fresh, actionable, DIRECTED REVERSES finding -> the walk's consent
  prompt reads `"Record <later> supersedes <earlier> (reversal; <earlier>
  is hidden as current)? [y/N]"`; accepting writes `supersedes` held by
  the later Decision via `_reconcile_pair`. **RED today**: the second
  walk does not exist yet — the finding is never offered.
- [ ] **P8b.2** [TEST] Same file — add
  `test_refines_known_direction_offers_revises_held_by_the_later_decision`:
  same shape for REFINES -> `"Record <later> revises <earlier>
  (refinement; both remain current)? [y/N]"`. **RED today**: same.
- [ ] **P8b.3** [TEST] Same file — add
  `test_unknown_direction_routes_to_the_combined_prompt`: an undirected
  (untyped-change) finding routes to `_ask_later_decision_and_type`
  (P8a.3) INSTEAD of the y/N consent P8b.1/P8b.2 use; each of the four
  numbered answers writes the matching relation with no further y/N
  step; `s`/empty writes nothing. **RED today**: same.
- [ ] **P8b.4** [TEST] Same file — add
  `test_reaffirms_and_unrelated_are_never_offered`: persisted REAFFIRMS
  and (separately) UNRELATED findings never appear in the walk's item
  list at all. Covers "REAFFIRMS and UNRELATED cause no bundle write"
  jointly with P7b.10. **RED today**: same.
- [ ] **P8b.5** [TEST] Same file — add
  `test_per_item_freshness_recheck_skips_a_finding_staled_mid_walk`: two
  findings sharing a Decision — accepting the FIRST (which rewrites that
  Decision) makes the SECOND item's immediate re-check
  (`service.is_fresh`) fail, printing `"  skipping <a> <-> <b> --
  changed since it was judged."` and counting it as skipped. **RED
  today**: same. Kills a missing per-item re-check.
- [ ] **P8b.6** [TEST] Same file — add
  `test_already_resolved_pair_interplay`: a pair already resolved
  DIFFERENTLY refuses at `_reconcile_pair`'s at-most-one gate, is
  counted skipped, and the walk CONTINUES to the next item; a pair
  already resolved THE SAME WAY is an idempotent no-op counted as
  applied with no change. **RED today**: same.
- [ ] **P8b.7** [TEST] Same file — add
  `test_contradiction_walk_output_is_byte_identical_when_no_revision_findings_exist`:
  a regression guard — `reconcile --from-findings` output is
  byte-identical to pre-change behavior when no revision findings are
  persisted (the existing walk "runs first and unchanged", design.md
  Decision 9 step 1). **RED today**: N/A as a behavior break — this test
  is written to PASS immediately once P8b.10 lands correctly; write it
  RED-by-absence first (the fixture helper for seeding zero revision
  findings alongside the new walk does not exist until this slice adds
  it), then confirm it stays GREEN through the rest of this slice's
  IMPL.
- [ ] **P8b.8** [TEST] Same file — add
  `test_non_tty_refusal_precedes_both_walks`: the existing non-TTY
  refusal still fires before either walk runs. **RED today**: regression
  guard, written alongside P8b.7.
- [ ] **P8b.9** [TEST] Same file — add
  `test_summary_counts_both_walks_and_lists_revision_declines`: the
  closing `"applied {n}, skipped {n}, declined {n}."` line sums BOTH
  walks; a declined revision item lists as `"  declined: <later>
  supersedes <earlier>"` (directed) or `"<a> <-> <b> (revision, order
  not chosen)"` (undirected skip). **RED today**: same.
- [ ] **P8b.10** [IMPL] `src/openkos/cli/main.py`: inside
  `_run_reconcile_from_findings` (`main.py:10269-10406`), change the
  early `return` after the contradiction walk (`main.py:10330-10336`) to
  "print the existing line, then continue"; add the second walk over
  `service.actionable_revision_findings(layout)` implementing design.md
  Decision 9 steps 2-10 — per-item freshness re-check, directed y/N
  consent via `curate_module._confirm`, the undirected combined prompt
  via `_ask_later_decision_and_type` (P8a.3), the shared `_reconcile_pair`
  write (paths resolved via `application_lifecycle.resolve_concept_path`,
  a resolution failure counted as skipped per `main.py:10344-10354`), the
  at-most-one-gate interplay (`main.py:10383-10389`), and shared
  counters/summary line. Makes P8b.1-P8b.9 GREEN.
- [ ] **P8b.11** `docs/cli.md`: the `--from-findings` help text and the
  `reconcile` section gain the revision-findings sentence (per design.md
  File changes, P8b).

### Slice P8b verification

- [ ] **P8b.12** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **P8b.13** Run `uv run pytest tests/unit/cli/test_reconcile.py` (the
  full file), then `uv run pytest` (unpiped) full suite — must be green.
- [ ] **P8b.14** Commit as one or more work-unit commits (e.g. `feat(cli):
  add the reconcile --from-findings revision walk`). Open PR 14 (Slice
  P8b, the final Phase B slice) targeting PR 13's branch.

---

## Design/spec reconciliation for Phase B (checked now, against the specs on disk)

Design.md's "Phase B re-plan" Decisions B1-B7 and its own "Spec changes the
sdd-spec phase must apply" list (14 items) were checked against
`specs/decision-revision-detection/spec.md` and
`specs/forget-command/spec.md` as they actually read today. **All 14 listed
changes are already applied**: the subject-pass requirements (Post-Hoc
Subject Pass, Verbatim Evidence Quote Check, Subject Cache Keyed By...,
Malformed Subject-Pass Reply) are absent from the spec, not merely marked
REMOVED — confirming they were deleted from an unmerged delta as design.md's
own instruction required; "Zero-LLM Probe Precedes The Cost Gate" and "One
Exact Cost Gate Before Pair Judgment" read as single-gate requirements with
no subject-gate scenario; "`revisions` Writes Only Derived State" names
`.openkos/vectors.db` as a permitted read, forbids any embedding call, and
uses "no other derived store's content changes" wording (the WAL-side-file
carve-out); "Candidate Vectors Come From The Reindexed Vector Store" is
present as a new requirement with all four scenarios design.md specifies;
the forget-command delta drops the subject-cache sentence and scopes "An
unrelated revision finding is preserved" to the revision finding alone. **No
divergence was found** — every literal string this tasks pass wrote into
P1-P8b (gate text, remedy messages, counts-line shape, empty-result
messages) was copied from the spec/design text directly, and the two agree
everywhere they overlap. Per the rule this project's Phase A tasks pass
already established: **should a future edit to either delta spec introduce a
divergence, the spec wins**, and the affected P-slice task must be rewritten
to match it, with a note explaining what design.md said instead.

## Follow-ups (not tasks in this change)

- **`graph/proximity.py`'s stale module docstring** (design.md Decision B7):
  it still cites the pre-#554 raw-frontmatter-and-body embedding shape as
  what `CANDIDATE_SIMILARITY_THRESHOLD = 0.70` was calibrated against, and
  that threshold has never been re-measured on the current `chunk-v1`
  composition. This change only stops CITING that docstring (P2.7); fixing
  it belongs to `graph`'s owner as a separate follow-up issue (a doc
  correction, plus an optional re-measurement).
- **O(D²) pure-Python cosine similarity over 1024-dimensional vectors is
  unmeasured at scale** (design.md, Open questions (Phase B)): at D = 500
  that is roughly 125k pairs, each re-normalizing both vectors. P5b's own
  `uv run pytest` run does not assert on wall time (timing assertions are
  flaky), but if P5b or P7b's manual exercise against a larger bundle shows
  this binds, the recorded mitigation is pre-normalizing each vector once in
  the service (a leaf-compatible change to `plan_revision_candidates`'s
  caller, not to the leaf itself) — not decided now, and not a task in this
  list.
