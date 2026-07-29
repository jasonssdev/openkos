# Design: Add a `list` verb so concept ids are discoverable from the CLI

Issue #184. Proposal: `openspec/changes/discover-concept-ids/proposal.md`.
Every proposal decision (dedicated verb, one walk, `link_dir` vocabulary with `REGISTRY.name`
alias, `--limit 50` / `--all`, no `--json`, deprecated shown and marked, `ID SENSITIVITY STATUS
TITLE`, ungated confidential titles) is an input here, not a question.

## Technical Approach

One new canonical-layer module, `src/openkos/bundle/listing.py`, exposing a single-pass
enumerator `list_objects(bundle_dir) -> list[BundleObject]` built directly on `okf._iter_docs`,
plus a vocabulary resolver `resolve_link_dir(raw)`. `cli/main.py` gains one `@app.command("list")`
that copies `duplicates`'s skeleton: vocabulary refusal, `require_workspace` gate, one call to
`list_objects`, in-memory filter/slice, aligned `typer.echo` rows. No existing module changes
behavior.

## Architecture Decisions

### D1 — Enumerator lives in `src/openkos/bundle/listing.py`

| Option | Tradeoff | Decision |
|---|---|---|
| New function in `model/okf.py` next to `survey_bundle` | `okf.py` is already ~1000 lines and is the OKF *format* module; enumeration for a CLI view is not format knowledge | Rejected |
| Package-root leaf (`listing.py` beside `lifecycle.py`/`sensitivity.py`) | Those two are root leaves for a *specific* reason stated in their own docstrings: both `retrieval/` and `resolution/` consume them, so a root leaf is what avoids a `retrieval`↔`resolution` cycle. `listing` has exactly one consumer (`cli/main.py`) and no cycle to break | Rejected |
| `bundle/listing.py` | Canonical layer, bundle-scoped, imports only `model.okf` + `model.types` + stdlib — the same import shape `bundle/index.py`, `bundle/merge.py`, `bundle/relations.py` already use | **Chosen** |

Layering (AGENTS.md) holds: `bundle` is canonical and depends only on `model`; nothing in the
derived layer (`retrieval`, `graph`, `memory`, `resolution`) is imported or imports back.

### D2 — Row shape, and the duplicated id derivation

```python
@dataclass(frozen=True)
class BundleObject:
    concept_id: str    # scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
    link_dir: str      # first path segment of concept_id, "" for a bundle-root doc
    title: str         # whitespace-collapsed frontmatter title, "" when absent/unreadable
    sensitivity: str   # a SENSITIVITY_ORDER member, else "unknown"
    status: str        # "active" | "deprecated"
    readable: bool     # False when read_error or parse_error was set
```

- `link_dir` is derived **structurally from the id**, never from frontmatter `type`. Decision E
  already establishes that an id's first segment *is* its `link_dir`; deriving it structurally is
  also what lets an unparseable document (no `type`) still be filtered correctly.
- `sensitivity`: `str(meta.get("sensitivity") or "").strip()` when it is one of
  `okf.SENSITIVITY_ORDER`, otherwise the literal `"unknown"`. Deliberately **not**
  `blocks_llm_send`'s fail-closed answer and **not** `okf._rank`'s `private` default: this column
  reports what the document *says*, and inventing `confidential` or `private` for a document that
  declared neither would misinform the owner. It gates nothing, so it must not borrow a gate's
  bias.
- `readable` exists so the CLI can render `(unreadable)` distinctly from `(untitled)` — "the file
  is broken" and "the file has no title" call for different follow-up actions.

**Concept-id derivation duplication: deliberately deferred.** The one-liner is currently spelled
verbatim in `lifecycle.py:70` and `sensitivity.py:116`; `listing.py` makes it a third. Extracting
`okf.concept_id_for(path, bundle_dir)` and migrating all three sites is a ~6-line mechanical
refactor, but it (a) adds public surface to the canonical `okf` API — an unrelated decision
deserving its own change — and (b) destroys this change's rollback plan, which banks on `list`
being additive and touched by nothing else ("`git revert` the commit removes the verb"). A partial
extraction used by `listing.py` only would be worse: three spellings of one rule, one of them
named. `listing.py` therefore carries an inline comment naming both other sites and the follow-up
trigger: **a fourth call site, or the first time the derivation itself changes.**

### D3 — Single-walk enforcement and its tests

Enforcement is structural: `list_objects` contains exactly one `for scan in
okf._iter_docs(bundle_dir):` loop and calls no other bundle-reading helper; the CLI body calls
`list_objects` exactly once and nothing else that touches disk.

