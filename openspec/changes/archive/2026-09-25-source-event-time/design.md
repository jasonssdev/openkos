# Design: source-event-time — a Source's event date, separate from ingest time

Refs #1014 piece (c). The proposal is the WHAT and ADR-0023
(`docs/adr/0023-source-event-date.md`) is the WHY. This document is the HOW.

## Technical Approach

The change adds one optional Source frontmatter key, `event_date`, along the path
that already carries `origin_key`:

```
cli/main.py ingest ──(flag, refusals)──► _ingest_single
      └─► application/ingest.py compose_source_document ──(resolve: flag > stored > file name > unset)
             └─► model/okf.py build_source_concept ──(emit only when not None)
```

Four properties shape every decision below:

- **Byte-identical when absent.** No flag, no dated file name and no stored value
  means no key, no new output line and no new write. Every existing test golden
  stays green without edits.
- **Format knowledge stays in `model/okf.py`.** That module owns the key name,
  the emitted form, and the tolerant reader. It is the AGENTS.md seam.
- **Resolution happens once, in the application layer** (ADR-0018). The CLI only
  parses the flag, refuses bad input, and renders the result.
- **Inferring the date from the input name is not format knowledge.** It lives
  in a new pure leaf module, the same way `source_title.py` handles title
  inference.

The design adds one mechanism the proposal did not foresee: the **date-only
rewrite on a converged re-ingest** (Decision 6). Without it, the #773
convergence gate (`application/ingest.py:583-640`, `cli/main.py:5264-5293`)
would write nothing on most re-ingests. `--event-date` would then silently do
nothing on any unchanged, already-extracted Source, which contradicts the
proposal's criterion "with a differing flag it overwrites".

## Architecture Decisions

### Decision 1: The parser lives in a new leaf module, `src/openkos/source_date.py`

**Choice**: A new pure-stdlib module with no `openkos` imports and two
functions:

- `parse_event_date(text: str) -> date | None` parses the flag value.
- `event_date_from_name(name: str) -> date | None` infers the date from a file
  name.

**Alternatives considered**:

- `model/okf.py`. Rejected. Reading a date out of a user's *input file name*
  says nothing about OKF's on-disk shape. Putting it there dilutes the one-seam
  rule in the other direction.
- `application/ingest.py`. Rejected. The CLI also needs `parse_event_date` to
  refuse with exit 2 before the workspace is touched. Importing the service
  module for that is heavier than a leaf, and the leaf can be unit-tested with
  no workspace.
- `model/`. Rejected. `model/` is the canonical data-model layer (types, the
  OKF adapter, relations), and an input-parsing policy is not part of the
  model.

**Rationale**: `src/openkos/source_title.py` is the exact precedent. It is a
top-level, pure, import-free leaf that derives a Source attribute from ingest
input (`source_title.py:1-12`), and `application/ingest.py:42` already imports
it. `source_date.py` mirrors it. It is pure and idempotent, it never raises, and
`None` means "no evidence" rather than an error.

**Matching rules** (fail-closed, stated precisely):

| Function | Rule |
| --- | --- |
| `parse_event_date(text)` | `re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text)`, then `date.fromisoformat`. On a `ValueError`, return `None`. The regex is required: since Python 3.11, `date.fromisoformat` also accepts `20260714` and `2026-W28-2`. ASCII `[0-9]`, not `\d`, which matches Unicode digits. |
| `event_date_from_name(name)` | Scan **only the basename given** (the caller passes `src.name`, so parent directory names never count) with `(?<!\d)([0-9]{4}-[0-9]{2}-[0-9]{2})(?!\d)`. The Unicode `\d` in the guards makes *any* adjacent digit disqualify a token. Parse every token with `date.fromisoformat`. **If any token is calendar-invalid, return `None`**: an unparseable date-shaped token means a naming scheme we do not understand. Otherwise return the single element of the distinct set of dates. If the set has zero or more than one element, return `None`. |

Parser table (these become the unit-test rows):

