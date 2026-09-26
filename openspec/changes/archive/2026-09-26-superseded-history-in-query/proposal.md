# Proposal: superseded-history-in-query — show how a retrieved answer came to be

## Intent

Refs #1014, piece **(b)**. The exploration is `exploration.md` in this folder,
and its "Decisions (2026-09-26)" section is binding.

`query` answers from the current state of the bundle only. When a concept has
been superseded (`supersedes`, a reversal: the old one is hidden as current) or
refined (`revises`, a refinement: both stay current), the answer states the
current position with no trace of how it got there. Status-aware retrieval
hides a superseded concept completely (`lifecycle.deprecated_concept_ids`), so
a question like "what did we decide about X, and did it change?" gets half an
answer. The engine already holds the chain in canonical frontmatter; it just
never shows it.

This change lets `query` attach, for each retrieved concept, the earlier
versions it supersedes or revises, as separately numbered and clearly labelled
**history blocks**. The model can narrate the change and attribute it through
the existing `USED:` line. The citation records that it was history, and
`query --save` files it as provenance marked as history. It is **opt-in and off
by default**, so a workspace that does not enable it pays nothing and sends a
byte-identical prompt.

The status-aware guarantee is preserved: a superseded concept is never a
retrieval **hit**. It can only reach the prompt as a history block attached to
the hit that superseded it.

## Scope

### In Scope

1. **A bounded chain walk** from each retrieved concept (the *successor*),
   following its outbound `supersedes` and `revises` edges, read from
   frontmatter through `okf.decode_relations`. There is no bundle walk and no
   graph read. Limits: depth 3, a `visited` cycle guard, at most 3 history
   blocks per successor, and a deterministic order.
2. **History blocks.** Each predecessor becomes its own numbered context block
   whose label carries a suffix naming the relation, the successor, and the
   predecessor's event date. This mirrors the Insight `synthesis_note`
   precedent (`answer.py:663-669`). **The system prompt does not change.**
3. **Deduplication.**
   - A `revises` predecessor that is already an ordinary hit is not repeated.
   - A predecessor reached from two successors is attached once, to the first
     successor in fused order.
4. **Every existing send-time guard applies to each predecessor re-read:** the
   `blocked` set, the `sensitivity.should_block` re-check, and the
   skip-on-unreadable rule. History can never bypass a gate that a hit must
   pass.
