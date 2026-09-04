# Design: Ingest Application Service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), ingest slice.
Applies ADR-0018 to its second bounded context. No new ADR — see "The one
arguably new element" below.

## Technical Approach

Three phases, in the order the existing code already runs them, with the
adapter keeping every rendering and IO step between them:

```
  cli/main.py::_ingest_single                 application/ingest.py
  ---------------------------------           ----------------------------
  read src, _snapshot_read(concept)  ──text──▶ compose_source_document()  [S3]
                                     ◀─plan──
  #773 gate                          ──text──▶ converged_reingest()       [S3]
                                     ◀─None|─    (adapter echoes + returns)
  stage_notice + Console().status    ──▶
    on_progress=phase_callback(...)  ──────▶ stage_derived_objects()      [S2]
    except OllamaError: <verbatim echo block> ◀─typed staging outcome─
  render notices/drops from the outcome
  _snapshot_read(index), (log)       ──text──▶ compose_catalog_update()   [S3]
                                     ◀─texts─
  guarded_targets, _reject_drifted_targets, fsio.write_*, _autocommit,
  _refresh_derived_after_write, confirm prompt, preview, exit codes
```

The service never renders, never prompts, never writes, and never catches a
backend exception. `extraction/concept.py` is untouched — it is already pure,
and it is this change's version of the trap the query slice hit.

## Architecture Decisions

### Decision: the service returns typed disclosure data; the adapter owns every word

**Choice**: `stage_derived_objects` returns `StagedDerivedObjects` carrying the
`ExtractionReport`, an ordered `tuple[StagingDrop, ...]`, the resolved
`skip_reason`/`notices`, and `lost_in_staging`. All 24 `typer.echo` calls and
the 14 `_*_notice(report)` helpers move verbatim to the call site.

**Alternatives considered**: typed notice *tokens* the adapter maps to strings
(open question 2 in the proposal); leaving the echoes in place.

**Rationale**: this restates ADR-0018's "a service returns typed data carrying
everything an adapter needs to render", and the shipped precedent is
`QueryOutcome.vector_store_unavailable` / `fts_unavailable` — flags whose only
consumer is a renderer. Typed tokens would be a stronger contract for `api`/
`mcp` but would rewrite ~290 output-text assertions to buy it, for consumers
that do not exist yet. `lost_in_staging` is a field rather than a value the
adapter re-derives from `drops`, because #884's counting rule (collisions and
create-only skips are *not* losses) is policy, and policy re-derived at a call
site is policy that drifts.

### Decision: the backend exception propagates; the adapter catches `OllamaError`

**Choice**: the service catches nothing from `llm.chat`. `except OllamaError` —
with its `is_timeout_failure` / `fans_out` #746 advisory — stays at the adapter
call site, verbatim, and the adapter supplies `skip_reason="failed"` itself.
The service owns only three degrade reasons: `no-extractable-text`,
`blocked-by-sensitivity`, `no-concepts-found`.

