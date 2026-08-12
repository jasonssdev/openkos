# Design: Durable pending work — the contradictions vertical

Settled inputs (proposal D1–D5, split storage, contradictions-only scope) are
treated as fixed. This document decides the seven questions the proposal left
open, and states exactly how far it extends ADR-0013.

## Technical approach

Two stores, one per nature, each owned by exactly one new leaf module.

```
curate Contradictions stage ──write──→ .openkos/findings.db   (machine findings)
`openkos contradictions`    ──read───→        │
        │                                     │ decision_key join at READ time
        └──--decline/--reopen──write──→ bundle/.state/decisions/<id>.decisions.okf
                                              │              (human decisions)
`openkos next` ──────────────read─────────────┘  (open ∧ not stale) → tier
```

The two stores are joined only at read time, by a `decision_key` derived from
the proposal. Neither store holds a pointer into the other. That single
property is what removes the crash-safety problem ADR-0013 had (Decision 7).

## Decision 1 — `.openkos/` container: SQLite, not sidecars

| Option | Argument against | Argument for |
|---|---|---|
| Frontmatter sidecars under `.openkos/` | Rationale text quotes concept bodies, so each sidecar is a confidential-content file; `.openkos/` has **no** INCLUDE-walk sweep today, so this creates a new privacy surface the proposal explicitly promised not to create. Requires a new walk and a new purge branch. | Reviewability — but `.openkos/` is a derived cache that never appears in a diff, so reviewability buys nothing here. It was the sidecar's advantage *inside* `bundle/`. |
| **SQLite `.openkos/findings.db`** (chosen) | Adds a 4th path to `_purge_rebuild_indexes`'s explicit tuple (`cli/main.py:4672-4676`) — a named hazard, not a silent one. | Opener already exists: `state.derived.open_derived_connection` (WAL + `busy_timeout` + generic `meta` table). Purge's delete-and-rebuild already physically deletes `.openkos/*.db` (`cli/main.py:4658-4684`), so this adds no new privacy surface. Ordered/filtered reads for the `next` tier and the staleness join cost one open, not N file reads. |

**Rebuild posture: `vectors.db`'s, not `fts.db`'s.** `_purge_rebuild_indexes`
rebuilds `fts.db` and `graph.db` but deliberately leaves `vectors.db` deleted
for lazy re-embed. Findings are the same shape of thing — regenerating them
costs LLM calls (measured: 64 calls / 3m59s for three contradictions), so
`findings.db` is deleted and **never** rebuilt in-line by `purge`. It also
does **not** participate in `derived.MANIFEST_HASH_KEY` gating: a findings row
is not derivable by a rebuild, so a whole-store staleness gate would be a lie.
Staleness is per-row (Decision 2).

## Decision 2 — Staleness: per-input-object digests, not one set digest

**Chosen:** each finding stores an ordered list of `(input_ref, sha256)` rows;
the finding is stale iff any row's current digest differs.

| Why not one digest over the ordered set | |
|---|---|
| Cannot name what moved | The operator-facing line must say *which* concept changed. A set digest says only "something did". |
| Cannot express merged-body candidates | A merged-body candidate's inputs are not files: `entry.survivor_before` and `entry.absorbed_snapshot` are strings inside the ledger sidecar (`resolution/contradiction.py:464-472`). Per-input rows digest those strings uniformly with file bytes; one path-keyed digest cannot. |
| Costs the same | Both need every input read. Per-input adds rows, not walks. |

**Primitive:** `state.vectorstore.content_hash` (`vectorstore.py:226-232`) —
sha256 over raw bytes, no encoding normalization. This is the *technique*
`origin_key_for` shipped (`model/okf.py:154-177`): exact rather than
heuristic, immune to the mtime reset a `git checkout` causes.
`origin_key_for` itself is **not** reusable — it hashes a resolved *path
string*, which is neither content nor stable across machines.

Inputs digested per candidate kind:

| Kind | Input rows |
|---|---|
| typed-edge (`merged_absorbed_id is None`) | both concept files' bytes, plus the `relation_type` label string (it is in the prompt — `contradiction.py:139-144` — so re-labelling an edge must invalidate the verdict) |
| merged-body | `survivor_before` and `absorbed_snapshot` strings from the `MergeLedgerEntry` |

Per D4, staleness marks the **finding**, never the decision.

## Decision 3 — Decision identity keys on the proposal

```
decision_key = sha256("contradiction/v1\n" + pair_ids[0] + "\n" + pair_ids[1]
                      + "\n" + (merged_absorbed_id or ""))[:32]
```

