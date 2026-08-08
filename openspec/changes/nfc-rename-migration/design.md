# Design: openkos renames NFD names to NFC itself (`normalize-names`, #474 part 2)

## Technical Approach

One names-only walk in `lint.py` becomes the single definition of "offending entry"
(`scan_non_nfc_entries`); `check_non_nfc_names` is rewritten as its projection so `lint`'s
output stays byte-identical apart from the intentional remediation wording. A new
`normalize-names` verb in `cli/main.py` consumes that scan, orders it deepest-first, applies
each rename through a new **canonical-layer** `fsio.rename_two_step` primitive that verifies
the resulting on-disk name by directory listing, appends one `log.md` entry, and issues one
scoped `_autocommit` staging old **and** new paths. Structural twin throughout:
`backfill-sensitivity` (`main.py:5227-5426`).

Canonical layer (`lint`, `fsio`, `bundle/log`) never imports `vcs`; the only git call is
`_autocommit` at the CLI layer after Phase B, best-effort and non-fatal.

## Architecture Decisions

### D1 — `scan_non_nfc_entries` shape, and the projection

```python
# src/openkos/lint.py
RENAME_TEMP_PREFIX = "okos-nfc-tmp-"          # ASCII => trivially NFC; see D3

@dataclass(frozen=True)
class NonNfcEntry:
    path: Path        # raw, byte-exact on-disk path (the thing LintFinding cannot carry)
    raw_name: str     # path.name as the filesystem returned it
    nfc_name: str     # unicodedata.normalize("NFC", raw_name)
    rel_posix: str    # NFC bundle-relative POSIX path == LintFinding.path
    depth: int        # len(path.relative_to(bundle_dir).parts) -- D4's sort key
    is_dir: bool      # lstat-derived; False on OSError (read-only-never-fail)
    is_symlink: bool

def scan_non_nfc_entries(bundle_dir: Path) -> list[NonNfcEntry]: ...
def scan_stranded_rename_temps(bundle_dir: Path) -> list[Path]: ...   # verb-only, D3
```

The walk body moves verbatim: `bundle_dir.rglob("*")`, pulled one entry at a time inside
`while True` with a per-`next()` `OSError` break, never fed through `sorted(...)`
(review R4-001). `is_dir`/`is_symlink` are stat'd **only for entries that already failed the
NFC test**, so a clean bundle pays exactly what it pays today. Results are sorted by
`rel_posix`; `check_non_nfc_names` maps them 1:1 in that order, so the finding list is
byte-identical and its own `findings.sort(key=...path)` disappears as redundant.

| Option | Tradeoff | Decision |
|---|---|---|
| Widen `LintFinding` / change `check_non_nfc_names`' signature | Shared render contract; ripples into every consumer and its pinned tests | Rejected (proposal D2) |
| Independent walk inside the verb | Two definitions of "offending entry" that silently drift | Rejected (proposal D2) |
| Sibling scan + thin projection | One walk, one definition, richer type where it is needed | **Chosen** |

**Regression guard — what the existing #490 tests pin** (must stay green, unedited except where noted):

| Test | Pins |
|---|---|
| `test_lint_non_nfc.py::…escaped_name_and_nfc_target` | `kind`, `path` = NFC rel POSIX, `́` and NFC target both in `detail` |
| `…directory_yields_one_finding_not_one_per_descendant` | one finding per decomposed **directory**, keyed on the entry's own name |
| `…fully_nfc_bundle_yields_no_findings` | empty list for a clean bundle |
| `…non_md_nfd_named_file_is_still_flagged` | non-`.md` entries are in scope |
| `…findings_come_out_in_sorted_walk_order` | output sorted by `path` |
| `…unreadable_garbage_doc_is_flagged` | names-only; never opens a file |
| `…broken_walk_degrades_to_findings_collected_so_far` | **monkeypatches `Path.rglob` and asserts `pattern == "*"`** — the scan MUST keep calling `rglob("*")` and MUST keep the incremental pull |
| `cli/test_lint.py::test_lint_flags_non_nfc_names` | rendered via `finding.path`, exit 0, `"is not NFC"` substring, snapshot unchanged |

The new remediation wording keeps `is not NFC`, the `́` escape and the NFC target, so **no
existing test needs editing**; a new assertion pins the verb name in `detail`.

### D2 — Rename primitive lives in `fsio`, not in the CLI

