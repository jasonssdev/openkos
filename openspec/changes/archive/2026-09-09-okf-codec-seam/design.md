# Design: okf-codec-seam

## Technical Approach

One **public** split function in `model/okf.py`, beside its siblings
`load_frontmatter`/`dump_frontmatter`, taking the caller's operator-facing label
as a required keyword. Each bundle module keeps a one-line private wrapper that
binds its own label exactly once. The wrappers are also the **test seam**: every
pin targets them, so they have identical signatures before and after the move and
no test import changes in the move commit.

## Architecture Decisions

### D1 — Signature and home

```python
# src/openkos/model/okf.py, immediately after load_frontmatter (currently :444-447)
_FRONTMATTER_RE: Final = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

def split_frontmatter_verbatim(text: str, *, label: str) -> tuple[str, str]:
    """Split `text` into its frontmatter block (byte-for-byte) and body.
    Never re-dumps... `label` names the file for the operator and becomes the
    refusal's prefix; it is required precisely so a caller cannot inherit
    another caller's name for its own failure.
    Raises ValueError(f"{label}: missing or malformed frontmatter block").
    """
```

`re` is already imported (`okf.py:15`); nothing else is needed. The two local
`_FRONTMATTER_RE` copies (`index.py:15`, `source_titles.py:125`) are deleted, so
exactly one survives repo-wide.

**Why a lost prefix becomes impossible, not merely discouraged.** Four distinct
failure modes, each closed by a named mechanism:

| Failure mode | What stops it | Class |
| --- | --- | --- |
| Caller omits the label and inherits the other module's prefix | there is **no default**; `uv run mypy .` (strict, CI-gated) errors at the call site and the call `TypeError`s before any test runs | static — cannot merge |
| Positional argument slides `text` into `label` | `*` makes `label` keyword-only | static |
| Caller passes a *wrong* label | full-string message assertions on both wrappers (D4.3) | dynamic — the pin |
| The label drifts across `index.py`'s 5 call sites | it is written once per module, in the wrapper (D2) | structural |

A `label: str = "index.md"` default would collapse row 1 back into the silent
reword this change exists to prevent, and is therefore **forbidden** by this
design. Requiredness alone does not catch a *mistyped* label — that is row 3's
job, and it is why the full-string assertions are load-bearing rather than
decorative.

### D2 — Both private wrappers stay

`index.py` and `source_titles.py` each keep `_split_frontmatter_verbatim(text)`,
now a single delegating line:

```python
_FRONTMATTER_LABEL = "index.md"          # "Source document" in source_titles.py

def _split_frontmatter_verbatim(text: str) -> tuple[str, str]:
    return okf.split_frontmatter_verbatim(text, label=_FRONTMATTER_LABEL)
```

| Option | Tradeoff | Decision |
| --- | --- | --- |
| Keep wrappers | logic is single; label written once per module; 6 call sites unchanged; stable test seam across the move | **chosen** |
| Inline `label=` at all 6 call sites | 6 drift surfaces, larger diff, and every pin's import target changes in the move commit | rejected |

The duplication that remains is a one-line label binding, not the regex, the
match, the slicing, or the message construction. That is the whole of what a
consolidation can honestly remove here.

### D3 — Public, and the refusal it answers

`source_titles.py:149-153` refused an import "to avoid cross-module private
coupling". Naming the moved helper `_split_frontmatter_verbatim` in `okf.py` and
importing it from two `bundle` modules would commit exactly the coupling that
comment refused. Publishing it removes the coupling instead of ignoring the
refusal: `load_frontmatter` (247 callers) and `dump_frontmatter` (116) are
already public in this module, so a private twin beside them is the anomaly.
**That docstring paragraph must be deleted in the same commit** — leaving it
would make the source assert a rule the code no longer follows.

Cost accepted: one more in-package name to keep. It is not a CLI/API/MCP surface,
so it is not a public product contract.

### D4 — The three pins, and why their ordering is a hard constraint

All three land in **WU1, one commit, zero `src/` changes**, against the two
existing copies:

1. **Parity** — `tests/unit/bundle/test_frontmatter_split_parity.py`, shaped after
   `tests/unit/vcs/test_scrub_snippet_parity.py`. Parameterized over the pair
   `((index._split_frontmatter_verbatim, "index.md"),
   (source_titles._split_frontmatter_verbatim, "Source document"))` crossed with a
   shared corpus (CRLF body, quoted `okf_version: '0.1'`, `---` inside the body,
   no trailing newline, empty body, non-ASCII, absent block). Asserts an
   **identical** `(block, body)` from both callables, and the **exact full**
   refusal per callable.
2. **Golden** — `tests/unit/model/test_okf_framing_characterization.py` +
   `tests/unit/model/fixtures/okf_framing_goldens.json`, shaped after
   `test_ingest_characterization.py`: a JSON fixture loaded once at import,
   keyed by scenario name, recording the exact `(block, body)`. It additionally
   asserts `block + body == text` for every scenario — the framing invariant that
   is falsifiable independently of the recorded bytes.
3. **Full-string messages** — `pytest.raises(ValueError, match=re.escape(<full
   string>))` for both prefixes. `tests/unit/bundle/test_source_titles.py:194`
   currently matches only the shared half; widen it in place rather than adding a
   second assertion beside a weak one.

**Why earlier.** After the move, both wrappers delegate to the same function, so
the parity test can no longer detect an *implementation* divergence — only a
*label* one. Its power as a divergence detector exists **only on the pre-move
tree**. Landing it after the move asserts the change's central claim against a
tautology.

**Unverifiable if they land late**: that the moved regex and slicing are
byte-identical to both originals; that `index.md:` survived at all (nothing
asserts it today); and that framing is stable across CRLF, quoted scalars, and a
`---` line inside the body.