Never a findings row id. `merged_absorbed_id` is mandatory in the key:
`contradiction.py:185-198` states it is the **SOLE** discriminator between a
typed-edge and a merged-body candidate and that `pair_ids` shape is *not* a
safe stand-in. 32 hex chars follows `_ORIGIN_KEY_HEX_CHARS`
(`okf.py:147-151`). RED test: recompute the whole findings store from scratch
and assert the declination still binds.

## Decision 4 — `bundle/.state/` layout for decisions

| Aspect | Decision | Rationale |
|---|---|---|
| Path | `bundle/.state/decisions/<pair_ids[0]>.decisions.okf` | One file per sorted-first concept id, mirroring ADR-0013's `<concept_id>.ledger.okf`; sits next to its concept in a tree view and stays human-inspectable (D4.3), unlike a hash-named file. |
| Suffix | `.decisions.okf`, **never** `.md` | Buys the same free structural exclusion from all six `rglob("*.md")` EXCLUDE walks with zero edits at those sites, and is already policed by the shipped `lint.check_state_dir_contains_no_markdown` (`lint.py:1274-1295`) without modification. |
| Container | `okf.dump_frontmatter({...}, body="")` | ADR-0002 invariant 3, preserved literally, exactly as `bundle/ledger.py:127-137`. |
| Id → path | `okf.concept_path_for(concept_id, decisions_root, suffix=...)` (`okf.py:1296`) | The `(root, suffix)` generalization ADR-0013 already shipped. Do not invent a second NFC/NFD mapping. |
| Record | `decision_key`, `pair_ids`, `merged_absorbed_id`, `state: declined\|open`, `decided_at` | Ids + verdict only. No rationale, no body text — that is what keeps this store non-confidential. |
| INCLUDE walk | one `bundle/decisions.py::iter_decisions` primitive | Structurally separate from the EXCLUDE walks, per ADR-0013 Decision 3. |

**Extension boundary:** this adds exactly one kind — *irreplaceable human
verdicts on machine proposals* — and deliberately keeps machine findings out.

## Decision 5 — The two inherited hazards

| Hazard | Call site | Fix | RED test |
|---|---|---|---|
| Scoped staging | `_autocommit(root, paths, message)` uses `git add -- <paths>`, never `-A` (`cli/main.py:929`); `MergeResult.ledger_sidecar_path` (`cli/main.py:6934-6948`) exists solely to feed that list | The decline/reopen path returns its written decision path and the command passes it to `_autocommit` | Run `--decline`, assert the `bundle/.state/decisions/**` path is in the committed set; mutate the caller to drop it and watch the test go red |
| `purge` path set | `expunge_targets` builds explicit `literal:` entries (`cli/main.py:4905-4945`); `vcs/git.py:515-551` rejects an empty list and a `..`/newline/`==>` path | Append each purge-set member's own decisions file to `expunge_targets`, mirroring `:4940-4945`; and reuse the two-branch sweep shape of `_sweep_ledger_sidecars_for_ids` (`cli/main.py:602-654`) — delete a member's own file, drop records naming a purged id from every other file | Purge a concept that is `pair_ids[1]` of a decision stored under a *live* `pair_ids[0]`; assert the record is gone from the live file and from git history |

`forget` shares the same sweep, exactly as it shares the ledger's.

## Decision 6 — `next` reads without weakening the honesty guard

New tier `_tier_open_contradictions`, appended **last** in `_TIERS`
(`next_action.py:599-613`), whose D1 order comment already places curate-class
work last. It fires only on findings that are **open** (no `declined`
decision) **and** not stale, and recommends `openkos contradictions`
(the shipped verb, `cli/main.py:10196`).

The guard at `next_action.py:616-621` — a `None` action means no tier fired,
never that the bundle is clean — is untouched, as are `_NO_ACTION_LINE`
(`:77-80`) and `_STATUS_POINTER` (`:71-75`). A persisted-findings tier makes
*more* findings rankable; it must not license the inverse inference for the
rest. RED test: a bundle whose only findings are stale or declined yields
`action is None`, and the rendered output still carries `_STATUS_POINTER` and
still does **not** assert that no contradictions exist.

**#565 answered, not assumed.** `status` already reports duplicate groups
(`cli/main.py:8839-8844`) and `next` already has `_tier_duplicate_groups`
(`next_action.py:528`). Both read `find_exact_title_groups`, so #565 is a
near-match-vs-exact-title recall question, **not** an absent signal and **not**
fixed by persistence. It needs its own fix; out of scope here.

## Decision 7 — No two-phase write

