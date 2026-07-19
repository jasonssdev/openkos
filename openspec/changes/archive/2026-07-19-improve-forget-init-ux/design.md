# Design: `improve-forget-init-ux` — idempotent re-ingest + init next-step hint

## Technical Approach

Both fixes live in `cli/main.py` and reuse existing primitives — no new `fsio`
primitive, no `forget` change, no schema/state. The trap is closed from the
`ingest` side: the blanket `raw_dest.exists() or concept_path.exists()` refusal
(main.py:245-256) is replaced by a **byte-content-aware** decision. When
`raw/<name>` already exists, the incoming source is full-byte compared against
it (in Phase A, **before any write**). Identical bytes → idempotent re-ingest:
raw is reused (never re-opened for write), and only the DERIVED concept doc plus
`index.md`/`log.md` are regenerated. Differing bytes → keep refusing (raw is
immutable). `init` gains one unconditional next-step `typer.echo`.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | Discriminant is `raw_dest.exists()` + full-byte compare, **superseding** the old OR-refusal. Byte compare happens inside the existing Phase-A `try/except (OSError, ValueError)` (main.py:220-262), before any write | Compare only when concept also absent (proposal's narrower phrasing) | Idempotency must hold regardless of concept presence (task 6: identical re-ingest with concept still present must also regenerate cleanly). Broadening to "raw present + identical → regenerate" is the coherent contract. |
| **D2** | Regenerate case: concept write is **non-exclusive** `fsio.write_atomic` (not `write_exclusive`) | `write_exclusive` + pre-unlink | Concept is a reconstructible DERIVED doc; it may already exist (no-forget case). `write_atomic` create-or-replaces atomically, covering both sub-cases without branching on concept presence. |
| **D3** | Regenerate index edit = `remove_index_entry(index_text, f"sources/{slug}")` **then** `insert_source_entry(...)` | `insert_source_entry` alone | `insert_source_entry` (index.py:54) appends with NO dedup. No-forget re-ingest already has the bullet → a bare insert duplicates it. `remove_index_entry` is idempotent (0 matches → unchanged, index.py:166), so remove-then-insert yields exactly one entry in BOTH the post-forget and no-forget cases. |
| **D4** | Full-byte compare (`src.read_bytes() == raw_dest.read_bytes()`), inline in `main.py`, not a new `fsio` helper | Hash/size heuristic; new `fsio.files_identical` | Honest exact identity; cheap at MVP-1 sizes. Proposal's dependency note pins "no new fsio primitive"; a read+compare is not a mutation, so it stays a local expression. |
| **D5** | Odd state (raw **absent** + concept present) → REFUSE with an "inconsistent workspace" message | Treat as fresh; regenerate | The inverse of `forget`; no raw bytes to compare, and `write_exclusive` on the existing concept would fail anyway. Refusing preserves the old `concept_path.exists()` half's intent and surfaces real drift. |
| **D6** | `init` hint is one unconditional `typer.echo` after the success summary (main.py:128) | TTY/quiet gating | `init` has no TTY/quiet gate on its success line and no `--quiet` flag; the hint matches that unconditional style. |

**ADR gate — verdict: NO ADR.** (1) Novel pattern/interface with tradeoffs? No —
byte-compare + `write_atomic` + `remove_index_entry`/`insert_source_entry` are all
established primitives; remove-then-insert is a small local idempotency idiom. (2)
Hard to reverse? No — additive branch, `git revert` restores the blanket refusal;
zero persisted state/schema/migration/dependency. BOTH must hold; neither does.
The contract change IS real, but its durable record is the `ingestion` spec
MODIFIED delta (sdd-spec's job) — an ADR would duplicate it. Matches the zero-ADR
precedent of `add-doctor-command` / `add-query-command`.

## Control flow (replaces main.py:245-256)

```python
raw_exists = raw_dest.exists()
regenerate = False
if raw_exists:
    if src.read_bytes() != raw_dest.read_bytes():          # D4, before any write
        # differing source under an immutable raw copy -> refuse
        typer.echo(
            f"openkos ingest: refusing to ingest -- '{src}' differs from the "
            f"existing 'raw/{name}' copy; raw sources are immutable. Ingest "
            "under a different name, or inspect the existing copy.", err=True)
        raise typer.Exit(code=1)
    regenerate = True                                       # identical -> idempotent
elif concept_path.exists():                                 # D5 odd state
    typer.echo(
        f"openkos ingest: refusing to ingest -- 'bundle/sources/{slug}.md' "
        f"exists but its raw source 'raw/{name}' is missing; the workspace is "
        "in an inconsistent state, inspect it before retrying.", err=True)
    raise typer.Exit(code=1)
# else: raw absent + concept absent -> fresh (regenerate stays False)
```

Index/log build (in the build `try`, replacing main.py:299-308):

```python
if regenerate:
    index_text, _ = bundle_index.remove_index_entry(index_text, f"sources/{slug}")  # D3
    log_line = (f"**Re-ingest**: Regenerated [{title}](/sources/{slug}.md) from "
                f"existing `{resource}` (identical source, raw copy reused).")
else:
    log_line = f"**Ingest**: Imported [{title}](/sources/{slug}.md) from `{resource}`."
new_index_text = bundle_index.insert_source_entry(index_text, title=title, slug=slug,
                                                  description=description)
new_log_text = bundle_log.insert_log_entry(log_text, now.astimezone().date(), log_line)
```

Preview (branch main.py:315-319) and Phase B (branch main.py:332-337):

```python
if regenerate:                       # PREVIEW
    typer.echo("openkos ingest: proposed changes (re-ingest -- identical source already present):")
    typer.echo(f"  ~ raw/{name} (existing copy reused -- not rewritten)")
    typer.echo(f"  ~ bundle/sources/{slug}.md (regenerated)")
    typer.echo(f"  ~ {index_path.name} (Source entry refreshed)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
# else: existing "+ raw / + concept / ~ index / ~ log" block unchanged

sources_dir.mkdir(parents=True, exist_ok=True)             # PHASE B
if regenerate:
    fsio.write_atomic(concept_path, concept_content)        # D2: raw copy SKIPPED
else:
    fsio.copy_exclusive(src, raw_dest)
    fsio.write_exclusive(concept_path, concept_content)
fsio.write_atomic(index_path, new_index_text)
fsio.write_atomic(log_path, new_log_text)
```

`init` hint (after main.py:128):

```python
typer.echo("Next: run `openkos ingest <path>` to import your first source.")
```

## Sequence: ingest idempotency decision

```
ingest src
  │ derive name=src.name, slug, raw_dest, concept_path   (basename-only, unchanged)
  ▼
raw_dest.exists()? ──no──▶ concept_path.exists()? ──yes──▶ REFUSE (D5 inconsistent, exit 1)
  │ yes                                    └─no──▶ FRESH: copy_exclusive + write_exclusive
  ▼                                                        + insert_source_entry  (exit 0)
src bytes == raw_dest bytes?  (D4, no write yet)
  ├─ differ ─▶ REFUSE ("differs from immutable raw", exit 1; raw untouched)
  └─ identical ─▶ REGENERATE (D2/D3): skip raw copy, write_atomic(concept),
                  remove_index_entry then insert_source_entry, Re-ingest log  (exit 0)
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | Replace ingest refusal (245-256) with byte-aware branch; `regenerate` flag through build/preview/Phase B; init hint echo |
| `openspec/specs/ingestion/spec.md` | Modify | MODIFIED refusal scenario + new byte-identical-regenerate + inconsistent-state scenarios (sdd-spec) |
| `openspec/specs/workspace-init/spec.md` | Modify | Next-step hint requirement (sdd-spec) |
| `tests/unit/cli/test_ingest.py` | Modify | Round-trip regen, differing-refuse, no-forget regen (dedup), odd-state refuse |
| `tests/unit/cli/test_init.py` | Modify | Assert hint text |
| `docs/cli.md` | Modify | ingest re-ingest behavior + init next-step mention |

## Testing Strategy (strict TDD, ≥90 branch, CliRunner + existing fixtures)

| Layer | What | Approach |
|---|---|---|
| CLI | Round-trip regen | `init` → `ingest --auto` → `forget --auto` → `ingest --auto` (same file): exit 0, concept back, `raw/<name>` byte-unchanged vs pre-forget `_snapshot`, exactly one index bullet, new `Re-ingest` log line |
| CLI | No-forget regen (D3) | `init` → `ingest` → `ingest` same file again: exit 0, exactly ONE index entry (dedup proof), raw byte-unchanged, concept overwritten |
| CLI | Differing source | `ingest` → mutate src bytes (same name) → `ingest`: exit 1, "differs from the existing 'raw/<name>' copy" message, raw byte-unchanged |
| CLI | Odd state (D5) | `ingest` → `rm raw/<name>` → `ingest`: exit 1, "inconsistent state" message |
| CLI | Preview | Regenerate run prints `~ raw/<name> (existing copy reused ...)` and no `+ raw` line |
| CLI | init hint | `init` in empty dir: `"ingest"` next-step string in `result.stdout` |

## Threat Matrix

**N/A** — no shell, subprocess, routing, VCS/PR automation, or executable-file
classification. The raw path is still derived basename-only (`src.name`), the SAME
containment as today; the design never trusts `resource`/`provenance` frontmatter
to locate a file. The only new I/O is two local `read_bytes()` reads for the
compare — no mutation, no untrusted-input surface.

## Migration / Rollout

No migration. Additive/local; `git revert` restores the blanket refusal and removes
the init hint. No persisted state, config-schema, or dependency change; `forget`
untouched (decision #717 boundary not reopened).

## Open Questions

- [ ] None blocking. Byte-vs-hash (D4), non-exclusive concept write (D2), index
      dedup (D3), and odd-state handling (D5) all resolved.