| Name | Result |
| --- | --- |
| `call-2026-07-14.txt` | 2026-07-14 |
| `2026-07-14.md` | 2026-07-14 |
| `notes.2026-07-14` (the date sits in the suffix) | 2026-07-14 (this is why the parser reads the name, not the stem) |
| `a-2026-07-14_b-2026-07-14.txt` (the same date twice) | 2026-07-14 |
| `2026-07-14-15.txt` | 2026-07-14 (`-` is not a digit, so the token stands) |
| `03-04-2026.txt` | None |
| `2026-13-01.txt` | None |
| `12026-07-14.txt`, `2026-07-140.txt` | None |
| `2026-07-14_2026-08-01.txt` | None (two distinct dates) |
| `2026-13-01_2026-07-14.txt` | None (one invalid token) |
| `20260714.txt`, `notes.txt` | None |
| `٢٠٢٦-٠٧-١٤.txt` (Arabic-Indic digits) | None |

### Decision 2: The on-disk form is a quoted string, and the reader tolerates a bare date

**Choice**: `build_source_concept` takes `event_date: date | None = None` and,
when it is not `None`, emits `metadata[EVENT_DATE_KEY] = event_date.isoformat()`.
A Python `str` shaped like a date is quoted by the YAML emitter, so the bytes
are `event_date: '2026-07-14'`. The line sorts alphabetically between
`description` and `freshness`, because `dump_frontmatter` sorts keys. This is
the same quoting `timestamp` already gets (`tests/unit/model/test_okf.py:922`).
Passing a `datetime` raises `TypeError`. It is a subclass of `date`, and its
`isoformat()` would write a time that no input stated.

The tolerant reader is `okf.read_event_date(metadata) -> StoredEventDate`:

| On-disk value | `value` | `malformed` |
| --- | --- | --- |
| key absent | `None` | `False` |
| `'2026-07-14'` (a `str` that passes the strict full-match and calendar check) | `date(2026, 7, 14)` | `False` |
| `2026-07-14` unquoted, which PyYAML loads as `datetime.date` | `date(2026, 7, 14)` | `False` |
| `datetime.datetime` (for example `2026-07-14 10:00`) | `None` | `True` |
| a bad string (`'14/07/2026'`, `'2026-13-01'`, `''`), `null`, an int, a list, anything else | `None` | `True` |

The check for `datetime.datetime` must come before the check for `date`.

**Alternatives considered**: emitting a bare `datetime.date`. It reads better,
but its type depends on the parser. PyYAML returns `date`, a YAML 1.2
core-schema parser returns `str`, and js-yaml returns a UTC-midnight `Date`.
OKF is read by third-party tools (ADR-0023). Failing the run on a malformed
value was also rejected: frontmatter is hand-editable, and the posture of
`extraction_notices` (`okf.py:284-291`) fails closed without crashing.

**Rationale**: A quoted value round-trips as the same `str` through
`load_frontmatter`, so a regenerate reproduces identical bytes. The malformed
policy follows the proposal's recommendation: the value is **not carried
forward**, a **warning** is printed, and the run **never fails**. The warning is
emitted by the CLI; see Decision 7.

The strict string check inside `okf` is a deliberate, documented
narrow twin of `source_date.parse_event_date`. `okf` already has `_ISO_DATE_RE`
(`okf.py:48`), so it reuses that regex and adds the calendar check. This keeps
`model/okf.py` free of any dependency on an input-parsing module. Widening the
flag syntax later must never silently widen what the on-disk reader accepts.

### Decision 3: The value is threaded down the existing call chain and emitted only when not `None`

**Choice**:

- `okf.build_source_concept(..., event_date: date | None = None)` emits the key
  after `origin_key` (`okf.py:587-588`) under the same guard,
  `if event_date is not None`.
- `application_ingest.compose_source_document(..., event_date_flag: date | None = None, source_name: str | None = None)`
  resolves the value (Decision 4) and passes `resolution.value` to the builder
  at `application/ingest.py:798-810`.
