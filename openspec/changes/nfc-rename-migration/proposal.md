# Proposal: openkos renames NFD names to NFC itself (`normalize-names`)

**Issue**: [#474](https://github.com/jasonssdev/openkos/issues/474) part 2 (part 1,
detection, shipped in PR #490). **Baseline**: `main` @ `a904628`. **Mode**: openspec.

## Intent

Since #430, openkos reads through the NFD/NFC split tolerantly: `okf.concept_id_for`
normalizes ids to NFC and `okf.concept_path_for` resolves an NFC id against a decomposed
on-disk name. Since #490, `lint` *reports* every on-disk name under `bundle_dir` that is
not NFC. What no one can do is **fix it**.

Today the only remedy is for the human to leave openkos, open a shell, and hand-craft
`git mv` / `mv` calls over names whose difference is invisible in rendered text (the
whole reason `check_non_nfc_names` escapes the raw name through `ascii(...)`). That is
exactly the class of work the engine is supposed to maintain: mechanical, deterministic,
byte-level, and dangerous to do by hand — one mistyped combining sequence and the user
has two documents where they had one.

The maintainer's product decision is therefore fixed: **openkos performs the renames
itself, through an openkos verb. The human never runs git or shell commands for this.**

Why now: tolerance is a compatibility layer, not a resting state. Every bundle carrying
decomposed names pays for it forever — `concept_path_for` falls back to a directory scan
on every non-ASCII miss, `lint` keeps reporting findings the user cannot clear, and the
on-disk spelling stays inconsistent with the canonical id. Detection without remediation
also leaves `lint` in the worst possible state: it names a problem and then tells the
user it is their call.

Success looks like: a user with a macOS-authored bundle runs one openkos verb, sees a
plan, confirms, and afterwards `lint` reports zero `non-nfc-name` findings — with no
concept id changed, no link rewritten, no index rebuilt, and a clean git commit if the
workspace is a repo.

## Decision

Add a dedicated mutating verb, **`openkos normalize-names`**, following the standard
Phase A → confirm gate → Phase B → `_autocommit` shape of `backfill-sensitivity`.

### D1 — Verb name: `normalize-names`

`verb-noun`, matching `set-sensitivity`, `backfill-sensitivity`,
`backfill-source-titles`, `set-volatility`, `suggest-relations`. Single-word verbs in
this CLI (`merge`, `forget`, `reindex`, `purge`) are reserved for unambiguous domains;
`normalize` alone is not one (normalize what — text? levels? ids?).

- `normalize-names` wins: "normalize" is the exact domain word (`unicodedata.normalize`,
  "Unicode normalization form C"), and "names" is the vocabulary already shipped in the
  finding kind `non-nfc-name` and in `lint`'s `Non-NFC names:` section header. A user who
  read the lint output finds the verb by the same noun.
- Rejected `migrate-names`: "migrate" implies a one-shot data migration. This verb is
  idempotent, re-runnable hygiene — a fresh clone from a macOS collaborator reintroduces
  the condition.
- Rejected `rename-nfc`: puts an encoding acronym in the primary CLI surface, and reads
  as "rename *to* nfc" only if you already know what NFC is.
- Rejected `normalize-filenames`: the check flags directories too (one finding per
  decomposed directory covers its whole subtree); "filenames" would understate it.

### D2 — Phase A obtains raw on-disk paths via a sibling helper in `lint.py`

`check_non_nfc_names` computes a real `Path` for each offending entry and then discards
it: `LintFinding.path` is the **NFC-normalized** bundle-relative path, which by
construction cannot locate the raw entry on a byte-exact filesystem. A rename verb cannot
be built on `LintFinding` alone.

**Decision**: add a sibling in `lint.py`, e.g.
`scan_non_nfc_entries(bundle_dir) -> list[NonNfcEntry]`, where `NonNfcEntry` carries the
raw on-disk `Path`, the raw name, and the NFC target name. `check_non_nfc_names` is then
rewritten as a thin projection of that scan into `LintFinding`s, so there is exactly one
walk implementation and one definition of "offending entry".

Rejected alternatives:

- *Widen `check_non_nfc_names`' return type.* `LintFinding` is a shared render contract:
  the CLI renders this kind via `finding.path` (main.py:8094-8096) and other checks emit
  the same type. Changing its shape or the function's signature ripples into every
  consumer and its pinned tests for no gain — the migration verb wants a different,
  richer type, not a wider `LintFinding`.
- *An independent walk inside the new verb.* Two walks would drift: the day `lint`'s
  definition of "offending entry" changes, the migration silently stops matching what
  `lint` reports.

`lint`'s own read-only contract is untouched by this: `lint` keeps calling the projection
and keeps never writing. What must change is the **wording** that says openkos never
renames (lint.py:1109-1112, main.py:8096-8097, and the finding `detail`'s "rename it to
…" remediation), which becomes false the moment this verb exists; the remediation should
name the verb instead.

`lint --fix` is ruled out on contract grounds and is not reopened here: `lint`'s pinned
spec is "Read-Only and Human-Readable Only", non-gating, no `--auto`, no confirm gate.

### D3 — Rename mechanics: deepest-first, two-step via a temporary sibling

- **Deepest-first ordering (requirement).** Renaming a parent directory first would
  invalidate every already-computed descendant path. The plan MUST be applied in order of
  decreasing path depth, so children move before their ancestors.
- **Two-step rename via a temporary sibling name (requirement).** APFS/HFS+ (and SMB
  shares) are normalization-*insensitive*, so `os.rename(nfd_path, nfc_path)` between
  canonically equivalent names can be treated as a same-file no-op that leaves the on-disk
  spelling untouched — a silent failure where the verb reports success and `lint` still
  reports the finding. Phase B MUST therefore rename `raw → <unique temp sibling> → NFC
  target`, and MUST verify the final on-disk name is byte-equal to the NFC target,
  failing loudly (and leaving no temp name behind) if it is not.
- **macOS spike is a design/apply task, not an assumption.** The exact APFS/HFS+ behavior
  (and `core.precomposeunicode`'s effect on how git sees the old path) MUST be verified by
  a real spike on macOS during design. Amended per the spike (design.md S1, Q2): a
  one-step rename SUCCEEDED on real APFS, so the one-step-fails property is pinned at the
  primitive level against an injected normalization-insensitive `os.rename`, and the
  platform test pins byte-exact `os.listdir` verification of the NFC result instead.

### D4 — Collision policy: report and skip. Symlinks: skip.

- If the NFC-spelled sibling already exists (both spellings present on a byte-exact
  filesystem), the entry is reported in the plan as **skipped**, never overwritten and
  never merged. Two real files with canonically equivalent names is a human curation
  decision, not an engine one, and overwriting would be silent data loss.
- Symlinks are skipped, mirroring `concept_path_for`'s fail-closed stance of admitting
  only regular entries and never resolving through unexpected structure.
- Skips are non-fatal: the run proceeds with the remaining entries and reports what it did
  not do. A run with only skips writes nothing and creates no commit.

### D5 — Confirm gate: the standard ladder, not `purge`'s typed phrase

`--auto` skips the prompt → else config `review: false` skips it → else a TTY
`typer.confirm` asks and aborts (exit 1) on decline → else (non-TTY without `--auto`)
refuse to write and tell the user to re-run with `--auto`. A drift re-check re-validates
the Phase-A plan immediately before Phase B.

`purge`'s exact typed phrase is deliberately **not** adopted: that gate exists because
`purge` rewrites git history irreversibly. A rename is reversible, loses no bytes, and is
readable through either spelling while it is in progress. Like every mutating verb except
`purge`, this verb does **not** refuse on a dirty tree; it writes and then scoped-commits
only the paths it touched.

### D6 — Logging and autocommit: one log entry, and `index.md` is NOT edited

Concept ids do not change (`concept_id_for` NFC-normalizes on the way in), so **no
`index.md` link, no `relations:` target, no `provenance:` reference, and no frontmatter
field needs rewriting**. The only bundle *file* whose content changes is `log.md`: one
entry per migration run summarizing the batch (renamed count, skipped count), not one per
entry — mirroring `check_non_nfc_names`' one-finding-per-directory economy.

`_autocommit` then commits, scoped: `log.md` plus every renamed path. Because a rename is
a delete + add to git, the commit MUST stage **both the old and the new path** for each
rename, or the index keeps the old spelling. `index.md` is deliberately absent from the
commit scope; if design finds any content in it that does change, that finding overrides
this paragraph and must be recorded.

Autocommit stays best-effort and non-fatal: not a repo, or no git identity, or a
`GitError` → stderr WARNING, exit code unchanged, renames stay on disk. The canonical
layer is untouched — git integration happens only at the CLI orchestration layer after
Phase B, and a parent repo is never hijacked (`repo_root` + scoped `git add --`).

### D7 — No reindex chaining

`bundle_manifest_hash` (`state/derived.py:104`) keys on `(concept_id, content_hash)`
pairs. A rename changes neither: ids are already NFC, and file bytes are untouched. The
migration therefore introduces **zero** derived-index staleness — `stale_derived_stores`
and `next_action`'s `_tier_stale_derived_indexes` are not triggered — and the verb must
not chain a reindex.

### D8 — `next_action` integration is out of scope

`next`'s tiers are contractually zero-walk / memoized-signal only
(`cli/next_action.py:5`). Non-NFC detection is a live `rglob` walk with no memoization, so
wiring it into `next` as-is would violate that cost contract. Recommending this verb from
`next` requires a memoization story and ships as a **follow-up issue**, filed by this
change.

## Scope

### In scope

1. `scan_non_nfc_entries` (or equivalently named sibling) in `lint.py`, returning raw
   on-disk paths + NFC targets; `check_non_nfc_names` rewritten as its projection, with
   its existing `LintFinding` output byte-identical except for the remediation wording.
2. The `normalize-names` verb in `cli/main.py`: Phase A plan (renames, skips with
   reasons), preview, confirm ladder, drift re-check, Phase B deepest-first two-step
   renames with post-rename verification, one `log.md` entry, one scoped `_autocommit`.
3. Docstring/wording corrections where "openkos never renames" is now false
   (`lint.py:1109-1112`, `main.py:8096-8097`, finding `detail`), pointing the human at the
   verb.
4. Spec deltas: a new capability for the migration verb; a `lint` delta only for the
   changed remediation wording.
5. Tests: ordering, two-step rename, collision skip, symlink skip, idempotency (second run
   plans nothing and creates no commit), confirm-ladder precedence, autocommit staging of
   both old and new paths, non-repo/no-identity warning paths.

### Out of scope (non-goals)

- **`lint --fix`** — violates `lint`'s pinned read-only, non-gating spec.
- **`next_action` integration** — D8; follow-up issue.
- **Transliteration / ASCII-folding of names** — explicitly rejected in #414. This change
  never changes which characters a name contains, only their normalization form.
- **NFKC** — compatibility normalization is lossy and changes the logical name. NFC only.
- **Renaming anything outside `bundle_dir`** — no `raw/`-adjacent, workspace-level, or
  `.openkos/` path is ever renamed; `raw/` stays immutable.
- **Rewriting ids, links, frontmatter, or `index.md`** — nothing to rewrite (D6).
- **A reindex** — D7.
- **Git history rewriting** — `expunge_paths` stays `purge`-only.
- **Dirty-tree refusal** — the ordinary verb pattern applies (D5).

## Capabilities

### New capabilities

- `name-normalization`: the `normalize-names` verb — bundle-wide scan, plan with skips,
  confirm ladder, deepest-first two-step renames, verification, one log entry, one scoped
  autocommit, idempotency.

### Modified capabilities

- `lint`: the `non-nfc-name` finding's remediation names the verb instead of asserting
  that openkos never renames. Detection behavior itself unchanged; `lint` stays read-only,
  non-gating, exit 0.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| One-step `os.rename` silently no-ops on HFS+/SMB (not observed on APFS — spike Q2 saw it succeed), verb reports success while `lint` still reports the finding | Med | D3's platform-defensive two-step rename + post-rename byte-exact `os.listdir` verification; the no-op property is pinned at the primitive level against an injected normalization-insensitive rename |
| Interrupted run leaves a temp sibling name on disk | Med | Temp name is unique and namespaced; failure path removes it; a stranded temp entry is itself detectable and reported by the next run |
| Git does not record the rename because only the new path was staged | Med | Stage both old and new paths per rename; the delete+add pair is asserted only on byte-exact filesystems (with `core.precomposeunicode=true` git records NFC regardless, sees no diff, and the commit carries `log.md` — spike Q7/Q8) |
| `core.precomposeunicode` makes git see a different spelling than Python does on macOS | Med | Part of the same macOS spike; design must state the observed behavior, not assume it |
| The `check_non_nfc_names` refactor changes lint's output | Med | Existing #490 tests are the regression guard; the projection must be byte-identical apart from the intentional remediation wording |
| Rename count is not 1:1 with lint's finding count (a decomposed directory carries N children) | Low | The plan states, per directory entry, that the whole subtree moves with it; preview wording is a design decision |
| A concurrent editor moves or deletes an entry between Phase A and Phase B | Low | Drift re-check immediately before Phase B; a vanished entry becomes a reported skip, not a crash |
| Both spellings exist and the user expected a merge | Low (accepted) | Report-and-skip, never overwrite (D4); resolving it is human curation |

## Rollback plan

Renames are reversible and lossless, which is why no `purge`-tier gate is warranted.

- **Code rollback**: `git revert` the PR. Already-normalized bundles keep their NFC names
  — which is the canonical spelling anyway, and which every read path handled before this
  change existed. Reverting the code can never corrupt a bundle.
- **Data rollback**: if the workspace is a git repo, each run is one scoped `_autocommit`.
  On a byte-exact filesystem `git revert <commit>` restores the previous spellings
  exactly. On macOS with `core.precomposeunicode=true` git never saw the decomposed
  spelling (spike Q7/Q8), so the commit records only `log.md` and there is nothing to
  revert — which is also harmless: the NFC spelling is the canonical one, and recovery in
  every case is re-running the idempotent verb.
- **Partially applied run**: safe by construction. `concept_path_for` resolves an NFC id
  against either spelling, so a bundle that is half NFC and half NFD reads correctly.
  Recovery is simply **re-running the verb** — it is idempotent, and the second run plans
  only the entries that did not move.
- **No git repo**: the writes are on disk with a stderr WARNING; recovery is still a
  re-run, and nothing was lost because no bytes were rewritten.

## Success criteria

- [ ] `openkos normalize-names` renames every non-NFC on-disk entry under `bundle_dir`,
      children before ancestors, with no shell or git command run by the human.
- [ ] After a successful run on a decomposed bundle, `lint` reports zero `non-nfc-name`
      findings.
- [ ] No concept id, `relations:` target, `provenance:` reference, `index.md` link, or
      file body changes; only `log.md` gains one entry.
- [ ] A second run immediately after a successful one plans nothing, writes nothing, and
      creates no commit.
- [ ] On macOS/APFS the rename actually changes the on-disk spelling, proven by
      byte-exact `os.listdir` verification of the NFC result; the one-step-fails
      property is proven separately, at the primitive level, against an injected
      normalization-insensitive `os.rename` (the macOS spike observed a real
      one-step `os.rename` succeeding on APFS, so that claim cannot be pinned
      honestly as an observed platform behavior).
- [ ] A pre-existing NFC sibling and a symlink are each reported as a skip; neither is
      overwritten or followed.
- [ ] `--auto` skips only the prompt; non-TTY without `--auto` refuses to write; declining
      the TTY prompt exits 1 with nothing written.
- [ ] The autocommit STAGES `log.md` plus each rename's old and new path (staging scope,
      not resulting diff: on a platform where git saw no spelling change the commit still
      succeeds, recording `log.md` — spike Q7/Q8), warns non-fatally when the workspace
      is not a repo or has no git identity, and never commits unrelated dirty content.
- [ ] No reindex is triggered and no derived store is marked stale.
- [ ] Issue #474 closed on archive; a follow-up issue filed for `next` integration.