**Alternatives considered**: the service catches and returns the exception (the
proposal's disposition row); a broad `except Exception`.

**Rationale**: catching `OllamaError` inside `application/ingest.py` requires
importing `openkos.llm.ollama`, a *concrete backend*. That is exactly what
`test_query_module_binds_no_concrete_backend` forbids and what ADR-0018 D1
exists to prevent — an `api` adapter on a different backend would sit under a
catch that can never fire. The proposal's settled constraint is that the
*adapter renders*, and this satisfies it; only the mechanism moves from
"returned exception" to "propagated exception", matching `run_query`'s shipped
D2 posture. A broad `except Exception` would swallow the `ValueError` that
today drops a single candidate.

### Decision: refusals raise; the adapter's existing handler is unchanged

**Choice**: service refusals raise `ValueError`/`OSError` and are caught by the
region's existing `except (OSError, ValueError)` → `typer.echo("openkos ingest:
failed while preparing the ingest -- {exc}.") ` → `raise typer.Exit(code=1)`,
which stays where it is and wraps the service calls.

**Alternatives considered**: a returned error outcome; a new `IngestError` type.

**Rationale**: `stage_filed_answer` already ships this exact shape ("every
refusal raises `ValueError`, caught once at the CLI call site"). The message is
`str(exc)`, so an unchanged handler over an unchanged exception is
byte-identical *by construction* rather than by assertion. A returned error
variant would overload one return type with two purposes — the #773 path
already needs a distinct `None`-vs-value return — and a new exception type adds
a mapping layer whose only job is to be re-caught.

### Decision: `on_progress` is an injected `ProgressHook`, spinner stays in the CLI

**Choice**: `stage_derived_objects(..., on_progress: ProgressHook | None = None)`
where `ProgressHook = Callable[[str], None]` is `extraction.concept`'s own
existing alias (`concept.py:2150`). The service forwards it unchanged to
whichever extractor `union_judge` selected. The adapter builds it:

```python
with Console(stderr=True).status("openkos ingest: extracting concepts…") as status:
    staged = ingest_service.stage_derived_objects(
        ..., on_progress=observability.phase_callback("ingest", status.update)
    )
```

**Rationale**: no new type is invented — `extract_concept` and
`extract_concept_union` already declare `on_progress: ProgressHook | None = None`
(`concept.py:3329`, `:3684`), and `observability.phase_callback` already returns
`Callable[[str], None] | None`. The seam already existed; only its *builder*
moves. Keeping the spinner adapter-side also keeps the four
`monkeypatch.setattr(main, "Console", _FakeConsole)` sites working untouched.

**Ordering invariant that makes this byte-identical**: today *every* echo except
the two pre-extraction degrades happens after the `with` block unwinds. The
adapter preserves that — it renders only after the spinner context exits, on
both the success and the `OllamaError` path.

### Decision: `converged_reingest` replaces the #773 mid-region `return`

**Choice**: a pure predicate-with-payload, not a variant of the composition
return:

```python
@dataclass(frozen=True)
class ConvergedReingest:
    carried_notices: tuple[okf.ExtractionNotice, ...]

def converged_reingest(concept_text: str, *, re_extract: bool) -> ConvergedReingest | None
```

`None` means "fall through to the full run". The adapter maps a non-`None` to
the *same* exit path it uses today:

```python
converged = ingest_service.converged_reingest(concept_text, re_extract=re_extract) \
    if had_prior_source and concept_text is not None else None
if converged is not None:
    typer.echo("openkos ingest: source unchanged and already extracted; ...", err=True)  # verbatim
    return _SingleIngestOutcome(regenerated=True, extraction_degraded=False,
                                extraction_skipped=True,
                                extraction_notice=converged.carried_notices)
```

**Alternatives considered**: a `converged` discriminator on the composition
outcome; leaving the gate in the adapter.

**Rationale**: the gate is three policy decisions (unparseable frontmatter falls
through, a legacy Source with no `origin_key` falls through, retryable debt
falls through) plus the `_carried_extraction_notice` narrowing — all pure over a
`Mapping`, all belonging to the service. A discriminator would force the
composition function to have a "did nothing" state, and the adapter would still
need the branch. `_extraction_retry_due` and `_carried_extraction_notice` move
with it and are imported back by `main.py`; that keeps **one** definition, which
matters because `_reingest_will_skip` (`main.py:5300`, the batch cost-gate
predictor, out of scope) already calls `_extraction_retry_due` and its docstring
calls it "the shared predicate".

### Decision: the layering guard is generalized, not copied

**Choice**: rewrite `tests/unit/application/test_layering.py` to iterate
`src/openkos/application/*.py` instead of a hardcoded `_QUERY_MODULE`, and widen
the offender set to `openkos.cli*`, `typer`, and `rich`. Add
`test_main_no_longer_exposes_the_extractor_names`.

**Rationale**: ADR-0018 states the invariant is "enforced by construction rather
than by a guard", and `docs/architecture.md` records that import-linter is not
wired. A per-module copy of the guard means the *third* context (lifecycle)
ships unguarded by default; iterating the directory means the guard covers a new
module the moment the file exists. `openkos.llm.*` stays pinned to exactly
`openkos.llm.base` — which is what forces the propagate-don't-catch decision
above to be real rather than aspirational.

**Allowed imports for `application/ingest.py`**, read as ADR-0018's list of
layers *below* `application/`: `model`, `bundle`, `config`, `fsio`,
`extraction`, `sensitivity`, `source_title`, and `openkos.llm.base` only.

## Interfaces / Contracts

```python
# src/openkos/application/ingest.py  (S1 creates the module + the first three)

@dataclass(frozen=True)
class DerivedPlan:                     # cli/main._DerivedPlan, moved verbatim
    doc_type: str; section: str; link_dir: str; slug: str; title: str
    description: str; path: Path; content: str
    disambiguated_from: str | None; type_alternative: str | None
    sensitivity: str; type_floor_raised: bool

def collision_family(link_dir: Path, base_slug: str) -> list[Path]: ...
def family_owns_source(family: list[Path], source_slug: str) -> bool: ...
def first_free_disambiguated_slug(family, base_slug, reserved) -> str: ...

# S2
DropKind = Literal["empty-slug", "in-batch-collision", "already-exists",
                   "disambiguated", "build-failed"]

@dataclass(frozen=True)
class StagingDrop:
    kind: DropKind
    slug: str                     # the slug the decision was about
    disambiguated_to: str | None = None   # "disambiguated" only
    error: str | None = None              # "build-failed" only: str(exc)

@dataclass(frozen=True)
class StagedDerivedObjects:
    plans: tuple[DerivedPlan, ...]
    skip_reason: okf.ExtractionStatus | None
    notices: tuple[okf.ExtractionNotice, ...]
    report: ExtractionReport | None   # None on the two pre-extraction degrades
    drops: tuple[StagingDrop, ...]    # loop order — the render order
    lost_in_staging: int              # #884 counting policy, not re-derivable

def stage_derived_objects(*, raw_content: str | None, source_title: str,
    source_slug: str, workspace_floor: str, stamp_sensitivity: str,
    timestamp: str, bundle_dir: Path, llm: LLMBackend, cfg: config.Config,
    include_confidential: bool = False, union_judge: bool = False,
    on_progress: ProgressHook | None = None) -> StagedDerivedObjects: ...
    # Raises: OllamaError family (propagated), never caught here.

# S3
@dataclass(frozen=True)
class SourceDocumentPlan:
    title: str; description: str; resolved_sensitivity: str
    on_disk_sensitivity: object | None; on_disk_title: object | None
    source_sensitivity: str          # read back from the rendered frontmatter
    content: str

def compose_source_document(*, raw_content: str | None, source_stem: str,
    source_display_path: str, resource: str, origin_key: str,
    concept_text: str | None, cfg: config.Config, timestamp: str
) -> SourceDocumentPlan: ...
    # concept_text is None <=> no prior Source (the adapter's `had_prior_source`)

@dataclass(frozen=True)
class CatalogUpdate:
    concept_content: str; new_index_text: str; new_log_text: str

def compose_catalog_update(*, source: SourceDocumentPlan,
    staged: StagedDerivedObjects, slug: str, resource: str, index_text: str,
    log_text: str, regenerate: bool, timestamp: str, entry_date: date
) -> CatalogUpdate: ...
    # Owns the conditional re-render (skip_reason or notices) and the
    # derived_plans index/log loop incl. the disambiguation audit bullet.
```

**D2 in the signatures**: every parameter above is `str` or a value, never a
path to read. `_snapshot_read` appears nowhere in `application/`; the adapter
keeps its three calls (concept, index, log) and retains the bytes for
`guarded_targets`.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/application/ingest.py` | Create | The service; S1 seeds it, S2 and S3 fill it |
| `src/openkos/cli/main.py` | Modify | `_stage_derived_objects` and the plan-composition core leave; the echo blocks stay and move to the call site; `extract_concept`/`extract_concept_union` imports deleted |
| `tests/unit/application/test_layering.py` | Modify | Generalize to every `application/*.py`; add `typer`/`rich`; add the extractor-name absence test |
| `tests/unit/application/test_ingest.py` | Create | Filesystem-free unit tests for the service |
| `tests/unit/cli/test_ingest.py` | Modify | 20 call-site repoints, 6 `plans == []` → `== ()`, 1 computed-name monkeypatch, new characterization goldens |
| `src/openkos/lint.py`, `extraction/evidence.py`, `model/okf.py` | Modify | Docstring references to `cli/main._extraction_retry_due` / `_carried_extraction_notice` repointed (S3) |

## Slice Plan

`delivery_strategy` is `auto-chain`; `review_budget_lines` is 2000. Each slice
leaves `openkos ingest <file>` and `--batch` fully working.

| Slice | Content | Est. lines | Still works because |
|---|---|---|---|
| S1 | Create `application/ingest.py` with `DerivedPlan` + the three collision helpers; generalize the layering guard. `main._stage_derived_objects` imports them and is otherwise untouched. | 250–350 | Zero behaviour change, zero call-site repoints — the helpers are internal to a function that stays put |
| S2 | De-present and move `stage_derived_objects`; typed drops/report/notices; `on_progress` injection; adapter renders; 20 call-site repoints; characterization goldens | 1,100–1,500 | `_ingest_single` calls the service and renders in the same order; `_ingest_batch` untouched |
| S3 | `converged_reingest`, `compose_source_document`, `compose_catalog_update`; `_chat_client` construction moves up; D2 text-passing; `_extraction_retry_due`/`_carried_extraction_notice` move + 3 doc references | 700–1,000 | The write/guard/preview/confirm shell is unchanged; only its inputs are computed elsewhere |

Total 2,050–2,850 across three PRs, each under the 2,000-line budget.
`Decision needed before apply: No` (auto-chain). S1 or S2 alone is a valid
stopping point.

**Why S1 splits off** without violating D1's "move and de-present in the same
slice": D1's cost argument is about touching the call sites twice. S1 touches
**zero** call sites — it relocates helpers that `_stage_derived_objects` calls
internally. The de-presentation and the move still land together, in S2.

## Test Migration Plan

**The search method matters more than the count.** ADR-0018 records a 124th
patch site that no string-literal grep could find. Ingest's is worse: the name
is *computed*.

| Step | Command | Measured result |
|---|---|---|
| 1 | `rg -n 'main\._stage_derived_objects\(' tests/` | **20** call sites, all in `tests/unit/cli/test_ingest.py` |
| 2 | `rg -n 'plans == ' tests/unit/cli/test_ingest.py` | **6** sites → `outcome.plans == ()` |
| 3 | `rg -n '"openkos\.cli\.main\.(extract_concept\|extract_concept_union)"' tests/` | **0**. A grep-only survey would conclude there is no risk. That conclusion is wrong. |
| 4 | `rg -nU 'monkeypatch\.setattr\(\s*(main\|module)\s*,' tests/` | 40 repo-wide, 7 in `test_ingest.py` — and **still misses the extractor sites** |
| 5 | Read `tests/unit/cli/test_ingest.py:8862-8863` | `target = "extract_concept_union" if union_judge else "extract_concept"` then `monkeypatch.setattr(main, target, ...)`. Invisible to every literal-name search in steps 3 and 4. |

**So the guarantee is mechanical, not a grep.** `extract_concept` and
`extract_concept_union` have exactly **one** production use in `main.py`
(`:4360`). Once the extractor-selection line moves, both imports are unused,
`ruff check .` (F401) forces their deletion, and `main` no longer carries the
attribute — so `monkeypatch.setattr(main, target, ...)` raises `AttributeError`
under pytest's default `raising=True` instead of silently no-opping. ADR-0018's
failure mode is structurally unavailable here, and
`test_main_no_longer_exposes_the_extractor_names` asserts
`not hasattr(main, "extract_concept")` so a future re-import cannot re-open it.

**Cost lever**: all 20 sites pass `**_stage_kwargs(tmp_path, ...)`
(`test_ingest.py:1139`), so a signature change is **one** edit. Per site only
the module prefix and the return unpacking change.

**Seams that survive untouched** (a design outcome, not luck):
`monkeypatch.setattr(main, "Console", ...)` ×4 — spinner stays adapter-side;
`monkeypatch.setattr(main, "_snapshot_read", ...)` (`:5877`) — D2 keeps it
adapter-side; `monkeypatch.setattr(main, "_autocommit", ...)` (`:5257`) — write
infrastructure stays put.

| Layer | What to test | Approach |
|---|---|---|
| Unit (service) | Drop ordering, `lost_in_staging` counting, degrade reasons, `converged_reingest` fall-through arms, catalog text composition | `tests/unit/application/test_ingest.py`, no Typer runner, no filesystem except a `tmp_path` bundle for collision scans |
| Unit (layering) | No `openkos.cli`/`typer`/`rich`; `openkos.llm.base` only; shared write helpers single-defined; extractor names absent from `main` | AST scan over `application/*.py` |
| Characterization | Full stdout + stderr + exit code per scenario | Committed goldens (below) |
| Integration | The 307 existing `test_ingest.py` tests, with their ~290 output-text assertions **unmodified** | `uv run pytest` |

## How Byte-Identical Output Is Proven

Assertion is not proof, and the existing 290 assertions pin *wording*, not the
*stream*. The proof has three parts:

1. **Full-stream goldens, generated before the move.** A characterization test
   captures complete `stdout`, complete `stderr` and the exit code for a
   scenario matrix, generated on the pre-move tree in S2's first commit and
   compared byte-for-byte thereafter. Matrix (one per rendering branch):
   healthy multi-object; `no-extractable-text`; `blocked-by-sensitivity`;
   `OllamaError` degrade; the same **with** the #746 concurrency advisory arm
   (`concurrent_extraction` on + timeout + `fans_out`) and **without**;
   `no-concepts-found`; each of the five staging drops; the `lost_in_staging`
   summary; the #773 convergence skip; the `(OSError, ValueError)` refusal.
2. **Falsification, because a test that passes first try proves nothing.**
   Mutate exactly one character in exactly one *relocated* echo string, purge
   `__pycache__` (a same-size mutation otherwise runs stale bytecode and the
   verdict is fiction), run `uv run pytest`, confirm RED, revert with the
   inverse replace — never `git checkout --`. Repeat for one drop message and
   one notice line. A golden that cannot go red is a golden that proves nothing.
3. **Filesystem identity.** `snapshot_bytes` / `snapshot_with_mtime`
   (`tests/unit/cli/conftest.py`) already pin that a refused run wrote nothing;
   the refusal scenario in the matrix reuses them, so "same stderr" cannot hide
   "different disk".

Strict TDD holds: RED-first for the genuinely new behaviour (the typed returns,
the layering guard, `converged_reingest`'s arms); the goldens are
characterization pins for behaviour that must not change, and step 2 is what
makes them non-vacuous. Branch coverage stays above 90% — the service's arms are
now reachable without driving a Typer runner, which is ADR-0018's stated
coverage benefit.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary is introduced or changed.
`_autocommit` (the one VCS touchpoint in the region) stays adapter-side,
unchanged, and is called through rather than forked; the layering guard's
single-definition assertion is what keeps that true.

## Migration / Rollout

No migration. No data, on-disk format, knowledge model, or CLI surface change.
Additive-then-subtractive per slice; reverting a merge commit restores
`cli/main.py` in full. Commits use `Refs #918`, never `Closes` — #918 stays open
for the lifecycle context.

## Open Questions

- [ ] None blocking. Proposal open questions 1 and 3 are resolved by the session
      settings (slices may land standalone; byte-identical output is a hard
      requirement) and question 2 by the first decision above (adapter-rendered
      wording, not typed tokens).