- `SourceDocumentPlan` (`application/ingest.py:691-718`) gains
  `event_date: EventDateResolution`. It is a frozen default, so the 14 test
  constructions stay valid.
- **`compose_catalog_update` must also pass `event_date=source.event_date.value`**
  on its conditional rebuild at `application/ingest.py:881-893`. Without it, any
  Source whose run stamps an `extraction_status` or `extraction_notice` is
  rebuilt from scratch and loses its date. The proposal missed this second
  `build_source_concept` call site. There is exactly one other call site in
  `src/`.

**Alternatives considered**: a required parameter with no default. Rejected,
because it would churn 14 call sites in `tests/unit/application/test_ingest.py`
for no safety gain. A `source_name` of `None` turns inference off, so each slice
stays inert until the CLI passes it. `origin_key` sets the same precedent.

**Rationale**: `extraction_status`, `extraction_notice` and `origin_key` all
follow this pattern of "emitted only when set" (`okf.py:570-588`). It is what
keeps every Source written without evidence byte-identical.

### Decision 4: Re-ingest resolves once, in the service, with the order flag > stored > file name > unset

**Choice**: A pure function in `application/ingest.py`:

```python
EventDateOrigin = Literal["flag", "file name", "kept"]

@dataclass(frozen=True)
class EventDateResolution:
    value: date | None               # what this run writes (None -> key absent)
    origin: EventDateOrigin | None   # None iff value is None
    previous: date | None            # the VALID stored value, None if absent or malformed
    stored_malformed: bool           # the key was present but unreadable
    stored_raw: object               # verbatim on-disk value, for the warning only

    @property
    def changed(self) -> bool:       # value != previous
        ...

def resolve_event_date(
    *, flag: date | None, stored: okf.StoredEventDate | None, inferred: date | None
) -> EventDateResolution: ...
```

- If `flag` is set, the result is `(flag, "flag")`.
- Otherwise, if `stored.value` is set, the result is `(stored.value, "kept")`.
- Otherwise, if `inferred` is set, the result is `(inferred, "file name")`.
- Otherwise the result is `(None, None)`.

A malformed stored value counts as absent. That is how "never carried forward"
is implemented, and it lets a flag or the file name fill the gap.

**Where the stored value is read back**: in `compose_source_document`, beside
`on_disk_sensitivity` and `on_disk_title` (`application/ingest.py:785-796`). A
new `_read_source_event_date(source_document_display_path, concept_text)`
mirrors `_read_source_title` (`application/ingest.py:673-688`). It calls
`okf.read_event_date`, and it raises the same `ValueError` wording on
unparseable frontmatter. That error cannot be reached first in practice,
because the sensitivity read raises before it. When `concept_text is None`
(a fresh ingest, or a regenerate after `forget`), `stored` is `None`. The
single `_snapshot_read` observation (`cli/main.py:5202-5210`) feeds all three
reads, so an edit cannot slip between them. `inferred` is
`source_date.event_date_from_name(source_name)` when `source_name` is not
`None`.

**Alternatives considered**: resolving in the CLI. Rejected by ADR-0018, and
the stored value is only parsed inside the service anyway. Refusing when the
flag and the stored value disagree was also rejected: the proposal settled that
the flag is the human's curation.

**Rationale**: The flag is explicit curation. A stored value is a previous
explicit or reviewed decision, so it outranks a fresh inference. A re-ingest
never clobbers a known date, and it never invents one.

### Decision 5: The flag with a directory or a glob is refused with exit 2, in the `ingest` body, before expansion

**Choice**: `ingest` gains
`event_date: str | None = typer.Option(None, "--event-date", metavar="YYYY-MM-DD", help=...)`.
Two up-front checks run first in the command body, before `_expand_batch_sources`
at `cli/main.py:4909`:

1. `parsed = source_date.parse_event_date(event_date)` returns `None`. Output:
   `openkos ingest: --event-date must be a calendar date written YYYY-MM-DD, got '<value>'.`
   The run exits 2.
