# Reconcile Command Specification

## Purpose

Defines `openkos reconcile <id-a> <id-b>` — the first WRITE verb of the freshness-lint-v1 arc. It lets a human record how a contradiction (surfaced read-only by `contradictions`, S3) was resolved: concepts coexist (`reconciled_with`) or one supersedes the other (`--winner`/`supersedes`). Additive-only, git-reversible, no ledger.

## Requirements

### Requirement: Workspace Gate and Pair Validation

The system MUST require an active workspace before `reconcile` runs. Both `<id-a>` and `<id-b>` MUST resolve to existing concepts. The command MUST reject a self-pair (`id-a == id-b`). Any validation failure MUST exit non-zero with NO write performed.

#### Scenario: Unknown concept id

- GIVEN a workspace with concept `alpha` but no concept `ghost`
- WHEN the user runs `reconcile alpha ghost`
- THEN the command errors and exits non-zero
- AND no file is modified

#### Scenario: Self-pair rejected

- GIVEN a workspace with concept `alpha`
- WHEN the user runs `reconcile alpha alpha`
- THEN the command errors and exits non-zero
- AND no file is modified

### Requirement: Default Symmetric Reconciliation

By default (no `--winner` flag), the system MUST write a SYMMETRIC `reconciled_with` typed edge on BOTH concepts, each referencing the other as `target`. The system MUST append a `# Reconciliation` body note to BOTH concepts referencing the counterpart, and MUST append a `**Reconcile**` line to `log.md`.

#### Scenario: Symmetric reconcile

- GIVEN concepts `alpha` and `beta` exist and are unreconciled
- WHEN the user runs `reconcile alpha beta` and confirms
- THEN `alpha` gains a `reconciled_with` edge targeting `beta` and a `# Reconciliation` note referencing `beta`
- AND `beta` gains a `reconciled_with` edge targeting `alpha` and a `# Reconciliation` note referencing `alpha`
- AND `log.md` gains a `**Reconcile**` line

### Requirement: Directional Reconciliation via --winner

When `--winner <id>` is passed, the system MUST write a DIRECTIONAL `supersedes` edge from the winner to the loser (one outbound edge on the winner only), and MUST add a `# Reconciliation` note on both concepts and a `**Reconcile**` log line. `<id>` MUST equal exactly one of the two pair members; any other value MUST error before any write occurs. The `supersedes` edge MUST be documented as label-only: it enforces no deprecation or lifecycle behavior.

#### Scenario: Winner supersedes loser

- GIVEN concepts `alpha` and `beta` exist and are unreconciled
- WHEN the user runs `reconcile alpha beta --winner alpha` and confirms
- THEN `alpha` gains a single outbound `supersedes` edge targeting `beta`
- AND `beta` gains no outbound edge
- AND both concepts gain a `# Reconciliation` note; `log.md` gains a `**Reconcile**` line

#### Scenario: --winner id not in pair

- GIVEN concepts `alpha`, `beta`, `gamma` exist
- WHEN the user runs `reconcile alpha beta --winner gamma`
- THEN the command errors and exits non-zero
- AND no file is modified

### Requirement: Safe-Write Confirm Gate

The system MUST plan the write in memory, PREVIEW the edge(s), notes, and log line to be written, then gate the write behind confirmation: `--auto` bypasses, config `review: false` bypasses, an interactive TTY MUST prompt via confirm-with-abort, and a non-TTY session without `--auto` MUST refuse with no write. Declining an interactive prompt MUST result in NO write.

#### Scenario: Interactive decline aborts

- GIVEN an interactive TTY session and an unreconciled pair
- WHEN the user runs `reconcile alpha beta`, views the preview, and declines
- THEN no edge, note, or log line is written
- AND the command exits non-zero

#### Scenario: --auto bypasses the gate

- GIVEN a non-TTY session and an unreconciled pair
- WHEN the user runs `reconcile alpha beta --auto`
- THEN the write proceeds without a prompt

#### Scenario: Non-TTY without --auto refuses

- GIVEN a non-TTY session, `review: true` in config, and an unreconciled pair
- WHEN the user runs `reconcile alpha beta` without `--auto`
- THEN the command refuses and exits non-zero
- AND no file is modified

### Requirement: Idempotent Re-run

Re-running `reconcile` on an already-reconciled pair (same shape: symmetric or same `--winner`) MUST NOT duplicate the edge (deduped on `(target, type)`) and MUST NOT re-append a `# Reconciliation` note already citing the same counterpart. The system MUST instead write a "no change" variant of the `**Reconcile**` log line.

#### Scenario: Re-run is a no-op write

- GIVEN `alpha` and `beta` were already reconciled symmetrically
- WHEN the user runs `reconcile alpha beta` again and confirms
- THEN no duplicate `reconciled_with` edge or `# Reconciliation` note is added to either concept
- AND `log.md` gains a `**Reconcile**: ...; no change.` line

### Requirement: Additive-Only, No Status/Lifecycle Write

The system MUST NOT delete or overwrite existing body content or relations; all writes MUST be additive (new edge, appended note, appended log line). The system MUST NOT write any `status`/deprecate field. The write path MUST NOT invoke contradiction detection or any LLM.

#### Scenario: Existing content preserved

- GIVEN `alpha` has prior unrelated body content and relations
- WHEN the user runs `reconcile alpha beta` and confirms
- THEN all prior body content and relations on `alpha` remain unchanged
- AND only the new edge and note are appended
