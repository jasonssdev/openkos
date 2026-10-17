# Tasks: The Review Screen

Tracking issue: #1463. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/desktop-review/spec.md` (a new domain), `specs/stable-python-api/spec.md`
and `specs/local-http-api/spec.md` (deltas), ADR-0066 (Proposed until the
archive slice), and `docs/decision-wording.md` for every word on screen.

PR text names the change in prose ("the `review-screen` change") and uses
`Refs #1463` on every slice but the last, which uses `Closes #1463`.

Spec shorthand: `P1` A Page Is Readable As A Curated Field Set; `P2` The Page
Read Extends v1 Additively; `H1` (local-http-api, modified) The Operations Are
Served Under `/v1`; `H2` An Unknown Page Answers Not Found; `R1` To Review
Lists Every Open Decision In Plain Words; `R2` One Decision Is Decided At A
Time; `R3` A Choice Is Previewed, Then Applied Against Its Version; `R4` The
Person Marks A Group's Members, Its Name And Its Kind; `R5` A Stale, Busy Or
Refused Choice Is Never Applied Blindly; `R6` The Check Is Agreed To Once Per
Batch And Can Be Stopped; `R7` A Combine Is Undone With Split Again; `R8` The
Screen Is Keyboard Operable And Never Signals By Color Alone. `D1`..`D8` are the
design decisions.

Strict TDD for every behaviour with a test: observe RED (a failing test, a
failing check or a compile failure on the missing item), then GREEN, then
refactor. New guards are mutation-checked on their exact line and restored from
a byte copy (Python: purge `__pycache__` first).

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`schema.d.ts`, `openapi.json` and `api_surface.json` are excluded. The
~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0066 | ~750 (docs) | none |
| S2 | The page read: `openkos.api.read_page`, `GET /v1/pages`, client method | ~400 | S1 |
| S3 | The wording module and its tests | ~500 | S2 |
| S4 | The review model (state and transitions) and its tests | ~700 | S3 |
| S5 | Components, render, navigation, styles, docs | ~550 | S4 |
| S6 | CHANGELOG, roadmap, brief, spec merge, ADR-0066 Accepted, archive | ~350 | S5 |

