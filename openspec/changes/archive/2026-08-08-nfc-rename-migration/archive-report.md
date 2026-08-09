# Archive Report — `nfc-rename-migration`

**Archived**: 2026-08-08. **Delivered**: PR #492, squash commit `6992465` on
`main`. **Closes**: #474 (part 1, detection, shipped separately in PR #490 the
same day). **Follow-ups**: #491 (open, P3 — `next_action` integration for
`normalize-names`, blocked on a memoization story per design D8).

## What shipped

`openkos normalize-names`: the dedicated mutating verb that renames every
non-NFC on-disk name under `bundle_dir` to its NFC spelling, so the human
never runs git or shell commands for it (the maintainer's fixed product
decision). Standard Phase A plan → confirm ladder → Phase B → scoped
best-effort `_autocommit`, mirroring `backfill-sensitivity`. Underneath:
`fsio.rename_two_step` (platform-defensive two-step rename via a unique
`okos-nfc-tmp-` sibling, verified by byte-exact `os.listdir` — a real APFS
spike proved `Path.exists()` answers `True` for the NFC spelling with only
NFD on disk) and `lint.scan_non_nfc_entries` (raw-path-preserving scan;
`check_non_nfc_names` is now its byte-identical projection, so lint and the
verb can never disagree about what counts as an offending entry). Deepest
first, collisions/symlinks/vanished entries reported as skips, one bounded
`log.md` entry per run, idempotent, zero derived-index staleness (the
manifest hash keys on NFC ids + content bytes, both rename-invariant).

Delta specs merged: new `name-normalization` capability;
`lint` gains the "Non-NFC Names Scan" requirement (first formal capture of
the detection shipped in #490 — the base lint spec had never recorded it)
with the remediation now naming the verb instead of asserting openkos never
renames.

## Review

Gentle AI receipt-driven review, lineage `review-e7b46e36687fe04c` (high
risk, canonical 4R, 2640 frozen lines, correction budget 200). R3
(reliability) caught a deterministic CRITICAL (R3-001): Phase B recorded each
rename's new path at rename time, so a decomposed directory's descendant
staged a path that no longer existed once its ancestor was renamed later in
the same deepest-first batch. Fixed in the single bounded correction (~70
lines): post-batch segment-conditional `_final_rel` resolution plus a
RED-first regression test. R1 (risk) filed an inferential CRITICAL claiming
`rglob("*")` follows symlinked directories on supported Pythons (a
`bundle_dir` escape); an empirical spike on CPython 3.12.7/3.13.13/3.14.6
showed the walk never descends a symlinked directory on any of them, and the
refuter refuted it. Five informational findings (unguarded double-fault
restore, three docstring nits, a combined-idempotency test gap) are recorded
in PR #492. Approved with receipt; `sdd-verify` PASS (0 CRITICAL/WARNING,
1 SUGGESTION).

Two follow-up tests-only lineages, both approved with receipts, fixed what
Linux CI exposed after the darwin-gated tests ran for the first time:
`review-b0469f9b534e73c9` (track the NFD file before asserting the rename
commit — the untracked-old-path setup was the WARNING contract, exactly as
review finding R3-002 had predicted) and `review-71aa3ef40317622f` (read the
commit through `git -c core.quotePath=false show --name-status --no-renames`,
because rename detection coalesces the D/A pair into `R100` and `quotePath`
octal-escapes non-ASCII paths). Final CI: green on Python 3.12/3.13/3.14.

## Verification

`sdd-verify` mapped every requirement/scenario in both delta specs to a
pinning test or implementation evidence; 39/39 tasks audited; proposal
success criteria 9/10 PASS with the tenth (issue closure + follow-up filing)
completed at delivery. Final suite: 3997 passed + 1 platform-gated skip,
`mypy .` clean (179 files), ruff clean.