ADR-0013 needed a hash-bound intent marker because one merge spans two files
(survivor + sidecar) that must agree, and disagreement is silently
irreversible. This change has no such pair:

- A decision write is **one** `fsio.write_atomic` to **one** file. There is no
  second file it must agree with.
- No decision record stores a findings row id, and no findings row stores a
  decision pointer — the join is computed at read time from `decision_key`.
  So a torn findings write cannot orphan a decision, and vice versa.
- A torn findings write costs LLM calls on the next run, never correctness:
  findings are recomputable by definition.
- Corollary made explicit: `--decline` MUST succeed for a `decision_key`
  with no matching findings row (the row may have been purged). Declining
  never reads the findings store as a precondition.

## File changes

| File | Action | Description |
|---|---|---|
| `src/openkos/state/findings.py` | Create | `.openkos/findings.db` schema, `record_findings`, `open_findings`, per-row staleness evaluation. Uses `derived.open_derived_connection`. |
| `src/openkos/bundle/decisions.py` | Create | `decisions_root`, `decisions_path_for`, `read_decisions`, `write_decisions`, `iter_decisions`, `decision_key_for`. Leaf module; MUST NOT import `openkos.graph` (AGENTS.md:41). |
| `src/openkos/resolution/contradiction.py` | Modify | Supersede the "Ephemeral — never a persisted OKF type" clause (`:161-164`, `:201-214`); expose the input refs a verdict was computed from so the caller can digest them. MUST NOT import `openkos.bundle` (`:436-446` layering rule). |
| `src/openkos/cli/curate.py` | Modify | Contradictions stage persists each finding after printing (`:1272-1320`). No prompt, no new write to the bundle (D1). |
| `src/openkos/cli/main.py` | Modify | `contradictions` gains `--decline`/`--reopen`/`--declined`, each short-circuiting before the graph build and LLM client; `_autocommit` path list; `expunge_targets` + `forget`/`purge` decision sweep; `_purge_rebuild_indexes` deletes `findings.db` without rebuilding it. |
| `src/openkos/cli/next_action.py` | Modify | `_BundleSignals.open_contradictions` + `_tier_open_contradictions`, appended last. |
| `docs/adr/0014-durable-pending-work-stores.md` | Create | See below. |
| `openspec/specs/curate-command/spec.md` | Delta | Requirements at `:166` and `:177` (see below). |

`lint.py` needs **no** change: `check_state_dir_contains_no_markdown` is
suffix-agnostic and already covers the new subtree.

## Spec deltas (both narrow, both argued)

- **"Contradictions Stage Is Report-Only And Last"** (`spec.md:166-175`): the
  MUST NOT is not relaxed. The delta distinguishes a **write to the knowledge
  bundle** — which the requirement exists to forbid — from **recording what
  the stage already computed** into a derived cache. The stage still proposes
  nothing and still ends with no write hint.
- **"Resumability By Construction"** (`spec.md:177-188`): persisting findings
  is not persisting a queue. Every stage queue is still re-derived from bundle
  state on each run, and no run-scoped progress marker is written. The delta
  states that boundary rather than leaving it to inference.

## ADR-0014 — yes, this change needs one

`docs/adr/0014-durable-pending-work-stores.md`, status `Proposed`, citing
ADR-0013. It decides: (a) that irreplaceable human decisions belong in
`bundle/.state/`, extending ADR-0013 by **exactly one kind**, with machine
findings deliberately excluded and why; (b) that recomputable findings belong
in `.openkos/` under `vectors.db`'s delete-without-rebuild posture; (c) that
the read-time `decision_key` join, not a stored cross-pointer, is what makes a
two-phase write unnecessary here — the one place this design consciously does
*less* than ADR-0013.

## Testing strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `decision_key` stability | same proposal, different `merged_absorbed_id` ⇒ different key |
| Unit | per-input staleness | mutate one input, assert the row names *that* input |
| Unit | decisions path mapping incl. NFC/NFD | mirror `concept_path_for`'s own suite |
| Integration | declination survives full recompute | wipe `findings.db`, re-run, assert still hidden |
| Integration | `--decline` with no findings row | succeeds, writes the decision |
| Integration | `_autocommit` staging | assert the decision path in the committed set |
| Integration | `purge`/`forget` sweep both branches | own-file delete + foreign-file record drop, live tree and git history |
| Integration | `next` honesty guard | stale/declined-only bundle ⇒ `None` action, `_STATUS_POINTER` intact |
| Integration | stage writes nothing to `bundle/` | byte-compare the bundle before/after the Contradictions stage |

## Threat matrix

