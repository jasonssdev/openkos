# Design: The Review Screen

Inputs: `proposal.md`, the [wording brief](../../../docs/decision-wording.md)
(authoritative for every word on screen), `docs/brand.md` and
`desktop/ui/tokens.css`, ADR-0053, ADR-0058, ADR-0059, ADR-0060, ADR-0063,
ADR-0064, ADR-0065, and ADR-0066 (Proposed here).

## D1 The page read is a curated, owner-local field set

`openkos.api.read_page(workspace, concept_id) -> Page | None`, over
`application.concept_read.read_concept`, the core the MCP `get` already uses.

```python
@dataclass(frozen=True)
class PageSource:
    source_id: str      # the Source document's Concept ID
    file_name: str      # the file the person added: the basename of its `raw/` resource
    added_at: str | None  # when it was added: the Source's `generated.at`, verbatim

@dataclass(frozen=True)
class Page:
    concept_id: str
    title: str
    okf_type: str | None
    excerpt: str        # the body's opening as plain text, at most 280 characters
    sensitivity: Literal["public", "private", "confidential"]
    sources: tuple[PageSource, ...]
```

- **Curated, never a passthrough.** Only these fields can reach a caller,
  whatever the frontmatter adds later (the same posture as the MCP `get`).
- **`None` for an id that is not a valid Concept ID, a page that does not
  exist, or one that cannot be read or parsed**, as `merge_history` returns an
  empty tuple for an invalid id. The read takes no lock, builds no client and
  writes nothing.
- **The excerpt** drops a leading `# ` heading equal to the title, keeps the
  label of a markdown link and drops its target, drops citation markers
  (`[1]`), emphasis and heading marks, collapses whitespace and cuts at the last
  word boundary before 280 characters, ending in `…` when cut. It is text, and
  the app writes it with `textContent`.
- **Sensitivity is normalised** to the three levels with the engine's own
  fail-closed rank (`okf.combine_sensitivity(raw, "public")`): missing is
  `private`, anything unrecognised is `confidential`.
- **Sources**: one per provenance entry whose document is a readable `Source`
  with a `raw/` resource, in provenance order. Other entries (a page derived
  from another page, a missing source) are not listed: they are not files the
  person added.
- **No disclosure gate** (ADR-0058; local-http-api "Responses Are Owner-Local
  And Apply No Disclosure Gate"): the token holder is the owner. The app shows
  the level as "who can see it".

The route is `GET /v1/pages?concept_id=<id>` (a Concept ID contains `/`, so it
travels as a query parameter, like `/v1/merges`). `None` answers 404 with the
existing `not_found` code and its fixed message; no new error code, so the
error table and the client's code list do not change. Additive within v1:
three new public names and one function; the surface classifier must report
every difference additive.

## D2 The screen is a view model, a wording module and thin render functions

Per ADR-0066:

```
desktop/ui/
  main.ts            the shell: polls the engine state, owns the two views and the navigation
  view.ts            the status page's wording (unchanged)
  components.ts      button, notice, card, choice list, heading helpers (DOM; no logic)
  review/
    wording.ts       pure: API data -> the brief's sentences, options, messages
    model.ts         the review state and every transition, over a ReviewClient
    render.ts        state -> DOM, events -> model transitions
```

`ReviewClient` is `Pick<Client, ...>` of the methods the model calls, so the
tests mock exactly what the screen uses and a renamed client method fails
`tsc`. The model never touches the DOM; `render.ts` never decides anything.

## D3 The state and its transitions

One `ReviewState` snapshot, replaced (never mutated) on each transition; the
render layer subscribes and redraws.

```
list ──open(key)──▶ choosing ──preview(action)──▶ previewing ──ok──▶ preview
  ▲                    │   ▲                          │                 │
  │            decideLater │ stale: reload,           │ problem         confirm()
  │                    ▼   │ re-preview, "changed"    ▼                 ▼
  └──────────────── closed ┴──────────────────── problem ◀── fail ── applying
                                                     │                 │ ok
                                       retry()/reload()/ok()           ▼
                                                                     done (confirmation, Undo for a combine)
```

- **Choosing** holds the group selection: the marked members, the chosen name
  (survivor) and, for a cross-type group, the chosen kind. The model enforces
  the brief: "Combine the ones I marked" is offered only with two or more
  marked and a name chosen among them; the cross-type kind narrows the name to
  the pages of that kind and asks which keeps its name only when more than one
  page has it; the app never chooses either for the person.
- **Decide later** sends nothing: the engine's `skip` changes nothing and
  answers `skip_not_applicable` on preview, so the app closes the decision with
  "Nothing changes. This stays in To review."
- **Preview → apply.** Apply echoes the preview's version unchanged and is
  wrapped in `retryWhileBusy` (five seconds, `Retry-After`).
- **Stale** (`stale`, `write_drift`, or a listed decision with `fresh: false`):
  the list is reloaded; if the decision is still open, the same action and
  selection are previewed again and the preview carries the brief's "Something
  about these pages changed since this was suggested. Here is the current
  version."; if it is gone, the screen returns to the list saying so. The old
  version is never applied.
- **After apply** the list is reloaded. A combine adds the survivor to the
  session's "Combined just now" list with its merge history.

## D4 Errors become the brief's messages and one action

`wording.problemFor(error)` maps every code the brief's table names to its
message and its one action: `try_again` (repeat the same call), `reload`
(reload the list and re-preview), `ok` (dismiss; for `member_missing` the
decision leaves the view), `none`. Codes the brief does not name, a
`ProtocolError` or an unreachable engine get a generic "Something went wrong.
Nothing was changed." only where the failure is before any write (preview,
quote, reads), and "Something went wrong. Check To review to see what changed."
after an apply or a split, where the outcome is unknown. No message contains a
command, a path, a code or the engine's text.