2. `not src.is_file() and (src.is_dir() or any(c in str(src) for c in _GLOB_MAGIC_CHARS))`.
   Output:
   `openkos ingest: --event-date applies to a single file; '<src>' is a directory or a glob. Ingest each file with its own --event-date, or name the date in each file name.`
   The run exits 2.

The input *shape* decides, not the number of matches, so an empty directory and
a glob that matches one file are both refused. An existing plain file whose name
contains `*`, `?` or `[` is still a file, which matches the is-file-first rule of
`_expand_batch_sources` (`cli/main.py:4366`). A nonexistent path without glob
characters falls through to the existing exit-1 "does not exist" refusal
(`cli/main.py:5098-5104`). `_ingest_single` gains `event_date: date | None = None`,
and only the single-file branch at `cli/main.py:4918` passes it. `_ingest_batch`
never sees it.

**Exit code**: the `docs/cli.md:26` contract says `2` is a usage error. Flag-misuse
refusals raised from the command body already use `typer.echo(..., err=True)`
followed by `raise typer.Exit(code=2)`. The `adjudicate` mutual-exclusion and
`--include-cross-source` refusals do this (`cli/main.py:11344-11398`), and
`docs/cli.md:335-336` documents it. This design uses the same shape.

**Alternatives considered**: a Typer `callback=` validator. It would also give
exit 2, but through Click's usage banner, which differs from every other refusal
in this file, and it cannot see `src` for the shape check. Refusing inside
`_ingest_single` was rejected because the batch path never reaches it with the
flag.

**Rationale**: The checks run before any read of the workspace and before any
write. One caveat: `@_guard_workspace_lock` (`cli/main.py:4841`) wraps the body,
so a concurrent-writer refusal (exit 3) can come before these checks. Nothing is
written either way.

### Decision 6: A converged re-ingest whose event date changes rewrites the Source only, with no LLM call

**Problem**: `converged_reingest` returns early and writes nothing when the
Source is unchanged, already extracted, and carries an `origin_key`
(`cli/main.py:5264-5293`). That is the common re-ingest. Without this decision,
two things go wrong:

- `openkos ingest call.txt --event-date 2026-07-14` on such a Source prints
  "skipping extraction" and records nothing.
- File-name inference never backfills a Source ingested before this change.

**Choice**: Convergence skips only when `not source_plan.event_date.changed`.
When the Source has converged and the date changed, the run takes the ordinary
regenerate path with extraction short-circuited:

- `ConvergedReingest` (`application/ingest.py:571-580`) gains
  `carried_status: okf.ExtractionStatus | None`, read with a new
  `carried_extraction_status(metadata)`. It narrows to
  `okf.EXTRACTION_STATUS_VALUES` by membership and fails closed, exactly like
  `carried_extraction_notice` (`application/ingest.py:547-568`).
- `stage_derived_objects` gains `carried: ConvergedReingest | None = None`. When
  it is set, the function returns
  `StagedDerivedObjects(plans=(), skip_reason=carried.carried_status, notices=carried.carried_notices, report=None, drops=(), lost_in_staging=0)`
  **before** any LLM call. This is the same pre-extraction short-circuit shape as
  the `no-extractable-text` and `blocked-by-sensitivity` returns
  (`application/ingest.py:340-358`).
- `compose_catalog_update` then rebuilds the Source with the carried markers and
  the new `event_date`. The preview, confirm gate, drift guard, `write_atomic`,
  and autocommit are unchanged.
- In the CLI, `observability.stage_notice(...)` and
  `_render_staged_derived_objects(staged)` are guarded by `converged is None`, so
  no extraction wording is printed for a run that extracted nothing. The spinner
  context stays as it is, to avoid re-indenting roughly 80 lines. It is
  entered and left at once. `_SingleIngestOutcome` reports
  `extraction_skipped=converged is not None` and
  `extraction_degraded=skip_reason is not None and converged is None`, so the
  batch summary does not count a carried marker as a new degrade.

