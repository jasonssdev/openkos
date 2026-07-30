# Design: `openkos next` — deterministic pointer to the one action worth taking

## Technical Approach

A new CLI-layer module, `src/openkos/cli/next_action.py`, owns an ordered tuple of tier
callables plus one lazily-memoized signal holder. `cli/main.py` gains only the workspace gate,
one call, and the echo loop. Every signal comes from a function already shipped
(`vector_store_is_empty`, `lint.collect_docs` + its two checks, `find_exact_title_groups`); no
walk logic is duplicated and `status` (`main.py:5209-5353`) is not touched.

The cost contract is enforced **structurally**, not by discipline: `_BundleSignals` is the only
object holding a `Path`, and tier callables receive only `_BundleSignals`. A tier that never
receives a directory cannot open a walk — the same pinned-signature guard `lint.py:615-619`
already documents for `check_unextracted(docs)`.

## Architecture Decisions

### Decision: the tier engine lives in `src/openkos/cli/next_action.py`

| Option | Trade-off | Verdict |
|---|---|---|
| Inline in `cli/main.py` | File is already 6000+ lines; command bodies stay thin by convention | Rejected |
| New top-level `openkos/next_action.py` | Would be the **first** top-level module importing the derived `resolution` package. Verified: `lint.py:21-23`, `lifecycle.py:32`, `sensitivity.py:57` import canonical only, while `resolution/volatility_typing.py:23` imports `lint` — inverting that edge creates real cycle risk | Rejected |
| `cli/next_action.py` | The CLI package is the composition root and already imports all three layers (`main.py:22`, `:53`, `:75`). Precedent: `cli/observability.py` is a CLI-layer helper holding logic that would otherwise pollute `main.py` or break a leaf module's invariant | **Chosen** |

Canonical (`model`, `bundle`, `state`) still never depends on derived; the new edge points
downward from the composition root only.

### Decision: lazy memoized signals, not self-contained tier closures

**Choice**: `_BundleSignals` exposes three memoized properties — `vector_store_empty` (0 walks),
`docs` (1 walk, `collect_docs`), `exact_title_groups` (2 walks, `find_exact_title_groups`).
Tiers 2 and 3 both read `signals.docs`; the memo makes "one `collect_docs()` call" true by
construction (proposal line 155).
**Rejected**: each tier a self-contained closure taking `bundle_dir` — the naive shape that
silently pays two `collect_docs()` calls, the exact trap this decision exists to close.
**Rejected**: staged evaluation with docs passed in — reintroduces caller ordering discipline
and forces tier 1 to see a `docs` argument it must not consume.

### Decision: tiers 2 and 3 read the command out of the finding they already carry

`LintFinding.detail` embeds its command in the first backtick pair (`lint.py:630`, `:729-730`).
`_command_from_detail(detail)` extracts it; the reason line is the detail rendered verbatim.
No re-derivation from `doc.resource` or hardcoded strings, so lint and `next` can never drift.

Two traps this closes:
- `multi-source-uncovered` also contains a backtick command — in a **negating** sentence
  (`lint.py:766-768`). Tier 3 filters `kind == "below-source-sensitivity"` before extraction.
- `check_unextracted`'s empty-`resource` fallback yields the bare `openkos ingest`
  (`lint.py:632`), which is not runnable. Tier 2 accepts only a command carrying an argument;
  if every unextracted finding is a fallback, tier 2 does not fire and evaluation continues.

## Interfaces

```python
@dataclass(frozen=True)
class NextAction:
    command: str   # runnable, verbatim
    reason: str    # one line

Tier = Callable[["_BundleSignals"], NextAction | None]
_TIERS: tuple[Tier, ...]   # D1 order: reindex, ingest, backfill-sensitivity, duplicates

def next_action(layout: config.WorkspaceLayout) -> NextAction | None   # first hit wins
def render_lines(action: NextAction | None) -> list[str]
```

`render_lines` appends `_STATUS_POINTER` ("For everything else, run `openkos status`.") at one
site, after both branches — the honesty guard (D4) is emitted on every path by construction.
No branch ever renders a count of unseen findings (D5). Tier 4's group count is a property of
the finding that fired, not of findings `next` skipped.

