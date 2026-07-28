# Exploration: `set-sensitivity <concept-id> <level>`

Issue #185, scope locked to problem 1. Propagation is #219 and is out of scope here.

## Locked scope

Issue #185 as filed claims sensitivity is inherited by derived objects via a
high-water-mark, and that hand-editing leaves derived objects stale. **That claim
is false.** At `cli/main.py:1660` and `:1674`, ingest stamps the Source concept
and its derived objects each with `cfg.default_sensitivity` — siblings fed the
same constant, not parent and child. There is no source-to-derived edge to
re-propagate. That gap is real but different, and is tracked as #219.

What ships here is the verb: validated, auto-committed, making no propagation
claim.

## Current state

`sensitivity` (`public` / `private` / `confidential`, ordered least-to-most
restrictive in `okf.SENSITIVITY_ORDER` at `model/okf.py:39-40`, per ADR-0003) is
write-only from two paths today:

- `ingest` stamps `cfg.default_sensitivity` verbatim.
- `merge` recomputes the survivor's value via `okf.combine_sensitivity`
  (`okf.py:233`, called at `okf.py:792`), and a citation fold does the same at
  `cli/main.py:5766`.

**No verb lets a human set one existing concept's `sensitivity` directly.** That
is exactly the gap.

`src/openkos/sensitivity.py` (`blocks_llm_send`, `should_block`,
`sensitive_concept_ids`) is a read-only, fail-closed enforcement layer feeding the
six `llm.chat` call sites. `openspec/specs/sensitivity-aware-llm/spec.md`'s eight
requirements are all enforcement, and its Non-Goals explicitly exclude *"any
change to how `sensitivity` is written."* No spec owns the write path.

## Correction: `set-volatility` is not the structural template

`set_volatility_cmd` (`cli/main.py:3044`) takes a **PascalCase concept type** and
writes `type_tiers[<ConceptType>]` into `openkos.yaml` via comment-safe YAML text
surgery. It touches no concept file. It is a workspace-config verb.

`set-sensitivity` mutates **one concept file's frontmatter field**, which is
structurally `relate` (`cli/main.py:2851-3040`). Copying `set-volatility`
literally would target the wrong storage entirely.

What `relate` establishes, and `set-sensitivity` should mirror:

| Step | Mechanism |
|---|---|
| Workspace gate | `config.require_workspace(root)`, then `config.read_config(root)` |
| Concept resolution | `_resolve_concept_path(layout.bundle_dir, concept_id)` (`:1853`) — rejects absolute ids, `..` segments, reserved basenames, and nonexistent files as `ValueError` before any read. Reusable unchanged. |
| Validation first | Before any read or write, exact-match the level against `okf.SENSITIVITY_ORDER` |
| Read-modify-write | `okf.load_frontmatter` → `metadata["sensitivity"] = level` → `okf.dump_frontmatter`. No generic set-one-field helper exists; `relate` hand-rolls the same pattern for `relations:` |
| Log | `bundle_log.insert_log_entry(log_text, date, log_line)`. No `index.md` touch — this edits an existing catalog entry rather than creating one |
| Confirm gate | The standard three-way precedence: `--auto` skips, config `review: false` skips, TTY prompts via `typer.confirm(..., abort=True)`, non-TTY without `--auto` refuses with exit 1 |
| Write | `fsio.write_atomic(concept_path, new_text)` |
| Commit | `_autocommit(root, [...], "openkos: set-sensitivity <id> -> <level>")` |
| Errors | Catch `(OSError, ValueError)`, echo `openkos set-sensitivity: refusing to set -- {exc}.`, `raise typer.Exit(code=1) from exc`. Never a raw traceback |

**Idempotence should follow `set-volatility`, not `relate`.** `relate` appends to a
list and writes even when the edge already exists. Setting a scalar to its current
value is a true no-op, so `metadata.get("sensitivity") == level` should
short-circuit: message, no write, no commit, exit 0.

`okf.combine_sensitivity` is confirmed **not** needed. It folds two values into a
max; this assigns one already-validated literal.

## The workspace floor does not constrain individual writes

`sensitivity-aware-llm/spec.md`'s "Extract Gates on the Workspace Sensitivity
Floor" gates `extract`'s `llm.chat` call when `cfg.default_sensitivity` is
`confidential`. It gates an LLM call, not a value a human may assign. Nothing
enforces `default_sensitivity` as a per-object minimum, so `set-sensitivity` need
not consult it as a floor. Introducing one would be new scope.

## Auto-commit and the log

`workspace-autocommit/spec.md`'s "Post-Phase-B Commit Per Mutating Verb"
enumerates six verbs: `ingest`, `forget`, `relate`, `merge`, `unmerge`,
`reconcile`. Note `set-volatility` is absent even though it calls `_autocommit`
(`cli/main.py:3177-3181`) — the enumeration is already stale. This change needs a
delta adding `set-sensitivity`, and should decide whether to fix the pre-existing
omission or merely note it.

