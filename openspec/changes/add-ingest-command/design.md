# Design: `openkos ingest <path>` — null compiler

## Technical Approach

`ingest` is a **compute-everything-then-write** command in the same shape as
`init` (`cli/main.py:41-112`): a pure Phase A that builds the entire result in
memory and a Phase B that writes all-or-nothing after confirmation. It adds no
extraction brain — it copies the raw source, generates **one** OKF `Source`
concept from engine-derived values, records provenance in frontmatter, and
updates the two catalog files. Every seam already exists; this slice fills the
gaps each module left at init: `config` gains a reader, `bundle/index.py` and
`bundle/log.py` gain append primitives beside their fresh-bundle renderers,
`fsio` gains an overwrite-safe atomic write next to `write_exclusive`, and
`model/okf.py` gains a `Source`-concept builder next to `dump_frontmatter`.
Verified against HEAD: fresh `index.md` is frontmatter + **empty body**
(`index.py:6-8`); `log.md` has no frontmatter (`log.py:6-13`); the demo shows the
target section/entry shapes (`examples/good-life-demo/bundle/index.md:5-21`,
`log.md:1-14`, `sources/notes-on-the-enchiridion-2026-07-05.md:1-12`).

## Module map

| Module | Gains | Stays true to |
|---|---|---|
| `config.py` | `read_config(root) -> Config` + frozen `Config`. `write_config` untouched (byte-identical `str.replace`, `:183-208`). | Workspace-root owner; reader has **no** byte-identity constraint. |
| `bundle/index.py` | `insert_source_entry(text, entry) -> str` — pure, body-only edit. | Pure renderer, never touches the filesystem. |
| `bundle/log.py` | `insert_log_entry(text, today, entry) -> str` — pure. | Pure, clock injected as a parameter (`:6`). |
| `model/okf.py` | `build_source_concept(...) -> str` (plain dict → `dump_frontmatter`). | The format seam; reuses `dump_frontmatter`, `check_conformance` unchanged. |
| `fsio.py` | `write_atomic` (overwrite) + `copy_exclusive` (raw binary copy). | `write_exclusive` "x"/D2 **untouched**. |
| `cli/main.py` | `ingest` command: Phase A/B, `--auto`, error convention. | Only sequences modules and maps exit codes. |

