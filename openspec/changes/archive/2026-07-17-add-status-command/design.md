# Design: `openkos status` — read-only bundle overview

## Technical Approach

`status` is the first **read** command and is **Phase-A only** — a pure
read/validate with no Phase B, no confirm gate, no `--auto`. It composes three
existing seams, adding a reader to each rather than a new orchestration layer:
`config` gains the shared `require_workspace` gate (extracted from `ingest`'s
duplicated inline check), `model/okf.py` gains a bundle **survey** that reuses
`check_conformance`'s `rglob` walk to yield counts **and** §9 findings in one
pass, and `bundle/log.py` gains a pure `read_recent_entries` parser beside its
existing `insert_log_entry` writer. `cli/main.py::status` sequences the three
and renders human text via `typer.echo`, mirroring `ingest`'s inline-preview
style. Exit is `0` on any successful read; the **only** non-zero path is a
workspace that is absent/unreadable (via `require_workspace`). Verified against
HEAD: `check_conformance` walks `sorted(rglob("*.md"))` skipping
`RESERVED_FILENAMES` and lets read errors propagate (`okf.py:76-99`); `log.md`
is header + newest-first `## YYYY-MM-DD` sections with `* ` bullets
(`log.py:42-72`, `examples/good-life-demo/bundle/log.md`); `ingest`'s workspace
check is `not index_path.is_file() or not log_path.is_file()`
(`cli/main.py:217-223`).

## Module map

| Module | Gains | Stays true to |
|---|---|---|
| `config.py` | `require_workspace(root) -> str \| None` — refusal reason or `None`. Mirrors `refusal_reason`'s shape (`:157-159`); **no typer import** (layering). | Workspace-root owner; returns data, CLI maps to exit codes. |
| `model/okf.py` | `_iter_docs` (shared walk), `survey_bundle -> BundleSurvey` (counts + findings). `check_conformance` rewritten to **consume** `_iter_docs`, behavior byte-identical. | The format seam; type reasoning + `rglob` walk live here only. |
| `bundle/log.py` | `read_recent_entries(log_text, limit) -> list[LogEntry]` — pure parser, text-in, newest-first, no sort. | Pure renderer/parser, never touches the filesystem. |
| `cli/main.py` | `status` command (Phase-A only); `ingest` refactored to call `require_workspace`. | Only sequences modules and maps exit codes. |