| Code | Action |
|---|---|
| `workspace_busy` (after the five-second retry) | Try again |
| `record_failed` | Try again |
| `stale`, `write_drift`, `invalid_selection`, `merge_group_interrupted`, `not_merged`, `unmerge_interrupted` | reload |
| `member_missing`, `survivor_edited`, `survivor_missing` | OK |
| `run_active` | OK (no run id to show from another launch) |
| `backend_unavailable` | Try again (guided setup's "Start it" is a later stage) |
| `quote_stale` | re-quote, never run |

## D5 The check: one consent per batch

`Check these` on a `pending_evaluation` identity decision, and `Check all` on
the list when any exist, both open the same consent for the identity batch:

```
person        app (model)                       local API
  │ Check these   │                                  │
  │──────────────▶│ POST /judging/quote {identity}   │
  │               │─────────────────────────────────▶│ free: no model call
  │  consent      │◀──────────── quote ──────────────│
  │◀──────────────│ "Check {n} pairs?" + where it runs, already checked, background
  │ Check {n}     │                                  │
  │──────────────▶│ POST /judging/runs {token}       │
  │               │─────────────────────────────────▶│ 202 run_id (or quote_stale → re-quote)
  │ "Checking {done} of {n}…" ◀── poll GET /judging/runs/{id} every second
  │ Stop          │ POST /judging/runs/{id}/stop     │
  │──────────────▶│─────────────────────────────────▶│ "Stopping after the current one…"
  │ "Checked {n}. {m} are ready for you to decide." ◀── finished; list reloaded
```

`n` is the quote's `to_judge`; "{k} are already checked" is `served`; the
background line shows for the `unattended_active` notice; a non-local binding
shows the provider sentence. A quote with nothing to judge says "These are
already checked." and offers no run. `m` counts the identity decisions that
were `pending_evaluation` before the run and are not after it. A failed run
shows the failure's message and keeps what was persisted.

## D6 Split again

The confirmation of a combine offers **Undo**, and the "Combined just now"
list shows, per survivor, one "Combined from '{title}' on {date}" line per
merge-history record (newest first) with **Split again**:

1. `previewUnmerge(survivor, absorbed)`; one step → "Split '{title}' back out?"
   with **Split again**; more steps → "To split this, the later combines on
   this page are split first: {list}. Continue?"
2. `applyUnmerge(version)` under `retryWhileBusy`; "Split. '{titles}' are back
   as they were."; the history and the list are reloaded.
3. `survivor_edited` shows "This page was changed after it was combined, so it
   can't be split here." with OK and nothing else; `discard_survivor_edits` is
   never sent (#1442). `unmerge_interrupted` shows its message and reloads the
   history, which now shows what is left.

The session list is not persisted: the engine has no read of every combine in
the library, and a list kept by the app would be a second source of truth. The
history of any page the screen shows is read from the engine each time.

## D7 Accessibility

- Every control is a native `<button>`, `<input type="checkbox">` or
  `<input type="radio">` with a visible label; nothing is a clickable `div`.
- Focus is visible (`:focus-visible` outline in `--accent`, offset), and a
  redraw restores focus to the control with the same `data-focus` key, or to
  the new view's heading (`tabindex="-1"`) when the view changed.
- Each decision is a `<section>` labelled by its question; the marks and the
  name choice are `<fieldset>`s with a `<legend>`.
- Progress and confirmations are in one `role="status"` region (polite);
  problems in `role="alert"`.
- State color never stands alone: every notice carries a symbol and a word
  (`✓ Done`, `! Changed`, `✕ Not saved`, `i Note`).
- Disabled "Combine the ones I marked" carries its reason as visible text tied
  by `aria-describedby`.

## D8 Testing

- Python: `read_page` (title, type, excerpt rules, sensitivity fail-closed,
  sources only from Source documents, `None` for an invalid, missing or
  unreadable id, nothing written); the route (200, 404 `not_found`, no lock);
  the surface classifier additive; the snapshot regenerated.
- TypeScript: `wording.test.ts` (every question, option, "what it does",
  confirmation, notice and problem the brief defines); `model.test.ts` (every
  transition with a mocked `ReviewClient`, an injected sleep and clock).
  Mutation checks on the guards: the two-marked rule, the stale re-preview,
  never sending `skip`, never sending `discard_survivor_edits`, the busy retry
  only around writes, the quote-stale re-quote.

## ADR Gate

The choice of no UI framework, and of where the screen's behavior lives, is a
technology and pattern decision that later screens inherit: ADR-0066. The page
read is an additive operation under ADR-0058 and ADR-0060's existing rules: no
ADR.