**Alternatives considered**:

| Option | Why not |
| --- | --- |
| Fall through the gate to the full run | This re-runs extraction on unchanged bytes, which is the unbounded object accumulation #773 was filed to stop (17 objects from 81 lines), and it costs LLM calls for a date. |
| Disclose and skip ("pass `--re-extract`") | This is honest but makes the feature a no-op on the common case, and its remedy is the expensive re-extraction above. |
| A surgical one-line frontmatter patch (like `bundle/source_titles._patch_title_line`) | It needs new insert/replace machinery and its own byte-fidelity guarantees, and it bypasses the tested regenerate write path. |

**Rationale and the one invariant touched**: `EXTRACTION_STATUS_KEY` is
documented as "never read back from disk by any writer" (`okf.py:113-120`).
That rule exists so a *fresh* extraction's marker cannot become stale and
sticky. In this path no extraction runs, and the derived objects the marker
describes are still exactly as they were. Carrying the marker forward preserves
a true statement, and dropping it would erase one. The convergence path already
reads the notice for the same reason (`application/ingest.py:640`). The
`okf.py:113-120` and `EXTRACTION_NOTICE_KEY` docstrings gain one sentence naming
this exception.

The date-only rewrite is reachable only when the resolved date differs from a
valid stored date. With no flag, no dated name and no stored date,
`None == None`, so the gate skips exactly as today. The rewrite is also
idempotent. After one date-only rewrite, the next re-ingest resolves to "kept",
and `changed` is false. A malformed stored value alone never triggers a rewrite,
because `previous` is `None` and a `None` resolution is unchanged. The warning
still prints.

**Spec impact**: the spec agent must cover this path. It needs a scenario for a
converged re-ingest with a differing flag, or with an inferable name on an
undated Source. The expected outcome is: the Source is rewritten, no LLM call is
made, derived objects are untouched, and the carried markers are preserved.
This is recorded under Open Questions so the orchestrator can reconcile it.

### Decision 7: One preview line on stdout and one warning on stderr

**Choice**:

- **Preview line** (stdout). It is printed in the proposed-changes block right
  after the `bundle/sources/{slug}.md` line, at `cli/main.py:5463-5466` for a
  re-ingest and `cli/main.py:5474` for a fresh ingest, **only when
  `resolution.value is not None`**:

  | Origin | Line |
  | --- | --- |
  | flag, no differing previous value | `    event date 2026-07-14 (from --event-date)` |
  | flag, overwriting a different stored value | `    event date 2026-07-14 (from --event-date, replacing 2026-07-10)` |
  | file name | `    event date 2026-07-14 (from the file name)` |
  | kept | `    event date 2026-07-14 (kept from the existing Source)` |

  The line is printed *before* the confirm gate, so an inferred or overwritten
  value is reviewed before anything is written. A run without a date prints
  nothing new, so the stdout goldens in `tests/unit/cli/test_ingest_characterization.py`
  do not move.
- **Warning** (stderr). It is printed right after `compose_source_document`
  returns, when `resolution.stored_malformed` is true:
  `openkos ingest: ignoring the malformed event_date <stored_raw!r> in 'bundle/sources/<slug>.md' -- expected YYYY-MM-DD.`
  The wording states only what happened. On the converged-and-unchanged path
  the file is left untouched, so the warning must not claim the value was
  removed.

**Rationale**: The proposal asks for one line that names the value and its
origin. The `replacing` clause mirrors the existing `title changed from … to …`
clause (`cli/main.py:5458-5462`). The spec may fix different exact wording. If
it does, the spec wins, and apply follows it.

### Decision 8: The merge keeps the survivor's own `event_date`

