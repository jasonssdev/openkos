# Design: `openkos lint` — freshness + orphan health check (read-only)

## Technical Approach

`lint` is the second **read** command and Phase-A only, following `status`'s
precedent: a pure read/validate, no Phase B, no confirm, exit `0`
on any successful run, exit `1` **only** when the workspace is absent/unreadable
(via `config.require_workspace`). It adds a new `src/openkos/lint.py` module with
its **own** `LintReport`/`LintFinding`/`LintDoc` vocabulary, fully separate from
`okf.BundleSurvey`/`check_conformance`. It reuses `okf._iter_docs` for the single
`rglob` walk + reserved-skip + error classification, and `okf.load_frontmatter`
to split bodies. Policy stays out of `config`: `read_config` passes
`freshness_window` through as a raw string; the `"7d"`→`timedelta` parser lives
in `lint.py`. The clock is injected — `cli/main.py::lint()` computes `today`
once and passes it in; the lint module never calls `datetime.now()`.

Verified against HEAD + `examples/good-life-demo/`: index.md catalog entries
**are** real markdown links (`* [Title](/concepts/x.md) - …`, produced by
`bundle/index.py::insert_source_entry` and present in the demo); concept bodies
link via `/`-rooted form (`/people/maria-salazar.md`) and carry inline
`(as of 2026-07-14)` stamps; `log.md` also contains links (excluded below).

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **Q1** | **Canonical identity = POSIX bundle-relative path minus `.md`.** `normalize_link(target, source_rel_dir)`: drop `#fragment` and ` "title"`; return `None` for any `scheme:` URL (http/https/mailto). `/`-rooted → `lstrip('/')`; relative/`./`/`../` → resolve against the linking doc's dir via `PurePosixPath`, normalize `..`; escapes-bundle → `None`. Strip a trailing `.md`. A doc's identity = `path.relative_to(bundle_dir).as_posix()[:-3]`. | Substring/basename matching (collides `people/x` with `concepts/x`); honoring only `/`-rooted (OKF tolerates both; demo mixes). | One identity unifies `/concepts/x.md`, `concepts/x.md`, `./x.md`, extension-less — no false orphans on link-form drift (proposal RISK-2). |
| **Q2** | **index.md IS a reference source; VERIFIED its entries are markdown links.** Referenced-set = links scanned in index.md **plus every concept body**. A cataloged concept is therefore correctly non-orphan under **uniform** treatment; no per-file special-casing needed. **`log.md` is EXCLUDED** — it links every logged concept, so counting it would make nothing an orphan once logged, defeating the check. | Special-casing index.md catalog rows (unnecessary — they're already links); including log.md (nullifies orphan detection). | The catalog + inter-concept links are the intended reachability signal; history is not. |
| **Q3** | **Uniform: a doc is an orphan iff nothing links to it — Sources included.** Safe precisely because Q2 confirms `ingest` catalogs every Source in index.md's `# Sources`, so ingested Sources are inherently linked. No `type` exemption. | Exempting `type: Source` (unneeded given Q2; would hide a genuinely uncataloged Source). | Simplest rule that is also correct; forward-compatible with MVP-2 pointer concepts. |
| **Q4** | **Grammar `<N>d`/`<N>w`, N a positive int** (days/weeks; `w`=×7d). `resolve_window(raw) -> (timedelta, notice)`: on unparseable/zero/negative, fall back to `DEFAULT_FRESHNESS_WINDOW="7d"` and return a notice string. Lint never raises on bad config. | Days-only (docs already imply weeks are plausible; `w` is cheap); crashing on bad window (violates read-only-never-fail). | Covers documented `7d`/`14d`, graceful degrade keeps a diagnostic tool from dying on a typo. |
| **Q5** | **Only a valid calendar date in `(as of YYYY-MM-DD)` is a stamp.** `STAMP_RE` shape-matches; `date(y,m,d)` in a `try/except ValueError` — a non-date like `2026-13-45` is silently skipped, never flagged, never crashes (MVP-1 lenient). Stale iff `today - stamp > window`; one finding per unique `(path, stamp-date)`. Output mirrors `status`'s sectioned `typer.echo`. | Flagging malformed stamps (MVP-2 concern; noisy now). | Mechanical/lenient MVP-1 scope. |

**ADR gate — zero created.** `openspec/config.yaml` requires (1) a
technology/pattern/interface trade-off AND (2) hard-to-reverse. Every decision is
purely additive and `git revert`-able (new module, one additive config field, a
reused read-only walk). None clears condition (2). Precedent: `add-status-command`
created zero. **Zero ADRs.**

## Data Flow

```
openkos lint            cli/main   config   lint          okf         FS
  PHASE A (reads only; no writes, no confirm)
   ├─ require_workspace(root) → config ─────────────────────────────→ index.md+log.md is_file()?
   │     └─ reason? → echo(err=True)+Exit(1)      [ONLY non-zero path]
   ├─ read_config(root).freshness_window ─────────────────────────────→ openkos.yaml
   ├─ index_text = bundle/index.md.read_text() ─────────────────────────→ (OSError → Exit 1)
   ├─ docs = lint.collect_docs(bundle_dir) → lint → okf._iter_docs ────→ rglob *.md (single walk)
   │     └─ per readable doc: re-read body via okf.load_frontmatter; errored/reserved skipped
   ├─ today = datetime.now(UTC).date()          [clock computed ONCE]
   ├─ window,notice = lint.resolve_window(cfg.freshness_window)
   ├─ stale   = lint.check_stale_stamps(docs, today=today, window=window)
   ├─ orphans = lint.check_orphans(docs, index_text=index_text)
   └─ render sections via typer.echo → exit 0
```

Note: `_iter_docs` does the one `rglob` walk; `collect_docs` re-reads only the
bodies `_iter_docs` discards (bundles are small, lint is read-only) — okf stays
byte-unchanged.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/lint.py` | New | `LintDoc`/`LintFinding`/`LintReport`, `collect_docs`, `parse_window`/`resolve_window`, `normalize_link`, `check_stale_stamps`, `check_orphans` |
| `src/openkos/config.py` | Modify | `DEFAULT_FRESHNESS_WINDOW="7d"`; `Config.freshness_window: str`; `read_config` `is not None` fallback |
| `src/openkos/cli/main.py` | Modify | New `lint` command (Phase-A: gate, inject `today`, render) |
| `src/openkos/model/okf.py` | Reused | `_iter_docs`/`load_frontmatter` read-only, **no change** |
| `tests/unit/**` | New | `test_lint.py` (pure), `cli/test_lint.py`; extend `test_config.py` |
| `docs/cli.md` | Modify | Record MVP-1 `lint` behavior + non-goals |

`openkos.yaml.template` is intentionally **not** changed — the `is not None`
fallback covers the field's absence in existing workspaces (avoids scope creep).

## Interfaces

```python
# config.py
DEFAULT_FRESHNESS_WINDOW = "7d"
@dataclass(frozen=True)
class Config:
    model: str; review: bool; default_sensitivity: str
    freshness_window: str            # NEW, raw passthrough

# lint.py — all pure except collect_docs (FS enumeration/read)
@dataclass(frozen=True)
class LintDoc:
    path: Path; identity: str; rel_dir: str; body: str
@dataclass(frozen=True)
class LintFinding:
    kind: str          # "stale" | "orphan"
    path: str; detail: str
@dataclass(frozen=True)
class LintReport:
    stale: list[LintFinding]; orphans: list[LintFinding]; notices: list[str]

def collect_docs(bundle_dir: Path) -> list[LintDoc]: ...        # wraps okf._iter_docs
def parse_window(raw: str) -> timedelta: ...                    # raises ValueError
def resolve_window(raw: str) -> tuple[timedelta, str | None]: ... # (window, notice)
def normalize_link(target: str, source_rel_dir: str) -> str | None: ...
def check_stale_stamps(docs, *, today: date, window: timedelta) -> list[LintFinding]: ...
def check_orphans(docs, *, index_text: str) -> list[LintFinding]: ...
```

## Output layout (Q5)

```
openkos lint: workspace at <root>
openkos lint: freshness_window '<raw>' is not a valid duration; using default 7d.   # only on fallback

Stale stamps:
  <concepts/x.md>: (as of YYYY-MM-DD) is <N> days old (window <raw>)
  ...                                  # or:  No stale stamps.

Orphan pages:
  <concepts/x.md>: not referenced by index.md or any concept
  ...                                  # or:  No orphan pages.
```

Empty-state wording: `  No stale stamps.` / `  No orphan pages.` (mirrors
`status`'s `Nothing needs attention.`). A pure-`ingest` bundle correctly prints
both empty states (Sources cataloged in index.md → no orphans; no `(as of)`
stamp → no stale).

## Testing Strategy (strict TDD, ≥90% branch, no network)

| Layer | What | How |
|---|---|---|
| Unit (pure) | `normalize_link`: `/`-rooted, relative, `./`/`../`, extension-less, fragment/title strip, external → `None`, escape → `None` | strings |
| Unit (pure) | `parse_window`/`resolve_window`: `7d`/`2w`, whitespace; zero/negative/garbage → fallback + notice | strings |
| Unit (pure) | `check_stale_stamps`: stale vs fresh boundary; invalid date skipped; multi-stamp dedupe; injected `today` | LintDoc fixtures |
| Unit (pure) | `check_orphans`: cataloged→not orphan, uncataloged→orphan, inter-concept link, Source uniform, log.md excluded | LintDoc + index_text |
| Unit (fs) | `collect_docs`: identity/rel_dir/body; reserved + errored files skipped | `tmp_path` |
| Unit (fs) | `read_config`: `freshness_window` present / absent / explicit null → fallback | `tmp_path` |
| Unit (cli) | `lint`: full render (exit 0); not-a-workspace → Exit 1 + message; window-fallback notice; both empty states; demo fixture | `CliRunner` + `chdir` |

## Threat Matrix

**N/A** — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process integration. `lint` performs **zero writes** and zero
execution. Its only surface is `rglob("*.md")` + `read_text`, which
`check_conformance`/`survey_bundle` already ship on HEAD with identical
symlink-traversal behavior — **no new containment surface**.

## Migration / Rollout

No migration. Purely additive + one additive config field. `git revert` removes
the command, `lint.py`, and `freshness_window`; `read_config` collapses to its
prior three fields. No persisted state.

## Review-workload footprint (feeds sdd-tasks forecast)

Estimated authored changed lines: **~170 source** (`lint.py` ~120, `cli` ~45,
`config` ~6) + **~15 docs** + **~300 test** ≈ **~485 total** — **over** the
400-line budget once tests are counted. `400-line budget risk: Medium-High`.
Recommend confirming a **2-slice split** if it exceeds budget: (1) config field +
`collect_docs` + `parse/resolve_window` + `check_stale_stamps` + CLI skeleton;
(2) `normalize_link` + `check_orphans` + orphan rendering. The orphan
normalization is the correctness-critical, test-heavy half — a natural seam.

## Open Questions

- [ ] None blocking. All five proposal open questions resolved (Q1–Q5). Exact
      `typer.echo` wording is finalized in tasks; the layout above is the contract.