**Layout decision.** The Phase A/B sequence stays **inline in
`cli/main.py::ingest`**, parallel to `init`. No `compiler/` package (there is no
extraction to house — it would be empty scaffolding) and no `engine.py`/
`workspace.py` extraction yet: `init`'s named-friction note defers that to the
**third** `read_config` consumer (`query`), not the second. Minimal module churn.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | **`fsio.write_atomic(path, content)`**: write a temp file in `path.parent`, `flush` + `os.fsync`, then `os.replace(tmp, path)`; on any pre-replace failure, unlink the temp — original survives. Text, `encoding="utf-8"`, `newline=""` (matches `write_exclusive`). | In-place `"w"` truncate (a crash leaves a half-written `index.md`/`log.md` — catalog corruption); reusing `write_exclusive` + unlink + rename (fuses two responsibilities, erodes the create-only primitive). | Temp-in-same-dir keeps `os.replace` on **one filesystem**, where it is atomic: a reader sees the whole old file or the whole new one, never a splice. `write_atomic` overwrites **by design** — that is its entire job (post-init updates) — and lives **separately** so `write_exclusive` stays "x"/create-only and D2 is structurally intact. `fsync` gives content durability; a directory `fsync` is deferred hardening (visibility atomicity comes from `replace`, not `fsync`). |
| D2 | **Append = parse-then-render, frontmatter bytes verbatim.** `index.md`: split off the frontmatter block (kept **byte-for-byte**, preserving `okf_version: "0.1"`), parse the body into `# `-headed sections, create/locate `# Sources` in canonical order `[Concepts, Decisions, People, Sources]`, append the bullet at the section end, re-render body. `log.md`: keep the `# Directory Update Log` header, parse `## YYYY-MM-DD` sections newest-first, **prepend** the bullet to today's section (creating today's section at the top if absent). | Targeted string insert at a byte offset (brittle — the `# Sources` section does **not** exist on a fresh empty-body index, `index.py:6-8`); full YAML/markdown AST round-trip (reformats, quote-drift — init gap #4). | Body-only parsing sidesteps the frontmatter quote-style divergence entirely. Untouched sections/entries round-trip byte-for-byte; only the edited section changes. `write_atomic` (D1) makes the replace atomic, so a failed append leaves the original intact. |
| D3 | **`read_config` via PyYAML `safe_load`, returns a frozen `Config`** (`model`, `review`, `default_sensitivity`). Missing keys → packaged defaults (`DEFAULT_MODEL`, `review=True`, `default_sensitivity="private"`); non-mapping root or `yaml.YAMLError` → wrapped `ValueError`. | `dict` return (untyped, no validation chokepoint); hand-rolled parser (fragile); `ruamel.yaml` (heavier, and we only **read**). | PyYAML is already importable (transitive via `python-frontmatter`); the reader has no byte-identity constraint, unlike `write_config`. A frozen dataclass matches `WorkspaceLayout`'s house style and gives one typed access point. Wrapping `YAMLError` as `ValueError` keeps the CLI's existing `except (OSError, ValueError)`. **Manifest note (affects tasks):** we now `import yaml` directly, so `pyyaml` should be declared an explicit runtime dependency — **no new install**, just an honest manifest (a transitive-only reliance breaks silently if `python-frontmatter` swaps parsers). |
| D4 | **`Source` concept = plain `dict` + `dump_frontmatter` + `check_conformance`. NO pydantic.** **CONFIRMS the proposal.** | Promoting `pydantic` dev→main. | Every field is engine-derived from **trusted local** inputs (config, filename, injected clock, the raw path) — there is no untrusted structured input to validate. pydantic's value is guarding LLM JSON, which does not exist in this slice; adding a runtime dep with nothing to validate is premature. `check_conformance` (§9 rules 1-2: parseable frontmatter + non-empty `type`, `okf.py:38-61`) is the only conformance gate and it suffices. `pydantic` stays dev-only. |
| D5 | **Phase A/B mirrors `init`.** Phase A (pure, no writes): `<path>` is an existing file; workspace present (`bundle/index.md` + `log.md` exist) else refuse; `read_config`; derive slug/dest; refuse if `raw/<name>` **or** `bundle/sources/<slug>.md` exists; build the concept; compute new `index.md`/`log.md` from current on-disk bytes; show preview. **Confirm gate:** `--auto` → no prompt; else `review=false` → no prompt; else TTY → `typer.confirm`; else (non-TTY, `review=true`, no `--auto`) → **refuse** (exit 1, "re-run with `--auto`"). Phase B (after confirm): `mkdir bundle/sources`; `copy_exclusive` raw; `write_exclusive` concept; `write_atomic` `index.md`; `write_atomic` `log.md`. | Write-then-rollback; silent non-TTY write like `init`. | Create-only immutables (raw, concept) first, **catalog last** — the catalog never points at a file that does not yet exist (parallels `init`'s marker-last, D3). `init` writes silently on non-TTY because it is the command's whole point; `ingest` honours `review` and refuses to write unattended without `--auto`, matching "review before save". Error convention verbatim from `init`: `except (OSError, ValueError)` → `echo(err=True)` + `Exit(1)`, prefix `openkos ingest:`. |

**Known limit (accepted).** Phase B is **not** transactional: `write_atomic`
makes each file update atomic (never half-written), but `index.md` and `log.md`
are two writes — a failure between them leaves a **recoverable, visible** partial
(same class as `init`'s D3 no-cleanup). Ordering the two catalog writes last and
adjacent minimizes the window.

**Derivations.** Slug = source filename **stem** lowercased, non-`[a-z0-9]`→`-`,
collapsed/trimmed; when already safe it equals the stem so `raw/notes.md` ↔
`bundle/sources/notes.md` line up. Title = stem with separators→spaces. Description
is an honest null-compiler phrase (no extraction yet). Timestamp: `cli/main` reads
`datetime.now(UTC)` at **one** line and passes `now.astimezone().date()` for the
`log.md` heading (local calendar date, matching `init` `cli/main.py:98` +
`log.py` rationale) and an ISO-8601 `Z` string for the concept `timestamp:`; pure
builders take both as parameters — no clock in domain code.

## Sequence

```
openkos ingest <path>        cli/main   config   okf   bundle   fsio   FS
  PHASE A (reads + in-memory build, NO writes)
    ├── is <path> a file? ────────────────────────────────────────────>│
    ├── workspace? (bundle/index.md + log.md) ────────────────────────>│
    ├── read_config(root) ──────────>│
    ├── slug/dest; raw/<name> or sources/<slug>.md exist? → refuse ────>│
    ├── build_source_concept(...) ──────────>│ (dict + dump_frontmatter)
    ├── insert_source_entry(index bytes) ─────────────>│
    ├── insert_log_entry(log bytes, today) ───────────>│
    └── preview → confirm gate (--auto / review / TTY)
  PHASE B (after confirm, all-or-nothing; catalog LAST)
    ├── sources.mkdir ───────────────────────────────────────────────>│
    ├── copy_exclusive(src, raw/<name>) ──────────────────────>│ "xb"  │
    ├── write_exclusive(sources/<slug>.md) ───────────────────>│ "x"   │
    ├── write_atomic(index.md) ───────────────────────────────>│ replace
    └── write_atomic(log.md)  ────────────────────────────────>│ replace
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/config.py` | Modify | Add `read_config`, frozen `Config`; `write_config` unchanged |
| `src/openkos/fsio.py` | Modify | Add `write_atomic` (temp+`os.replace`) and `copy_exclusive` ("xb"); `write_exclusive` unchanged |
| `src/openkos/bundle/index.py` | Modify | Add `insert_source_entry` (body-only parse-then-render) |
| `src/openkos/bundle/log.py` | Modify | Add `insert_log_entry` (dated-section append) |
| `src/openkos/model/okf.py` | Modify | Add `build_source_concept`; reuse `dump_frontmatter`; `check_conformance` unchanged |
| `src/openkos/cli/main.py` | Modify | Add `ingest` command, Phase A/B, `--auto` |
| `pyproject.toml` | Modify | Declare `pyyaml` as a direct runtime dep (no new install) |
| `openspec/specs/ingestion/spec.md` | New | `ingestion` capability spec |
| `tests/unit/**` | New/Modify | Reader, append round-trips, atomic write, exclusive copy, Phase A/B, `--auto`, conformance |
| `docs/cli.md` | Modify | Record the null-compiler behavior of `ingest` |

## Interfaces

```python
@dataclass(frozen=True)
class Config:
    model: str; review: bool; default_sensitivity: str
def read_config(root: Path) -> Config: ...            # config.py; YAMLError→ValueError

def write_atomic(path: Path, content: str) -> None: ...   # fsio.py; temp+os.replace
def copy_exclusive(src: Path, dst: Path) -> None: ...     # fsio.py; "xb", create-only

def insert_source_entry(index_text: str, *, title: str, slug: str, description: str) -> str: ...
def insert_log_entry(log_text: str, today: date, entry: str) -> str: ...
def build_source_concept(*, title, description, resource, tags, timestamp, sensitivity, provenance) -> str: ...
```

## Testing Strategy (strict TDD, ≥90% branch, no network)

| Layer | What | How |
|---|---|---|
| Unit (pure) | `insert_source_entry`: fresh empty-body index gains `# Sources`; existing sections round-trip byte-for-byte | Plain string asserts on real fixtures |
| Unit (pure) | `insert_log_entry`: same-day prepend above `Initialization`; new day-section at top | String asserts |
| Unit (fs) | `write_atomic` overwrites; interrupted write leaves original intact; `copy_exclusive` refuses collision | `tmp_path`; monkeypatch `os.replace`/open to raise mid-write |
| Unit (fs) | `read_config` fields + defaults; `YAMLError`→`ValueError`; non-mapping root | `tmp_path` fixtures |
| Unit (fs) | generated `Source` concept passes `check_conformance`; `sensitivity == default_sensitivity` | build + write + check |
| Unit (cli) | Phase A preview vs Phase B all-or-nothing; `--auto`; non-TTY-refuse; missing-path/collision/not-a-workspace refusals; exit codes | `CliRunner` + `monkeypatch.chdir` |

## Threat / path-containment note

Matrix **N/A** — no routing, shell, subprocess, VCS/PR automation,
executable-file classification, or process integration (raw copy is byte I/O;
the LLM/httpx backend is deferred). One filesystem-injection surface **is**
present and is a design requirement: destinations derive from `Path(src).name`
(strips directory components) and a sanitized slug (no `/`), never from raw
user path segments — so `../../x` lands as `raw/x`, never outside `raw/` or
`bundle/sources/`. **RED test required:** a traversal basename stays contained.

## ADR gate — evaluated, zero created

`openspec/config.yaml` requires both (1) a technology/pattern/interface/trade-off
**and** (2) hard-to-reverse. Every decision here is **purely additive** and
`git revert`-able: `write_atomic`/`copy_exclusive` add functions (existing files
unaffected if removed), `read_config` adds a reader, the appenders add pure
functions, Phase A/B reuses a **documented** pattern, and D4 is an
*un-decision* (declining pydantic — like `add-model-selection`'s declined
deferral, which recorded no ADR). None clears condition (2). Precedent:
`add-init-command` and `add-model-selection` created zero ADRs for comparable
interface/pattern surfaces. **Zero ADRs.**

## Findings that shape tasks

1. **`copy_exclusive` is a fifth `fsio`/task item** the proposal did not name:
   the raw copy must be an **exclusive binary** create ("xb"), because
   `write_exclusive` is text-only and raw sources are "any extension".
2. **`pyproject.toml` must declare `pyyaml`** (D3) — a manifest edit, not an
   install; a small dedicated task.
3. **Non-TTY-refuse (D5)** is a behavior contract → must land in the `ingestion`
   spec and get its own RED test, not just code.
4. **Path-containment RED test** (threat note) is mandatory.

## Open Questions

- [ ] Confirm the null-compiler `description` wording with the spec author (this
      design uses an honest "raw source imported; not yet compiled" phrasing).
- [ ] `docs/cli.md:62-66` lists `--sensitivity`/`--batch` flags this slice
      **defers** — spec/docs must mark them "not in this slice" to avoid drift.