Three tests, all using a **non-generator** counting wrapper so the call is recorded at call time
rather than at first `next()`:

```python
calls: list[Path] = []
original = okf._iter_docs

def _counting_iter_docs(bundle_dir: Path) -> Iterator[okf.DocScan]:
    calls.append(bundle_dir)
    return original(bundle_dir)          # plain function returning the iterator, NOT `yield from`

monkeypatch.setattr(okf, "_iter_docs", _counting_iter_docs)
```

Patching the attribute on the `okf` module works for every consumer because they all do
`from openkos.model import okf` then `okf._iter_docs(...)` (existing precedent:
`tests/unit/test_sensitivity.py:103-114`).

| Test | Location | Assertion |
|---|---|---|
| Enumerator walks once | `tests/unit/bundle/test_listing.py` | `list_objects(bundle_dir)`; `len(calls) == 1` |
| Command walks once | `tests/unit/cli/test_list.py` | `CliRunner` invokes `list`; `len(calls) == 1` |
| `lifecycle` is never called | `tests/unit/cli/test_list.py` | `monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _fail)` where `_fail` raises `AssertionError`; command still exits 0 |

The third is redundant with the second's count but names the proposal's explicit prohibition, and
fails with a legible message instead of `2 != 1`.

### D4 — Status derived in-pass, cross-checked against `lifecycle`

Inside the same loop, alongside row construction, collect
`supersedes: set[tuple[str, str]]` from `okf.decode_relations(meta)` inside
`try/except ValueError` (malformed `relations:` yields no edges, no crash), dropping self-edges.
After the loop — no second disk walk — `superseded = {target for _, target in supersedes}`, and a
row's status is `"deprecated"` when its own frontmatter `status` equals `"deprecated"` **or** its
id is in `superseded`, else `"active"`. This replicates `lifecycle`'s pinned R2 rule verbatim,
including its no-cycle-detection fail-safe.

That is a second implementation of one semantic, so it gets a drift guard rather than a comment:
a test builds a bundle with own-deprecated, superseded, self-superseding, and cyclic objects and
asserts

```python
{r.concept_id for r in rows if r.status == "deprecated"} \
    == lifecycle.deprecated_concept_ids(bundle_dir) & {r.concept_id for r in rows}
```

(intersected because `lifecycle` can name supersession targets that have no file on disk, which
by definition produce no row).

### D5 — Fail-visible, not fail-safe-by-skipping, and never fail-closed