**Choice**: `okf.EVENT_DATE_KEY` is added to `_SPECIAL_KEYS` at
`okf.py:1705-1712`, next to `TYPE_ALTERNATIVE_KEY`. `merged` starts as
`dict(survivor_metadata)` (`okf.py:1693`), so the survivor keeps its own value
or stays absent. The generic "adopt when absent" branch (`okf.py:1721-1722`)
can no longer copy the absorbed side's date onto the survivor. The
`build_merged_document` docstring gains a sentence beside the `type_alternative`
note (`okf.py:1656-1667`). The absorbed value can be recovered through `unmerge`
and git.

**Rationale**: As the proposal verified, a date belongs to one piece of recorded
content. Stamping it onto the survivor's undated content would create evidence
nobody gave.

## Data Flow

```
user ── openkos ingest <src> [--event-date D]
          │
          ├─ D invalid ───────────────────────────────► exit 2, nothing read or written
          ├─ D given and src is a dir or glob ────────► exit 2, nothing read or written
          ▼
   _ingest_single(src, event_date=parsed D)
          │  one _snapshot_read(concept_path) when a prior Source exists
          ▼
   compose_source_document(event_date_flag=D, source_name=src.name, concept_text)
          │  stored   = okf.read_event_date(prior metadata)
          │  inferred = source_date.event_date_from_name(src.name)
          │  res      = resolve_event_date(flag, stored, inferred)
          │  content  = okf.build_source_concept(..., event_date=res.value)
          ▼
   SourceDocumentPlan(event_date=res) ──► CLI prints the stderr warning if res.stored_malformed
          │
          ├─ converged and not res.changed ──► skip (today's behaviour, byte-identical)
          ├─ converged and res.changed ──────► stage_derived_objects(carried=converged): no LLM
          └─ not converged ──────────────────► stage_derived_objects(...): extraction as today
          ▼
   compose_catalog_update(source=plan) ── rebuild? ──► build_source_concept(..., event_date=res.value)
          ▼
   preview (+ "event date D (origin)" line) ─► confirm ─► drift guard ─► write ─► autocommit
```

Sequence for the converged date-only rewrite, the one new flow:

```
CLI                     service (application/ingest)         okf
 │ compose_source_document ─►│ read_event_date(prior) ─────────►│
 │                           │ resolve → changed=True           │
 │◄──────── plan ────────────│                                  │
 │ converged_reingest ──────►│ carried_notices + carried_status │
 │◄─ ConvergedReingest ──────│                                  │
 │ (changed → do not return) │                                  │
 │ stage_derived_objects(carried=…) ─► returns carried markers, no llm.chat
 │ compose_catalog_update ──►│ build_source_concept(markers, event_date) ─►│
 │ preview + confirm + drift guard + write_atomic(Source, index, log) + autocommit
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/source_date.py` | Create | Pure leaf: `parse_event_date`, `event_date_from_name` (Decision 1). |
| `src/openkos/model/okf.py` | Modify | `EVENT_DATE_KEY` constant with its docstring. `StoredEventDate` and `read_event_date` (tolerant reader). An `event_date` parameter and conditional emission in `build_source_concept`. `EVENT_DATE_KEY` in the merge `_SPECIAL_KEYS` plus a docstring line. A docstring note on the carried-marker exception. |
| `src/openkos/application/ingest.py` | Modify | `EventDateResolution` and `resolve_event_date`, and `_read_source_event_date`. New parameters on `compose_source_document` and a new field on `SourceDocumentPlan`. Threading through `compose_catalog_update`. `ConvergedReingest.carried_status` and `carried_extraction_status`. The `carried=` short-circuit in `stage_derived_objects`. |
| `src/openkos/cli/main.py` | Modify | The `--event-date` option and the two exit-2 refusals in `ingest`. The `event_date` parameter on `_ingest_single`, passed through. The warning, the preview line, the convergence condition, the render and stage-notice guards, and the outcome flags. |
| `docs/cli.md` | Modify | One row in the `ingest` flag table (`:134-138`), covering the precedence, the file-name rule, and the directory/glob refusal. |
| `docs/adr/0023-source-event-date.md`, `docs/adr/README.md` | Create / Modify | ADR-0023, status Proposed, and its index row. Both were written in this phase. |
| `tests/unit/test_source_date.py` | Create | The parser tables. |
| `tests/unit/model/test_okf.py` | Modify | Emission, absence pin, reader tolerance, datetime refusal, merge. |
| `tests/unit/application/test_ingest.py` | Modify | Resolution table, threading, catalog rebuild, carried status, short-circuit. |
| `tests/unit/cli/test_ingest.py` | Modify | Flag, refusals, preview line, warning, re-ingest carry-forward and overwrite, converged date-only rewrite. |

