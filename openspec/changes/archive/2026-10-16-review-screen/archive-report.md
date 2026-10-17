# Archive Report: review-screen

**Archived**: 2026-10-16 (S6, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-review-screen/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1463

## Final State At Close

- **Implementation**: five slices before this one merged to `main`, each one squash-merged PR, stacked to `main` in order. S6 (this archive) closes the issue.

| Slice | PR | Squash commit | Authored lines (additions + deletions; generated `openapi.json`, `api_surface.json`, `schema.d.ts` excluded) | CI `test (3.14)` | CI `desktop client` | CI `desktop bundle (macOS)` |
|-------|----|---------------|------------------------------------------------------------------------------------------------------------------|------------------|---------------------|-----------------------------|
| S1 plan, ADR-0066 | #1464 | `cd55bd65` | about 930 (docs) | 11m45s | 25s | 2m12s |
| S2 the page read: `read_page`, `GET /v1/pages`, `readPage` | #1465 | `be004464` | about 545, 334 of them tests (`size:exception`) | 11m59s | 26s | 3m11s |
| S3 the wording module | #1468 | `0aa42c1c` | about 1,290, 605 of them tests (`size:exception`) | 11m46s | 35s | 2m30s |
| S4 the review model | #1469 | `c71c7e01` | about 1,280, 650 of them tests (`size:exception`) | 9m41s | 23s | 2m12s |
| S5 the screen, components, navigation, styles | #1470 | `0dba09aa` | about 1,000, 271 of them `style.css` (`size:exception`) | 9m38s | 21s | 2m21s |
| S6 CHANGELOG, roadmap, brief, spec merge, ADR-0066, archive | this PR | | docs only | | | |

- **Readiness, checked first**: judging was already reachable from the app (`POST /v1/judging/quote`, `POST /v1/judging/runs`, `GET /v1/judging/runs/{id}`, `POST /v1/judging/runs/{id}/stop`), as were decisions with group selections, merge history and unmerge. The one gap was reading a page: a decision names its pages by Concept ID only, and the brief's evidence, questions and cross-type choice need titles, types, openings and source files.
- **Engine and API (S2)**: `openkos.api.read_page`, `Page`, `PageSource`, over `application.concept_read` (`read_concept`, plus `read_source_file`, `plain_excerpt` and `sensitivity_level`); `GET /v1/pages?concept_id=<id>`, with an unknown page answered 404 `not_found` (no new error code). The surface classifier reported three names added; `API_VERSION` stays 1. Both snapshots and the client's types were regenerated; `client.readPage` was added.
- **Shipped screen (S3-S5)**: `desktop/ui/review/wording.ts` (every word, from the brief), `desktop/ui/review/model.ts` (`ReviewModel` and `ReviewClient`, every transition), `desktop/ui/review/render.ts` (drawing only), `desktop/ui/components.ts` (`el`, `button`, `notice`, `heading`, `card`, `choiceGroup`, `redraw`), navigation in `main.ts` and `index.html`, styles on `tokens.css` only, and `ui/source.test.ts`.
- **ADR-0066**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| desktop-review | Created | A new domain. The delta's 8 ADDED requirements became the living spec under a new Purpose section: To Review Lists Every Open Decision In Plain Words; One Decision Is Decided At A Time, Worded From The Brief; A Choice Is Previewed, Then Applied Against Its Version; The Person Marks A Group's Members, Its Name And Its Kind; A Stale, Busy Or Refused Choice Is Never Applied Blindly; The Check Is Agreed To Once Per Batch And Can Be Stopped; A Combine Is Undone With Split Again; The Screen Is Keyboard Operable And Never Signals By Color Alone. |
| stable-python-api | Modified | 2 ADDED requirements appended: A Page Is Readable As A Curated Field Set; The Page Read Extends v1 Additively. The Purpose's v1 operation list now names reading the pages a decision shows. |
| local-http-api | Modified | The Operations Are Served Under `/v1`, One Per Python API Operation replaced by the delta's text (the `GET /v1/pages` row; the page route named among those carrying a Concept ID as a query parameter); one scenario added (A page maps to the page read). 1 ADDED requirement appended: An Unknown Page Answers Not Found. |

The deltas' "(Previously: ...)" notes were not carried into the living specs.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against the living spec after the merge: desktop-review 23 headings (8 requirements, 15 scenarios), stable-python-api 7 (2 requirements, 5 scenarios), local-http-api 13 (2 requirements, 11 scenarios); 0 missing, no duplicate heading in any of the three living specs. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against the shipped files, not against the task list.

| Requirement | Result |
|-------------|--------|
| A Page Is Readable As A Curated Field Set | Met. `read_page` builds `Page` from `read_concept`'s curated record only; `plain_excerpt` drops a leading `# ` heading equal to the title, keeps link labels, drops `[n]`, emphasis and heading marks, collapses whitespace and cuts at the last word boundary before 280 characters with `…`; `sensitivity_level` is `okf.combine_sensitivity(raw, "public")`; `read_source_file` returns a `Source`'s `raw/` base name and `generated.at` (a parsed timestamp as ISO text). `ConceptNotFound` and `UnreadableConcept` return `None`. `test_read_page.py` covers each rule and the unchanged tree. |
| The Page Read Extends v1 Additively | Met. `api_surface.json` regenerated; the classifier printed three `additive` lines (`Page`, `PageSource`, `read_page`); `API_VERSION` is 1. |
| The Operations Are Served Under `/v1` (modified) | Met. `_routes.py` serves `GET /pages` calling `api.read_page`; `test_routes_security.py` and `test_openapi_snapshot.py` list it. |
| An Unknown Page Answers Not Found | Met. `None` raises an `ApiProblem` of 404 `not_found` with the fixed message "no such page"; a missing `concept_id` is 422 `invalid_request`; tests check the body never contains the id. |
| To Review Lists Every Open Decision In Plain Words | Met. `drawList` lists every decision through `listEntry` (titles only; "A page that couldn't be read" for an unread page), tags changed ones, says "Nothing to review right now." for an empty queue; kinds without actions get the brief's sentence and "This can't be settled in the app yet."; `Status` and `To review` switch views. |
| One Decision Is Decided At A Time, Worded From The Brief | Met. `describeDecision` gives the question, the lead by `evaluation`, the evidence (title, kind, excerpt, who can see it, "From:" lines), the model's reason in quotation marks, and one option per offered action plus Decide later; an unjudged pair offers Check these and no combine; `choose` of Decide later or `skip` sends nothing. |
| A Choice Is Previewed, Then Applied Against Its Version | Met. `#preview` then `confirm` sends `preview.version` unchanged; `previewLines` and `confirmation` word the plan and the result, with the Only-on-this-Mac line on `committed_confidential`; the list is reloaded; only a combine's confirmation carries Undo. |
| The Person Marks A Group's Members, Its Name And Its Kind | Met. A group opens with nothing marked; `canCombineMarked` needs two marks and a name kept among them by `toggleMark` and `chooseName`; the render shows "Mark at least two pages" as the disabled option's described reason; a kind narrows the names, asks only when two share it, and the selection is sent as `{survivor_id, member_ids}`. |
| A Stale, Busy Or Refused Choice Is Never Applied Blindly | Met. Apply runs in `retryWhileBusy`; `stale`/`write_drift` reload and re-preview once with the changed notice, or return to the list saying so; `problemFor` words every code in the brief's table and never claims nothing changed after a write it cannot name. |
| The Check Is Agreed To Once Per Batch And Can Be Stopped | Met. `startCheck` quotes and shows `checkConsent`; `agreeCheck` starts with `quote.token`, polls every `POLL_MS`, words progress and stopping; `stopCheck` posts the stop; the result counts what became decidable; `quote_stale` re-quotes and shows the changed sentence. |
| A Combine Is Undone With Split Again | Met. "Combined just now" shows `historyLine` per record with Split again; `openSplit` previews with survivor and absorbed only (never `discard_survivor_edits`), `splitQuestion` lists the later combines; `survivor_edited` offers OK only; an interrupted split reads the history again. |
| The Screen Is Keyboard Operable And Never Signals By Color Alone | Met in code; not visually verified. Native buttons, checkboxes and radios with text labels; `fieldset`/`legend` groups; `:focus-visible` outlines; `redraw` restores focus; persistent `role="status"` and `role="alert"` regions; `notice` pairs every tone with a symbol and a word; `source.test.ts` bars markup writes, inline styles, color literals and undefined custom properties. |

## Decisions Taken During Implementation

1. **The page read's format rules live in `application` (S2).** The first CI run failed six inventory tests the targeted run had not covered. The API facade may import only `application`, so the excerpt and the sensitivity rank moved into `application.concept_read`. The excerpt's link regex joined the link-recognizer inventory's exemptions (it resolves no destination). The route joined both route tables.
2. **The identity question's shape comes from the engine's withheld reason only (S3).** A draft also compared the pages' types, which would have turned a pair with one unreadable page into a choice of kinds. A surviving mutation showed it.
3. **Busy is retried around writes only (S4).** A preview is not retried blindly; Try again repeats it. After a write was sent, an unnamed failure says "Look at To review to see what changed."
4. **Undo only where the API has one.** Keep separate, connections and how-quickly settings show no Undo until #1466. Split again lists this session's combines until #1467.
5. **Brief points the implementation settled**, written into `docs/decision-wording.md`:
   - the cross-type "already noted" sentence names no earlier kind, because the plan carries none;
   - the cloud consent names the provider through the model, because the quote names the model only;
   - the singular and empty forms of "{n} other pages";
   - the one-step split question and confirmation;
   - "Check all" on the list;
   - "These are already checked.";
   - `backend_model_not_found`;
   - OK in place of "Show it" and "open the other page";
   - Try again in place of "Start it" until guided setup;
   - the gone-after-stale message;
   - the two unnamed-failure messages;
   - #1442 recorded as decided (refusal only).

## Verification Record

- **Strict TDD.** Each slice saw its tests fail before the code that passed them:
  - S2: `test_read_page.py` failed to collect; the route answered 404 "no such route"; `tsc` reported TS2339/TS2344 on `readPage`.
  - S3: the wording test failed to load.
  - S4: the model test failed to load.
  - S5: `model.back is not a function`, then two tests failing on the stale decision after a check and the leftover Undo.
- **Mutations** on the exact guarded lines, each restored from a byte copy (Python with `__pycache__` purged):
  - S2: 8, all killed after two tests were added.
  - S3: 13. Four survived and got tests; one of those removed a defective branch.
  - S4: 22. Three survived and got tests; one redundant guard was removed.
  - S5: three on `source.test.ts`, all killed.
- **Local runs.**
  - Python on S2: `ruff check`, `ruff format --check` and `mypy .` were clean. `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov` passed 13,561 (6 skipped, 96.77% coverage) after the fix. `evals/run_self_tests.py` ran 58 of 58 with none failing.
  - Node on every slice: `typecheck`, vitest (196 tests at S5), `check:schema` and `build` were clean.
  - Rust was not touched, so no `cargo` run was made locally. CI's shell and bundle jobs built the page and passed on every PR.
- **Not verified**:
  - Visual rendering of the screen. No screenshot, screen recording or UI automation was used, because this machine permits none.
  - The screen against a live engine with a real queue.
  - The MVP 6 exit-criteria session (`evals/desktop_app/PREREGISTRATION.md`, S5), which needs guided setup and a participant.

## Follow-ups

- #1466: API operations for undoing keep separate, connections and how-quickly settings, so the app can offer the brief's Undo for them.
- #1467: an API read listing every combined page, so Split again survives an app restart.
