# Proposal: `openkos status` — read-only bundle overview

## Intent

`docs/cli.md` promises `openkos status` as an MVP-1 command — "shows what the
bundle contains (counts of sources and concepts), recent activity, and anything
needing attention" — but nothing in `src/` serves it. After `init`/`ingest`, a
user has no way to ask "what is in my workspace, and is anything wrong?" without
opening `index.md`/`log.md` by hand. `status` is the first **read** command:
a Phase-A-only (pure read/validate, no writes) slice that also establishes the
bundle-reader precedent `query`/`lint` will follow.

## Scope

### In Scope

- `openkos status` command in `cli/main.py` — read-only, no confirm, no `--auto`.
- **Counts** from a disk scan of `bundle/**/*.md`: `sources` = files with
  `type: Source`; `concepts` = all other non-reserved typed files (0 in MVP-1
  bundles until MVP-2 extraction — still reported).
- **Recent activity** from a new `log.md` reader (newest-first by construction).
- **Needs attention** = OKF §9 conformance findings by reusing
  `okf.check_conformance` (unparseable frontmatter, missing/empty `type`).
- Extract a shared `require_workspace(root)` helper from `ingest`'s duplicated
  inline workspace check into `config.py`.
- Human-readable `typer.echo` output; exit 0 on a successful read.

### Non-goals (explicitly deferred)

| Deferred | Why |
|---|---|
| Lint checks — stale-stamp, orphan-page detection | Belong to the future `lint` command; `status` reuses `check_conformance` only |
| `--json` / structured output | Human-readable only in MVP-1, matching `init`/`ingest` |
| Non-zero exit on findings / CI-gate behavior | Findings are informational; non-zero exit ONLY when the workspace cannot be read |
| Any mutation of the bundle, catalog, or log | `status` is strictly read-only |

## Capabilities

### New Capabilities
- `status`: the read-only `openkos status` command — disk-scan counts
  (sources vs. concepts by OKF `type`), recent activity from `log.md`, and
  `check_conformance`-based "needs attention", plus the shared
  `require_workspace` check. Reader precedent for `query`/`lint`.

### Modified Capabilities
- None. `ingestion`/`workspace-init` behavior is unchanged; `require_workspace`
  is a refactor-extraction of `ingest`'s existing inline check (same condition).

## Approach

- **One pass, honest about disk.** Scan `bundle/**/*.md` (excluding
  `RESERVED_FILENAMES`) reusing `check_conformance`'s `rglob` walk so a single
  walk yields both the type tally and the §9 violation list. Do NOT trust
  `index.md` as sole source of truth — OKF §5.3 tolerates catalog drift, and
  `status` is the tool a user reaches for after an interrupted `ingest`.
- **`log.md` reader** — new read function beside the existing writer
  `insert_log_entry`; newest-first already, no sort.
- **`require_workspace`** — extract `ingest`'s check into `config.py`; both
  callers share it.
- Phase-A shape only; follow the `try/except (OSError, ValueError)` →
  `echo(err=True)` + `Exit(1)` convention for the unreadable-workspace path.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `status` command (Phase-A only) |
| `src/openkos/config.py` | Modified | Extract shared `require_workspace(root)` |
| `src/openkos/bundle/log.py` | Modified | New `log.md` reader (recent activity) |
| `src/openkos/model/okf.py` | Modified | Share/refactor `check_conformance` walk to also yield type counts |
| `openspec/specs/status/spec.md` | New | `status` capability spec |
| `tests/unit/**` | New | Reader, count/scan, conformance surfacing, CLI paths |
| `docs/cli.md` | Modified | Record MVP-1 `status` behavior and non-goals |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Refactoring `check_conformance` to also return counts regresses its shipped behavior | Med | Keep existing signature/behavior via shared-walk extraction; round-trip tests on real bundle fixtures |
| Scope creep from "needs attention" into `lint` territory | Low | Non-goal drawn explicitly; reuse §9 rules 1–2 only |
| Adding a third copy of the workspace check compounds drift | Low | Extract `require_workspace` now, not later |

## Open Questions (for design)

1. **Recent-activity count** — how many recent `log.md` entries/date-sections to
   show (fixed N, e.g. 5, vs. today's section only).
2. **Malformed `index.md`/`log.md`** — strict-fail (`Exit(1)`, matching `ingest`)
   vs. lenient-degrade (show a "could not read" line, keep counts). Disk-scan
   counts sidestep `index.md`; the log reader still needs this answered.
3. **Individual unreadable/malformed concept file** — silently skip vs. surface
   as its own "needs attention" line (note: `check_conformance` already flags it).
4. **Exact output layout** — section order, wording, empty-state text (0 sources,
   0 concepts, no activity, no findings).
5. **Reader module home** — `bundle/log.py` sibling vs. a new `bundle/status.py`;
   sets the precedent `query`/`lint` inherit.

## Rollback Plan

Purely additive plus one internal refactor. `git revert` the change commit(s):
the `status` command, the `log.md` reader, and the shared-walk count helper
disappear; `require_workspace` collapses back into `ingest`'s inline check.
No persisted state, no migration, no published artifact to unwind.

## Success Criteria

- [ ] `openkos status` in a workspace prints source/concept counts, recent
      activity, and any §9 conformance findings; exits 0.
- [ ] Counts come from a disk scan of `bundle/**/*.md`, not `index.md` alone.
- [ ] Outside a workspace, `status` refuses with `Exit(1)` and the shared
      `require_workspace` message; `ingest` uses the same helper.
- [ ] No mutation of any bundle file; no `--json`; no lint checks.
- [ ] `status` spec added; `uv run pytest --cov` ≥ 90% branch; ruff/mypy green.