`docs/knowledge-object-model.md` and `examples/good-life-demo/` are not touched,
as the proposal says.

## Interfaces / Contracts

```python
# src/openkos/source_date.py  (no openkos imports)
def parse_event_date(text: str) -> date | None: ...
def event_date_from_name(name: str) -> date | None: ...

# src/openkos/model/okf.py
EVENT_DATE_KEY: Final = "event_date"

@dataclass(frozen=True)
class StoredEventDate:
    value: date | None
    malformed: bool
    raw: object

def read_event_date(metadata: Mapping[str, object]) -> StoredEventDate: ...
def build_source_concept(..., origin_key: str | None = None,
                         event_date: date | None = None) -> str: ...

# src/openkos/application/ingest.py
def resolve_event_date(*, flag: date | None, stored: okf.StoredEventDate | None,
                       inferred: date | None) -> EventDateResolution: ...
def compose_source_document(..., event_date_flag: date | None = None,
                            source_name: str | None = None) -> SourceDocumentPlan: ...
def carried_extraction_status(metadata: Mapping[str, object]) -> okf.ExtractionStatus | None: ...
def stage_derived_objects(..., carried: ConvergedReingest | None = None) -> StagedDerivedObjects: ...
```

On disk (the only new bytes, and only when a date is known):

```yaml
event_date: '2026-07-14'
```

## Testing Strategy

Strict TDD is on, and the runner is `uv run pytest`. Every row below is written
RED first. Per the project's mutation discipline, a test that passes on first run
is checked by mutating the exact line it guards.

| Layer | What to test | Approach |
| --- | --- | --- |
| Unit, `tests/unit/test_source_date.py` | Both parser tables from Decision 1, the flag parser (`2026-02-30`, `20260714`, `2026-W28-2`, `' 2026-07-14'`, and `''` all give `None`), and purity (no raise on any input) | Parametrized tables. |
| Unit, `tests/unit/model/test_okf.py` | **Absence pin** `test_build_source_concept_emits_no_event_date_key_by_default`, modeled on `test_build_source_concept_emits_no_volatility_key` (`:687`). A **full-bytes pin** of a Source built with `event_date=date(2026,7,14)`, asserting the exact line `event_date: '2026-07-14'` and its sorted position. A round-trip through `load_frontmatter` then `read_event_date`. A `datetime` argument raises `TypeError`. The reader tolerance table from Decision 2. `build_merged_document`: a survivor without the key does not gain the absorbed side's value, and a survivor with the key keeps it. These sit beside `test_build_merged_document_never_inherits_absorbed_type_alternative` (`:1519`). | Direct calls, byte assertions. |
| Unit, `tests/unit/application/test_ingest.py` | The `resolve_event_date` precedence table: flag > stored > file name > unset; malformed treated as absent; `changed` in each case. `compose_source_document` emits the value, and emits nothing when `source_name` and the flag are `None`. The re-ingest `kept` case. The **catalog rebuild keeps `event_date`** when `skip_reason` or `notices` force a rebuild (regression for the second call site). `carried_extraction_status` fails closed. `stage_derived_objects(carried=...)` returns the carried markers and never calls the LLM: use a stub `LLMBackend` whose `chat` raises, and assert that it is not called. | Pure-function tables and a stub backend. |
| Unit, `tests/unit/cli/test_ingest.py` (CliRunner) | `--event-date 2026-13-01` exits 2 with zero writes. The flag with a directory, a glob, and an empty directory each exit 2 with zero writes. A valid flag on a file writes the key and prints the preview line. A dated file name with no flag writes the key and prints `(from the file name)`. Re-ingest without a flag keeps the value and prints `(kept …)`. Re-ingest with a differing flag prints `replacing`. A malformed stored value produces the stderr warning and is not carried. The **converged date-only rewrite**: the Source is rewritten, the stub chat is never called, no new derived files appear, and a carried `extraction_notice` survives. A second identical run writes nothing. No derived concept ever carries `event_date`. | Uses the existing fixtures in this file. The zero-writes checks use a tree snapshot. |
| Characterization (must stay green, **unedited**) | `tests/unit/cli/test_ingest_characterization.py`, `tests/unit/bundle/test_frontmatter_split_parity.py`, `tests/unit/model/test_okf_framing_characterization.py`, `tests/unit/test_adr_index.py`, and every existing `test_build_source_concept_*` golden | If one of these needs an edit, that means the key or the line leaked into the absent case. That is a defect, not a fixture update. |

