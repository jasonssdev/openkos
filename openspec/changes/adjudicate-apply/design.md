# Design: adjudicate --apply (interactive merge path, #137 Slice 2b-ii)

## Technical Approach

Add an `--apply` mode to the existing `adjudicate` command. After the unchanged
adjudication run (find_candidates → adjudicate_candidates, same Ollama error
tiers), branch into an interactive walk that, per eligible SAME 2-member group,
re-resolves both members (stale-id guard), builds a no-write preview via
`prepare_merge`, prompts `[y/N/skip]`, and on `y` executes
`merge_core` + `_autocommit` — reusing 2b-i building blocks verbatim. Non-apply
behavior stays byte-identical. All logic lives inline in
`src/openkos/cli/main.py`'s `adjudicate` (~3790).

## Verified signatures (real source, main)

| Symbol | Real signature (line) | Proposal claim | Status |
|--------|----------------------|----------------|--------|
| `prepare_merge` | `(bundle_dir, index_path, log_path, survivor_path, absorbed_path, survivor_canonical, absorbed_canonical, root, *, now)` (2358) | matches | OK |
| `merge_core` | `(bundle_dir, index_path, log_path, prepared) -> MergeResult` (2471) | `merge_core(prepared)` | **MISMATCH** — pass all four args |
| `_autocommit` | `(root, paths, message)` (156) | matches | OK |
| `_resolve_concept_path` | `(bundle_dir, concept_id) -> (Path, canonical)`; raises `ValueError` if file absent (1049) | — | OK — doubles as stale-id guard |

`member_ids: tuple[str, ...]` is sorted ascending, unique, ≥2 (candidates.py:63).

## Architecture Decisions

### D1: Mutual exclusivity (`--apply` + `--json`)
**Choice**: First statement in `adjudicate` body, before the workspace gate:
if `apply and json_output` → stderr message, `raise typer.Exit(code=2)`.
**Rationale**: fail-fast, no side effects; interactive vs machine output is
contradictory (proposal decision 5).

### D2: Mode branch placement
**Choice**: `--apply` branch runs AFTER the Ollama handlers and AFTER the
`if json_output:` short-circuit, REPLACING the human-render path. `if apply:
<walk>; return` sits before the existing tally/legend render.
**Rationale**: apply reuses the same `results`; D1 guarantees json and apply
never coexist, so ordering is unambiguous.

### D3: Eligibility + survivor/absorbed
**Choice**: eligible = `verdict is Verdict.SAME and len(member_ids) == 2`.
survivor = `member_ids[0]`, absorbed = `member_ids[1]` (alphabetical-first,
zero I/O). SAME with >2 → `skipped (N>2, merge manually)`. Non-SAME → silently
ineligible (not tallied).

### D4: Stale-id guard (explicit pre-check)
**Choice**: per group, call `_resolve_concept_path(bundle_dir, survivor_id)` and
`_resolve_concept_path(bundle_dir, absorbed_id)` inside `try/except ValueError`.
On `ValueError` → `skipped (member already merged)`, continue. This both detects
an already-absorbed member AND yields the `(path, canonical)` pairs
`prepare_merge` needs.
**Rationale**: results are computed once up front; a prior merge this run may
have removed a later group's member. An explicit re-resolve gives a clean
message vs. catching a deep `prepare_merge` raise.

### D5: Preview line (from PreparedMerge)
**Choice**: one concise line per group before the prompt:
`  merge {absorbed} into {survivor} (sensitivity {before}->{after}, {len(touched_files)} rewrite(s), removes bundle/{absorbed}.md)`.
Sources: `prepared.sensitivity_before/after`, `prepared.touched_files`,
`prepared.removed`. Surfaces exactly what fuses (decision 8).

### D6: Prompt parse
**Choice**: `typer.prompt(f"Merge {absorbed} into {survivor}? [y/N/skip]", default="N", show_default=False)`.
Parse `answer.strip().lower()`: `{"y","yes"}` → apply; everything else
(`n`,`no`,``,`skip`,`s`, unknown) → decline+continue. Empty resolves to default
`"N"`. **Rationale**: `typer.confirm` is y/N only; a manual prompt gives the
third `skip` token. CliRunner drives each prompt via `input="y\n"` /`"\n"`/`"skip\n"`.

### D7: Apply + commit (per merge)
**Choice**: on `y`, `now = datetime.now(UTC)` (fresh per group), then
`prepare_merge(...)`, then
`result = merge_core(layout.bundle_dir, index_path, log_path, prepared)`, then
`_autocommit(root, ["bundle/index.md","bundle/log.md", *(f"bundle/{r}" for r in result.touched_files), f"bundle/{survivor}.md", f"bundle/{absorbed}.md"], f"openkos: merge {absorbed} into {survivor}")`.
merge_core has NO VCS side effect (2483), so the loop owns the commit — same as
`merge`. Commit message reused verbatim from `merge`.