## Data Flow

    next → require_workspace → _BundleSignals(layout)
             │
             └→ tier1 vector_store_empty? ─hit→ NextAction ─┐
                tier2 signals.docs ────────hit→ NextAction ─┤
                tier3 signals.docs (memo) ─hit→ NextAction ─┼→ render_lines → echo
                tier4 signals.exact_title_groups ─────────  ┘

| Stops at | `collect_docs` calls | `find_exact_title_groups` calls | Walks |
|---|---|---|---|
| 1 | 0 | 0 | 0 |
| 2 | 1 | 0 | 1 |
| 3 | 1 | 0 | 1 |
| 4 / none | 1 | 1 | 3 |

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/next_action.py` | Create | Signals, tiers, `next_action`, `render_lines` |
| `src/openkos/cli/main.py` | Modify | `next` command: gate, one call, echo loop (~40 lines) |
| `tests/unit/cli/test_next.py` | Create | Order, cost, output, no-backend |
| `docs/cli.md` | Modify | New verb entry |

## Testing Strategy

Follows `test_status.py:414-425` exactly: a plain-function counting wrapper installed via
`monkeypatch.setattr` on a **public module attribute** — never a private internal, never an
rglob counter.

| Seam patched | Asserts |
|---|---|
| `openkos.cli.next_action.lint_check.collect_docs` | ==0 at tier 1, ==1 at tiers 2/3/4 |
| `openkos.cli.next_action.find_exact_title_groups` | ==0 for tiers 1-3, ==1 at tier 4 |
| `openkos.cli.next_action.vector_store_is_empty` | tier-1 state machine |
| `openkos.cli.main.OllamaClient` (sentinel raising on `__init__`) | never constructed |
| `openkos.cli.main.build_graph`, `okf.survey_bundle` | ==0 on every path |

Vector states use the shared `seed_vectors_db` fixture (`tests/unit/cli/conftest.py:25`), and
`_init_workspace` is copied from `test_status.py:37`. Order tests seed a bundle carrying **all
four** findings at once, then peel conditions off one tier at a time. `test_status.py` is not
edited: its unchanged pass is the `status`-untouched regression guard.

### RED-first order (`rules.apply.tdd: true`)

1. Workspace gate (exit 1 outside, 0 inside). 2. Tier order, all-findings bundle, four tests.
3. Cost contract (table above) + `find_exact_title_groups` never called for tiers 1-3.
4. No backend constructed. 5. No-action honesty line; no unseen count. → GREEN: module + verb.
→ REFACTOR, then `docs/cli.md`.

## ADR Gate

**No ADR.** Re-run independently against `openspec/config.yaml` `rules.design`, both conditions
required. (1) Does this decide a technology, pattern, interface, or trade-off? **Yes** — module
placement and the memoized-signals pattern. (2) Is it hard to reverse? **No** — no dependency,
no on-disk format, no protocol, no public API beyond the spec'd output; the whole change is one
new file plus one command, revertible by deleting them. Condition 2 fails, so the gate closes.
The tier order is spec-level WHAT, reversible by editing `_TIERS`.

## Threat Matrix

| Row | Status | Behavior |
|---|---|---|
| Shell / subprocess execution | N/A | `next` executes nothing; it prints a string |
| Untrusted content in printed command | Applicable | `doc.resource` is user frontmatter. `next` prints the detail `lint`/`status` already print verbatim (`lint.py:630`) — no new surface, no new escaping rule. RED test: a Source with a shell-metacharacter `resource` renders verbatim and still exits 0 |
| Routing / VCS-PR automation / executable classification / process integration | N/A | None present |

## Migration / Rollout

No migration. Additive, read-only, single-slice revert.

## Forecast

~450-650 changed lines (module ~200, `main.py` ~40, tests ~280, docs ~25), within the 800-line
budget. Above the 400-line review budget — a trailing docs commit is the split if needed.

## Open Questions

- None blocking.