| Row | Verdict |
|---|---|
| Routing | N/A — no new routing surface. |
| Shell / subprocess | **Applicable** via `purge` only: new `literal:` entries must pass `vcs/git.py:515-551` validation (no newline, no `..`, no `==>`, non-empty). Concept ids are user-controlled, so the decision path must be validated before it reaches `expunge_targets`. RED test: a concept id containing `==>` is rejected, never silently written into the paths file. |
| VCS automation | **Applicable** — `_autocommit`'s scoped `git add`. See Decision 5. |
| Executable-file classification | N/A — no executable is produced or classified. |
| Process integration | N/A beyond the above. |

## Review Workload Forecast

| Slice | Contents | Est. changed lines (add+del, incl. tests) |
|---|---|---|
| **A** | `state/findings.py`, `bundle/decisions.py`, `decision_key`, staleness, curate stage persist, ADR-0014, spec deltas | ~450 |
| **B** | `contradictions --decline/--reopen/--declined`, `_autocommit` path, D3 listing view | ~350 |
| **C** | `purge`/`forget` sweeps + `_purge_rebuild_indexes`, `next` tier + honesty-guard tests | ~400 |

`400-line budget risk: High`
`Chained PRs recommended: Yes`
`Decision needed before apply: No` (delivery strategy is `auto-chain`)

Suggested split: A → B → C as a Feature Branch Chain, each child targeting the
previous branch. **Slice C must not be deferred past the same release** — until
it lands, a decision referencing a purged concept id survives a privacy purge.
Slice A's PR body must say so.

## Alternatives considered

| Rejected | Reason |
|---|---|
| One store for both natures | Findings and decisions have opposite requirements — recomputable/bulky/confidential vs irreplaceable/tiny/clean. One store forces the worse policy on one of them. |
| Frontmatter sidecars in `.openkos/` for findings | Creates a confidential-content file set with no existing INCLUDE-walk sweep — a new privacy surface. |
| Decisions in `.openkos/` too | Deleted by `purge`'s existing delete-and-rebuild; the loss this change exists to prevent, reintroduced. |
| One digest over the ordered input set | Cannot name what changed; cannot express merged-body snapshot inputs. |
| Keying decisions on a findings row id | The row id changes on recompute and every declination silently evaporates (proposal's Critical risk). |
| `pair_ids` shape as the merged-body discriminator | Explicitly warned against at `contradiction.py:185-198`. |
| A `[y/N]` prompt on the Contradictions stage | Repeals a deliberate shipped requirement (D1, already rejected by the maintainer). |
| A two-phase write with an intent marker | No cross-file invariant exists here; the marker would add a refusal state with nothing to protect. |
| Reusing `origin_key_for` for staleness | It hashes a resolved path string, not content; a `git checkout` changes content without changing the path. |

## Open questions

- [ ] Should `status` gain an open-contradictions line? Not required by the
      five properties; deliberately left out of this slice to avoid widening
      the `status`/`next` divergence #565 already represents.

---

## Maintainer decisions — 2026-08-12 (override the slice plan above)

### D6 — The privacy gap is closed by construction, not disclosed

The design's slice plan lands the `purge`/`forget` decision sweep in a later
slice than the one that first writes decisions to `bundle/.state/`, and
proposes disclosing the window in the first slice's PR body.

**Rejected.** The sweep MUST land in the SAME slice that first writes a
decision. No version of the tracker branch, and no version of `main`, may
exist in which a decision file is written but `purge` does not reach it.

Rationale: `purge` exists for real privacy needs, so a known window in which
it silently misses data is not a cost this change gets to defer. It is also
this repo's established standard — ADR-0013 flagged exactly this hazard for
the ledger rather than discovering it later, and #573 was filed rather than
left silent when an unreachable privacy scenario was found.

Consequence, stated plainly: the first slice is larger than the design
forecast. Accepted. The alternative of persisting findings only (deferring
every decision write until the sweep exists) was also rejected — it makes the
first slice non-vertical, which is the property that was chosen to de-risk
the whole design.

### D7 — Chain strategy: `feature-branch-chain`

PR #1 targets a tracker branch; each child PR targets the immediate previous
PR branch; ONLY the tracker merges to `main`. This is how #550 was delivered
(a 5-PR stack behind `tracker/durable-derived-state`), so the pattern and its
known traps are already exercised in this repo.

`delivery_strategy` is `auto-chain`; `review_budget_lines` is 2000.

Note the trap recorded from #550's delivery: re-validating a pre-PR gate
after folding commits binds the WHOLE branch, so a commit-then-fold sequence
can leave two half-branch receipts. Revalidate after every fold.
