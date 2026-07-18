# Proposal: `openkos lint` — Freshness lint v0 (read-only health check)

## Intent

`docs/cli.md` and `docs/roadmap.md` promise `openkos lint` as the MVP-1 bundle
health check — "flags stale stamps and orphan pages" — but nothing in `src/`
serves it. After `ingest`, a user has no mechanical way to ask "is anything in
my workspace drifting or disconnected?" `lint` is the second **read** command
(after `status`): a purely mechanical, read-only scan that reports two quality
signals and establishes the clock-injected reader precedent MVP-2 will extend.

## Scope

### In Scope

- `openkos lint` command in `cli/main.py` — read-only, `require_workspace` gate,
  computes `now` once and injects it (clock precedent from `bundle.create`).
- **Stale-stamp scan** — regex-scan concept bodies for inline `(as of YYYY-MM-DD)`
  stamps and flag any older than the configured `freshness_window` (default `7d`).
- **Orphan-page scan** — flag any concept not referenced by a markdown link from
  `index.md` or another concept (flat link scan; no graph).
- New `src/openkos/lint.py` module — own findings vocabulary (`LintReport` /
  `LintFinding`), reusing `okf._iter_docs` for one bundle walk; owns the
  `"7d"` → `timedelta` duration parsing.
- `config.py` / `read_config` — add `freshness_window: str` field with the
  existing `is not None` packaged-default fallback (raw passthrough, no parsing).
- Human-readable `typer.echo` output; exit 0 on any successful run.

### Non-goals (explicitly deferred)

| Deferred | Why |
|---|---|
| CI-gating / non-zero exit on findings / severity thresholds | `docs/cli.md` reserves exit-code gating as future work; findings are informational (non-zero ONLY when the workspace can't be read), mirroring `status` |
| Error vs. warning tiers | Flat warning-level only in MVP-1; tiering arrives with MVP-2 volatility |
| `--json` / structured output | Human-readable only, matching `init`/`ingest`/`status` |
| Volatility classification (reading `freshness`) | MVP-2; lint stays a pure text scan, never reads the `freshness` field |
| Conformance checking | `check_conformance` (OKF §9) stays a separate vocabulary — `docs/okf-alignment.md:55` warns against blurring the two |
| Any mutation of the bundle | Strictly read-only; the docs' "does not modify without confirmation" is satisfied by doing nothing |

## Honest MVP-1 limitation

MVP-1 `ingest` only ever emits `Source` concepts, which are `freshness: snapshot`
and carry **no** `(as of ...)` body stamp by design. **A bundle built purely from
`openkos ingest` therefore trips zero stale-stamp findings** — this is correct,
not a bug. The check is implemented generically (scans every non-reserved concept
body) so it works today on hand-authored bundles (e.g. `examples/good-life-demo/`)
and forward-compatibly once MVP-2 emits stamped `pointer` concepts.

## Capabilities

### New Capabilities
- `lint`: the read-only `openkos lint` command — mechanical stale-stamp scan
  (inline `(as of YYYY-MM-DD)` vs `freshness_window`) and orphan-page scan
  (unreferenced concepts via flat markdown-link scan), with its own
  `LintReport`/`LintFinding` vocabulary and the `freshness_window` config field.

### Modified Capabilities
- None. `config`/`read_config` gains one additive field; `okf._iter_docs` is
  reused read-only with no behavior change.

## Approach

- **One walk, own vocabulary.** Reuse `okf._iter_docs`/`DocScan` for the single
  bundle walk (its errors already degrade to data), but keep a fresh
  `LintReport`/`LintFinding` shape fully separate from `okf.BundleSurvey`/
  conformance — honoring the documented "distinct vocabularies" stance.
- **Two pure checks.** `check_stale_stamps(now, window, docs)` regex-scans bodies
  and compares dates; `check_orphan_pages(docs)` builds a referenced-set from ALL
  markdown links in every body (citation and Related links both count) and flags
  concepts absent from it.
- **Policy stays out of config.** `read_config` passes `freshness_window` through
  as a raw string; the `"7d"` → `timedelta` parser lives in `lint.py`.
- **Clock injected.** `cli/main.py::lint()` computes `now` once and passes it in;
  the lint module never calls `datetime.now()`/`date.today()`.
- **Phase-A shape only.** Follow the `try/except (OSError, ValueError)` →
  `echo(err=True)` + `Exit(1)` convention for the unreadable-workspace path.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/lint.py` | New | Pure `check_stale_stamps` / `check_orphan_pages`, duration parser, `LintReport`/`LintFinding` |
| `src/openkos/cli/main.py` | Modified | New `lint` command (gate, inject `now`, render sections) |
| `src/openkos/config.py` | Modified | Add `freshness_window: str` field + `DEFAULT_FRESHNESS_WINDOW = "7d"`, `is not None` fallback |
| `src/openkos/model/okf.py` | Reused | `_iter_docs`/`DocScan` reused read-only; no change |
| `tests/unit/**` | New | `test_lint.py` (pure) + `cli/test_lint.py` (CLI); extend `test_config.py` |
| `docs/cli.md` | Modified | Record MVP-1 `lint` behavior and non-goals |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Feature looks "broken" because dogfooded `ingest` bundles show zero stale findings | Med | State the `snapshot`/no-stamp limitation plainly; test the check against a stamped hand-authored fixture |
| Orphan false-positives on mixed `/`-rooted vs plain-relative link forms | Med | Resolve link-resolution rules explicitly at design; test both forms |
| Scope creep into conformance or severity/gating | Low | Non-goals drawn explicitly; keep vocabularies and exit contract separate |
| PR approaches the 400-line budget (new module + two test files) | Med | Keep checks minimal; single PR if under budget, else slice stamp-scan and orphan-scan |

## Open Questions (for design)

1. **Orphan link-resolution rules** — how strictly to normalize `/`-rooted
   (OpenKOS's own convention) vs plain-relative link forms (OKF tolerates both;
   Google's reference bundles mix them) when building the referenced-set.
2. **Malformed stamps** — `(as of yesterday)` / invalid dates: silently skip vs.
   surface as their own finding.
3. **Can Sources be orphans?** — a Source's only inbound link is typically its
   citation; decide whether `type: Source` concepts are exempt from orphan
   flagging or scanned like any other concept.
4. **Duration grammar** — support only documented `7d`/`14d` day-forms, or a
   broader grammar (hours/weeks); behavior on an unparseable `freshness_window`.
5. **Output layout** — section order, per-finding wording, and empty-state text
   (no stale stamps, no orphans).

## Rollback Plan

Purely additive plus one additive config field. `git revert` the change
commit(s): the `lint` command, `src/openkos/lint.py`, and the `freshness_window`
field disappear; `read_config` collapses to its three prior fields. No persisted
state, no migration, no published artifact to unwind.

## Success Criteria

- [ ] `openkos lint` in a workspace prints stale-stamp and orphan-page findings
      (or clean empty-state) and exits 0.
- [ ] Stale-stamp scan reads inline `(as of YYYY-MM-DD)` body text only — never
      the `freshness` field — against `freshness_window` (default `7d`).
- [ ] Orphan scan flags concepts unreferenced by any markdown link in `index.md`
      or another concept.
- [ ] Outside a workspace, `lint` refuses with `Exit(1)` via `require_workspace`.
- [ ] No mutation of any bundle file; no `--json`; no severity tiers; no gating.
- [ ] `lint`/`LintReport` vocabulary stays separate from `check_conformance`.
- [ ] `uv run pytest` green at 100% line+branch coverage; ruff/mypy clean.
