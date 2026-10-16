# Proposal: The Review Screen

Tracking issue: #1463. Governing decisions: ADR-0066 (the desktop screens are
plain DOM over a tested view model, with no UI framework; Proposed, accepted by
this change), ADR-0065 (the TypeScript web view and its generated client),
ADR-0053 (two-phase curation decisions and their consent rules), ADR-0058
(owner-local loopback API), ADR-0059 (quote-bound judging), ADR-0060 (the
versioned Python API), ADR-0061 (the local API over FastAPI), ADR-0063
(identity groups), ADR-0064 (unmerge). Wording authority:
[`docs/decision-wording.md`](../../../docs/decision-wording.md).

## Intent

The roadmap calls the review screen the heart of the desktop app: where a person
who has never used a terminal settles the pending-decision queue. The
[preregistered exit criterion](../../../evals/desktop_app/PREREGISTRATION.md)
(S5) asks exactly that: the participant finds an open decision, applies one
action other than "decide later", agrees to the AI model's check first when the
decision needs it, and then describes what the choice did in words that match
the brief.

Every stage before this one left the screen to now: the shell, the frozen
engine, the identity-group decision ("built before the review screen"), the
unmerge ("MVP 6's review screen needs the undo before it ships") and the typed
client ("the next stage, the review screen, calls most of the API").

## Readiness

Checked against `openkos.api` and the local API at `3f0fe555`:

- **Judging is reachable from the app.** `POST /v1/judging/quote` (free, the
  cost consent's input), `POST /v1/judging/runs` (against the echoed quote
  token), `GET /v1/judging/runs/{id}` (progress by polling) and
  `POST /v1/judging/runs/{id}/stop` (cooperative cancellation) exist, with
  `quote_stale`, `run_active` and the backend failures typed. An identity
  decision the model has not judged lists `pending_evaluation` and withholds
  both merges; once a run persists its verdicts, `GET /v1/decisions` offers
  them. Nothing to add.
- **Decisions, previews with selections, applies, merge history and unmerge**
  exist and are typed in the client.
- **Missing: reading a page.** A decision names its pages by Concept ID only.
  The brief's evidence ("the pages involved, each by its title and a short
  opening excerpt"; "From: allotment-volunteers.md, added 12 October"), its
  questions ("Are these the same person?", "What is {title}?") and its
  cross-type choice (one option per kind among the pages) all need each page's
  title, type, opening and source files. Nothing in `openkos.api` or the local
  API returns them. Without them the screen would show
  `people/marguerite-osei` to a person who has never seen a path, which fails
  the exit criterion. This change adds one owner-local read.

## Scope

### In scope

- **A page read**, additive within `API_VERSION` 1: `openkos.api.read_page`
  returning a curated `Page` (title, type, plain opening excerpt, who can see
  it, and the source files it came from with the date each was added), served
  as `GET /v1/pages?concept_id=<id>`; regenerated OpenAPI snapshot, API
  surface snapshot and client types; one client method.
- **The review screen** in `desktop/ui/`, over the client:
  - "To review": every open decision listed, the ones the app cannot act on
    with a plain sentence and no command;
  - one decision at a time with its evidence and the brief's question,
    options and "what it does" lines, for every kind and action the API
    offers: combine a pair (`merge`), combine a group with the person marking
    the members and choosing the name (`merge_group`, `more_than_two_members`),
    choosing what something is (`merge_group`, `cross_type`), keep separate
    (`keep_distinct`), connect either way round (`relate`, `relate_reversed`),
    how quickly something goes out of date (`set_tier`), and decide later
    (client-side, nothing sent);
  - preview, then apply against the echoed version; the confirmation in the
    past tense;
  - the check: one consent per batch from the free quote, progress, stop, and
    the result; a stale quote re-asked, never run;
  - busy handled with the client's bounded retry, then "Try again"; a stale
    preview or apply reloaded and re-previewed, never applied; an interrupted
    group or split explained; every refusal of the brief's table worded;
  - undo of a combine through "Split again" over the merge history ("Combined
    from '…' on …"), with the later combines listed first when a split needs
    them, and the edited-page refusal shown without an offer to discard the
    edit (#1442).
- Minimal navigation between the status page and "To review".
- Components extracted as the screen needs them (button, notice, decision
  card, page evidence, choice list), on `tokens.css` only.
- Accessibility basics: every control a native button or input, keyboard
  operable, visible focus, labelled groups, status announced politely, color
  always paired with a symbol or a word.
- Unit tests (vitest, Node) for the wording and every state transition with a
  mocked client.
- ADR-0066; CHANGELOG, roadmap status line, the brief's settled points.

### Out of scope

- Undo for keep separate, connections and "how quickly" settings: the API has
  no reopen, unrelate or tier-revert operation, so the confirmation offers no
  Undo for them (the brief's rule 4: "offers the undo where one exists"). Filed
  as a follow-up for the engine, not built here.
- Bulk acceptance ("Accept all {n} 'related' suggestions", all tier
  suggestions): the brief allows it, the exit criterion does not need it.
- "Pages you kept separate" and the other library settings; guided setup
  (the brief's "Start it" for a stopped AI model); dropping files and asking.
- Offering to split a page edited after its combine (#1442, decided: refusal
  only).
- Spanish copy; UI automation, screenshots or DOM tests (ADR-0066).
- Any change to judging, curation or unmerge semantics.

## Approach

A read service in `openkos.api` over the existing `application.concept_read`
core, adding the source files from each provenance entry's Source document. One
route under `/v1`, no new error code (an unknown page is `not_found`, as an
unknown run id is). The screen follows ADR-0066: a view model (state and
transitions over a narrow client interface) and a wording module (pure
functions from API data to the brief's sentences) carry all behavior and are
tested; render functions draw from state with `textContent` only.

## Risks And Rollback

- **A read that returns page text.** Owner-local by ADR-0058 and the local API
  spec ("Responses Are Owner-Local And Apply No Disclosure Gate"): the token
  holder is the owner, as for the decision payloads. The read is a curated
  field set, never a frontmatter passthrough, and its excerpt is bounded.
- **The copy is mostly hypotheses (H).** The screen follows the brief exactly
  so the participant session tests the brief, not an improvisation.
- **Render functions are untested.** Mitigated by keeping them thin (ADR-0066)
  and by typechecking; visual rendering is not checked by this change.
- Rollback: every slice is one squash-merged PR. The page read is additive;
  reverting it means regenerating both snapshots and the client types.