`fsio.rename_two_step(src: Path, nfc_name: str) -> Path` (module already imports `os` and
`uuid`). It renames `src -> src.parent/f"{RENAME_TEMP_PREFIX}{uuid4().hex}" -> src.parent/nfc_name`,
then **verifies by directory listing**: `nfc_name in os.listdir(src.parent)` compared
byte-exactly. `Path.exists()` is unusable as the check — **spike Q4 observed it returning `True`
for the NFC spelling with only the NFD name on disk**, which is exactly the silent success the
proposal's top risk describes. On verification failure it raises `OSError` after attempting to
restore the temp to `src.name`. On a step-2 failure it restores likewise; the run then aborts
loudly.

Why two-step, given that **spike Q2 observed a one-step `os.rename(NFD → NFC)` succeeding and
actually changing the bytes** on APFS + Python 3.13: the no-op hazard is uncorroborated on HFS+
and SMB, not disproven there; Q3 shows the second hop costs one extra `rename` syscall and
changes nothing about the result on APFS; and the listing verification — which Q4 proves is
mandatory regardless — makes a hypothetical no-op a loud failure rather than a silent one. The
primitive is therefore *platform-defensive*, not a workaround for an observed APFS defect.

CLI-layer placement was rejected: the primitive must be unit-testable without a Typer runner
and is pure filesystem, exactly like `write_atomic`.

### D3 — Temp-name scheme and stranded-temp detection

`okos-nfc-tmp-<uuid4().hex>` — ASCII (so trivially NFC and never self-flagged; **spike Q3
confirms APFS neither rejects nor re-normalizes it**), unique per rename (no collision even with
concurrent runs), prefixed so it is machine-detectable, and **not** dot-prefixed. The non-dot
choice is purely about human conspicuousness in a `ls` of a bundle that has a stranded temp:
**spike Q6 observed `pathlib.rglob("*")` returning `['.hidden', 'plain']`**, so a dotted name
would be detected by the scan just as reliably. Tooling visibility is not the reason; being
impossible to overlook is.

A temp can only be stranded by a hard kill between the two `os.rename` calls — every ordinary
failure path restores. **The next run reports it, and never touches it**:
`scan_stranded_rename_temps` (a second names-only walk, called by the verb only) yields each
`RENAME_TEMP_PREFIX` entry; Phase A prints a stderr WARNING naming the path and stating that the
original spelling is unrecoverable from the temp name, so the human renames it. Auto-deleting
would be data loss; auto-renaming would be a guess.

Rejected: encoding the target name into the temp (255-byte name limits, re-encoding
complexity); folding the temp scan into `scan_non_nfc_entries`' return tuple (`lint` would pay
for and have to unpack a signal it does not report).

### D4 — Ordering, plan representation, drift re-check

- **Apply order**: `sorted(entries, key=lambda e: (-e.depth, e.rel_posix))`. A child at depth
  *d* is renamed before any ancestor at depth < *d*, so every raw path is still valid when it is
  used; siblings are independent.
- **Preview** is printed in apply order (it is a plan, and the ordering is the safety property
  under review), raw side escaped through `ascii(...)` like the lint detail:

```
openkos normalize-names: proposed renames (2, deepest first), 2 skipped:
  ~ 'concepts/café/notas-café.md' -> 'notas-café.md'
  ~ 'concepts/café' -> 'café'  (directory -- its whole subtree moves with it)
  ! 'concepts/otro-café.md' -- skipped: 'otro-café.md' already exists
  ! 'link-café.md' -- skipped: symlink
  ~ log.md (new dated entry)
```

- **Drift re-check** (after the gate, before the first rename) is purpose-built, because nothing
  here rewrites file bytes: for each planned rename, (1) `raw_name` still present byte-exactly in
  `os.listdir(parent)`, (2) `is_dir`/`is_symlink` classification unchanged, (3) `nfc_name` still
  absent byte-exactly from that listing. Any failure demotes that entry to a **reported skip**,
  not a crash (proposal risk row). `log.md` alone goes through the existing
  `_reject_drifted_targets` (exit 3), matching every other verb. If re-check empties the plan,
  the run writes nothing, appends no log entry, and creates no commit.

### D5 — Skips (proposal D4) and the empty-plan path

Classified in Phase A: `symlink`; `collision` (NFC name already present byte-exactly in the
parent listing); and at re-check, `vanished` / `reclassified`. All non-fatal. An all-skip or
empty plan prints an explicit no-op line, exits 0, writes nothing, commits nothing — which is
also the idempotency property (second run plans nothing).

