# Delta for stable-python-api

## ADDED Requirements

### Requirement: A Page's Sensitivity Is Set Against A Previewed Version

`openkos.api` MUST export `PageSensitivity` (`"public"`, `"private"`,
`"confidential"`), `preview_sensitivity(workspace, concept_id, level)` and
`apply_sensitivity(workspace, version, *, wait_seconds=0.0, on_warning=None)`,
planning exactly as `openkos set-sensitivity` does. A level outside
`PageSensitivity` MUST raise `ValueError`. An invalid Concept ID or a page that
does not exist MUST return `SensitivityRefused("not_found")`; a raw stored
value equal to `level` MUST return `SensitivityRefused("unchanged")`.

The preview MUST return a `SensitivityPreview` with `concept_id`, `title`,
`current` (the stored level ranked fail-closed: missing or blank is `private`,
unrecognised is `confidential`), `level`, `direction` (`raise`, `same` or
`lower`), `also_raised` and a `SensitivityVersion` (`concept_id`, `level`,
`content_digest`). Only a raise of a page whose `type` is `Source` MUST list
pages in `also_raised` (`RaisedPage`: `concept_id`, `title`, `before`,
`after`): each provenance descendant whose level the Source's new level
raises, as `set-sensitivity` stages them. A lowering MUST NOT change any other
page. The digest MUST cover the page's bytes and, for a cascading raise, every
page the closure was resolved from. The preview MUST NOT take the lock, build a
client or write.

The apply MUST take the lock once, plan again, and return `SensitivityStale`
(the bytes or the echoed version changed) or `SensitivityRefused` without
writing. Otherwise it MUST write the raised pages, then the page, then one
`log.md` line in the verb's words, and commit only those paths with the verb's
message, returning `SensitivityChanged` (`concept_id`, `level`, `previous` —
the stored value replaced, `None` when absent — `also_raised` ids,
`commit_sha`, `committed_confidential`). A commit that cannot be made MUST be
reported once through `on_warning` and leave `commit_sha` `None`. It MUST NOT
refresh a derived store or reach a model.

#### Scenario: Marking a page confidential writes one field and one commit

- GIVEN a private page `concepts/garden` in a git-backed workspace
- WHEN it is previewed and applied at `confidential`
- THEN its `sensitivity` reads `confidential`, its body is unchanged, `log.md`
  holds one Set-sensitivity line, and HEAD holds exactly those two files

#### Scenario: Raising a Source raises what was made from it

- GIVEN a Source and a private page whose provenance is that Source
- WHEN the Source is set to `confidential`
- THEN the preview lists the page in `also_raised` and the apply raises both in
  one commit

#### Scenario: Lowering a Source never lowers its descendants

- GIVEN a confidential Source with a confidential descendant
- WHEN the Source is set to `private`
- THEN `also_raised` is empty and the descendant stays `confidential`

#### Scenario: A page that starts citing the Source makes the raise stale

- GIVEN a previewed raise of a Source
- WHEN a new page citing the Source appears before the apply
- THEN the apply returns `SensitivityStale` and nothing is written

### Requirement: Whether Search Needs Rebuilding Is A Model-Free Read

`openkos.api.search_index(workspace)` MUST return a `SearchIndex` with
`model_changed` (the vector store holds vectors under an embedding tag other
than the one the configured AI model for search and backend produce),
`stale_indexes` (the derived indexes whose contents predate the pages, as
`status` names them), `needs_rebuild` (true exactly when either holds) and
`sends_to` (the redacted host of the embedding endpoint when it is not on this
machine, else `None`). It MUST NOT build a client, take the lock, create a
store or write. An unreadable `openkos.yaml` MUST raise `WorkspaceUnreadable`.

#### Scenario: A changed AI model for search needs a rebuild

- GIVEN vectors stored under the tag of `bge-m3`
- WHEN `embedding_model` is changed and `search_index` is called
- THEN `model_changed` and `needs_rebuild` are true and nothing is written

#### Scenario: A library with nothing embedded is not changed

- GIVEN a workspace with no vector store
- WHEN `search_index` is called
- THEN `model_changed` is false and no store is created

### Requirement: Search Is Rebuilt With Consent, Progress And A Stop

`openkos.api.rebuild_search(workspace, *, allow_remote=False, stop=None,
observer=None, wait_seconds=...)` MUST run the incremental rebuild the CLI's
`reindex` runs, over the same service. When the embedding client is not
verifiably on this machine and `allow_remote` is false it MUST return
`RebuildRefused("remote_not_allowed")` before any page is sent. Confidential
pages MUST be withheld from a client that is not on this machine whatever
`allow_remote` says.

Embedding MUST run without the workspace lock; the store writes MUST run inside
it, waiting at most `wait_seconds`, and a busy lock or busy store MUST raise
`WorkspaceBusyError`. The observer MUST receive `RebuildProgress` (`phase`
`embedding` with `done` and `total`, then `finishing`). A stop MUST be observed
before the next page and before any store write, and MUST return
`RebuildStopped` with nothing written. A finished run MUST return
`RebuildOutcome` (`embedded`, `unchanged`, `removed`, `skipped`, `failed`,
`withheld`, `complete` — true exactly when nothing was skipped, failed or
withheld). An unreachable runtime, a missing model and a changed dimension MUST
raise `BackendUnavailable`, `BackendModelNotFound` and
`BackendEmbeddingDimensionMismatch`; an unusable store or a failed graph write
MUST raise `SearchUnavailable`.

#### Scenario: A remote AI model for search needs consent

- GIVEN `backend: openai-compatible` with a remote `base_url`
- WHEN `rebuild_search` is called without `allow_remote`
- THEN it returns `RebuildRefused("remote_not_allowed")` and no page is sent

#### Scenario: A stopped rebuild changes nothing

- GIVEN a rebuild in progress
- WHEN the stop is set after the first page is embedded
- THEN it returns `RebuildStopped` and the vector store is byte-identical

### Requirement: Sensitivity And Search Extend v1 Additively

The names `PageSensitivity`, `RaisedPage`, `SensitivityPreview`,
`SensitivityVersion`, `SensitivityChanged`, `SensitivityRefused`,
`SensitivityStale`, `preview_sensitivity`, `apply_sensitivity`, `SearchIndex`,
`RebuildPhase`, `RebuildProgress`, `RebuildObserver`, `RebuildOutcome`,
`RebuildRefused`, `RebuildStopped`, `SearchUnavailable`, `search_index` and
`rebuild_search` MUST be added within `API_VERSION` 1, with the surface
snapshot regenerated and classified additive.

#### Scenario: The snapshot classifies the change as additive

- WHEN the surface snapshot is regenerated
- THEN every difference is a name added and `API_VERSION` is still 1