### D8: Mid-run failure
**Choice**: wrap `prepare_merge`/`merge_core` in `try/except (OSError, ValueError)`
→ stderr `openkos adjudicate --apply: failed while merging {absorbed} into {survivor} -- {exc}.` → `raise typer.Exit(code=1)`. STOP the loop; prior per-merge
commits remain (reversible via `unmerge`). Never continue past a destructive
failure (decision 9).

### D9: End summary
**Choice**: track four ints — `applied`, `skipped_n_gt2`,
`skipped_already_merged`, `skipped_declined`. Final line:
`openkos adjudicate --apply: applied {applied}, skipped {n_gt2+already+declined} (N>2: {n_gt2}, already-merged: {already}, declined: {declined})`.
Zero-eligible → `applied 0, skipped 0 (...)`, exit 0, no crash.

## Data Flow

    adjudicate --apply
      └─ D1 reject (apply+json → exit 2)
      └─ workspace gate → read_config → find_candidates → adjudicate_candidates (Ollama tiers, unchanged)
      └─ if json: emit+return   |   if apply:
           for result in results (order):
             eligible? ── no(N>2) ─→ msg, skipped_n_gt2++
                │ yes
             _resolve_concept_path×2 ── ValueError ─→ msg, skipped_already_merged++
                │ ok
             prepare_merge → preview line → prompt [y/N/skip]
                ├─ decline → skipped_declined++
                └─ y → merge_core → _autocommit (applied++) | except → stderr, Exit(1)
           summary line

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` (`adjudicate` ~3790) | Modify | add `apply` Option, D1 guard, apply branch (D2–D9) |
| `tests/unit/cli/test_adjudicate.py` | Modify | new `--apply` tests (prompt/eligibility/stale-id/summary/failure) |

## Interfaces / Contracts

New flag only: `apply: bool = typer.Option(False, "--apply", help="Interactively merge each SAME 2-member group after previewing it.")`.
No new types; reuse `PreparedMerge`/`MergeResult`. Building blocks unchanged.

## Testing Strategy (strict TDD, RED first)

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | apply+json rejected exit 2 | `runner.invoke(app, ["adjudicate","--apply","--json"])`, assert code 2, no adjudicate call |
| Unit | prompt y applies+commits | monkeypatch `adjudicate_candidates` → SAME 2-member over real bundle docs; `input="y\n"`; assert absorbed removed, survivor ledger, commit made |
| Unit | N/empty/skip decline | `input="n\n"`/`"\n"`/`"skip\n"`; assert no write, `skipped_declined` in summary |
| Unit | N>2 group skipped | fake SAME with 3 member_ids; assert `skipped (N>2...)`, no prompt |
| Unit | stale-id guard | two overlapping SAME groups sharing a member; first `y`, second → `skipped (member already merged)` |
| Unit | mid-run failure stops | monkeypatch `merge_core` to raise `OSError`; assert exit 1, stderr, loop stops, prior commit stays |
| Unit | summary counts | mixed groups; assert `applied X, skipped Y (...)` exact wording |
| Integration | unmerge round-trip | apply a real merge, then `unmerge` restores byte-identical pre-merge state |

Seams: `runner.invoke(..., input=...)` for prompts; `monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake)`; `_simulate_tty` only if a code path checks `isatty` (apply uses `typer.prompt`, which reads stdin directly — CliRunner `input=` suffices, no TTY simulation needed).

## Threat Matrix

Destructive write + VCS automation surface, but reused unchanged from 2b-i.

| Row | Applicable | Behavior / test |
|-----|-----------|-----------------|
| Path traversal via member id | N/A | `member_ids` are canonical, from `find_candidates`; `_resolve_concept_path` re-validates (reserved/`..`/absolute) |
| Destructive write recovery | Applicable | mid-run failure stops loop; prior merges are standalone reversible commits — test D8 + unmerge round-trip |
| Commit scoping (no `-A`) | N/A | `_autocommit`→`commit_paths` scoped `git add -- <paths>`, unchanged |
| Stale/absorbed id acted twice | Applicable | D4 re-resolve guard — overlapping-group test |
| Prompt injection / mis-parse | Applicable | strict token allowlist (only `y`/`yes` apply); decline tests |

## Migration / Rollout

No migration. Revert = drop the `--apply` branch/flag; building blocks and
read-only `adjudicate` untouched.

## Open Questions

- [ ] None blocking. `merge_core` arity mismatch in the proposal is resolved
      here (D7): call with `(bundle_dir, index_path, log_path, prepared)`.