### D6 — Verb skeleton, mapped to `backfill-sensitivity`

| `backfill-sensitivity` | `normalize-names` |
|---|---|
| `main.py:5287-5308` Phase A snapshot + `resolve_backfill_raises` | `lint_check.scan_non_nfc_entries(layout.bundle_dir)` + `scan_stranded_rename_temps` WARNING, classify skips, sort deepest-first |
| `:5310-5316` empty → no-op line, exit 0 | identical shape (D5) |
| `:5318-5319` `confirm_enabled = not auto and cfg.review` | verbatim |
| `:5322-5335` build the `log.md` line pre-prompt | verbatim shape, wording below |
| `:5344-5351` preview | D4's preview |
| `:5353-5362` `--auto` > `cfg.review` > TTY `typer.confirm(abort=True)` > non-TTY refuse (exit 1) | verbatim (proposal D5) |
| `:5366-5378` `_reject_drifted_targets` | D4's re-check, plus `_reject_drifted_targets` for `log.md` only |
| `:5380-5407` Phase B writes + `landed` | `fsio.rename_two_step` per entry, `landed` appended after each returns; failure names every landed rename, no rollback |
| `:5418-5426` one `_autocommit` | D7 |

`log.md` line (single line — `insert_log_entry` rejects newlines; `ascii()` output contains
none):

```
**Normalize-names**: Renamed 2 on-disk name(s) to NFC: 'concepts/café/notas-café.md'
-> 'notas-café.md', 'concepts/café' -> 'café'. Skipped 2 (collision: 1, symlink: 1).
```

### D7 — Autocommit scope: old **and** new paths

`_autocommit(root, [<old rel>, <new rel>, …, "bundle/log.md"], "openkos: normalize-names")`.
`git add <pathspec>` stages removals as well as additions, so on a byte-exact filesystem the
vanished old path stages as a delete. A renamed **directory** needs no child enumeration: a
directory pathspec matches everything beneath it, so the old dir stages every descendant
deletion and the new dir every addition. **Spike Q8 confirms the call itself is safe**:
`git add -- <vanished-old-path> <new-path>` exited 0.

**The macOS case is a genuine no-change, not a bug.** With `core.precomposeunicode=true` — the
observed default (Q7) — git recorded `"caf\303\251.md"` (NFC) for a file *created* with NFD
bytes, so git never saw the decomposed spelling. After the rename, Q8 observed an empty
`git status --porcelain` and an empty `git diff --cached --name-status`. Nothing about the
rename is committable on macOS because, from git's point of view, nothing changed. The renamed
paths stay in the `_autocommit` scope regardless: they are load-bearing on Linux and on any
byte-exact volume, and harmless where git already agrees.