Coverage: the new branches (both refusals, all three origins, malformed, the
converged-and-changed path) are covered by the rows above, which keeps the 90%
branch gate.

## Threat Matrix

N/A. The change adds no routing, shell command, subprocess, VCS/PR automation,
executable-file classification, or process-integration boundary. The file name
is only matched by a regex. It is never interpolated into a command. The value
written to disk is `date.isoformat()`, never the raw name, so the
untrusted-filename surface of `build_source_concept` (`okf.py:501-528`) does
not grow.

## Migration / Rollout

No migration is required. The key is absent unless there is evidence, and
existing Sources gain it only on a re-ingest with evidence. That includes the
converged date-only rewrite, which acts as a zero-cost backfill in the
`origin_key` style for Sources whose file names carry a date. Rollback follows
the proposal: revert the slices in reverse order. A Source written in the
meantime keeps a legal §4.1 key that every reader ignores.

**Auto-chain slice plan** (review budget of about 400 authored changed lines per
slice). The design adds the convergence path and the second call site, so
authored lines grow past the proposal's 450-550 forecast to about **600-680**.
The work is therefore **three** slices instead of two. Each slice is inert on its
own until the next one wires it in, so each can be rolled back independently.

| Slice | Scope | Source | Tests | Docs | Total (est.) |
| --- | --- | --- | --- | --- | --- |
| 1: OKF seam and parser | `source_date.py`, `okf` key, emission, reader, merge special-case, docstrings | ~105 | ~130 | 0 | ~235 |
| 2: Application resolution | `resolve_event_date`, stored read-back, compose/plan/catalog threading, carried status, `carried=` short-circuit | ~95 | ~120 | 0 | ~215 |
| 3: CLI and docs | Option, refusals, `_ingest_single` threading, warning, preview line, convergence condition and guards, outcome flags, `docs/cli.md` row | ~55 | ~150 | ~6 | ~210 |

The design artifacts and ADR-0023 (about 70 lines of ADR) ride with slice 1, or
with a separate docs commit if the orchestrator prefers to keep slice 1 focused
on code. Slices 1 and 2 are inert on their own: no production caller passes
`event_date` or `source_name` until slice 3.

## Open Questions

- [ ] **Spec alignment (non-blocking, needs orchestrator reconciliation):** the
  spec runs concurrently and may not cover Decision 6, the converged date-only
  rewrite, or Decision 3's second call site in `compose_catalog_update`. Both
  need scenarios. The exact wording of the preview line and the warning
  (Decision 7) is a recommendation. If the spec states other wording, the spec
  wins.
- [ ] **Product confirmation (non-blocking):** Decision 6 makes the first plain
  re-ingest of an unchanged Source with a dated file name rewrite that Source
  once, to add the date. This is disclosed in the preview and goes through the
  confirm gate. It matches the proposal's "a pre-feature Source gains a date on
  its next re-ingest", but a batch re-ingest of a dated folder now yields one
  Source-only commit per file where it used to yield none.
