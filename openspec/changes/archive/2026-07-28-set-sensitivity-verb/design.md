# Design: `set-sensitivity <concept-id> <level>`

## Technical Approach

`set_sensitivity_cmd` is a structural clone of `relate` (`cli/main.py:2851-3040`): Phase A builds
everything in memory behind `require_workspace` → `_resolve_concept_path` → `load_frontmatter`,
Phase B does `write_atomic(concept)` → `write_atomic(log)` → `_autocommit`. Only two things are new:
`set-volatility`'s exact-equality short-circuit, and a downgrade gate. One new public helper in
`model/okf.py` keeps the rank policy behind the OKF seam.

## Architecture Decisions

### Decision: `okf.sensitivity_direction(current, target)`, not a public `rank`

**Choice**

```python
def sensitivity_direction(current: object, target: str) -> Literal["raise", "same", "lower"]:
    """Classify assigning `target` over `current`, ranking `current` fail-closed (ADR-0003)."""
```

Body is `_rank(target)` vs `_rank(current)`; `_rank` stays private.

| Option | Tradeoff | Verdict |
|---|---|---|
| CLI imports `okf._rank` | Breaks the seam (AGENTS.md:41); private symbol in a second layer | Rejected |
| Promote `_rank` → `okf.rank` | Exports an integer, inviting ad-hoc comparisons at call sites — the third alternative ADR-0003 already rejected | Rejected |
| `is_downgrade(...) -> bool` | Enough for the gate, but the preview then cannot honestly name a `same` (dirty-but-equivalent current) case | Rejected |
| `sensitivity_direction(...)` | Same cost, exports the policy not the number, serves gate **and** preview | **Chosen** |

**Rationale**: one function, one expression, ~3 unit tests; the CLI never learns the ordering.

### Decision: exact-equality idempotence, no strip

`metadata.get("sensitivity") == level` (mirrors `cfg.type_tiers.get(...) == tier`). A dirty value
(`"public "`, `None`, `7`) is never exactly equal, so it never short-circuits — it always reaches the
direction check and is ranked fail-closed. Stripping first would launder malformed frontmatter.

### Decision: flag name `--allow-downgrade`

Existing long flags across `main.py`: `--auto`, `--force`, `--confirm-phrase`, `--scope`,
`--include-confidential`, `--include-deprecated`, `--json`, `--apply`, `--apply-same`,
`--confirm-count`, `--same-only`, `--all`, `--winner`, `--limit`, `--save`, `--title`,
`--description`, `--type`, `--model`, `--embedding-model`, `--version`. No collision.
`--force` was rejected: it already means three different things (`forget`, `purge`, `reconcile`) and
names no act. `--allow-downgrade` names exactly the act it authorizes.

### Decision: the preview labels the direction in words

The confirm prompt is the whole friction budget for an interactive downgrade, so the direction must
be readable, not inferred from an ordering the user is expected to remember:

```
  ~ bundle/{id}.md (sensitivity: lowering 'confidential' -> public)
```

Word by direction: `raise`→`raising`, `lower`→`lowering`, `same`→`normalizing`. `current` is rendered
`!r` so a dirty value shows as `None` / `'public '` / `7` rather than being silently prettified.

### Decision: gate placement

Phase A order, all before any write:

