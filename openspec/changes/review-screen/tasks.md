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

- [ ] S2.1 `openkos.api.read_page`, `Page`, `PageSource` (P1, D1): tests for
  the fields, every excerpt rule, the sensitivity fail-closed rank, sources
  from Source documents only, `None` for invalid, missing and unreadable ids,
  and no write.
- [ ] S2.2 Surface snapshot regenerated; the classifier reports every
  difference additive (P2).
- [ ] S2.3 `GET /v1/pages?concept_id=` with 404 `not_found` for `None` and 422
  without the parameter (H1, H2); OpenAPI snapshot regenerated.
- [ ] S2.4 `pnpm --dir desktop generate`; `client.readPage(conceptId)`; the
  type-equality test covers it; `check:schema` passes.
- [ ] S2.5 Mutations on the excerpt cut, the heading drop, the Source-type
  filter and the fail-closed sensitivity.

## S3 Wording

- [ ] S3.1 `ui/review/wording.ts`: type nouns, the identity question and line
  by evaluation, group and cross-type wording, connection sentences and their
  reversal, tier lines, options with "what it does", confirmations,
  sensitivity labels, source lines and dates, the list sentences for kinds
  without actions, the check's consent, progress and result, and the problem
  table (R1, R2, R5, R6, R7; D4). Tests per brief section.
- [ ] S3.2 A test that no wording output contains an engine word of the
  brief's rule 1 for the fixtures it is given.

## S4 The Review Model

- [ ] S4.1 `ui/review/model.ts` with `ReviewClient`: load (decisions and
  pages), open, decide later (nothing sent), the group selection rules,
  preview, apply with `retryWhileBusy`, stale reload and re-preview,
  problems and their actions, the session's combined pages (R1-R5, R7; D2-D4,
  D6).
- [ ] S4.2 The check: quote, consent, run with the echoed token, polling with
  an injected sleep, stop, result, `quote_stale` re-quote, failures (R6, D5).
- [ ] S4.3 Split again: preview, the later-combines confirmation, apply,
  refusals without `discard_survivor_edits`, the interrupted split (R7, D6).
- [ ] S4.4 Mutations on the two-marked rule, the stale re-preview, never
  sending `skip`, never sending `discard_survivor_edits`, the busy retry only
  around writes, the quote-stale re-quote.

## S5 The Screen

- [ ] S5.1 `ui/components.ts` and `ui/review/render.ts`: draw from state with
  `textContent` only; focus restored after a redraw; labelled groups; status
  and alert regions (R8, D7).
- [ ] S5.2 Navigation between the status page and To review in `main.ts` and
  `index.html`; styles on tokens only, with a visible focus ring.
- [ ] S5.3 A test that no file under `desktop/ui/` uses `innerHTML`,
  `outerHTML`, `insertAdjacentHTML` or `document.write`, and that `style.css`
  uses no color literal outside `tokens.css`.
- [ ] S5.4 `docs/architecture.md` (the `ui/` tree) only if its shape changes.

## S6 Archive

- [ ] S6.1 CHANGELOG, roadmap status line, `docs/decision-wording.md` for any
  point the implementation settled.
- [ ] S6.2 Merge the delta specs into `openspec/specs/` (new `desktop-review`;
  `stable-python-api` and `local-http-api` deltas); name-match every heading;
  check each requirement against the shipped code.
- [ ] S6.3 ADR-0066 Accepted in all three places.
- [ ] S6.4 Move the change to `openspec/changes/archive/<date>-review-screen/`
  with `archive-report.md`; verify on `origin/main` that the original folder is
  gone.
