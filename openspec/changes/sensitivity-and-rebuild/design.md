# Design: Who Can See A Page, And Rebuilding Search, From The App

## Decisions

### D1. The sensitivity change is `set-sensitivity`'s plan, in two phases

`application/sensitivity_service.py` plans exactly as the verb does: the level
must be one of the three (`ValueError` otherwise), the id goes through
`lifecycle.resolve_concept_path` (an invalid id or a missing page is
`not_found`), an exact match of the raw stored value is `unchanged`, the
direction is `okf.sensitivity_direction`, and only a raise of a page whose type
is `Source` stages descendant raises through
`bundle.provenance.resolve_source_raises` (ADR-0009). A lowering never
cascades. The log line and the commit message are functions of the service
that the CLI now calls too, so the history reads the same from either surface.

The digest covers the page's bytes and, for a cascading raise, every page in
the whole-bundle snapshot the closure was resolved from: a page that starts
citing the Source after the preview would escape the raise, so it must make
the apply stale (the read-dependency rule of ADR-0036). The apply takes the
lock once, plans again and writes only on a matching digest, as the undo
operations of ADR-0067 do. Write order is the verb's — descendants, page,
`log.md` — so a failure part-way leaves the bundle over-classified.

What the CLI has and the API does not: the confirmation prompt, the
`--allow-downgrade` gate and the notes. ADR-0008 lets a lowering ride on a
review; here the review is the preview the app shows before it applies, so a
lowering needs no extra flag. The app adds friction where it matters: lowering
"Only on this Mac" while a cloud AI model is on asks first (D6).

No derived store is refreshed (statement 5 of `openkos.api`). The sending rules
re-read each page's frontmatter at send time, so a stale index never sends a
page that is now marked "Only on this Mac".

Rejected: lifting the CLI command body into the service wholesale (its output,
prompt and gates are interleaved with the plan, and the verb's behaviour is
pinned by a large suite); a one-shot write with no preview (a lowering and a
cascading raise must be seen before they happen).

### D2. Search state is a model-free read

`search_index(workspace)` reports `model_changed` (the vector store holds
vectors under an embedding tag other than `embedding_tag(embedding_model,
backend)`, read over a read-only connection that never creates the store),
`stale_indexes` (`status.stale_index_names`, the lexical and graph indexes that
predate the pages), `needs_rebuild` (either), and `sends_to` (the redacted host
of the AI model for search when it is not on this machine, as
`classify_backend_host` judges it, else `None`). It builds no client, takes no
lock and writes nothing.

A vector store with no stored tag and no vectors is not "changed": a library
with nothing embedded yet has nothing to rebuild for a model change.

### D3. The rebuild is the reindex service with a commit section and a stop

`rebuild_search` calls `reindex_service.reindex_workspace(force=False)`. The
incremental run is the right one: the embedding-model gate already re-embeds
every page when the tag changed, and an unchanged page costs nothing.

`ReindexPorts` gains `commit_section` (default `None`, holding nothing — the
CLI and the daemon are unchanged). The API passes the workspace lock's commit
section, so embedding runs unlocked and only the store writes (vectors and FTS,
then the graph) take the lock (ADR-0036). A busy lock raises
`WorkspaceBusyError`.

A stop is observed in the per-document progress hook. Raising there ends the
run before the store write — the vectors are committed once, at the end — so a
stopped rebuild writes nothing and is reported as `RebuildStopped`. It is
therefore honest to say "search wasn't changed".

### D4. Sending text off this machine needs consent at the call

When the AI model for search is not on this machine, a rebuild sends the text
of every page that is not confidential. `rebuild_search` refuses with
`RebuildRefused("remote_not_allowed")` unless `allow_remote` is true; the check
runs on the built client's own locality verdict, before any document is sent.
Confidential pages are withheld by the reindex rule whatever the consent, and
the outcome counts them (`withheld`).

A withheld page keeps the old model's tag unwritten (the reindex self-healing
rule), so `model_changed` stays true after such a run. That is the truth —
those pages are not searchable by the new model — and the app says why rather
than hiding it.

Rejected: a quote token like judging's (ADR-0059). A rebuild has no per-item
consent and no cost bound to agree to beyond "this text goes to that host",
which one boolean states; the screen shows the warning before it sets it.

### D5. Errors

The reindex service raises typed refusals carrying CLI sentences. The API maps
them to its own vocabulary: an unreachable runtime, a missing model and a
changed dimension re-raise the backend errors the API already exports
(`BackendUnavailable`, `BackendModelNotFound`,
`BackendEmbeddingDimensionMismatch`); store contention is
`WorkspaceBusyError`; an unreadable `openkos.yaml` is `WorkspaceUnreadable`; an
unusable store or a failed graph write is the new `SearchUnavailable`.

### D6. The screens

- **The label.** A page panel (title, "From:", the label as a three-way choice:
  Shareable, Private, Only on this Mac) opens from an Ask citation and from a
  page card in To review. Choosing a label previews and applies in one step,
  like an undo, then confirms with Undo, which puts back the level it replaced.
  Ask citations also carry the one action "Keep only on this Mac" directly.
  Lowering from "Only on this Mac" while a cloud AI model is on opens a dialog
  first, starting on its safe answer. When other pages were raised with it, the
  confirmation says so and Undo is not offered, because putting this page back
  would leave the others raised.
- **Rebuild.** One component, used in Settings (after a change of the AI model
  for search, and whenever `needs_rebuild`), from Ask's stale-search note, and
  on Status while `needs_rebuild`. With a cloud AI model for search it asks
  first, with the same send rule as the cloud warning. While it runs: progress
  and Stop.

## Flow

```
Screen                 Local API                     Engine
  | POST sensitivity/preview ->|  plan (no lock)          |
  |<- preview + version -------|                          |
  | POST sensitivity/apply --->|  lock, re-plan, write,   |
  |<- changed -----------------|  commit                  |

  | GET search -------------->|  read tags, manifests    |
  | POST search/rebuilds ---->|  thread: build client,   |
  |<- 202 + id (admitted) ----|  consent check, admit    |
  | GET search/rebuilds/{id} >|  embed (unlocked) ...    |
  |<- progress ---------------|  lock: write stores      |
  | POST .../stop ----------->|  stop before the write   |
```

## ADR gate

No ADR. Each choice is reversible within the additive rule: new names and
routes, one defaulted port. The consent boolean (D4) could later be replaced by
a quote without breaking a client that never sends it.
