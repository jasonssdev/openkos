# Design: `status` surfaces pending duplicate groups

Add a fourth `needs_attention` source in `status`: one `find_candidates` call, an inline `Tier.HIGH` filter, one line naming `openkos duplicates`. No new symbol, no change to `resolution/`.

## ADR verdict: NO ADR — gate confirmed, not inherited

| Gate | Verdict |
|---|---|
| (1) Decides a technology, pattern, interface, or trade-off? | **Marginally.** Only the tier-filter policy (HIGH-only) is a trade-off; it is a wording/threshold choice inside one command, not a project-wide pattern. |
| (2) Hard-to-reverse? | **No.** |
| Both true? | **No → no ADR.** |

Reversibility evidence: the change adds a read-only `typer.echo` line and no symbol any other module can import. No persisted state, no file format, no config key, no public API. Reverting is deleting the block; `find_candidates` is untouched, so `duplicates`, `adjudicate`, and `merge` behave identically before and after. `rules.design`: "when in doubt, do not create one." Next free ADR number stays 0008.

## Decisions

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D1 | **Filter inline in `status`**: `sum(1 for group in find_candidates(layout.bundle_dir) if group.tier is Tier.HIGH)`. Both `find_candidates` (main.py:53) and `Tier` (main.py:59) are already imported. | (a) A module-private `_exact_title_group_count(bundle_dir)` helper. (b) A new function in `resolution/`. | `duplicates` already computes `high_count = sum(1 for group in groups if group.tier is Tier.HIGH)` at main.py:4744 — inline **is** the established pattern, so a helper would invent a second one for a single caller. (b) is barred by the proposal's non-goal "no change to `find_candidates`". When #195 consolidates the walks, an inline expression moves more easily than a helper carrying its own tests. |
| D2 | **Do NOT reuse `_format_group_tally`.** | Reuse it (main.py:687). | It renders `N candidate group(s) (X exact, Y near)` — it prints the two words the spec forbids and a total. It stays deliberately unused; record this so apply does not "helpfully" wire it up. |
| D3 | **Insertion point confirmed** — after the dangling-reference `extend` (main.py:4564), before the `vectors_missing` assignment (4569). | Inside/after the `vectors_missing` block. | Structural: `find_candidates` reaches only `difflib` (`similarity.py`), never embeddings, so it must be unconditional; sitting *above* the `vectors_missing` variable makes that independence impossible to misread as a gate. Groups content findings (§9, dangling, duplicates) ahead of the infra finding. Position is **not** spec-enforced — tests assert presence/absence only, never absolute ordering, so #195 may reorder. |
| D4 | **The line** (see below). | Issue #186's own "N duplicate groups awaiting adjudication". | That wording is a bare total and points at the Ollama-dependent verb. |

## The line

```python
needs_attention.append(
    f"{exact_title_groups} candidate group{_plural(exact_title_groups)} with "
    "identical titles — run `openkos duplicates` to review."
)
```

Rendered: `1 candidate group with identical titles — run \`openkos duplicates\` to review.`

Modelled on the `vectors.db` line (4571-4574): `<finding> — run \`openkos <cmd>\` (<detail>).` Same em-dash, same backticked command, same imperative.

