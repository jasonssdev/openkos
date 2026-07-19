# Proposal: `improve-forget-init-ux` — fix the forget→re-ingest trap (idempotent ingest) + init next-step hint

## Intent

Two verified MVP-1 first-run UX gaps:
- **#1 (the trap)**: After `openkos forget <id>`, the `raw/<file>` copy stays behind
  (`forget` never touches `raw/`). Re-ingesting that same source is then REFUSED by
  `ingest` because `raw_dest.exists()` — forcing a manual `rm raw/<file>` that requires
  engine-internal knowledge. `ingest`'s refusal is content-blind: it refuses even a
  byte-identical source.
- **#2**: `openkos init` prints what it created but never tells the user what to do next.

## Scope

### In Scope
- **#1 — idempotent re-ingest, fixed in `ingest` (NOT `forget`)** (`cli/main.py:245-256`):
  when the incoming source's `raw/<name>` already exists AND `concept_path` does not,
  compare incoming bytes to the existing `raw/<name>` bytes. If **identical** → skip the
  raw copy (raw is reused, never re-opened for write) and (re)generate only the bundle
  concept + catalog (`index.md`/`log.md`). If **differing** → still refuse, with a
  distinguishing message (content differs from the immutable raw copy).
- **#2 — init hint** (`cli/main.py:123-128`): add one `typer.echo(...)` after the success
  summary naming the next step (`openkos ingest <path>`). Printed unconditionally (init has
  no TTY/quiet gating).

### Out of Scope (non-goals)
- NO `forget --purge-raw` (decision #717 keeps purge machinery in MVP-2).
- NO changes to `forget` whatsoever.
- NO overwriting/deleting/re-writing raw bytes in ANY path — ever.
- NO change to the differing-source refusal (still refuses).
- NO hash/checksum store — an in-place byte comparison only, no persisted digest.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ingestion`: "already-ingested source is refused, not overwritten" becomes — an
  **identical** already-present source is **regenerated** (concept + catalog, raw reused);
  a **differing** already-present source is **still refused**. Adds one new named scenario
  for the byte-identical re-ingest path.
- `workspace-init`: "Workspace Creation" gains a next-step hint requirement (success output
  names `openkos ingest <path>` as the next action).

## Approach

The fix lives in `ingest`, not `forget`, precisely to preserve raw immutability: the raw
path is derived the same basename-only way `ingest` already does (`src.name`), so it never
trusts a concept's `resource`/`provenance` frontmatter — **zero new path-traversal surface**.
In Phase A, split today's blanket `raw_dest.exists()` refusal into: (raw exists + concept
exists) → refuse; (raw exists + concept absent + bytes identical) → regenerate; (raw exists
+ concept absent + bytes differ) → refuse with distinct message. Phase B then skips
`copy_exclusive` on the identical path. This closes the trap **retroactively** — because the
fix is in `ingest`, anyone already trapped by a prior plain `forget` is rescued with no
`forget` change and no reopening of #717. The identity check is a full-byte comparison
(exact, simplest for MVP-1; design confirms full-byte vs hash). Design also specifies the
preview line for the regenerate case (a `~` "regenerate concept" line, not a `+ new raw`
line, since raw is unchanged) under the existing `--auto`/confirm gate.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | `ingest` Phase A refusal split + Phase B copy-skip; `init` hint echo |
| `openspec/specs/ingestion/spec.md` | Modified | MODIFIED refusal scenario + new byte-identical regenerate scenario |
| `openspec/specs/workspace-init/spec.md` | Modified | Next-step hint requirement |
| `tests/unit/cli/test_ingest.py` | Modified | Re-ingest-after-forget (identical→regen, differing→refuse) |
| `tests/unit/cli/test_init.py` | Modified | Assert hint text present |
| `docs/cli.md` | Modified | Describe idempotent re-ingest + init hint |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Behavior change to a shipped, tested `ingest` refusal contract | Med | Explicit MODIFIED delta; new scenario names the identical vs differing split |
| Full-byte read on large sources adds Phase A cost | Low | MVP-1 scale small; design confirms full-byte vs hash; bounded single read |
| Silent raw overwrite via same-name modified source | Low | Differing bytes still refuse — raw bytes never written/deleted |
| Preview implies a raw write in the regenerate case | Low | Design specifies a `~` regenerate line, not `+ new raw` |

## Rollback Plan

Additive and local: revert the `ingest` idempotency branch (restore the original
`raw_dest.exists()` blanket refusal), remove the `init` next-step echo line, drop the new
tests, and revert the two spec deltas + `docs/cli.md` prose. No persisted state, no
migration, no config-schema change, no dependency change, `forget` untouched.

## Dependencies

- Reuses existing `src.read_bytes()`/`raw_dest.read_bytes()` reads and `okf` concept/catalog
  regeneration — no new `fsio.py` primitive.

## Success Criteria

- [ ] `init → ingest → forget --auto → ingest` (same source) exits 0; concept + catalog regenerated.
- [ ] `raw/<name>` bytes are byte-unchanged across the re-ingest (never written/deleted).
- [ ] Re-ingesting a MODIFIED source under the same name still refuses, with a distinct message.
- [ ] `openkos init` prints a next-step hint naming `openkos ingest <path>`, unconditionally.
- [ ] `uv run pytest` green; ruff/mypy clean.

## AGENTS.md Non-Negotiables

Raw/ immutability is FULLY preserved: raw bytes are never written or deleted in any code
path (the identical case reuses the existing file; the differing case refuses). This is the
exact reason the fix lives in `ingest`, not a `forget`-purge — no raw-touching command is
added, no `resource`/`provenance` frontmatter is trusted to locate a file to delete, and
decision #717's forget-purge MVP-1/MVP-2 boundary is not reopened.
