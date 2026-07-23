# Design: freshness-reconcile (S4 — first WRITE verb of the freshness arc)

## Technical Approach

`reconcile <id-a> <id-b>` clones the verified `relate` template (main.py:923): a
pure Phase-A that computes every mutation in memory and validates all inputs, a
preview, the shared confirm-gate, then a Phase-B of atomic writes. It records a
human's resolution of an S3-surfaced contradiction as an **additive, deterministic,
git-reversible** edit — never a delete, never an LLM call in the write path (pair-id
args only). Safety is the design's center because this is the arc's first write.

## Architecture Decisions

### Decision: symmetric edge = one outbound edge per side (both directions)
**Choice**: default (no `--winner`) writes `reconciled_with` on BOTH docs — A→B and B→A.
**Alternatives**: single edge + a documented "symmetric" convention.
**Rationale**: `okf.Relation` is outbound-only `{target,type}` (okf.py:477). A single
edge is physically asymmetric — a reader on the un-edged side sees nothing and must
know a convention. Two outbound edges make the relation read cleanly from EITHER
concept, matches the graph's actual traversal model, and stays purely additive.

### Decision: `--winner W` = one directional `supersedes` edge on W → L
**Choice**: `supersedes` outbound edge on the winner's doc pointing at the loser.
**Alternatives**: `--supersedes <loser>`; a `superseded_by` back-edge on the loser.
**Rationale**: outbound-only model reads naturally as "winner supersedes loser". No
back-edge — a `superseded_by` edge would imply enforcement S4 does not have. `supersedes`
is **label-only** today: nothing reads it. It PLANTS a forward-semantic that gap #8
(`deprecate`) may later honor; S4 enforces no deprecation and writes no `status`.

### Decision: idempotency via `(target,type)` dedup + body-note anchor + no-change log
**Choice**: reuse relate's `(target,type)` edge dedup; suppress body-note re-append via a
hidden HTML-comment anchor keyed on the counterpart id; emit a `no change` log variant.
**Rationale**: keeps re-runs purely additive with zero body-note bloat — safest re-run
story for a first write verb.

### Decision: reversibility = git-undo only (NO ledger, NO `unreconcile`)
**Choice**: rollback is `git checkout`/`revert` of the two concept files + `log.md`.
**Rationale**: the write is additive and lossless — nothing is deleted or overwritten
destructively. Contrast merge/ADR-0002, whose `merged_from` snapshot ledger exists only
because merge is LOSSY (deletion, high-water sensitivity, provenance dedup). A ledger here
would be over-engineering. (Per proposal verdict: NO new ADR.)

## Data Flow

    reconcile a b [--winner W] [--auto]
        │  Phase-A (pure, NO writes)
        ├─ require_workspace gate
        ├─ _resolve_concept_path(a), _resolve_concept_path(b)  → canonical ids
        ├─ distinct-id guard (a != b)
        ├─ if --winner: resolve W, assert W ∈ {a,b}  → loser = other   ← validate BEFORE write
        ├─ load_frontmatter → decode_relations → dedup-append edge(s) → encode_relations → dump
        ├─ append body note per side (unless anchor already present)
        └─ build **Reconcile** log line (or "no change")
        │  PREVIEW → confirm-gate (--auto | review:false | TTY confirm | non-TTY refuse)
        ▼  Phase-B (atomic writes, ordered)
        write_atomic(doc_a) → write_atomic(doc_b) → write_atomic(log.md)

## Interfaces / Contracts

**Body note** appended to each concept `body` (round-trips via load/dump_frontmatter).
Detection anchor keyed on counterpart id; re-append suppressed if the `target=<id>` anchor
is already in the body.

    ## Reconciliation
    <!-- okos:reconcile target=<counterpart-id> role=<reconciled|supersedes|superseded> -->
    <human-readable sentence with [<counterpart>](/<counterpart>.md) and the date>

- symmetric: `role=reconciled` — "Reconciled with [Y](/Y.md) on YYYY-MM-DD (both coexist)."
- winner W: `role=supersedes` — "Supersedes [L](/L.md) as of YYYY-MM-DD (this concept wins)."
- loser L: `role=superseded` — "Superseded by [W](/W.md) as of YYYY-MM-DD (label-only, no status change)."

Note: proposal wrote `# Reconciliation`; design uses `## Reconciliation` (h2) to avoid a
second h1 colliding with the concept title. Detection ignores heading level and role —
any prior `target=<counterpart>` anchor suppresses re-append.

**Log line** (`bundle_log.insert_log_entry`, mirroring relate's `**Relate**` link style):
- symmetric new: `**Reconcile**: Recorded a symmetric 'reconciled_with' between [A](/A.md) and [B](/B.md).`
- winner new: `**Reconcile**: [W](/W.md) supersedes [L](/L.md) (recorded 'supersedes').`
- no-change: `**Reconcile**: [A](/A.md) and [B](/B.md) are already reconciled; no change.`

**`--winner` validation**: resolve `W` via `_resolve_concept_path`, compare its canonical id
to the two resolved pair members; if it equals neither → `ValueError` → exit 1, BEFORE any
write. The non-winner member becomes the loser.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modify | Add `reconcile` command (verb, two write shapes, gate, body note, log, idempotency) |
| `tests/.../test_reconcile.py` | Create | Unit/integration coverage for the verb |

No new modules, no schema change: relation vocabulary is open (relations.py);
`reconciled_with`/`supersedes` are new string values only.

## Phase-B write order & partial-failure handling

Order: `doc_a` → `doc_b` → `log.md` (content before audit trail, mirroring relate). Each
`write_atomic` is individually atomic (temp + rename) so no single file is left corrupt.
The whole verb is NOT transactional — same documented limitation as every write verb. A
crash between files yields a benign, additive, git-recoverable partial (e.g. `doc_a`
written, `doc_b` not, `log.md` untouched → no misleading audit entry for an incomplete
write). Recovery: `git checkout`, or simply re-run — idempotency completes the missing
side without duplicating the landed one.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | winner validation; anchor detection; log line variants | direct helper calls |
| Integration | symmetric → two edges + two notes + log; `--winner` → one edge; re-run idempotent (no dup edge/note, "no change" log); errors (unknown id, self-pair, `--winner` not in pair, missing concept) all fail in Phase-A pre-write; confirm-gate precedence | typer CliRunner over a temp workspace |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Write safety is handled by the confirm-gate + atomic writes +
additive/git-undo model, not by any external-process surface.

## Migration / Rollout

No migration required. Purely additive new verb; existing bundles unaffected.

## Open Questions

None blocking. (Minor: h2 vs h1 for the note heading — resolved to h2 above.)
