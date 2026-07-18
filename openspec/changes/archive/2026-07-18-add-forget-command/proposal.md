# Proposal: `openkos forget <concept-id>` (MVP-1 simplified delete)

## Intent
MVP-1 has no way to remove a concept. `bundle/index.py` only has `insert_source_entry` (append into `# Sources`); there is no removal primitive. `forget` fills that gap: delete a concept file and remove its `index.md` references, per decision #717 (undo = plain git; NO tombstones/purge — those are MVP-2). This is the missing counterpart to `ingest`.

## Scope
### In Scope
- New `forget` Typer command in `cli/main.py`, mirroring `ingest`'s Phase A (validate+preview) / confirm-gate / Phase B (write) shape.
- New GENERIC removal primitive in `bundle/index.py` (per #922): drop the bullet whose markdown link resolves to `<concept-id>` across ALL sections (Concepts/Decisions/People/Sources). Link-matching lives in the bundle layer with a narrow, self-contained normalizer — NO `bundle -> lint` dependency.
- Concept-id = bundle-relative POSIX path minus `.md` (same identity notion as `lint.py`'s `LintDoc.identity`, not imported).
- Delete the concept file; write updated `index.md` (+ plain `log.md` activity line).
- Tests in `tests/unit/cli/test_forget.py` mirroring `test_ingest.py`.

### Out of Scope (Non-Goals)
- Tombstones and purge machinery (MVP-2, per #717).
- SQLite operational-state updates (`.openkos/openkos.db` — no such code exists in `src/` yet; no-op for MVP-1).
- Inbound-link rewriting: `forget` removes the target's OWN entry+file; it does NOT hunt/rewrite links from other concepts. KNOWN LIMITATION — can leave dangling inbound links. No dangling-link detection built here.
- `docs/cli.md` corrections (cli.md:99 SQLite no-op; cli.md:103 false dangling-link/lint claim) — filed as a SEPARATE follow-up per #922.

## Resolved Open Questions (recommended defaults)
1. **log.md**: YES — write a plain `**Forget**: Removed [Title](/path.md).` bullet via `insert_log_entry`, mirroring `ingest`. Plain activity line, NOT a tombstone (#717-safe).
2. **Confirm-gate**: reuse `ingest`'s proven gate verbatim — `--auto` > `cfg.review: false` > TTY `typer.confirm` > non-TTY refuse (exit 1, re-run with `--auto`). NO new `--yes`/`--force` (avoids inventing house pattern reviewers must relearn).
3. **Concept-id validation**: it is a full relative path. Reject `..` segments, absolute paths, any path escaping `bundle_dir`, and reserved filenames (`index.md`/`log.md`). Reuse `OSError`/`ValueError` catch-and-report.
4. **Nonexistent concept-id**: clear error + exit 1. NOT a silent no-op.
5. **Dangling inbound links**: deferred (see Non-Goals) — stated as known limitation.
6. **Malformed bundle**: reuse existing `OSError`/`ValueError` convention (`_split_frontmatter_verbatim` already raises `ValueError`).

## Capabilities
### New Capabilities
- `forget-command`: remove a concept file and its `index.md` entry, with confirm gate and log line.
### Modified Capabilities
- None.

## Approach
Mirror `ingest` (Approach 1 + #922 generic removal):
- **Phase A (pure)**: `require_workspace` gate; validate concept-id (path-safety, not reserved); resolve `bundle_dir/<id>.md`, refuse if missing; read `index.md`, compute new text via the new `bundle/index.py` removal fn; compute new `log.md`; print `~`/`-` preview.
- **Confirm gate** (ingest semantics).
- **Phase B (write, non-transactional; git=recovery)**: preserve the invariant "catalog never references a missing file" — mirror-image of ingest: remove the `index.md` entry (+ `log.md`) FIRST, then delete the concept file LAST. Transient state is a benign orphan, never a dangling catalog ref.
- **okf.py stays byte-unchanged** (reuse `RESERVED_FILENAMES` only), matching lint/status precedent.
- **Deletion primitive**: recommend a new `fsio.remove_file` for layering symmetry with `write_exclusive`/`copy_exclusive` (design to confirm vs inline `Path.unlink`).

## Affected Areas
| Area | Impact | Description |
|------|--------|-------------|
| `cli/main.py` | New | `forget` command (Phase A/B + gate) |
| `bundle/index.py` | Modified | New generic removal fn + narrow link normalizer |
| `bundle/log.py` | Reuse | New call site only (`insert_log_entry`) |
| `fsio.py` | New | `remove_file` primitive (recommended) |
| `config.py`, `model/okf.py` | Reuse | Unchanged |
| `tests/unit/cli/test_forget.py` | New | Mirror `test_ingest.py` |

## Risks
| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Generic removal over-matches a bullet | Med | Match on normalized link target == exact concept-id; only drop that line; test all four sections |
| Path traversal deletes outside bundle | Low | Strict Phase-A validation before any unlink |
| Dangling inbound links post-delete | Med | Documented known limitation; deferred to MVP-2 |
| Non-transactional Phase B partial write | Low | Content-vs-catalog invariant + git recovery (ingest precedent) |

## Rollback Plan
`git status` / `git checkout` / `git clean` — the bundle is version-controlled; no manual restore path (ingest's stated position).

## Success Criteria
- [ ] `forget people/maria-salazar` deletes the file and removes its `index.md` bullet (any section).
- [ ] Nonexistent / traversal / reserved-name concept-ids refuse with exit 1, no mutation.
- [ ] Confirm gate matches `ingest` (`--auto`/`cfg.review`/TTY/non-TTY).
- [ ] `log.md` gains a plain `**Forget**` bullet; no tombstone.
- [ ] `bundle/index.py` has NO import from `lint`.

## Residual Questions for Spec/Design
- Exact bullet-matching for non-Source sections (no engine-authored format exists yet — only `# Sources` has a known shape; design must define the match contract for hand-authored bullets).
- `fsio.remove_file` vs inline `Path.unlink` (layering call).
- Preview glyph for deletion (`-` file, `~` index) — confirm wording.