| Token | Constraint it carries |
|---|---|
| `{n}` + `_plural(n)` | Correct singular/plural via the existing helper (main.py:661). |
| `candidate` | Codebase vocabulary (`CandidateGroup`); keeps the "might be" hedge — `status` never asserts these *are* duplicates. |
| `group` | Same count unit `duplicates` prints, so the two numbers are comparable rather than apples-to-oranges. |
| `with identical titles` | **Load-bearing, two jobs.** (a) Plain-English description of the HIGH tier with no tier label (#192). (b) A *restrictive qualifier* — it scopes the count to a subset, so the line cannot be read as a total. Without it the stem is verbatim `_format_group_tally`'s and would read as `duplicates`'s number. |
| `titles` (plural at every `n`) | A group always has ≥ 2 members, so the phrase stays grammatical at `n == 1`. There is no finite verb, so `_plural()` is the **only** inflection point — no second agreement to get wrong. |
| `to review` | `duplicates` is the REPORT step; the ACTION verb is `merge`, which `duplicates` itself names. |

Absent by requirement: `HIGH`, `LOW`, `exact` (→ "identical"), `near`, and any of `total` / `all` / `found`.

## Docstring change (main.py:4498-4507)

"THREE independent `bundle/**/*.md` walks" → **FOUR**, adding: `resolution.find_candidates` (exact-title candidate groups, #186), run **unconditionally** — unlike the edge-count line it is never gated on `vectors_missing`, because its similarity path is stdlib `difflib` and never touches embeddings; with the default `include_deprecated=False` it also evaluates `lifecycle.deprecated_concept_ids`. Keep verbatim: the "#195 consolidation is out of scope" sentence and the "`status` calls `build_graph` exactly once" guarantee.

## Testing strategy (Strict TDD — every row RED first)

Target `tests/unit/cli/test_status.py`, new marker block after line 279. `_write_doc` does **not** exist there — copy it from `tests/unit/cli/test_duplicates.py:66` (per-module duplication is the existing pattern; `_init_workspace` is already duplicated the same way).

> **Fixture trap:** a fresh `init` leaves `.openkos/vectors.db` absent, which appends the vectors line. **Every test asserting `Nothing needs attention.` MUST take the shared `seed_vectors_db` conftest fixture** (#197) — see test_status.py:516-530.

| # | Behavior | Fixture | Asserts |
|---|---|---|---|
| T1 | Exact-title group surfaced | two `Concept` docs, `Stoicism` / `STOICISM` | line present, `1 candidate group`, `openkos duplicates` named, `Nothing needs attention.` absent, exit 0 |
| T2 | No tier labels | same as T1 | `HIGH`, `LOW`, `exact`, `near` all absent from stdout (scope to the line if another status string collides) |
| T3 | **Near-match only → still all-clear** | `Stoicism` / `Stoic Philosophy` **+ `seed_vectors_db`** | line absent, `Nothing needs attention.` present, exit 0 |
| T4 | No candidate groups | fresh bundle **+ `seed_vectors_db`** | line absent, `Nothing needs attention.` present, exit 0 |
| T5 | Deprecated-only group excluded | `Stoicism` + `STOICISM` with `status: deprecated` **+ `seed_vectors_db`** (mirrors test_duplicates.py:408) | line absent, exit 0 |
| T6 | Plural wording | two distinct exact-title groups | `2 candidate groups` |

**T3 is the highest-value test**: it is the only pin on the overridden HIGH-only decision and the only cover for the filter's false arm. It must not be dropped or merged into T4.

### Branch coverage (90% branch gate)

| New conditional | True arm | False arm |
|---|---|---|
| `if exact_title_groups:` | T1 | T4 |
| `if group.tier is Tier.HIGH` (generator) | T1 | **T3** (and T5, via exclusion before pairing) |
| `_plural(n)` on this path | T6 (`n != 1`) | T1 (`n == 1`) |

## File changes

| File | Action | Change |
|---|---|---|
| `src/openkos/cli/main.py:4564-4569` | Modify | Fourth `needs_attention` source: count + `if` + one `append`. ~6 lines plus a `#186` comment in the style of the `#141`/`#183` comments above it. |
| `src/openkos/cli/main.py:4498-4507` | Modify | THREE walks → FOUR. |
| `tests/unit/cli/test_status.py` | Modify | `_write_doc` helper + T1-T6. |

## Threat matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Read-only `typer.echo` only.

## Migration / rollout

No migration. No state, no config key, no format. Rollback is a single-commit revert of the `main.py` block and its tests.

## Open questions

- [ ] None blocking.