5. **Budget allocation.** History spends a sub-share of its successor's share
   (see Decisions). The existing excerpt and omission disclosure (#882) applies
   to history blocks unchanged.
6. **`Citation.history: Literal["superseded", "refined"] | None = None`.** This
   mirrors `excerpted` and `confidential`. `_split_attribution` and the #753
   subset rule apply unchanged.
7. **A bounded event-date resolver** in a new package-root leaf,
   `src/openkos/event_dates.py`, beside `lifecycle.py`. It owns the four-state
   `DateState` vocabulary. `resolution/decision_revision.py` re-exports the
   leaf's `DateState`, so there is one vocabulary.
8. **An opt-in config key, `revision_history: false`.** It is threaded like
   `sufficiency_check`: `config.py`, then `run_query`, then `answer(...,
   revision_history=False)`. `retrieval/answer.py` stays config-free.
   Validation is bool-only, and the template gains a comment.
9. **Suppressed under `--include-deprecated`.** When that flag is on, the walk
   does not run at all.
10. **CLI surface.** Citation markers `[superseded]` / `[refined]`. `--save`
    marks history provenance in the filed Insight's `## Related` section.
11. Delta specs, ADR-0026 (written in the design phase), a `docs/cli.md`
    `query` note, and tests.

### Out of Scope

- **The measurement probe** (see Decisions). It is a follow-up change.
- **Enabling the key by default, or recommending it**, until it is measured.
- **Unifying this resolver with the detector's Phase B resolver.** Phase B
  (`provenance_source_ancestors_many`, not on main) is whole-bundle and
  transitive. This one is bounded. They share a vocabulary and an aggregation
  rule, not an implementation.
- **Other relations.** Inbound history (who superseded *me*), `reconciled_with`,
  and any relation other than `supersedes`/`revises` are not walked.
- **Changes** to the system prompt, the sufficiency prompt, retrieval ranking,
  `fused_count`, or the FTS/dense hit counts.
- **A configurable depth or breadth.** These are constants.
- **Other verbs.** A history view in `list`, `status`, or `show`.
- MCP/API surfaces, and `examples/good-life-demo/`.

## Decisions

| Decision | Chosen | Rejected | Why (one line) |
| --- | --- | --- | --- |
| Relations walked | Outbound `supersedes` and `revises` from each hit | `supersedes` only | Binding owner decision: reversals and refinements are both narrated, each with its own label. |
| Block shape | Its own numbered block with a label suffix; no system-prompt text | Splice into the successor body (A); an unnumbered block (C) | (A) breaks "a citation names the bytes shown" (#882/#753); (C) prevents attribution. |
| Label wording (design finalizes) | `(earlier version, superseded by <S>; event date <d>; no longer current)` / `(earlier version, refined by <S>; event date <d>; still current)` | A generic "history" tag | It names the relation, the direction and the currency. The successor id ties a chain together without changing the successor's own label, so hit labels stay byte-identical. |
| Citation field | `history: Literal["superseded","refined"] \| None = None` | `superseded: bool` | A refined predecessor is still current, so marking it `[superseded]` would be false. A bool cannot carry the second case, and `None` keeps every construction site valid. |
| Budget | Nested water-fill: shares are computed over the hit blocks as today, then each successor's share is split by `fair_shares` across `[successor, *its history]`. The history labels' overhead is charged to that same share. | A flat pool (history blocks raise the global divisor) | Conservative. An unrelated hit's share cannot shrink because some other hit has history. The cost falls only on the successor that asked for it, and an omitted history block is already disclosed by #882. |
| Depth, breadth, order | Depth 3; at most 3 history blocks per successor; BFS from the successor; at each node, targets sorted by (`supersedes` before `revises`, concept id); `visited` guard | Unbounded; per-hop fan-out without a total cap | Bounded reads and bounded prompt growth, deterministic across runs. The cap stops a heavily revised concept from filling the window. Truncation is disclosed on `AnswerResult`, never silent (design picks the field). |
| Where the date helper lives | New package-root leaf `openkos/event_dates.py` (imports `okf` and stdlib only), with `DateState` defined there | `bundle/provenance.py`; `model/okf.py`; `retrieval/` | `bundle/provenance.py`'s contract is pure and no-I/O over an in-memory snapshot. `okf.py` owns format, not provenance semantics. A `retrieval/` home would block the detector's `resolution/` service from sharing the vocabulary. `lifecycle.py` is the precedent for a root leaf that both layers import. |
| Date resolution rule | Read the member's own `provenance:`. For each `sources/` entry, one guarded read plus `okf.read_event_date`. For a non-Source entry, one further hop, then stop. Aggregation matches the detector spec: all agree → `dated`; any absent or malformed → `missing`; >1 distinct → `multiple`; nothing reached → `none-reached`. | Whole-bundle `provenance_source_ancestors` | The cost is bounded by chain length, not bundle size, and it keeps the same vocabulary and verdicts as "Decision Event Date Resolution". |
| Date display | `dated` → `event date 2026-07-14`; `multiple` → `event dates 2026-07-01 to 2026-07-14` (earliest to latest); `missing` / `none-reached` → `event date unknown` | Omitting the date when unknown; ingest `timestamp` as a fallback | Unknown is said, never guessed. ADR-0023 forbids treating ingest time as event time. |
| Key name | `revision_history` | `superseded_history`, `query_history`, `include_history` | It covers both relations. Unprefixed, like `sufficiency_check`. `include_*` would read as a CLI flag and collide with `--include-deprecated`'s vocabulary. |
| Default | Off in `answer()` and in the packaged config | On in config (the `sufficiency_check` shape) | It is unmeasured prompt-affecting behavior. Off means zero extra reads and a byte-identical prompt (the #812 `rationale_language` pattern). |
| `--include-deprecated` | Suppress the walk | Dedupe against `fused_ids` | Under the flag the predecessors are already ordinary hits, so the history label would contradict their hit status. Suppressing also keeps the flag's "zero added cost, byte-identical" contract. |
| `--save` rendering | `provenance:` stays a flat id list, byte-shape unchanged. `okf.build_concept` gains an optional per-ref `related_notes` mapping (default `None`, byte-identical). A history citation's `## Related` bullet reads `— earlier version (superseded) cited as history for this answer` / `(refined)`. | A new frontmatter key such as `history_provenance:` | No new persisted schema, so merge, forget, purge and lint need no change. The mark is prose that degrades gracefully (OKF §5.3). The sensitivity high-water mark already folds over every citation, history included. |
| Measurement probe | A follow-up change | In this change | The dated fixture (`evals/decision_revisions/`) is not on `main`: it sits on the unmerged harness branch. Default-off lets this ship **unmeasured**. The template comment and `docs/cli.md` MUST say so and MUST NOT recommend enabling it. |
| ADR | Yes: ADR-0026, written in design | No ADR | Both gate conditions hold. It is the first path by which a status-hidden concept re-enters synthesis (a trade-off against status-aware retrieval's hiding), and filed Insights persist provenance pointing at superseded concepts in users' bundles. |

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-answer`:
  - ADDED "Revision History Is Attached To A Retrieved Concept When
    Requested". This covers the walk, the limits, order, dedupe, per-block
    guards, labels, the nested budget, `Citation.history`, attribution,
    truncation disclosure, and zero reads or a byte-identical prompt when off.
  - MODIFIED "AnswerResult Carries Retrieval Metadata": `context_block_count`
    includes history blocks, while `fused_count` and the hit counts do not.
    Adds the truncation field.
  - MODIFIED "Module Is Config-Free And Backend-Injected": the new keyword
    defaults to off.
- `status-aware-retrieval`:
  - MODIFIED "Deprecated Concepts Excluded By Default": a scenario that a
    superseded concept is never a hit and never counted, and reaches the
    prompt only as an attached history block.
  - MODIFIED "`--include-deprecated` Escape Flag": a scenario that history is
    suppressed.
- `query-command`:
  - ADDED "Revision History Citations Are Marked" (`[superseded]` /
    `[refined]`).
  - MODIFIED "`--save` Files The Cited Answer As An Insight": history
    citations are filed in `provenance:` with a marked `## Related` note.
  - ADDED the `revision_history` config key, validated as a bool and threaded
    by `run_query`.

## Approach

- **`openkos/event_dates.py` (new leaf).** `DateState`, a small
  `ResolvedEventDate` (state, value, and an earliest/latest pair for
  `multiple`), and `resolve_event_date(bundle_dir, concept_id)`. It never
  raises: an unreadable file counts as `missing`, following the aggregation
  rule. `resolution/decision_revision.py` changes to
  `DateState = event_dates.DateState`.
- **`retrieval/history.py` (new).** A pure chain walk over re-read frontmatter.
  It returns ordered `(predecessor_id, role, holder_id)` tuples per successor
  and a truncation count. It imports `okf` and `event_dates`, never
  `resolution/`.
- **`retrieval/answer.py`.**
  - `_assemble_context` gains `revision_history: bool = False` (keyword-only,
    so the positional eval harness callers are untouched). When it is on,
    history is appended after each successor's block, under the same guards.
  - `_bound_bodies` implements the nested split.
  - `answer()` gains `revision_history: bool = False` and forces it off under
    `include_deprecated`.
- **`application/query.py`.**
  - `run_query` injects `cfg.revision_history`.
  - `stage_filed_answer` passes `related_notes` for history citations.
- **`model/okf.py`.** Adds the optional `related_notes` to `build_concept`,
  staying inside the OKF seam.
- **`config.py` and the template.** The key, its bool validation, its default,
  and the template comment.
- **`cli/main.py`.** The two citation markers only.
- The core stays synchronous, there are no new dependencies, and the canonical
  layer imports nothing derived.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/event_dates.py` | New | bounded event-date resolver + `DateState` |
| `src/openkos/resolution/decision_revision.py` | Modified | `DateState` re-exported from the leaf |
| `src/openkos/retrieval/history.py` | New | chain walk |
| `src/openkos/retrieval/answer.py` | Modified | attach, nested budget, `Citation.history`, `answer` kwarg |
| `src/openkos/application/query.py` | Modified | config injection; `related_notes` on save |
| `src/openkos/model/okf.py` | Modified | optional `related_notes` in `build_concept` |
| `src/openkos/config.py`, `templates/openkos.yaml.template` | Modified | `revision_history` key |
| `src/openkos/cli/main.py` | Modified | `[superseded]` / `[refined]` markers |
| `tests/unit/...` (event_dates, history, answer, query service, okf, config, cli query) | New / Modified | per slice |
| `openspec/changes/superseded-history-in-query/specs/{query-answer,status-aware-retrieval,query-command}/` | New | delta specs |
| `docs/cli.md` (`query`), `docs/adr/0026-*.md`, `docs/adr/README.md` | Modified / New | markers, the unmeasured notice, ADR |

## Principles Impact

- **Representation, not truth.** The engine shows the recorded chain. It never
  decides which version is right.
- **Provenance.** Strengthened. History is cited, attributed, and filed with a
  mark.
- **Sensitivity.** Every predecessor passes the same send-time gate as a hit,
  and `--save`'s high-water mark covers it.
- **Human curates.** The chain is exactly what `reconcile` recorded, and
  nothing is inferred.
- **Local-first, reconstructible, OKF, sync core, immutable `raw/`.**
  Untouched. OKF format knowledge stays in `okf.py`.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| The model presents a superseded version as current | Med | The label states "no longer current". It is off by default and unmeasured, and the follow-up probe measures it before any recommendation. |
| The nested budget starves the successor itself when history is large | Med | Water-fill gives small blocks what they need, and omission and excerpting are disclosed (#882). The successor is still sent first in the split. |
| The sufficiency check accepts context answerable only from history | Low | This is intended for history questions. The label tells the model the history is not current. The follow-up probe covers it. |
| The bounded resolver says `none-reached` where the detector's transitive walk would find a date | Low | The display says "unknown", never a guess. The divergence is documented, and unification is a follow-up. |
| Re-exporting `DateState` collides with the open harness branch | Low | A one-line alias with the same `Literal` members, so a rebase is trivial. |
| Filed Insights now cite superseded concepts, and a later purge or forget meets that provenance | Low | Inbound-provenance handling is already type-agnostic (`bundle/provenance.py`). Nothing new is required. |

## Rollback Plan

Revert the slice commits in reverse order. There is no migration: the key is
absent from existing configs. Design confirms how `read_config` treats a stale
top-level `revision_history` line after a revert, whether it is ignored or
refused. If it is refused, the rollback note tells the user to delete that
line. Filed
Insights written in the interim keep valid `provenance:` lists and prose
`## Related` notes. Both are legal OKF and are read by the old code as ordinary
provenance.

## Dependencies

- Merged: `source-event-time` (ADR-0023, `okf.read_event_date`) and
  `revises-relation` (ADR-0024).
- Not required: the detector's Phase B, and the harness branch (only the
  follow-up probe needs its fixture).

## Size and slices (auto-chain, stacked-to-main)

Forecast: about 800-950 authored changed lines (about 280 source, 450-550
tests, and 70-100 for docs and the ADR; delta specs excluded). `400-line budget
risk: High`. Three slices, each green on its own:

1. **Event-date leaf** (~200): `event_dates.py`, the `DateState` alias, tests,
   and ADR-0026.
2. **Retrieval** (~380): `history.py`, `_assemble_context` attach, the nested
   `_bound_bodies`, `Citation.history`, the `answer()` kwarg (no caller
   enables it yet), and tests.
3. **Surface** (~300): the config key and template, `run_query` threading, CLI
   markers, the `build_concept` `related_notes` and `--save` marking, and
   `docs/cli.md`.

## Success Criteria

- [ ] With the key off (default), `query` makes zero extra file reads and its
      prompt is byte-identical to `main` (pinned by a captured-messages test).
- [ ] With the key on, a hit S that `supersedes` P sends P as a separate
      numbered block labelled superseded, with P's event date. P is absent
      from the fused ids and the hit counts.
- [ ] Under `revises`, P is labelled refined. If P is also a hit, it appears
      once.
- [ ] A 4-long chain stops at depth 3, a cycle terminates, and the order is
      identical across runs. More than 3 predecessors truncates, with the
      truncation disclosed.
- [ ] A confidential or unreadable predecessor is never sent unless the
      existing gates admit it.
- [ ] Adding history to one hit leaves every other hit's bounded body
      unchanged.
- [ ] `--include-deprecated` sends no history.
- [ ] An attributed history citation carries `history`, renders
      `[superseded]`/`[refined]`, and `--save` files it in `provenance:` with
      the marked `## Related` note.
- [ ] `retrieval/` imports nothing from `resolution/`, and `answer.py` does not
      import `config`.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      and `uv run pytest --cov` are green, with the 90% gate. ADR-0026 is
      `Proposed` and indexed.

## Orchestrator correction (2026-09-26)

The proposal says the `evals/decision_revisions/` dated fixture is not on `main`. That was true when it was written. It is now false: the harness merged as #1026 (`b9f382b`), and this branch is rebased onto it. The measurement probe stays a **follow-up** for a different reason: it needs its own labelled question set of expected "before it was X" answers, which is not the revision-pair fixture. It is no longer blocked. The owner's defaults stand: at most 3 history blocks per successor, and default off while unmeasured.
