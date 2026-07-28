# Proposal: `set-sensitivity <concept-id> <level>`

Issue [#185](https://github.com/jasonssdev/openkos/issues/185) (P1), **problem 1 only**. Prior reading: `explore.md` in this folder.

## Intent

`sensitivity` (`public` / `private` / `confidential`) is written today by exactly two paths: `ingest` stamps `cfg.default_sensitivity` verbatim, and `merge` recomputes the survivor's value via `okf.combine_sensitivity`. **No verb lets a human set one existing concept's `sensitivity`.** A concept mis-stamped by the workspace default can only be corrected by hand-editing frontmatter — unvalidated, unlogged, uncommitted.

After this change, `openkos set-sensitivity <concept-id> <level>` sets that one field on that one concept, with validation, a preview, the standard confirm gate, a `log.md` entry, and an auto-commit.

## Scope narrowing versus #185 as filed

#185 asserts that sensitivity is inherited by derived objects via a high-water-mark and that manual edits leave derived objects stale. **That premise is false, and was verified false.** At `cli/main.py:1660` and `:1674`, ingest stamps the Source concept and each derived object with `cfg.default_sensitivity` independently — siblings fed one constant, not parent and child. No source-to-derived sensitivity edge exists, so there is nothing to re-propagate.

The propagation gap is real but different, and is tracked as **#219**. What ships here is the verb alone, making no propagation claim.

## Approach

Mirror `relate`'s Phase A / Phase B shape (`cli/main.py:2851-3040`), the structural sibling — both mutate one concept file's frontmatter.

| Step | Mechanism |
|---|---|
| Workspace gate | `config.require_workspace` → `config.read_config` |
| Validation first | Exact-match `<level>` against `okf.SENSITIVITY_ORDER`, before any read |
| Resolution | `_resolve_concept_path` (`:1853`), reused unchanged |
| Read-modify-write | `okf.load_frontmatter` → set `metadata["sensitivity"]` → `okf.dump_frontmatter` |
| Idempotence | `set-volatility`'s stricter short-circuit: current value equals `<level>` → message, no write, no commit, exit 0 |
| Log | `bundle_log.insert_log_entry`; no `index.md` touch (edits an existing catalog entry) |
| Confirm gate | Standard precedence: `--auto` skips; config `review: false` skips; TTY prompts; non-TTY without `--auto` refuses (exit 1) |
| Write / commit | `fsio.write_atomic`, then `_autocommit(root, [...], "openkos: set-sensitivity <id> -> <level>")` |
| Errors | Catch `(OSError, ValueError)`, `openkos set-sensitivity: refusing to set -- {exc}.`, exit 1. Never a raw traceback |

`okf.combine_sensitivity` is **not** used: it folds two values into a max; this assigns one validated literal.

**Why `set-volatility` is not the template despite the name symmetry.** `set_volatility_cmd` (`:3044`) takes a PascalCase concept *type* and edits `type_tiers[...]` in `openkos.yaml` by comment-safe text surgery. It touches no concept file. Copying it would write to the wrong storage entirely and conflate the per-object field with `cfg.default_sensitivity`. Only its idempotence rule transfers.

**No workspace floor.** `cfg.default_sensitivity` is a stamp at ingest and an LLM gate for `extract`, never a per-object minimum. `set-sensitivity` must not consult it as a floor; introducing one would be new scope.

## Decision — lowering is permitted, and needs a flag only where no human answers the prompt

**Settled.** Raising and same-value assignment pass the standard gate. Lowering passes the standard gate **when the confirm prompt actually runs and is accepted**. On every path where the prompt does not run — `--auto`, or config `review: false` — lowering MUST additionally require an explicit `--allow-downgrade`; without it the verb refuses in Phase A with exit 1, no write, no commit, and a message naming the flag.

**Why not "nothing extra".** ADR-0003 rejected survivor-wins because it "can silently downgrade a confidential absorbed object into a public survivor", and states that "a security field must fail toward more restrictive, never less." Under `--auto` — or under `review: false`, which is broader still, since it silences the prompt for *every* verb workspace-wide — a script can downgrade an access-control field with no human in the loop at the moment it happens. That is structurally the `merge` case ADR-0003 refused, not the reviewed case AGENTS.md permits.

**Why not "refuse outright" or "a distinct confirmation phrase".** Refusing outright destroys the verb's purpose: correcting a wrong default is a legitimate downgrade, and #185 asks for exactly that. A typed confirmation phrase adds ceremony where a human is already reading a preview line that says `confidential -> public` and typing `y`, and it cannot cover the unattended path at all — it solves the case that is not the problem.

**Scoping ADR-0003 rather than contradicting it.** "Never less" governs the *automatic combine* of two derived values, and its fail-closed ranking governs *dirty input*. Neither governs a human's explicit, argv-stated assignment. AGENTS.md's "Human curates, engine maintains. Consequential changes stay reviewable, not silently automatic" is satisfied by review — so the friction belongs precisely, and only, where review is absent.

**Precedent for a mode-dependent refusal.** The confirm gate already refuses on non-TTY without `--auto` and tells the user how to proceed. `--allow-downgrade` follows the same shape, so it is not a novel UX.

**Dirty current values fail closed.** A missing, blank, or unrecognized current `sensitivity` ranks per `okf._rank` (ADR-0003), so any assignment below `confidential` from such a value counts as a lowering. The verb must not become a laundering path for malformed frontmatter. Design must decide whether the CLI reuses module-private `okf._rank` or `model/okf.py` exposes a public rank; the seam rule (`AGENTS.md`) says the answer lives in `okf.py`.

**What the spec must then require:** direction classification against `okf.SENSITIVITY_ORDER` with fail-closed ranking of the current value; the preview naming the direction; `--allow-downgrade` required on the unprompted paths only; refusal leaving the bundle byte-identical.

## Capabilities

### New Capabilities

- `sensitivity-config`: `openspec/specs/sensitivity-config/spec.md` — the write layer for one concept's `sensitivity`. Modeled on `volatility-config`, per that same read/write split (`volatility-suggestion` reads, `volatility-config` writes). It must **not** fold into `sensitivity-aware-llm`, whose Non-Goals explicitly exclude "any change to how `sensitivity` is written."

### Modified Capabilities

- `workspace-autocommit`: delta adding `set-sensitivity` to the mutating-verb enumeration and the commit-message table.

**Call on the pre-existing omission: fix it in the same delta.** `set-volatility` calls `_autocommit` (`:3177-3181`) yet is absent from the enumeration, so the requirement is already factually false about shipped behavior. More decisively, the fix is unavoidable regardless: the requirement's universal "plus `bundle/index.md` and `bundle/log.md`" clause is wrong for `set-sensitivity` (log only, no index) *and* for `set-volatility` (`openkos.yaml` only). Rewording that clause to "the verb's own Phase-B-written paths, including `index.md`/`log.md` where the verb writes them" is required for this change; folding `set-volatility` into the same reworded sentence costs one word and one table row. **Bounded to enumeration + paths clause + message table + one scenario** — no wider reconciliation of that spec.

**Cross-reference, do not reimplement.** The same spec's **"One-Time Confidential Transparency Notice"** already fires when `_autocommit` stages any concept file whose `sensitivity` is `confidential`. Setting a concept to `confidential` is exactly that scenario, so the notice is inherited for free. The delta must say so explicitly; the risk is a new mutating verb re-implementing a notice it already gets.

## Honesty requirement

Three surfaces could invite an assumption of propagation, and all three must state that this sets exactly the one named concept and touches no sibling or derived object:

1. the verb's `--help` text,
2. the success message after a write,
3. a new `### openkos set-sensitivity <id> <level>` section in `docs/cli.md`, modeled on the `set-volatility` entry.

`docs/cli.md` has no dedicated sensitivity section today, and `merge`'s high-water-mark language sits nearby in the same document — the symmetry is exactly what would mislead.

## Out of Scope (non-goals)

- **Source-to-derived propagation (#219).** Not designed, not scoped, not partially implemented.
- Any use of `okf.combine_sensitivity` in this verb.
- Any per-object floor derived from `cfg.default_sensitivity`.
- Bulk / glob / recursive application; `--dry-run`; a read-only `get-sensitivity`.
- Changes to `src/openkos/sensitivity.py`, the six `llm.chat` gates, or `sensitivity-aware-llm/spec.md`.
- A generic set-frontmatter-field primitive (two call sites, different shapes; premature).
- Wider reconciliation of `workspace-autocommit/spec.md` beyond the four bounded edits above.
- Reopening `okf.SENSITIVITY_ORDER` or ADR-0003's combine rule.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `set_sensitivity_cmd`, placed near `relate` / `set_volatility_cmd` |
| `src/openkos/model/okf.py` | Modified? | Only if design promotes `_rank` to a public helper; `SENSITIVITY_ORDER` reused as-is |
| `openspec/specs/sensitivity-config/spec.md` | New | New capability spec |
| `openspec/specs/workspace-autocommit/spec.md` | Modified | Delta: enumeration, paths clause, message table, one scenario |
| `docs/adr/0008-human-sensitivity-override.md` | New | See ADR gate |
| `docs/adr/README.md` | Modified | Index row |
| `docs/cli.md` | Modified | New `set-sensitivity` section |
| `tests/unit/cli/test_set_sensitivity.py` | New | Fixtures from `test_relate.py`: `_init_workspace`, `_ingest_source`, `_simulate_tty`, `_snapshot`; add a `_sensitivity_of` analog |
| `tests/unit/cli/test_main_autocommit.py` | Modified | Shared autocommit-contract case |

**Rough size**: ~90 production lines (verb + help text), ~250 test lines, ~120 spec/doc/ADR lines. Total near the 400-line review budget; `sdd-tasks` should forecast a split with the spec/doc/ADR text as a separate slice if the code slice grows.

## ADR gate

**Yes — ADR required**, `docs/adr/0008-human-sensitivity-override.md` (0001–0007 are taken). Status `Proposed` at design, flipped to `Accepted` only at archive; index row added to `docs/adr/README.md`.

The project gate is *significant AND hard to reverse*, and this clears both — unlike the prior cycle's straightforward "no ADR", which added one read-only output line.

- **Significant**: it settles a standing policy question about an access-control field, and it *scopes an accepted ADR*. A future reader of ADR-0003's "a security field must fail toward more restrictive, never less" will otherwise read `set-sensitivity` as a violation. ADRs are append-only and project-wide, and the reasoning that "never less" governs automatic combine rather than human assignment belongs to no single future change — which is exactly AGENTS.md's test for an ADR over a spec.
- **Hard to reverse**: `--allow-downgrade` becomes a load-bearing part of the unattended contract. Removing it later breaks every script that passes it; loosening it silently re-opens the unattended-downgrade path that this change closed deliberately.

`sdd-design` may override with reasoning, but the burden is on the override.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Apply copies `set-volatility` and writes `openkos.yaml` | Med | Proposal and spec both name `relate` as the template and say why |
| Help text or success message implies propagation | Med | Honesty requirement is a spec requirement, not a doc nicety; three named surfaces |
| Confidential notice gets reimplemented in the verb | Med | Delta cross-references the existing `workspace-autocommit` requirement explicitly |
| `--allow-downgrade` scope creeps to the interactive path | Low | Spec states the flag is required only where the prompt does not run |
| `okf._rank` imported privately from the CLI, breaking the OKF seam | Med | Design decides: promote to public or add a public wrapper in `okf.py` |
| Readers conflate this with #219 | Med | Scope-narrowing section states the corrected premise with file:line evidence |
| Autocommit delta drifts into full spec reconciliation | Low | Four bounded edits enumerated |

## Rollback Plan

Revert the `main.py` command and its tests; delete `tests/unit/cli/test_set_sensitivity.py`, the new spec file, and the `docs/cli.md` section. The `workspace-autocommit` delta documents already-shipped behavior for `set-volatility`, so it may stay. No persisted state, no migration, no config key; concept files already written keep their values, which remain valid `SENSITIVITY_ORDER` members. Per AGENTS.md, an accepted ADR is never edited — a reversal is recorded by a superseding ADR.

## Dependencies

None. `_resolve_concept_path`, `okf.SENSITIVITY_ORDER`, `bundle_log.insert_log_entry`, `fsio.write_atomic`, and `_autocommit` all ship on `main`. Independent of #219.

## Success Criteria

- [ ] `openkos set-sensitivity <id> <level>` sets that concept's frontmatter `sensitivity` and no other file's.
- [ ] An invalid `<level>` is refused before any read, exit non-zero, bundle byte-identical.
- [ ] An unresolvable, absolute, `..`-bearing, or reserved `<concept-id>` is refused the same way.
- [ ] Setting the current value is a no-op: message, no write, no commit, exit 0.
- [ ] A raise succeeds under the standard gate on every path.
- [ ] A lowering succeeds when the interactive prompt is accepted, with no extra flag.
- [ ] A lowering under `--auto`, or under config `review: false`, is refused without `--allow-downgrade` — exit non-zero, no write, no commit — and the message names the flag.
- [ ] A lowering from a missing, blank, or malformed current value is classified as a lowering (fail-closed).
- [ ] A declined or refused run leaves the concept file and `log.md` byte-identical.
- [ ] A successful write appends a dated `log.md` entry and produces exactly one commit, `openkos: set-sensitivity <id> -> <level>`, containing the concept file and `log.md` only.
- [ ] Setting a concept to `confidential` emits the existing one-time transparency NOTICE, with no new notice code in the verb.
- [ ] `--help`, the success message, and `docs/cli.md` each state that only the one named concept is touched.