**Empty-commit tolerance.** `commit_paths` raises `GitError` if `git commit` exits non-zero,
which includes "nothing to commit". That path is **unreachable here**: every run that reaches
`_autocommit` has a non-empty plan, and every non-empty plan appends a `log.md` bullet via
`insert_log_entry`, so `bundle/log.md` is always a real staged change; an emptied plan never
reaches `_autocommit` (D4/D5). **No change to `commit_paths` or `_autocommit` is made** — both
are shared by thirteen callers. If a future variant ever commits without `log.md`, guard it with
the existing `vcs_git.paths_dirty` (`git.py:477`, purge's empty-diff guard), never by loosening
`commit_paths`.

`index.md` is deliberately absent (proposal D6 — no id, link, relation, or provenance changes;
confirmed against `okf.concept_id_for`'s NFC normalization). No reindex (proposal D7:
`bundle_manifest_hash` keys on `(concept_id, content_hash)`, both invariant).

### D8 — Wording corrections (#490 lines that are now false)

| Location | Change |
|---|---|
| `lint.py:1109-1112` | "DETECTION ONLY: openkos never renames -- that migration decision is deliberately out of scope…" → "`lint` itself never writes (spec: Read-Only and Human-Readable Only), but the rename is no longer the human's shell problem: `openkos normalize-names` (#474 part 2) performs it, consuming this function's own `scan_non_nfc_entries`, so detection and migration can never disagree about what an offending entry is." |
| `lint.py:1167-1171` (`detail`) | `f"on-disk name {entry.name!a} is not NFC -- run `openkos normalize-names` to rename it to {nfc_name!r} so the spelling on disk matches the canonical id (#430)"` |
| `main.py:8096-8097` | "DETECTION ONLY: openkos never renames -- the detail names the NFC target, but the migration is the human's call" → "`lint` stays read-only and never renames; the detail points at `openkos normalize-names`, the dedicated verb that does (#474 part 2)." |
| `tests/unit/test_lint_non_nfc.py:15-16` (module docstring) | same correction |

## S1 Spike: macOS/APFS rename + git semantics — **RUN; observed results below**

Script: `/private/tmp/claude-501/-Users-jasonssdev-Dev-Projects-openkos/d2a3c44a-7006-4adc-811e-bccf7b3ee1f2/scratchpad/nfc_rename_spike.sh`
(writes only under a `mktemp -d /private/tmp/…`, throwaway git repo with
`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`; touched no openkos file and no user
git config).

**Verbatim output** — macOS, APFS (`/dev/disk2s5` on `/System/Volumes/Data`), Python 3.13.13,
git 2.55.0:

```
Q1 created 'café.md' -> listdir: ["'cafe\\u0301.md'"]
Q2 one-step os.rename: returned normally
Q2 listdir after one-step: ["'caf\\xe9.md'"]
Q2 byte-exact NFC present: True
Q2 byte-exact NFD present: False
Q3 two-step: returned normally
Q3 listdir after two-step: ["'caf\\xe9.md'"]
Q3 byte-exact NFC present: True
Q4 (d2/NFC).exists() with only NFD on disk: True
Q4 listdir d2: ["'cafe\\u0301.md'"]
Q5 listdir d3: ["'caf\\xe9'"] children: ["'child.md'"]
Q6 rglob('*'): ['.hidden', 'plain']
Q7 core.precomposeunicode (repo default): true
Q7 git status --porcelain after creating an NFD name:
?? "caf\303\251.md"
Q7 git ls-files after commit:
"caf\303\251.md"
Q8 listdir: ["'caf\\xe9.md'"]
Q8 git status --porcelain after the rename: (EMPTY — no output)
Q8 git add -- <old> <new>  (old no longer exists on disk): exit=0
Q8 git diff --cached --name-status: (EMPTY — no D/A lines)
```

### What the observations change

| Q | Observed | Consequence for this design |
|---|---|---|
| Q1 | APFS stored the NFD bytes exactly as written (`café.md`) | APFS is normalization-**preserving**; the drift #474 describes is real on this machine |
| **Q2** | one-step `os.rename(NFD → NFC)` **returned normally and actually changed the on-disk bytes**. The feared same-file no-op **did not reproduce** | The proposal's inherited assumption is **empirically false on modern APFS + Python 3.13**. The two-step primitive **stays** (D2) — it is uncorroborated-but-plausible on HFS+ and SMB, Q3 shows it costs nothing, and it is correct everywhere — but its justification is now *defense against unverified platforms*, not an observed APFS defect. **The test wording changes**: see the discrepancy note below |
| Q3 | two-step reached a byte-exact NFC name | The chosen primitive works on APFS; the ASCII temp sibling is not rejected or re-normalized |
| **Q4** | `Path(NFC).exists()` is `True` with **only** NFD on disk | **Confirms D2's verification requirement.** `exists()` is empirically unusable as a rename check; byte-exact `os.listdir` comparison is REQUIRED, not defensive taste. The D2 footgun warning stands, now with evidence |
| Q5 | renaming a decomposed directory carried its child untouched | D4's one-entry-per-directory plan and "the whole subtree moves with it" preview wording are correct |
| Q6 | `pathlib.rglob("*")` yields dotfiles (`['.hidden', 'plain']`) | A dot-prefixed temp *would* be detectable. The temp name stays **non-dot** anyway (D3) — the choice is about human conspicuousness in a stranded-temp listing, not about tooling visibility. Q6 removes the tooling argument from the rationale |
| **Q7/Q8** | `core.precomposeunicode` defaults to `true`; git recorded `"caf\303\251.md"` (**NFC**) for a file created with NFD bytes. After the rename: **empty `git status`, `git add -- <vanished-old> <new>` exit 0, empty staged diff** | On macOS git **never saw** the NFD spelling, so the rename is a genuine no-change to git. This is correct, not a failure. See D7 below |

**Discrepancy with the proposal's success criteria** (recorded, not silently absorbed): the
proposal requires "*On macOS/APFS the rename actually changes the on-disk spelling, proven by a
test that fails against a one-step `os.rename`*". Per Q2 that test **cannot be written honestly
on this platform** — a one-step rename succeeds here. The honest pin is *byte-exact `os.listdir`
verification of the NFC result*, which proves the same user-facing property (the spelling really
changed) without asserting a platform behaviour that does not exist. The one-step-fails property
is still pinned, but at the **primitive** level against an injected normalization-insensitive
`os.rename` (see Testing Strategy), where it is a true statement about the primitive rather than
a false statement about APFS. The spec phase should amend that success criterion accordingly.

**Q8 → D7 tolerance, designed explicitly.** `commit_paths` (`vcs/git.py:460-474`) runs
`git add -- <paths>` then `git commit -m`, and raises `GitError` if **either** exits non-zero —
including `git commit`'s non-zero "nothing to commit". Q8 proves the `add` step is safe
(exit 0 for a vanished old path), so the only exposure is an empty commit. **It cannot occur
here**: every run that reaches `_autocommit` has a non-empty plan, and every non-empty plan
appends a `log.md` entry via `insert_log_entry`, which always adds a bullet — so `bundle/log.md`
is always a real staged change and the commit is never empty. An emptied plan never reaches
`_autocommit` at all (D4/D5). Therefore **no change to `commit_paths` or `_autocommit` is
required**, and none is made — those are shared by thirteen callers. Recorded for any future
variant that would commit *without* `log.md`: guard it with the existing
`vcs_git.paths_dirty` (`git.py:477`, purge's empty-diff guard), never by loosening
`commit_paths`. Pinned by a test asserting a macOS-shaped run exits 0 with **no** WARNING on
stderr even though the renames contribute nothing to the diff.

## Data Flow

```
bundle_dir ──rglob("*") names-only──→ scan_non_nfc_entries ──┬─→ check_non_nfc_names → LintFinding[] → lint (read-only)
                                                             │
                                                             └─→ normalize-names Phase A
                                                                   classify skips → sort (-depth, rel)
                                                                   → preview → --auto/review/TTY gate
                                                                   → drift re-check (listdir, byte-exact)
                                                                   → Phase B: fsio.rename_two_step per entry
                                                                          raw → okos-nfc-tmp-<hex> → NFC
                                                                          verify via os.listdir(parent)
                                                                   → insert_log_entry → log.md
                                                                   → _autocommit([old…, new…, log.md])   ← only git call
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/lint.py` | Modify | `NonNfcEntry`, `RENAME_TEMP_PREFIX`, `scan_non_nfc_entries`, `scan_stranded_rename_temps`; `check_non_nfc_names` becomes a projection; D8 wording |
| `src/openkos/fsio.py` | Modify | `rename_two_step` + listing verification + restore-on-failure |
| `src/openkos/cli/main.py` | Modify | `normalize-names` verb; D8 wording in `lint`'s docstring |
| `tests/unit/test_lint_non_nfc.py` | Modify | docstring correction + one assertion pinning the verb name in `detail` |
| `tests/unit/test_lint_scan_non_nfc_entries.py` | Create | scan-level tests (raw path, depth, flags, order, degraded walk) |
| `tests/unit/test_fsio_rename_two_step.py` | Create | two-step, injected-primitive one-step failure, verification, restore |
| `tests/unit/cli/test_normalize_names.py` | Create | verb tests |
| `openspec/changes/nfc-rename-migration/specs/…` | Create | `name-normalization` capability + `lint` remediation delta |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (lint) | raw `Path` preserved; `depth`; `is_dir`/`is_symlink`; sorted by `rel_posix`; degraded walk; projection byte-identical to today | tmp dirs, `unicodedata.normalize("NFD", …)`-built names (never pasted decomposed literals) |
| Unit (fsio) | two-step reaches a byte-exact NFC listing; **the primitive survives a normalization-insensitive `os.rename`, where a one-step rename would leave the old spelling**; verification failure raises; temp restored on failure; temp name matches `RENAME_TEMP_PREFIX` | monkeypatch `os.rename` in `fsio`'s namespace with a fake that no-ops when `NFC(src.name) == NFC(dst.name)`. Runs everywhere, including Linux CI. This is a statement about the **primitive under a simulated hostile filesystem**, explicitly NOT a claim about APFS — spike Q2 observed one-step succeeding on real APFS |
| Unit (fsio, macOS) | on a real APFS volume, `rename_two_step` leaves the parent listing containing the **byte-exact NFC name and not the NFD one** | `@pytest.mark.skipif(sys.platform != "darwin")`, asserting on `os.listdir` bytes exactly as S1's Q3 observed. **Deliberately does not assert that a one-step rename fails** — Q2 proves that claim false here (see S1's discrepancy note) |
| CLI | deepest-first order; directory carries subtree; collision skip; symlink skip; idempotent second run (nothing planned, nothing written, no commit); confirm-ladder precedence (`--auto` / `review: false` / TTY decline exit 1 / non-TTY refuse exit 1); drift demotes to skip; stranded-temp WARNING; one `log.md` entry; not-a-repo and no-identity WARNING paths, exit unchanged | `CliRunner` + tmp workspace, mirroring `tests/unit/cli/test_backfill_sensitivity.py` |
| CLI (git, byte-exact FS only) | the commit shows `D` for the old path and `A` for the new | `@pytest.mark.skipif(sys.platform == "darwin")` — per S1 Q7/Q8 macOS git records NFC from the start, so there is no D/A to assert |
| CLI (git, all platforms) | a run whose renames contribute nothing to the diff still exits 0, commits `log.md`, and prints **no** WARNING on stderr | the macOS-shaped case from S1 Q8, asserted unconditionally so Linux also pins it |
| Regression | all 7 `test_lint_non_nfc.py` tests + 2 `cli/test_lint.py` non-NFC tests green, unedited except D8's docstring/assertion | pinned |

STRICT TDD at apply time: every row above is RED before its implementation.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | **Applicable** — the verb renames arbitrary on-disk entries, not just `.md` | Scope is `bundle_dir` only; nothing outside it is walked or renamed; `raw/` under the bundle is renamed only for its own non-NFC *name*, never its bytes | `test_normalize_names_never_touches_paths_outside_bundle_dir` |
| Executable / special-file classification | **Applicable** — a symlink could redirect a rename outside the bundle | Symlinks are skipped, never followed (mirrors `concept_path_for`'s fail-closed admission, `okf.py:1209-1219`); `is_symlink` re-checked at drift time | `test_symlink_is_reported_as_skip_and_never_renamed` |
| Git repository selection | **Applicable** — a parent repo must never be hijacked | Reuses `_autocommit(root, …)` unchanged: `repo_root` + scoped `git add -- <paths>`, never `-A`/`-a` | `test_normalize_names_not_a_repo_warns_and_exits_zero` |
| Commit state | **Applicable** — an all-skip or empty plan must not produce an empty commit | Phase A/drift re-check exit before log + commit when the plan is empty | `test_second_run_plans_nothing_and_creates_no_commit` |
| Shell / subprocess | N/A — renames are `os.rename`; the only subprocess is the existing `commit_paths` | — | — |
| Push / PR commands | N/A — no push, no PR automation | — | — |
| History rewriting | N/A — `expunge_paths` stays `purge`-only | — | — |

## Migration / Rollout

No data migration and no feature flag: the verb is opt-in and idempotent. Ships as one PR
(scan refactor + primitive + verb + wording), since the refactor is meaningless without the
consumer and the wording correction is false until the verb exists. Rollback per proposal —
`git revert` the code (never corrupts a bundle) or the run's scoped commit (restores the
previous spellings exactly).

## Open Questions

- [ ] **For the spec phase (not blocking apply)**: amend the proposal's success criterion
      "*proven by a test that fails against a one-step `os.rename`*" to "*proven by byte-exact
      `os.listdir` verification of the NFC result*". S1 Q2 observed a one-step rename succeeding
      on APFS, so the original wording cannot be satisfied honestly on macOS. The one-step-fails
      property survives at the primitive level against an injected hostile `os.rename`.
- [ ] **For the spec phase**: the success criterion "*the autocommit is scoped to `log.md` + the
      renamed paths (old and new)*" is satisfied by the **scope passed**, not by the resulting
      diff — on macOS the renamed paths contribute nothing (S1 Q7/Q8). Word it as staging scope.
- [ ] Non-blocking: the follow-up `next_action` integration issue (proposal D8) is filed by this
      change, not designed here.
- [ ] Unverified by the spike, and deliberately left so: HFS+ and SMB rename semantics. The
      design is defensive there by construction (D2); no openkos test asserts anything about
      them.
