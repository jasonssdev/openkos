# Proposal: deprecated-status-export — write `status: deprecated` on a superseded concept for OKF v0.2 consumers

Refs #1075. ADR-0032 (amends ADR-0029 Decision 5).

## Intent

OpenKOS computes deprecation at read time: a concept is deprecated when its
own `status` is `deprecated` or when another concept `supersedes` it
(`src/openkos/lifecycle.py:55-87`). Nothing writes that result to disk —
ADR-0029 Decision 5 and the reconcile requirement "Additive-Only, No
Status/Lifecycle Write" (`openspec/specs/reconcile-command/spec.md:130`)
forbid it. Since #1064 every new concept carries `status: stable`
(`src/openkos/model/okf.py:1151`, `:1305`), and OKF v0.2 §5.4 reads absent
`status` as `stable`, so any non-OpenKOS reader sees a superseded concept
as current.

This change exports the computed supersession into the superseded
concept's frontmatter as `status: deprecated` plus an engine marker, keeps
`supersedes` edges as the only authority, and gives `lint` and `repair` a
way to detect and fix drift from any state.

## Scope

### In Scope

- One pure projection in `model/okf.py` (the OKF seam) deciding every
  export outcome, with the precedence table in the new
  `deprecated-status-export` spec.
- The `status_derived_from: supersedes` marker, and `okf.declares_deprecated`
  ignoring a marked `deprecated` so the engine never reads its own export.
- An edge-only superseded set in `lifecycle.py`, usable over both disk and
  an in-memory planned view.
- Writers: `reconcile --winner` (and its `--from-findings`/revision-findings
  walks, which share `_reconcile_pair`), `relate <a> supersedes <b>`,
  `forget`, `purge`, `merge`, `unmerge`.
- `lint`: `status-export-drift` and `status-export-blocked` findings.
- `repair`: rewrites every document to its projection, idempotently, in its
  existing commit.
- ADR-0032; `docs/knowledge-object-model.md` Lifecycle sentence and
  `docs/cli.md` reconcile/relate lines.

### Out of Scope

- An `unreconcile` verb. Recovery for a path with no reversal verb is: edit
  the edge away, run `repair`.
- Making `status` authoritative (rejected, ADR-0032).
- Recording which concepts supersede a concept in its own frontmatter.
- Any change to retrieval, `list`, `concept_read`, or MCP output: effective
  status is unchanged for every concept by construction.
- `revises`/`reconciled_with` status semantics (neither deprecates).

## Decisions

| # | Decision | Evidence |
|---|---|---|
| 1 | Marker key `status_derived_from: supersedes`; the export is invisible to `deprecated_concept_ids` | the predicate reads own `status` today (`lifecycle.py:72`, `okf.py:2147-2154`), so an unmarked export would feed back |
| 2 | Only absent/`stable`/legacy `active` toggle; any other human value is BLOCKED and reported; an unmarked `deprecated` is never withdrawn | "human curates, engine maintains" (AGENTS.md) |
| 3 | Withdraw writes `status: stable` | OKF v0.2 §5.4: absent ≡ `stable`; the engine writes `stable` since #1064 |
| 4 | Drift fixer lives in `repair`; `lint` only reports | `lint` is read-only by spec (`openspec/specs/lint/spec.md:242`); `repair` already rewrites derived frontmatter (`cli/main.py:16082-16110`) |
| 5 | `relate` exports too | `relate` accepts `supersedes` (`model/relations.py:136-161`, open vocabulary), so omitting it would create drift on the user's own command |
| 6 | Withdrawal needs a complete edge walk | unreadable docs contribute no edges (`lifecycle.py:69-70`) |
| 7 | `merge` projects only the survivor; `unmerge` projects the two restored docs | merge rewiring (`openspec/specs/entity-resolution-merge/spec.md:629`) can change only the survivor's superseded-ness |

## Capabilities

### New Capabilities

- `deprecated-status-export` — the marker, the projection and its
  precedence, the never-read-back rule, the complete-walk rule, and
  `repair`'s fix.

### Modified Capabilities

- `reconcile-command` — RENAMED + MODIFIED "Additive-Only, No
  Status/Lifecycle Write" → "Additive-Only, Status Written Only As The
  Supersedes Export".
- `status-aware-retrieval` — MODIFIED "Effective Status Resolution" (a
  marked `deprecated` is not a declaration).
- `okf-format-migration` — MODIFIED "Legacy Lifecycle Values Are Not
  Deprecated" (same rule, dual-reader wording).
- `forget-command` — MODIFIED "Resurrection Interaction Disclosure" and
  "Catalog-Before-File Write Ordering".
- `privacy-purge` — ADDED "Purge Withdraws The Deprecated-Status Export Of
  Resurrected Targets".
- `entity-resolution-merge` — MODIFIED "Frontmatter-Conflict Resolution"
  and "Unmerge Achieves Round-Trip Parity".
- `typed-relationships` — ADDED "`relate` Of A `supersedes` Edge Writes The
  Deprecated-Status Export".
- `lint` — ADDED "Deprecated-Status Export Drift Scan".

## Approach

Build the safety net before any writer: projection and read-back rule
first (effective status provably unchanged), then `lint` + `repair` (so
every state is recoverable), then the writers in order of risk: additive
(`reconcile`, `relate`), subtractive (`forget`, `purge`), rewiring
(`merge`, `unmerge`). See `tasks.md` for the six PR-sized phases.

## Affected Areas

`src/openkos/model/okf.py`, `src/openkos/lifecycle.py`,
`src/openkos/lint.py`, `src/openkos/application/repair.py`,
`src/openkos/application/lifecycle.py` (forget, purge, relate, merge,
unmerge), `okf.build_merged_document`,
`src/openkos/cli/main.py` (`_reconcile_pair`, previews, `repair` report,
`purge` live-tree cleanup), tests under `tests/unit/`, two docs.

## Principles Impact

- **Adopt OKF**: uses the v0.2 `status` vocabulary and a §4.1 extension key;
  nothing invented.
- **Reconstructible**: the export is a projection of edges; `repair`
  recomputes it from canonical files.
- **OKF adapter is one seam**: the marker and projection live only in
  `model/okf.py`.
- **Human curates**: human-authored values are never overwritten; every
  write is previewed.

## Risks

- **Feedback loop** if any consumer reads raw `status` instead of
  `okf.declares_deprecated` — mitigated by a test that greps for raw
  `status` reads outside `model/okf.py` (today there are none besides the
  migration path, `okf.py:2439`, `:2652`).
- **Unmerge parity regressions** — mitigated by keeping the projection the
  identity on consistent bundles, with a parity test over an exported
  fixture.
- **Purge is irreversible** — its export write is non-fatal and lands in
  the live tree after the rewrite, like the rest of its cleanup.

## Rollback Plan

Each phase is its own PR and reverts cleanly. Reverting the writers leaves
exported files on disk; they are harmless while Phase 1 remains (the
engine ignores them). Reverting Phase 1 too would make a marked
`deprecated` count as a human declaration: before that revert, strip
markers with a one-off `repair` built from the Phase 2 code, or delete the
key by hand.

## Dependencies

None beyond the shipped OKF v0.2 migration (#1064).

## Success Criteria

- A `reconcile --winner` loser reads `status: deprecated` on disk.
- `forget` of its superseder restores `status: stable`.
- `lint` reports zero export findings on every shipped example after
  `repair`; `repair` twice writes nothing the second time.
- `deprecated_concept_ids` returns the same set before and after `repair`
  on every fixture.