Rule: a document with `read_error` or `parse_error` is **still listed** — `concept_id` and
`link_dir` come from its path and need no successful read — with `title=""` (rendered
`(unreadable)`), `sensitivity="unknown"`, `readable=False`, and `status` computed the same way
(so another document's `supersedes` edge can still mark it deprecated). Nothing raises.

Direction rationale: `sensitivity.sensitive_concept_ids` fails **closed** because it answers "may
this content leave the machine?", where a wrong `False` leaks. `list` answers "what ids exist
here?", produces output that never leaves the terminal, and gates nothing; failing closed would
*hide* the id of a broken document — the precise opposite of the verb's purpose, and it would hide
exactly the objects most likely to need `forget` or manual repair. So `listing` follows
`lifecycle`'s fail-**safe** direction (never raise), but in a stronger form: `lifecycle` *skips*
the doubtful document because it is building a predicate set; `listing` *includes* it with unknown
fields because the row is the payload.

### D6 — Column formatting

`status`'s `ljust` pattern (`cli/main.py:4989-4992`), widened over the header labels plus the rows
actually printed (post-filter, post-truncation), two spaces between columns:

```python
shown = rows[:limit] if limit is not None else rows
id_w   = max(len("ID"), *(len(r.concept_id) for r in shown))
sens_w = max(len("SENSITIVITY"), *(len(r.sensitivity) for r in shown))
stat_w = max(len("STATUS"), *(len(r.status) for r in shown))
```

Long titles are **not** truncated: `TITLE` is the last column, so its length cannot disturb the
alignment of anything, and truncating would discard text the owner wrote for zero benefit.
Newlines/tabs/whitespace runs **are** collapsed, at row-construction time in `listing.py` via
`" ".join(str(raw).split())`, so the dataclass never carries a control character and the
one-line-per-object contract that makes `cut`/`grep` work cannot be broken by a hand-edited
multiline YAML title. `""` renders `(untitled)` when readable, `(unreadable)` when not. Empty
bundle (or empty filter result) prints a friendly note and exits 0; the width computation is
skipped on that branch, which also avoids `max()` on an empty sequence.

### D7 — Type-filter resolution and the error ladder

```python
_LINK_DIRS: frozenset[str]           = frozenset(ot.link_dir for ot in REGISTRY if ot.link_dir)
_NAME_TO_LINK_DIR: dict[str, str]    = {ot.name: ot.link_dir for ot in REGISTRY if ot.link_dir}
```

**Gotcha, must not be shortcut:** build these from `REGISTRY` directly, *not* from
`types.TYPE_TO_LINK_DIR`, which is `llm_classifiable`-only and therefore omits `Source` — reusing
it would make `list sources` work while `list Source` failed.

`resolve_link_dir(raw) -> str | None`: canonical `link_dir` exact match first, then case-sensitive
`REGISTRY.name` alias, else `None`.

Error ladder, following `set-volatility`'s precedent (`cli/main.py:3545-3559`) — vocabulary
refusal happens **before** any workspace access, so a typo never touches disk:

```
openkos list: refusing to list -- 'Widget' is not a known object type
(expected one of ['concepts', 'decisions', 'entities', 'events', 'organizations',
'people', 'places', 'procedures', 'projects', 'sources']).
```

stderr, exit 1. Only canonical `link_dir` names are enumerated, per decision A. `--limit 0` (or
negative) refuses the same way, same exit code.

> **Refinement of a proposal success criterion.** The proposal says "`require_workspace` failure is
> the only non-zero exit path". That holds for every *bundle-read* outcome, but an unknown TYPE and
> `--limit 0` are usage refusals that the proposal itself already declares invalid. Design resolves
> this as `set-volatility` does: exit 1 on stderr, before any read. The spec must state the ladder
> as **vocabulary/limit refusal → workspace refusal → always 0**, not "one non-zero path".

## Data Flow

```
openkos list [TYPE] [--limit N | --all]
        │
        ▼
  resolve_link_dir(TYPE) ──── None ──▶ stderr refusal, exit 1   (no disk access)
        │ str
        ▼
  config.require_workspace(cwd) ── reason ──▶ stderr refusal, exit 1
        │ None
        ▼
  listing.list_objects(layout.bundle_dir)
        │
        │   ┌──────── ONE okf._iter_docs walk ────────┐
        │   │ per DocScan:  id ← path (always)        │
        │   │               title/sensitivity ← meta  │
        │   │               own status ← meta         │
        │   │               supersedes ← relations    │
        │   └─────────────────────────────────────────┘
        │   post-loop, in memory: status = own=="deprecated" or id ∈ superseded
        ▼
  list[BundleObject]  (already alphabetical — _iter_docs is sorted)
        │
        ▼
  filter by link_dir ──▶ slice to limit ──▶ ljust rows ──▶ typer.echo ──▶ exit 0
                                          └─▶ "Showing 50 of 412 — use --all"
```

### Sequence — `openkos list people --limit 2`

```
 user      cli.list        listing        okf._iter_docs      disk
  │           │               │                 │              │
  ├─ argv ───▶│                                                │
  │           ├─ resolve_link_dir("people") ─▶ "people"        │
  │           ├─ require_workspace(cwd) ─────▶ None            │
  │           ├─ list_objects(bundle) ──────▶│                 │
  │           │               ├─ _iter_docs(bundle) ─────────▶ │
  │           │               │                 ├─ rglob+read ▶│
  │           │               │◀─ DocScan #1 ───┤              │
  │           │               │  (row, supersedes edges)       │
  │           │               │◀─ DocScan #n ───┤   ← ONE walk │
  │           │               ├─ resolve status from superseded set (no I/O)
  │           │◀─ list[BundleObject] (sorted) ─┤               │
  │           ├─ filter link_dir == "people"                   │
  │           ├─ slice [:2], compute column widths             │
  │◀─ header + 2 rows + "Showing 2 of 7 — use --all" ──────────┤
  │◀─ exit 0                                                   │
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/bundle/listing.py` | Create | `BundleObject`, `list_objects`, `resolve_link_dir` (~150 lines incl. this repo's docstring density) |
| `src/openkos/cli/main.py` | Modify | `@app.command("list")` on `def list_objects_cmd(...)` — the function may not be named `list` (shadows the builtin; `set-sensitivity`/`set-volatility` already establish the decorator-name form) |
| `tests/unit/bundle/test_listing.py` | Create | Enumerator, single-walk counter, lifecycle drift guard, malformed docs |
| `tests/unit/cli/test_list.py` | Create | Filter, alias, limit/`--all`, footer, confidential title, deprecated marker, refusals |
| `docs/cli.md` | Modify | `### openkos list` section beside `### openkos status` |
| `openspec/specs/list-command/spec.md` | Create | (owned by `sdd-spec`, merged on archive) |

## Testing Strategy

Strict TDD, RED→GREEN→REFACTOR, `uv run pytest`. Branch coverage ≥ 90 is the gate, so every branch
below needs a named test.

| Layer | What | Approach |
|---|---|---|
| Unit (`test_listing.py`) | id/link_dir/title/sensitivity derivation; root-level doc (`link_dir == ""`); sensitivity `unknown` for absent/blank/garbage/non-string; title whitespace+newline collapse; `readable=False` rows; malformed `relations:` (`ValueError` branch); self-`supersedes` dropped; cycle → all members deprecated; empty bundle → `[]`; alphabetical order | `tmp_path` bundle fixtures mirroring `tests/unit/test_lifecycle.py`; injected `DocScan` with `read_error`/`parse_error` set, per `test_sensitivity.py:103-114` |
| Unit (`test_listing.py`) | Exactly one `_iter_docs` call; deprecated set == `lifecycle.deprecated_concept_ids ∩ row ids` | Non-generator counting wrapper (D3); direct cross-call |
| Unit (`test_listing.py`) | `resolve_link_dir` | `@pytest.mark.parametrize` over all 10 `link_dir`s, all 10 `REGISTRY.name`s (incl. `Source`), `""`, wrong case (`People`, `person`), unknown |
| Unit (`test_list.py`) | Happy path columns/alignment; header; empty bundle note; filter hit and filter-with-zero-matches; alias path; `--limit N`; `--all`; truncation footer present / absent when not truncated; confidential title printed in full; deprecated row marked; `(untitled)` and `(unreadable)` markers; unknown TYPE refusal (exit 1, no disk touch); `--limit 0` refusal; `require_workspace` refusal; one walk; `lifecycle` never called | `CliRunner` on `tests/unit/cli/test_duplicates.py`'s fixture shape |
| Integration / E2E | — | Not enabled in this project (`openspec/config.yaml` → `layers.integration: false`) |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. `list` reads `.md` files and writes to stdout; it spawns nothing,
mutates nothing, and takes no path argument from the user (the bundle dir comes from
`WorkspaceLayout`).

## ADR Gate

**No ADR.** Both conditions must hold; only one does.
1. *Decides a pattern/interface/trade-off?* Marginally yes — module placement, the fail-visible
   direction, an in-pass copy of the deprecation rule.
2. *Hard to reverse?* **No.** The verb is additive and read-only, freezes no on-disk format, no
   serialization contract (`--json` is explicitly deferred exactly so nothing is frozen), and no
   persisted state. Reversal is `git revert` of one commit.

Both must be true to warrant an ADR, and the config says "when in doubt, do not create one". None
is created. The decisions above are recorded here instead, which is where reversible design
decisions belong.

## Migration / Rollout

No migration. No feature flag. No on-disk change. Rollback is `git revert` of the commit(s) —
preserved intact by D2's decision not to touch `lifecycle.py`/`sensitivity.py`.

## Delivery

Revised forecast against the proposal's ~600 and the 800-line budget:

| Slice | Lines (add+del) |
|---|---|
| `bundle/listing.py` | ~150 (this repo's docstrings run 3-5× the code) |
| `cli/main.py` `list` command | ~100 |
| `tests/unit/bundle/test_listing.py` | ~220 |
| `tests/unit/cli/test_list.py` | ~200 |
| `docs/cli.md` | ~30 |
| spec | ~160 |
| **Total** | **~860** |

The proposal's ~600 **under-counts**, mostly on test volume (the 90% branch gate plus the two
walk-discipline tests plus the lifecycle drift guard) and on docstring density, which is a hard
convention here, not padding.

- `400-line budget risk: High`
- `Chained PRs recommended: Yes`
- `Decision needed before apply: No` (cached `delivery_strategy: auto-chain` already resolves it)

**Take the proposal's own fallback split**, stacked:
- **PR1** — `src/openkos/bundle/listing.py` + `tests/unit/bundle/test_listing.py` (~370 lines).
  Autonomous: the enumerator is fully testable with no CLI, verified by `uv run pytest` + ruff +
  mypy. Rollback: revert; nothing imports it yet.
- **PR2** — `cli/main.py` command + `tests/unit/cli/test_list.py` + `docs/cli.md` + spec
  (~490 lines), targeting PR1's branch.

## Open Questions

None blocking. One item for `sdd-spec` to reflect: the error-ladder refinement in D7 (the proposal's
"only one non-zero exit path" wording must become vocabulary/limit refusal → workspace refusal →
always 0).