Delivery strategy: stacked-to-main.

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md`, the delta specs and
  ADR-0066 (Proposed, indexed).

## S2 The Page Read

- [x] S2.1 `openkos.api.read_page`, `Page`, `PageSource` (P1, D1): tests for
  the fields, every excerpt rule, the sensitivity fail-closed rank, sources
  from Source documents only, `None` for invalid, missing and unreadable ids,
  and no write. RED: `test_read_page.py` failed to collect (`cannot import
  name 'Page' from 'openkos.api'`). The Source file is read by
  `application.concept_read.read_source_file`; an unquoted `generated.at`
  (parsed as a datetime) is returned as ISO text. The first CI run failed six
  inventory tests the targeted run had not covered: the API facade may import
  only `application` (the excerpt and the sensitivity rank moved to
  `application.concept_read`), the excerpt's link regex joined the link
  recognizer inventory's exemptions, and the route joined both route tables.
- [x] S2.2 Surface snapshot regenerated; the classifier reports every
  difference additive (P2).
- [x] S2.3 `GET /v1/pages?concept_id=` with 404 `not_found` for `None` and 422
  without the parameter (H1, H2); OpenAPI snapshot regenerated. RED: the read
  answered 404 "no such route" and the unknown page carried that message.
- [x] S2.4 `pnpm --dir desktop generate`; `client.readPage(conceptId)`; the
  type-equality test covers it; `check:schema` passes. RED: `tsc` TS2339 and
  TS2344 on `readPage`, and the request test failed.
- [x] S2.5 Mutations on the excerpt cut, the heading drop, the Source-type
  filter and the fail-closed sensitivity. 8 run, each restored from a byte
  copy with `__pycache__` purged: 6 killed at once; the cut-on-a-space guard
  and the `Source` type filter survived and each got a test, then were
  killed.

## S3 Wording

- [x] S3.1 `ui/review/wording.ts`: type nouns, the identity question and line
  by evaluation, group and cross-type wording, connection sentences and their
  reversal, tier lines, options with "what it does", confirmations,
  sensitivity labels, source lines and dates, the list sentences for kinds
  without actions, the check's consent, progress and result, and the problem
  table (R1, R2, R5, R6, R7; D4). Tests per brief section. RED: the test file
  failed to load (`Cannot find module './wording.ts'`). GREEN: 71 tests.
  `identityShape` decides from the engine's withheld reason only: a first draft
  also compared the pages' types, which would have turned a pair with one
  unreadable page into a choice of kinds.
- [x] S3.2 A test that no wording output contains an engine word of the
  brief's rule 1 for the fixtures it is given.
- [x] S3.3 Mutations, each restored from a byte copy: 13 run; 9 killed at
  once; 4 survived (the type-based shape, the already-checked line, `fresh`
  null versus false, a reversed connection's preview) and each got a test,
  then was killed (the first by removing the branch it guarded).

## S4 The Review Model

- [x] S4.1 `ui/review/model.ts` with `ReviewClient`: load (decisions and
  pages), open, decide later (nothing sent), the group selection rules,
  preview, apply with `retryWhileBusy`, stale reload and re-preview,
  problems and their actions, the session's combined pages (R1-R5, R7; D2-D4,
  D6). RED: the test file failed to load (`Cannot find module './model.ts'`).
- [x] S4.2 The check: quote, consent, run with the echoed token, polling with
  an injected sleep, stop, result, `quote_stale` re-quote, failures (R6, D5).
  `tsc` caught a phase comparison it had narrowed away (TS2367): `stopCheck`
  changes the phase during the poll's wait, so the loop reads it through a
  method.
- [x] S4.3 Split again: preview, the later-combines confirmation, apply,
  refusals without `discard_survivor_edits`, the interrupted split (R7, D6).
  GREEN: 38 model tests, 186 in the package.
- [x] S4.4 Mutations, each restored from a byte copy: 22 run. Killed at once:
  the stale re-preview on preview and on apply, never sending `skip`, never
  sending `discard_survivor_edits`, the busy retry around apply and not around
  preview, the quote-stale re-quote and its message, the ready count, the name
  cleared on unmark, the name restricted to the marked pages, nothing marked
  for a group, the only page of a kind named, Try again repeating the apply.
  Survived and got a test: the two-marked rule (one mark with a name), the
  stopping text kept while the run still reports running, the history read
  after a split. The name-among-marked check in `canCombineMarked` survived as
  the redundant half of a pair (`chooseName` and `toggleMark` already keep it)
  and was removed.

## S5 The Screen

- [x] S5.1 `ui/components.ts` (`el`, `button`, `notice`, `heading`, `card`,
  `choiceGroup`, `redraw`) and `ui/review/render.ts`: draw from state with
  `textContent` only; focus restored after a redraw, or moved to the view's
  heading; labelled groups; persistent polite and alert live regions; a notice
  is a symbol, a word and a color (R8, D7). Two transitions the render layer
  needed joined the model test-first: `back()` from a preview (RED:
  `model.back is not a function`), the open decision refreshed after a check,
  and the Undo taken away once its combine is split (RED: both failed on the
  stale decision and the remaining Undo).
- [x] S5.2 Navigation between Status and To review in `main.ts` and
  `index.html` (`aria-current`); one client and one review model per engine
  launch; To review read again on each visit. Styles on tokens only, with a
  visible focus ring on every control.
- [x] S5.3 `ui/source.test.ts`: no file under `desktop/ui/` uses `innerHTML`,
  `outerHTML`, `insertAdjacentHTML`, `document.write` or an inline style, and
  `style.css` uses no color literal and only custom properties `tokens.css`
  defines. RED by mutation, each restored from a byte copy: an `innerHTML`
  write, a hex color and an undefined custom property each failed it.
- [x] S5.4 `docs/architecture.md`: one bullet for the screen's shape
  (ADR-0066).

## S6 Archive

- [x] S6.1 CHANGELOG, roadmap status line, `docs/decision-wording.md` for the
  points the implementation settled (see the archive report).
- [x] S6.2 Merge the delta specs into `openspec/specs/` (new `desktop-review`;
  `stable-python-api` and `local-http-api` deltas); name-match every heading;
  check each requirement against the shipped code.
- [x] S6.3 ADR-0066 Accepted in all three places.
- [x] S6.4 Move the change to `openspec/changes/archive/2026-10-16-review-screen/`
  with `archive-report.md`; verify on `origin/main` that the original folder is
  gone.
