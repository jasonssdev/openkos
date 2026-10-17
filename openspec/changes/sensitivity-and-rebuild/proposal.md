# Proposal: Who Can See A Page, And Rebuilding Search, From The App

Tracking issues: #1517 (mark a page Only on this Mac in one action) and #1518
(rebuild search after the AI model for search changes). Refs #1477, #1514.
Governing decisions: ADR-0008 (an explicit human assignment may lower a level),
ADR-0009 (raise-only propagation from a Source), ADR-0036 (split verbs hold the
lock only around their commit phase), ADR-0058 (the local API's boundary),
ADR-0060 (the versioned Python API), ADR-0061 (the local API adapter), ADR-0065
(the generated TypeScript client), ADR-0066 (plain DOM screens over tested view
models), ADR-0067 (two-phase operations keyed by what they write), ADR-0068
(asking applies the query disclosure rule). Paths are under `src/openkos` unless
they start with `tests/`, `docs/`, `desktop/` or `openspec/`.

## Intent

MVP 6 says that while a cloud AI model is on, "marking something confidential
is one action away" (roadmap), and `docs/decision-wording.md` says "Changing
the label is one action from any page." Neither is possible in the app:
`openkos.api` has no operation that sets a page's `sensitivity`. The settings
screen turns a cloud model on behind a warning that names the labels, but
offers no way to change one.

Changing the AI model for search makes the stored vectors stale; the settings
screen says "Search needs to be rebuilt" and the app has no way to do it. Only
`openkos reindex` does, in a terminal.

## Scope

### In scope

- **Sensitivity, engine and API.** `preview_sensitivity(workspace, concept_id,
  level)` and `apply_sensitivity(workspace, version, ...)`: the plan of `openkos
  set-sensitivity` — exact-match no-op, raise-only propagation from a Source to
  its provenance descendants, the verb's log line and commit message — as a
  two-phase operation keyed by a digest, one lock, one commit. A new
  `application/sensitivity_service.py`; the CLI shares its log line and commit
  message.
- **Search, engine and API.** `search_index(workspace)`, a model-free read of
  whether search needs rebuilding (the AI model for search changed, or the
  lexical indexes predate the pages) and where the text would go; and
  `rebuild_search(workspace, *, allow_remote, stop, observer, wait_seconds)`,
  the incremental `reindex` with progress and a stop. A run that would send
  text off this machine needs `allow_remote`. The lock is held only around the
  store writes (ADR-0036).
- **Local API and client.** `POST /v1/pages/sensitivity/{preview,apply}`;
  `GET /v1/search`, `POST /v1/search/rebuilds`, `GET
  /v1/search/rebuilds/{id}`, `POST /v1/search/rebuilds/{id}/stop`, a pollable
  job like the model pull; the TypeScript client regenerated.
- **Screens.** The page's label as one action wherever the app shows a page,
  with Undo; a "Rebuild search" action where Settings says a rebuild is needed,
  from Ask's stale-search note, and on Status while search is stale.
- **Wording** in `docs/decision-wording.md`.

### Out of scope

- A page view of its own. A small page panel opened from where a page is shown
  is enough for the label.
- A forced rebuild (`reindex --force`). The incremental rebuild already
  re-embeds every page when the AI model for search changed.
- Refreshing the derived stores after a sensitivity change. The API refreshes
  none after a write (`openkos.api` statement 5); the sending rules read the
  page itself, so a stale index never sends a page it should not.

## Approach

Both operations reuse the engine's own code: the sensitivity plan calls the
same resolver, propagation and log helpers `set-sensitivity` calls, and the
rebuild calls `application/reindex_service.reindex_workspace`, the service the
CLI's `reindex` and the daemon call. The service gains one optional port, a
commit section, so the API can hold the lock only around the store writes. A
stop is observed between documents, before anything is written: the vectors
are committed once at the end of a run, so a stopped run leaves search exactly
as it was.

## Rollback

Every addition is additive in `API_VERSION` 1 and the routes are new. Reverting
the PRs removes them; no stored format changes. The one shared-code change, the
optional commit-section port of the reindex service, defaults to holding
nothing, which is the CLI's and the daemon's behaviour today.

## Principles

Local-first: a rebuild sends text only to the configured AI model for search,
and off this machine only with consent. Confidential pages are never sent off
it (the reindex withholding rule). Human curates: a lowering is applied only
after a preview the person saw. Reconstructible: the rebuild writes only the
derived stores.