More important: the same spec's **"One-Time Confidential Transparency Notice"**
fires when `_autocommit` stages any concept file whose `sensitivity` is
`confidential`. Setting a concept to `confidential` is precisely that scenario. The
delta spec should cross-reference this requirement explicitly rather than rely on
it silently, because it is easy to assume a new mutating verb must reimplement it.

## Spec capability placement

Follow the volatility precedent exactly: `suggest-volatility` (read, LLM) lives in
`volatility-suggestion/spec.md`; `set-volatility` (write, no LLM) has its own
`volatility-config/spec.md`.

By the same split, target a **new** capability spec
`openspec/specs/sensitivity-config/spec.md`, modeled on `volatility-config`.
Folding it into `sensitivity-aware-llm/spec.md` would contradict that spec's own
Non-Goals.

## Open decision: does lowering deserve more friction than raising?

Not settled here. The evidence:

- **ADR-0003 rejected survivor-wins for `merge`** precisely because it *"can
  silently downgrade a confidential absorbed object into a public survivor"*, and
  states the policy: *"a security field must fail toward more restrictive, never
  less."*
- **`AGENTS.md` non-negotiables**: high-water-mark propagation, and *"Human
  curates, engine maintains. Consequential changes stay reviewable, not silently
  automatic."*
- **Countervailing**: `set-sensitivity` is the human-in-the-loop override verb.
  Unlike `merge`'s automatic recompute, every write already passes the preview and
  confirm gate, or an explicit `--auto` opt-in. A human deliberately lowering
  through an interactive confirm is arguably the reviewable mechanism the
  principle asks for, not a violation of it.

The standard gate is probably sufficient friction. But the proposal must decide
explicitly whether lowering warrants something stronger — a distinct confirmation
phrase, or a `--force`-style requirement under `--auto` — rather than letting the
standard gate be assumed adequate against ADR-0003's "never less" framing.

## Test homes and fixtures

New file `tests/unit/cli/test_set_sensitivity.py`, with fixtures copied from
`tests/unit/cli/test_relate.py`: `_init_workspace`, `_ingest_source`,
`_simulate_tty` (monkeypatches `_NamedTextIOWrapper.isatty`), and `_snapshot` for
untouched-on-refusal assertions. Add a `_sensitivity_of` analog to `_relations_of`.

`tests/unit/cli/test_main_autocommit.py` is the shared autocommit-contract file and
likely needs a case.

## Honesty requirement

Three places a user could reasonably assume propagation: the verb's `--help` text,
the success message after a write, and `docs/cli.md`.

`docs/cli.md` has **no dedicated sensitivity section** today — only scattered
`--include-confidential` flag mentions and `default_sensitivity: private` in the
example config at line 371. Add a `### openkos set-sensitivity <id> <level>`
section modeled on the `set-volatility` entry, and state explicitly, in both the
help text and the doc, that this sets exactly the one named concept and touches no
sibling or derived object. The risk is real because `merge`'s high-water-mark
language sits adjacent in the same document and invites an assumption of symmetry.

## Affected areas

| Area | Change |
|---|---|
| `src/openkos/cli/main.py` | New `set_sensitivity_cmd`, near `relate` / `set_volatility_cmd` |
| `src/openkos/model/okf.py` | None expected; `SENSITIVITY_ORDER` reused as-is |
| `openspec/specs/sensitivity-config/spec.md` | New capability spec |
| `openspec/specs/workspace-autocommit/spec.md` | Delta adding `set-sensitivity` to the verb list and message table |
| `docs/cli.md` | New section |
| `tests/unit/cli/test_set_sensitivity.py` | New test file |

## Approaches

1. **Mirror `relate`'s Phase A/B shape** — recommended. Reuses
   `_resolve_concept_path`, direct frontmatter mutation, `insert_log_entry`, the
   existing confirm precedence, and `_autocommit`. No new shared infrastructure,
   matches an already-reviewed pattern, inherits the error ladder for free. Low
   effort.
2. **Mirror `set-volatility`** — rejected as a category error. It targets
   `openkos.yaml`, not concept frontmatter, and would conflate the per-object field
   with `cfg.default_sensitivity`.
3. **Extract a generic set-frontmatter-field primitive** — rejected as premature.
   Two call sites with different shapes (`relate` mutates a list with dedup
   semantics, this mutates a scalar with short-circuit idempotence). Contradicts the
   project's start-lean convention and widens the blast radius of a narrow change.

## Recommendation

Approach 1, with `set-volatility`'s stricter short-circuit idempotence. Target the
new `sensitivity-config` capability spec. Leave the raise/lower asymmetry as an
explicit decision for the proposal, backed by ADR-0003.

## Risks

- Copying `set-volatility` literally would write to the wrong place.
- `workspace-autocommit`'s verb enumeration is already incomplete; adding
  `set-sensitivity` narrowly is in scope, fully reconciling that spec is not.
- The raise/lower question must be decided explicitly, not resolved by default.
- Help text and docs must not imply propagation.

## Ready for proposal

Yes.