1. Level vocabulary check against `okf.SENSITIVITY_ORDER` (pure, pre-workspace — `set-volatility`'s shape)
2. `require_workspace` → `read_config`
3. `_resolve_concept_path`
4. read text → `okf.load_frontmatter`
5. **idempotence short-circuit** → message, exit 0, no write, no commit
6. `direction = okf.sensitivity_direction(current, level)`
7. **downgrade gate**: `prompt_will_run = not auto and cfg.review`; refuse if
   `direction == "lower" and not prompt_will_run and not allow_downgrade`
8. build new document text + log entry in memory
9. preview → confirm gate (`--auto` / `review: false` / TTY / non-TTY refusal)
10. Phase B

The gate sits at (7) because it needs both the parsed current value and `cfg.review`, and because a
refused unattended downgrade must print no preview — the refusal is terminal.

## Data Flow

    argv ──→ level vocab ──→ require_workspace ──→ _resolve_concept_path ──→ load_frontmatter
                                                                                    │
                          exit 0 ←── idempotent? ←──────────────────────────────────┤
                                                                                    ▼
                       exit 1 ←── downgrade gate ←── okf.sensitivity_direction ── current
                                                                                    │
      dump_frontmatter + insert_log_entry ──→ preview ──→ confirm ──→ write ×2 ──→ _autocommit

## Interfaces / Contracts

Literal strings (binding):

| Surface | Text |
|---|---|
| Short help | `Set the 'sensitivity' frontmatter field of exactly one concept.` |
| Help/`docs/cli.md` honesty line | `Writes only the named concept: no sibling and no derived object is updated, and no source is re-stamped.` |
| Idempotent | `openkos set-sensitivity: 'bundle/{id}.md' is already {level!r}; no change made.` |
| Downgrade refusal | `openkos set-sensitivity: refusing to lower {id} from {current!r} to {level} without confirmation -- the confirm prompt is disabled (--auto, or config review: false); re-run with --allow-downgrade.` |
| Success | `openkos set-sensitivity: set 'bundle/{id}.md' sensitivity to {level} (log.md updated). Only this concept was changed; no sibling or derived object was touched.` |
| Log line | `**Set-sensitivity**: Set [{id}](/{id}.md) sensitivity to {level!r} (was {current!r}).` |
| Commit | `openkos: set-sensitivity {id} -> {level}` |
| Staged paths | `[f"bundle/{id}.md", "bundle/log.md"]` |
| Error ladder | `openkos set-sensitivity: refusing to set -- {exc}.` / `... failed while preparing the set -- {exc}.` / `... failed while writing -- {exc}.` |

The `confidential` transparency NOTICE is inherited from `_autocommit` (`main.py:378,402`). The verb
adds no notice code.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modify | Add public `sensitivity_direction` beside `combine_sensitivity` |
| `src/openkos/cli/main.py` | Modify | `set_sensitivity_cmd`, placed between `relate` and `set_volatility_cmd` |
| `openspec/specs/sensitivity-config/spec.md` | Create | New capability spec |
| `openspec/specs/workspace-autocommit/spec.md` | Modify | Four bounded edits only |
| `docs/adr/0008-human-sensitivity-override.md` | Create | Written in this phase, status `Proposed` |
| `docs/adr/README.md` | Modify | Index row (written in this phase) |
| `docs/cli.md` | Modify | New `### openkos set-sensitivity <id> <level>` section |
| `tests/unit/cli/test_set_sensitivity.py` | Create | Fixtures copied from `test_relate.py` + `_sensitivity_of` |
| `tests/unit/model/test_okf*.py` | Modify | Direction-helper cases |
| `tests/unit/cli/test_main_autocommit.py` | Modify | One shared-contract case |

## Testing Strategy

Strict TDD: every row below is RED first.

| # | Behavior | Weight |
|---|---|---|
| 1 | `sensitivity_direction` — raise / same / lower / `None` / blank / unrecognized / non-string | Load-bearing (pins ADR-0003 reuse) |
| 2 | Lowering under config `review: false` without the flag → exit 1, bundle byte-identical | **Load-bearing — pins the security decision** |
| 3 | Dirty current (`None`, blank, `7`) + target `public` → classified lower → refused unattended | **Load-bearing — pins fail-closed** |
| 4 | Lowering under `--auto` without the flag → exit 1 | Load-bearing |
| 5 | Lowering under `--auto --allow-downgrade` → writes, one commit | Load-bearing |
| 6 | Lowering on a TTY, confirm accepted, no flag → writes | Load-bearing |
| 7 | Raise under `--auto`, no flag → writes | Load-bearing |
| 8 | Idempotent exact-equal → message, exit 0, `_snapshot` unchanged | Load-bearing |
| 9 | Invalid `<level>` refused before any read | Load-bearing |
| 10 | Bad `<concept-id>` (absolute, `..`, reserved, missing) | Droppable to one parametrized case — `_resolve_concept_path` is already covered |
| 11 | Declined TTY confirm → `_snapshot` unchanged | Load-bearing |
| 12 | Non-TTY, no `--auto`, raise → refusal exit 1 | Droppable — shared gate, covered elsewhere |
| 13 | Setting `confidential` emits the existing NOTICE | Load-bearing (proves no re-implementation) |
| 14 | Success message + `--help` contain the honesty line | Droppable to one assertion |
| 15 | Commit message and staged path list exact | Load-bearing |

**Branch coverage (90% branch gate).** Both arms required for: `direction == "lower"` (2:4,7),
`prompt_will_run` (2:5/6), `allow_downgrade` (4:5), idempotence (8: rest), `auto`/`cfg.review`/
`isatty` (6,11,12), preview direction word (6,7 + one `same` case), plus the three helper arms in (1).

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — writes only a resolved `bundle/*.md` concept and `bundle/log.md`; no classification, no execution | — | — |
| Git repository selection | N/A — reuses `_autocommit(root, ...)` unchanged; no new `git -C`/path composition | — | — |
| Commit state | Applicable — verb stages exactly two paths and must produce exactly one commit | Explicit path list, no `commit -a` | Test 15 |
| Push state | N/A — the verb never pushes | — | — |
| PR commands | N/A — no PR automation | — | — |

Path containment (absolute id, `..`, reserved basename) is delegated unchanged to
`_resolve_concept_path`; test 10 asserts the delegation, it does not re-derive the rule.

## Migration / Rollout

No migration required. No new config key, no persisted state. `--allow-downgrade` defaults `False`,
so existing scripts are unaffected unless they were lowering unattended — which is the behavior this
change deliberately closes.

## Cheaper-equivalent notes (size budget)

- Test 10 collapses to one `pytest.mark.parametrize`; test 14 to one assertion on each of two strings.
- No `_apply_frontmatter_field` helper is extracted: two call sites, different shapes (proposal non-goal).
- The direction word is a 3-entry dict literal, not a function.

## Open Questions

None.
