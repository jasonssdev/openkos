# Design: Relocate the merge ledger to `bundle/.state/ledger/`

## Corrections to the proposal (read first)

Three proposal claims did not survive code re-verification.

| Proposal claim | Verified reality |
|---|---|
| `src/openkos/bundle/references.py` — Modified | **No change needed.** `find_inbound_references` (`references.py:90`) is pure over a `files: Mapping[str, str]` its *caller* builds. Every builder is a `sorted(bundle_dir.rglob("*.md"))` loop (`cli/main.py:3841, 4438, 5172, 5444, 5959, 6542`). A sidecar that is not `*.md` is excluded by construction. |
| `src/openkos/sensitivity.py::merged_content_blocked` — Modified | **No change needed.** Its signature (`sensitivity.py:181-188`) takes a `MergeLedgerEntry`, not a survivor. Its only caller `contradiction._load_ledger_bodies` (`:517`, gate at `:567`) also takes an entry. Only `_merged_body_candidates` (`:431`) changes — its entry *source*. |
| "viable because every operation auto-commits" (Migration) | **Qualified.** `_autocommit` (`cli/main.py:804-843`) is best-effort and non-fatal: it silently skips with a stderr WARNING when there is no repo, no git identity, or on any `GitError`/`OSError`. Git is a *likely* safety net, never a derivable invariant. This design does not derive crash safety from git. |

## Technical approach

One new leaf module `src/openkos/bundle/ledger.py` owns the sidecar store: path mapping, read, two-phase write, crash recovery, and the two `doctor` scans. Nothing else grows ledger knowledge. Entry schema (`okf.MergeLedgerEntry`, v1/v2/v3, `model/okf.py:118-140`) is untouched; the sidecar is a *container* around `okf.encode_merged_from`/`decode_merged_from`.

## Decision 1 — Crash safety in a two-file world

### Reachable partial states

Writes today (`merge_core`, `cli/main.py:6689-6734`): index → log → touched rewrites → **V** survivor → **D** absorbed delete. The sidecar adds **S**.

Ordering candidates, each crashed at every boundary:

| Order | Crash point | State | Verdict |
|---|---|---|---|
| S→V→D | after S | ledger has entry N; survivor unmerged; absorbed present | Phantom entry. `_reject_already_merged` (`merge.py:74`) then refuses a merge that never happened. **Detectable, not self-healing.** |
| V→S→D | after V | survivor merged; **no ledger entry** | Merge is permanently irreversible. Absorbed still on disk, so no data loss, but round-trip parity (ADR-0002 #2) is gone. **Silently wrong.** |
| V→D→S | after D | absorbed deleted; no entry | **Data loss.** Rejected outright. |
| any | after D | ledger + survivor consistent, absorbed orphan | Same state reachable today. Status quo. |

Unmerge is the mirror: restore survivor/absorbed/index/log, then pop the tail. Pop-last leaves a re-runnable no-op (restores are byte-identical on retry); pop-first leaves an unreversible restore. **Pop last.**

### Chosen mechanism: two-phase write with a hash-bound intent marker

Rejected alternatives: *ledger-last* (V→S, silently irreversible); *derive from git* (auto-commit is best-effort, see corrections); *hash in the survivor pointing at its sidecar* (the survivor is user-editable, so the hash goes stale on any legitimate edit and the detector becomes noise).

```
S1  write_atomic(<id>.ledger.okf.pending)   # full new container + expected_survivor_sha256
V   write_atomic(survivor.md)               # unchanged call site
S2  os.replace(pending, <id>.ledger.okf)    # commit
D   remove_file(absorbed.md)
```

Recovery is a total function of on-disk state, with no heuristic:

| `.pending` | `sha256(survivor_on_disk)` | Meaning | Repair |
|---|---|---|---|
| absent | — | consistent | none |
| present | == `expected_survivor_sha256` | V landed, S2 torn | **roll forward**: promote pending |
| present | != | V never landed | **roll back**: unlink pending |

`expected_survivor_sha256` is available before any write — it is `sha256(prepared.plan.merged_survivor)` (`cli/main.py:6733`).

### Filesystem assumptions, stated

- `os.replace` / `Path.replace` on one filesystem is atomic against concurrent readers (POSIX `rename(2)`; Windows `MoveFileEx REPLACE_EXISTING`). Both `.pending` and the committed sidecar live in the same directory.
- `fsio.write_atomic` (`fsio.py:39-69`) fsyncs the temp file's **content** and deliberately **omits the parent-directory fsync** (`fsio.py:56-58`). Therefore this scheme is correct against a **process** crash (SIGKILL, `^C`, uncaught exception) and best-effort against **power loss**, where the journal may reorder the S1 and V renames. This is not a regression: ADR-0002's single-file write had the identical durability gap. Adding a directory fsync is a separate, orthogonal change.
- `fsio.rename_two_step` (`fsio.py:107`) **does not apply**. It exists for NFC/NFD spelling collisions on normalization-insensitive volumes, where a rename between canonically equivalent names is a silent no-op. `pending` → `ledger.okf` differ by a literal ASCII suffix, never a canonical equivalence, so a direct `os.replace` is a real rename on every volume.

### ADR-0002's four invariants

1. LIFO-tail-only reversal — **preserved**, `merge.py:192-197` moves verbatim.
2. Byte-for-byte round-trip parity — **preserved**.
3. I/O only via `okf.dump_frontmatter`/`load_frontmatter` — **preserved literally**; the container is a frontmatter document with an empty body.
4. Survivor-write-before-absorbed-delete — **preserved as an order, replaced as a guarantee.** The property "ledger and survivor cannot disagree, by construction" is unachievable across two files without a filesystem transaction. It is replaced by: *they can disagree, but only through a marker whose presence makes the disagreement decidable and repairable in exactly one direction.* Constructive impossibility → mechanical detection.

**Residual, accepted:** a crash between V and S2 *followed by a hand-edit of the survivor before the next openkos run* makes the hash mismatch and rolls back a merge that actually landed. No process yields control between two syscalls; this needs a crash plus a human edit in the window. Documented, not defended against.

## Decision 2 — Container format and naming

| Aspect | Decision | Rationale |
|---|---|---|
| Path | `bundle/.state/ledger/<concept_id>.ledger.okf` | Mirrors the concept's own hierarchical id, so a diff sits next to its concept in a tree view. |
| Extension | `.okf`, **never** `.md` | Every existing walk is `rglob("*.md")`. A non-`.md` suffix excludes the ledger from all six sites with **zero edits** — the EXCLUDE walk is free and structural. Not `.yaml`: the bytes are a frontmatter document, not pure YAML. |
| Container | `okf.dump_frontmatter({"schema": "openkos.merge_ledger_sidecar/v1", "survivor_id": ..., "merged_from": okf.encode_merged_from(entries)}, body="")` | Reuses the shipped codec; invariant 3 holds literally. Container versioning is separate from entry versioning, so v1/v2/v3 entries coexist unchanged. |
| LIFO order | List order inside `merged_from`, identical to today's frontmatter key | Zero semantic change; `plan_unmerge` still reads `entries[-1]`. |
| Id → filename | `ledger.ledger_path_for(concept_id, bundle_dir)`, implemented by generalizing `okf.concept_path_for` (`model/okf.py:1179-1269`) over `(root, suffix)` | **Do not invent a mapping.** That function already solves the exact NFC/NFD problem this project fought in #430/#474: probe direct, then resolve segment-by-segment by NFC-normalized name, ASCII fast path, symlink-hostile fallback. A sidecar written on HFS+ and cloned to ext4 hits it identically. |
| Marker | `<concept_id>.ledger.okf.pending` | Same directory, same container plus `expected_survivor_sha256`. |

## Decision 3 — Two opposite walks, kept separate structurally

The proposal warns against collapsing these into one predicate. This design makes collapsing *impossible* rather than merely discouraged: the two walks match disjoint glob patterns in disjoint directories.

| Direction | Who | Mechanism | Edits |
|---|---|---|---|
| **EXCLUDE** (the #550-3 fix) | `references.py::find_inbound_references`, `links.find_inbound_link_rewrites`, `relations.find_inbound_relation_rewrites`, `provenance.find_inbound_provenance_rewrites`, `okf._iter_docs`, `reindex`, `fts` | `rglob("*.md")` never matches `*.ledger.okf` | **None.** |
| **INCLUDE** (the regression guard) | `forget` (`cli/main.py:3841` region), `purge` (`:4438` region), `sensitivity.sensitive_concept_ids` / the `set-sensitivity` sweep (`:5172`, `:5444`) | one new `ledger.iter_ledgers(bundle_dir)` → `sorted((bundle_dir/".state"/"ledger").rglob("*.ledger.okf"))` | one helper, three call sites |

No walk logic is duplicated: `iter_ledgers` is written once. The safety net is a new `lint` rule (`lint.py` already does `rglob("*")` at `:1178`, `:1233`) that flags **any `.md` file under `bundle/.state/`** — so a future author who reintroduces `.md` there is told immediately, and the structural exclusion is enforced rather than assumed.

## Decision 4 — Per-entry sensitivity gate

Unchanged by design. `_merged_body_candidates` (`contradiction.py:462-482`) keeps its `for entry in entries: candidates.append(...)` loop; only line `:471` changes from `okf.decode_merged_from(metadata)` to `ledger.read_entries(survivor_id, bundle_dir)`. The once-per-entry contract is preserved *by construction* because one `_CandidateSpec` per entry still reaches `_load_ledger_bodies` → `merged_content_blocked` at `contradiction.py:567`. `sensitivity.py` is not touched.

RED test (proposal risk row): assert `merged_content_blocked` call count == entry count for a three-entry survivor, and mutate the loop to hoist the call to prove the test fails.

## Decision 5 — Two `doctor` checks, not one

The torn-write detector and the post-merge-mutation check are **different checks with different mechanics**. Conflating them was the proposal's implicit assumption.

**Check A — torn ledger write.** Mechanically exact: does any `*.ledger.okf.pending` exist, and does its hash match? Zero false positives, zero false negatives. `doctor` reports it (a 13th `CheckResult`, staying read-only per `openspec/specs/doctor-command/spec.md:466-477`); the **repair verb** (1b) fixes it. `merge`/`unmerge` **refuse** while a `.pending` exists for the survivor they touch, with **no `--force`** — it is trivially repairable and forcing it would commit a known-inconsistent ledger. This is a different refusal from the settled `--force`-escapable refusal on a mutation-flagged ledger.

**Check B — entries free of post-merge mutation.** Mechanism: **nested-prefix equality**, not either heuristic the exploration offered. Entry *k*'s `survivor_before` embeds a full survivor document that itself carries entries `0..k-1`. Decode it and compare to the survivor's own current entries `0..k-1`. Any inequality is exactly #550 consequence 2 — a later merge rewrote bytes inside an earlier embedded snapshot. Exact, no thresholds.

**The skip rule.** The comparison runs only on an entry whose `survivor_before` **embeds ledger entries of its own**. An entry that embeds nothing is silently skipped, never flagged. This is required for correctness, not an optimization: after the relocation a `survivor_before` snapshot is a survivor document whose frontmatter never carried a `merged_from` key at all — the entries live in the sidecar, not in the document — so *every* post-relocation entry embeds nothing. Without the skip, the check would false-flag every legitimate multi-entry ledger created after this change. `scan_nesting_violations` (`src/openkos/bundle/ledger.py`) implements it as `if not embedded_entries: continue`, and `test_scan_nesting_violations_skips_a_post_relocation_entry_with_nothing_embedded` is its regression alarm.

Honest false negatives:

- A **single-entry** ledger has nothing nested; the check is blind to it.
- **Cross-survivor pollution** is invisible at any *k*. `merge_core`'s `other_files` (`cli/main.py:6542`) includes every non-reserved `.md`, so a merge of X→Y can rewrite a link inside **Z**'s embedded snapshot. The ledger alone cannot distinguish that from correct bytes.
- **Every post-relocation entry**, via the skip rule above. This is the widest of the three and the easiest to misread: it means a clean Check B on a workspace created after this change is not evidence that its ledgers were examined — there was nothing in scope to examine. State it wherever the check's result is reported, or a `[PASS]` reads as a stronger claim than it is.

Because those two gaps are real, the migration gate is deliberately coarser than the check: **the repair verb refuses whenever any survivor in the bundle carries ≥2 entries**, bundle-wide — two merges anywhere means cross-survivor pollution is possible. Check B supplies the precise, citable finding; the merge-count gate supplies soundness. Check B is a **migration-era** check: once entries live outside `bundle/**.md`, no link scan can reach them and the corruption class is structurally extinct for post-1a entries.

## File changes

| File | Action | Description |
|---|---|---|
| `src/openkos/bundle/ledger.py` | Create | Path mapping, `read_entries`, `write_pending`/`commit_pending`/`discard_pending`, `recover`, `iter_ledgers`, `scan_torn_writes`, `scan_nesting_violations`. Leaf module; must not import `openkos.graph` (AGENTS.md:41). |
| `src/openkos/model/okf.py` | Modify | Generalize `concept_path_for`'s resolver over `(root, suffix)`; export `STATE_DIRNAME`. |
| `src/openkos/bundle/merge.py` | Modify | `plan_merge` takes `existing_entries` and returns `ledger_entries` (full new list) instead of writing `MERGED_FROM_KEY` into `merged_metadata` (`:160-163`); `plan_unmerge` takes `entries` instead of decoding from `survivor_text` (`:187-188`). Both become *more* pure. |
| `src/openkos/cli/main.py` | Modify | `_prepare_merge` reads entries via `ledger`; `merge_core` gets S1/S2 around V; `unmerge_core` pops last; preflight refusal on `.pending`; `forget`/`purge`/sensitivity INCLUDE sweeps; `doctor` checks 12–13. |
| `src/openkos/resolution/contradiction.py` | Modify | `_merged_body_candidates:471` entry source only. |
| `src/openkos/lint.py` | Modify | New rule: no `.md` under `bundle/.state/`. |
| `docs/adr/0013-relocate-merge-ledger-to-bundle-state.md` | Create | `Proposed`; supersedes ADR-0002's storage clause only; cross-refs ADR-0005, ADR-0011, ADR-0008. ADR-0002 gets a Status-line edit and nothing else. |
| `src/openkos/state/reindex.py` | Modify (1b) | Composed embed text per `fts.py:220-234`. |
| repair verb | Create (1b) | Torn-write repair + verbatim frontmatter→sidecar extraction, gated. |

## Testing strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `ledger.py` path mapping incl. NFD-on-byte-exact-FS | tmp_path fixtures mirroring `concept_path_for`'s own suite |
| Unit | Recovery truth table (3 rows) | construct each on-disk state directly, assert the verdict |
| Unit | Nesting check on a hand-built corrupted ledger | exact-equality assertion |
| Integration | Crash injection at S1, V, S2, D | monkeypatch `fsio.write_atomic`/`os.replace` to raise at each boundary, then run recovery and assert the truth table |
| Integration | merge → unmerge byte-for-byte parity | compare survivor/absorbed/index/log bytes |
| Integration | Per-entry gate call count + hoist mutation | prove the test fails when hoisted |
| Integration | `forget`/`purge`/set-sensitivity each reach `bundle/.state/ledger/` | one test per sweep |
| Integration | EXCLUDE: inbound-reference count ignores ledger bytes (#550-3) | pre/post assertion |

## Threat matrix

`N/A` for routing, executable-file classification, and PR automation. **Applicable:** VCS/process integration — `_autocommit` must stage the new `bundle/.state/ledger/**` paths (`commit_paths` uses scoped `git add -- <paths>`, never `-A`), or the ledger silently never enters git and the whole portability rationale fails. RED test: assert `MergeResult.committed_paths` contains the sidecar path. **Applicable:** `purge` runs `git filter-repo`; its path set must cover `bundle/.state/ledger/` or historical confidential snapshots survive a privacy purge. RED test: purge a confidential concept, assert its snapshot bytes are absent from git history.

## Migration / rollout

Per proposal. Repair verb (1b) is opt-in, prints the `git reset --hard` inverse before writing, and refuses on both Check A and the ≥2-entry gate.

## Slice boundary and line forecast

| Slice | Contents | Forecast (add+del, incl. tests) |
|---|---|---|
| **1a-i** | `ledger.py`, `okf` resolver generalization, `merge.py` purity change, `merge_core`/`unmerge_core` two-phase write + preflight refusal, crash-injection tests, ADR-0013 | ~550 |
| **1a-ii** | INCLUDE sweeps (forget/purge/sensitivity), `contradiction.py:471`, `lint` guard, `_autocommit`/`filter-repo` path coverage, per-sweep tests | ~350 |
| **1b** | `reindex.py` embed composition (#554), `doctor` checks A+B, repair verb | ~500–700 |

The proposal's single 1a (~700–900) exceeds the 400-line review budget by more than 2×. **Recommend splitting 1a into 1a-i and 1a-ii as chained PRs.** The split is clean: 1a-i is the store and its crash semantics; 1a-ii is everything that *reads* the store. 1a-i alone leaves the bundle correct but the privacy sweeps not yet extended — so **1a-ii must not be deferred past the same release**, and 1a-i's PR body must say so.

## Open questions

- [ ] Should `bundle/.state/` be added to any `.gitattributes` (e.g. `-diff` or `linguist-generated`) so ledger churn does not dominate PR diffs in user bundles? Cosmetic; not blocking.