**Layout decision (Q5, precedent-setting).** Readers live **beside their
writers**, not in a new `bundle/status.py` composer. `log.py` owns `log.md`'s
shape (writer **and** reader); `okf.py` owns type/frontmatter reasoning (the
survey). The **CLI command is the composer** — there is no composer module.
This is the precedent `query`/`lint` inherit: `query` adds an `index.md`/concept
reader beside `index.py`/`okf.py`; `lint` reuses `okf` conformance plus its own
checks. A `bundle/status.py` would pull each file's reading logic away from the
module that owns its format, breaking the "each module owns its file's shape"
layering `init`/`ingest` established. Minimal module churn, no scaffolding.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | **`require_workspace(root) -> str \| None`** in `config.py`: `None` when `bundle/index.md` **and** `bundle/log.md` are both `is_file()`, else the exact reason string `"no OpenKOS workspace found in this directory (run 'openkos init' first)"`. `ingest` calls it and formats `f"openkos ingest: refusing to ingest -- {reason}."` — **byte-identical** to today's message; `status` formats its own prefix. | Returning `bool` (loses the message; forces each caller to duplicate wording — the very drift we are removing); raising `typer.Exit` from `config` (imports typer into a layer below the CLI). | Mirrors the established `refusal_reason` pattern (`config.py:157-159`): config returns **data**, the CLI owns exit codes and prefixes. Shared reason text kills the third copy of the check now (proposal RISK-3). `is_file()` swallows `OSError`→`False`, so a permission-blocked `bundle/` reports "not a workspace" and still routes to Exit 1. |
| D2 | **Single-walk survey via a shared `_iter_docs` generator.** `_iter_docs(bundle_dir)` walks `sorted(rglob("*.md"))`, skips `RESERVED_FILENAMES`, and yields `DocScan(path, metadata, read_error, parse_error)` — catching `(OSError, UnicodeDecodeError)` as `read_error` and any `frontmatter.loads` failure / `handler is None` as `parse_error`. `check_conformance` consumes it and **re-raises** `read_error` (preserving its documented raise contract) and appends the same rule-1/rule-2 strings. `survey_bundle` consumes the **same** generator: `type == "Source"` → `sources`, any other non-empty type → `concepts`, and every read/parse/missing-type case → a **finding** (not counted). | Two independent walks (survey + conformance) — the proposal's "one pass" is lost, and the two `rglob` filters drift; leaving `check_conformance` untouched and duplicating its walk verbatim in `survey_bundle` (same drift, plus a second read of every file). | One walk, one filter, one source of truth for "what is a bundle doc". `check_conformance`'s **outputs and its raise-on-read-error contract are byte-identical** (regression-guarded by round-trip tests on real fixtures, proposal RISK-1). `survey_bundle`'s findings are a **superset** of §9 violations (adds per-file unreadable lines) — which resolves Q3 directly. |
| D3 | **Q3 — per-file unreadable/malformed concept = a finding, never a crash.** In `survey_bundle` a `read_error` becomes `"<path>: unreadable (<exc>)"`; a `parse_error` becomes the §9 rule-1 line; empty/missing `type` becomes the §9 rule-2 line. The scan continues; the file is **not** counted as a source or concept. | Silently skipping unreadable files (hides real corruption from the one command meant to surface it); aborting the whole scan on the first bad file (one broken concept blinds counts for the entire bundle). | Consistent with the "reuse `check_conformance`" contract: everything wrong shows up under **Needs attention**. `check_conformance` itself keeps propagating read errors (D2) — only the status survey degrades them, because status is the diagnostic tool. |
| D4 | **Q1 — Recent activity = the most-recent 5 log entries**, flattened newest-first across `## YYYY-MM-DD` sections, each tagged with its date. `RECENT_ACTIVITY_LIMIT = 5` lives in `cli/main.py` (display policy) and is passed to `read_recent_entries`; `log.py` stays policy-free. | "Today's section only" (unbounded on a heavy ingest day, and empty on any day after the last activity — a fresh bundle's init entry would vanish the next day); unbounded full dump (unbounded output). | Bounded, predictable output regardless of how active any single day was. The log is newest-first **by construction** (`insert_log_entry` prepends), so the reader walks sections top-down then bullets top-down and stops at 5 — **no sort**. A trailing `(… and N more)` line is shown when entries exceed the limit. |
| D5 | **Q2 — Recent activity degrades leniently; counts/conformance do not.** The `status` command wraps the `log.md` read + `read_recent_entries` in `except (OSError, ValueError)` → prints a `Recent activity unavailable — log.md could not be read/parsed.` notice and **continues at exit 0**. `read_recent_entries` raises `ValueError` on a malformed section chunk (matching `insert_log_entry`'s `raise ValueError`, `log.py:65`). The **workspace-unreadable** path (`require_workspace` refusal, or `survey_bundle`'s `rglob` failing at the directory level) is the only Exit-1 path. | Strict-fail (`Exit 1`) on a malformed `log.md` (a single bad log section would deny the user their counts **and** their §9 findings — the two things `status` exists to show); trusting `index.md` for counts (OKF §5.3 tolerates catalog drift; `status` is reached for *after* an interrupted `ingest`). | Counts + conformance come from a disk scan (not `index.md`), so they survive a broken log. Recent activity is the one nice-to-have, so it is the one thing allowed to degrade. Net: non-zero exit **only** when the workspace genuinely cannot be read. |

**ADR gate — zero created.** `openspec/config.yaml` requires
(1) a technology/pattern/interface trade-off **and** (2) hard-to-reverse. Every
decision is purely additive and `git revert`-able: `require_workspace` is a
refactor-extraction (collapses back inline), `_iter_docs`/`survey_bundle` add
functions (removing them restores the standalone `check_conformance`),
`read_recent_entries` is a pure addition, and the Phase-A shape reuses a
documented pattern. None clears condition (2). Precedent: `add-ingest-command`
and `add-init-command` created zero ADRs for comparable surfaces. **Zero ADRs.**

## Data Flow

```
openkos status              cli/main    config    okf       log       FS
  PHASE A (reads only, NO writes; no confirm, no --auto)
    ├── require_workspace(root) ──> config ─────────────────────────> │ index.md+log.md is_file()?
    │       └─ reason? → echo(err=True) + Exit(1)   [ONLY non-zero path]
    ├── survey_bundle(bundle_dir) ──────────> okf ──────────────────> │ rglob *.md  (single walk)
    │       └─ BundleSurvey(sources, concepts, findings)
    │          (per-file read/parse/no-type → finding; dir-level OSError → Exit 1)
    ├── read log.md text; read_recent_entries(text, 5) ──> log ─────> │
    │       └─ except (OSError, ValueError) → degrade notice (exit 0)
    └── render three sections via typer.echo → exit 0

check_conformance(bundle_dir) also consumes okf._iter_docs — one walk each,
outputs + raise-on-read-error UNCHANGED from HEAD.
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/config.py` | Modify | Add `require_workspace(root) -> str \| None` (mirrors `refusal_reason`) |
| `src/openkos/model/okf.py` | Modify | Add `_iter_docs`, `DocScan`, `BundleSurvey`, `survey_bundle`; rewrite `check_conformance` to consume `_iter_docs` (behavior byte-identical) |
| `src/openkos/bundle/log.py` | Modify | Add `LogEntry` + `read_recent_entries(log_text, limit)` (pure parser, newest-first) |
| `src/openkos/cli/main.py` | Modify | Add `status` (Phase-A only); refactor `ingest` to call `require_workspace` |
| `openspec/specs/status/spec.md` | New | `status` capability spec (produced by sdd-spec) |
| `tests/unit/**` | New/Modify | Survey counts/findings, conformance round-trip regression, log reader, `require_workspace`, CLI Phase-A + degrade + empty-state |
| `docs/cli.md` | Modify | Record MVP-1 `status` behavior and non-goals |

## Interfaces

```python
# config.py
def require_workspace(root: Path) -> str | None: ...   # None = ok, else refusal reason

# model/okf.py
@dataclass(frozen=True)
class DocScan:
    path: Path
    metadata: dict[str, object] | None
    read_error: OSError | UnicodeDecodeError | None
    parse_error: str | None

@dataclass(frozen=True)
class BundleSurvey:
    sources: int
    concepts: int
    findings: list[str]          # §9 violations + per-file unreadable lines

def survey_bundle(bundle_dir: Path) -> BundleSurvey: ...
def check_conformance(bundle_dir: Path) -> list[str]: ...   # now consumes _iter_docs

# bundle/log.py
@dataclass(frozen=True)
class LogEntry:
    date: str        # "YYYY-MM-DD"
    text: str        # bullet text, "* " prefix stripped

def read_recent_entries(log_text: str, limit: int) -> list[LogEntry]: ...
```

## Output layout (Q4)

```
openkos status: workspace at <root>

Bundle contents:
  Sources:  <N>
  Concepts: <N>

Recent activity:
  <YYYY-MM-DD>  <entry text>
  ...
  (… and <N> more)                         # only when entries > limit

Needs attention:
  <finding line>
  ...
```

Empty-state / wording:
- **Recent activity**, none parseable: `  No activity recorded yet.` A fresh
  bundle still shows its `Initialization` bullet, so this line is an edge case.
- **Recent activity**, degraded (Q2/D5): `  Recent activity unavailable — log.md could not be read/parsed.`
- **Needs attention**, empty findings (fresh/clean bundle): `  Nothing needs attention.`
- Counts on a fresh bundle: `Sources: 0`, `Concepts: 0` (both honest zeros).

## Testing Strategy (strict TDD, ≥90% branch, no network)

| Layer | What | How |
|---|---|---|
| Unit (pure) | `read_recent_entries`: newest-first across sections; stops at `limit`; multi-bullet same-day order; malformed chunk → `ValueError`; empty log body → `[]` | String fixtures |
| Unit (fs) | `survey_bundle`: `Source`→sources, other type→concepts, missing/empty type→finding, unparseable→finding, unreadable file→finding + not counted; fresh bundle → (0,0,[]) | `tmp_path` + `chmod`/bad bytes |
| Unit (fs) | **Regression**: `check_conformance` outputs byte-identical on real fixtures; still **raises** on an unreadable file | Round-trip vs HEAD behavior |
| Unit (fs) | `require_workspace`: `None` when both files present; reason string when either missing; ingest still emits its exact message | `tmp_path` |
| Unit (cli) | `status`: full render in a workspace (exit 0); not-a-workspace → Exit 1 + shared message; malformed/unreadable `log.md` → degrade notice + exit 0; empty-state wording; counts from disk not `index.md` | `CliRunner` + `monkeypatch.chdir` |

## Threat Matrix

**N/A** — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process integration. `status` performs **zero writes** and
**zero execution**. The only surface is `rglob("*.md")`, which
`check_conformance` already ships on HEAD with identical symlink-traversal
behavior — so this slice introduces **no new containment surface**. At worst a
symlinked directory could contribute an out-of-tree file to counts/findings
(informational only; nothing is opened for write or executed). No new RED
containment test beyond the survey's read-error handling is required.

## Migration / Rollout

No migration. Purely additive plus one internal refactor (`require_workspace`
extraction). `git revert` removes the `status` command, the log reader, and the
survey helper; `require_workspace` collapses back into `ingest`'s inline check.
No persisted state, no data migration.

## Review-workload footprint (feeds sdd-tasks forecast)

Estimated authored changed lines: **~140 non-test source** (`okf` survey/refactor
~50, `cli` status + ingest refactor ~55, `log` reader ~25, `config` helper ~10)
+ **~15 docs** + **~250 test** ≈ **~400 total**. This sits **at** the 400-line
budget. Single-PR-shaped and MVP-1-sized, but tasks should treat the
`400-line budget risk` as **Medium** and confirm the split; the `okf.py` refactor
is the one item whose regression risk (not size) warrants care.

## Open Questions

- [ ] None blocking. All five proposal open questions are resolved (D1–D5).
      Exact `typer.echo` string wording is finalized in the spec/tasks phase but
      the layout and empty-state text above are the contract.