**Falsification is required, not optional.** Before WU2, each pin is shown RED
against the divergence it guards on the pre-move tree — mutate one copy's regex,
one copy's message prefix — then reverted with the exact inverse replace. Purge
`__pycache__` around each mutation: a same-size mutation runs stale bytecode and
returns a fictional verdict.

### D5 — `log.py` stays a sibling (note only, no code change)

| Axis | `index.py` | `log.py` |
| --- | --- | --- |
| frontmatter | present | absent, banned by OKF §6/§7 |
| heading | `#` topic sections | `##` dated sections |
| vocabulary | canonical topics | ISO dates |
| reversal key | resolved link identity | exact bullet text, LIFO |

`log.py:7` already imports the one rule that genuinely is shared
(`_BULLET_MARKERS`, `_LINK_RE`, `_link_identity`). A single "section" abstraction
would need a frontmatter-optional parameter no caller wants and would
misrepresent `log.md` as an ordinary OKF document.

**To revisit, one of these must become true**: OKF §6/§7 stop banning frontmatter
in `log.md`; or a *third* dated-section consumer appears needing the same LIFO
bullet-text reversal. Neither holds today, and neither is speculative to check.

### D6 — What must not move

`_patch_title_line` (`source_titles.py:161-202`) and its `_TOP_LEVEL_TITLE_RE` /
`_TITLE_DUMP_WIDTH`. Its single-line `yaml.safe_load` answers a *different*
question: is this `title:` a self-contained scalar safe to patch surgically (no
anchor, block scalar, multi-line value, or trailing comment). It exists because
`load_frontmatter`/`dump_frontmatter` do **not** round-trip byte-identically — a
re-dump re-sorts keys, flattens inline `tags`, and turns an ISO-8601 `Z`
timestamp into a `datetime`. Folding it into the moved helper destroys the byte
preservation `source_titles.py` exists to provide. `index.py`'s
`_SECTION_SPLIT_RE`/`_SECTION_HEADER_RE` also stay (out of scope, per proposal).

### D7 — ADR gate: no ADR (proposal verdict upheld)

The gate needs **both** conditions. Condition 1 is arguably met — publishing
`split_frontmatter_verbatim` names an in-package interface. Condition 2 is not:
reversing it is one refactor across 6 call sites, with no persisted format, no
on-disk byte shape, no CLI/API/MCP surface, and no data to migrate. D5 is the
genuine *pattern* decision, but nothing is built on it and reversing it costs one
refactor too. A design note carries both. Writing an ADR "to be safe" pollutes an
append-only record whose value is that every entry was hard to reverse.

## Data Flow

```
    index.py                       source_titles.py
    _FRONTMATTER_LABEL             _FRONTMATTER_LABEL
    = "index.md"                   = "Source document"
        │                                  │
        └── _split_frontmatter_verbatim ───┘        <- stable test seam
                        │  (label bound once per module)
                        ▼
        okf.split_frontmatter_verbatim(text, *, label)
                        │
            _FRONTMATTER_RE.match(text)
                  ├── hit  -> (match.group(0), text[match.end():])
                  └── miss -> ValueError(f"{label}: missing or malformed
                                          frontmatter block")
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `tests/unit/bundle/test_frontmatter_split_parity.py` | Create (WU1) | cross-module parity + both full messages |
| `tests/unit/model/test_okf_framing_characterization.py` | Create (WU1) | golden round-trip pin |
| `tests/unit/model/fixtures/okf_framing_goldens.json` | Create (WU1) | recorded `(block, body)` per scenario |
| `tests/unit/bundle/test_source_titles.py` | Modify (WU1) | `:194` widened to the full string |
| `src/openkos/model/okf.py` | Modify (WU2) | gains `_FRONTMATTER_RE` + public `split_frontmatter_verbatim` |
| `src/openkos/bundle/index.py` | Modify (WU2) | wrapper delegates; local regex removed; 5 call sites unchanged |
| `src/openkos/bundle/source_titles.py` | Modify (WU2) | wrapper delegates; local regex removed; stale "deliberate separate copy" paragraph deleted; `_patch_title_line` untouched |
| `tests/unit/model/test_okf.py` | Modify (WU2) | one direct test: keyword-only `label` is required and reaches the message |

## Testing Strategy

| Layer | What to test | Approach |
| --- | --- | --- |
| Unit (WU1) | both copies split identically; both full messages | parametrized parity over a shared corpus |
| Unit (WU1) | OKF on-disk framing is stable | JSON golden + `block + body == text` |
| Unit (WU1) | pins can go red | manual mutate/revert with `__pycache__` purge, transcript recorded |
| Unit (WU2) | `label` is required and keyword-only | direct call on `okf.split_frontmatter_verbatim`; `mypy .` is the static half |
| Regression | existing `test_index.py` verbatim-preservation and `test_source_titles.py` retitle tests | unchanged, must stay green |

Strict TDD: every pin is written and shown failing before the WU2 edit exists.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. This is a pure-function move
inside the canonical layer.

## Migration / Rollout

No migration. Nothing touches `raw/`, any canonical file on disk, or any public
signature. Layering holds: both `bundle` modules already import
`openkos.model.okf`, which imports nothing from `openkos.bundle`.

**Rollback**: `git revert` the WU2 commit. Both copies return, each module is
self-contained again, and — because every WU1 pin targets the wrappers, not
`okf.split_frontmatter_verbatim` — the parity, golden, and full-string tests stay
green and stay meaningful against two independent copies. Only the WU2 direct
test disappears, and it lives in that same commit. A rollback loses the
consolidation, never the protection.

## Open Questions

None.
