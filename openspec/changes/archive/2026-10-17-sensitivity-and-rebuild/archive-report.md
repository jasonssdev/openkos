# Archive Report: sensitivity-and-rebuild

**Archived**: 2026-10-17 (with the third and last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-sensitivity-and-rebuild/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issues**: #1517 and #1518, closed by the third slice. Follow-up: #1529 (search reads as behind forever with a cloud AI model for search and pages marked Only on this Mac).

## Final State At Close

| Slice | PR | Content |
|-------|----|---------|
| 1 sensitivity, engine and API | #1527 | `application/sensitivity_service.py`, the CLI's shared log line and commit message, `api._sensitivity` and nine public names, two routes, two client methods |
| 2 search, engine and API | the second PR | `vectorstore.read_stored_model_tag`, `ReindexPorts.commit_section`, `model_settings.remote_host`, `application/search_rebuild.py`, `api._search` and ten public names, the rebuild registry, four routes, four client methods |
| 3 screens, wording, archive | the PR that closes #1517 and #1518 | `desktop/ui/label/`, `desktop/ui/search/`, the label choice on To review page cards, Keep only on this Mac beside Ask citations, the rebuild section in Settings, Ask and Status, `docs/decision-wording.md`, this archive |

- **Shipped surface** (additive, `API_VERSION` 1): `PageSensitivity`, `RaisedPage`, `SensitivityPreview`, `SensitivityVersion`, `SensitivityChanged`, `SensitivityRefused`, `SensitivityStale`, `preview_sensitivity`, `apply_sensitivity`; `SearchIndex`, `RebuildPhase`, `RebuildProgress`, `RebuildObserver`, `RebuildOutcome`, `RebuildRefused`, `RebuildStopped`, `SearchUnavailable`, `search_index`, `rebuild_search`. HTTP: `POST /v1/pages/sensitivity/{preview,apply}`, `GET /v1/search`, `POST /v1/search/rebuilds`, `GET /v1/search/rebuilds/{id}`, `POST /v1/search/rebuilds/{id}/stop`; codes `page_not_found` (404), `sensitivity_unchanged` (409), `rebuild_active` (409), `remote_not_allowed` (422), `search_unavailable` (500).
- **ADRs**: none created (design, "ADR gate": every choice is reversible within the additive rule).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 4 ADDED appended: A Page's Sensitivity Is Set Against A Previewed Version; Whether Search Needs Rebuilding Is A Model-Free Read; Search Is Rebuilt With Consent, Progress And A Stop; Sensitivity And Search Extend v1 Additively. |
| local-http-api | Updated | 2 ADDED appended: A Page's Sensitivity Is Served Under `/v1/pages/sensitivity`; Search Rebuilds Are A Pollable Job Under `/v1/search`. Six rows added to the route table of "The Operations Are Served Under `/v1`" and six to the error table. |
| desktop-client | Updated | 1 ADDED appended: The Client Sets A Page's Sensitivity And Rebuilds Search. |
| desktop-review | Updated | 1 ADDED appended: Who Can See A Page Is One Action Wherever A Page Is Shown. |
| desktop-settings | Updated | 1 ADDED appended: Search Is Rebuilt From Where It Is Behind. |

Every `### Requirement:` heading in the five living specs is unique (checked by script). No delta was MODIFIED or REMOVED.

## Requirement-vs-Code Check

Each requirement was read against the shipped code, not the task list: the preview writes nothing and takes no lock (snapshot and held-lock tests); a lowering never cascades, even below the new level (a test written after a surviving mutation); a page that starts citing the Source makes the raise stale; the search read builds no client and creates no store; a remote AI model for search is refused before any client is built without `allow_remote`, and on the built client's verdict; a stop writes nothing (byte snapshot); a busy lock raises at the write; the start of a rebuild answers a refusal itself and a later failure on the poll; the screens take every sentence from the brief (word-for-word tests against `docs/decision-wording.md`).

## Not Done

- The screens were verified in jsdom and the engine paths against a scratch copy of a real library with the local Ollama; the running Tauri app, its dialogs and the keyboard path were not exercised.
- A model change was not smoke-tested against a second installed embedding model; the tests cover it with a fake client.
- #1529: with a cloud AI model for search, a library with confidential pages keeps reading as behind after a rebuild.
