"""The `doctor` command's read core (issue #995, PR 6 -- the LAST slice of
MVP 3's "prerequisite zero": the read verbs need an application service
before an MCP adapter can be a thin layer instead of a second
implementation).

**Compute-then-render, like `lint`, not `status`.** Verified against
`git show main:src/openkos/cli/main.py`, reading the pre-extraction
`doctor()` body top to bottom: there are exactly THREE `typer.echo` calls
in the whole function, all three AFTER every one of the fifteen checks
below has already run and been appended to `results` -- the version
banner, the `openkos doctor: checking environment at {root}` header, and a
blank line -- immediately followed by the `for r in results: _render_check(r)`
loop and the single `typer.Exit`. Nothing is printed before every check has
run, so (like `lint`, unlike `status`) there is no partial-output property
to preserve and one function, `run_diagnostics`, is the whole service.

**Fifteen checks, not twelve.** The pre-extraction docstring said "runs
ALL twelve checks" -- stale even before this extraction:
`tests/unit/cli/test_doctor.py` already asserted
`result.stdout.count("[PASS]") == 15` on a fully healthy workspace. The
enumeration below is thirteen NUMBERED checks plus two lettered
sub-checks (5b, 7b) = fifteen actual `CheckResult`s. This module's checks
are numbered to match that enumeration; nothing in the count or the
checks themselves changed, only the stale docstring prose is fixed here.

**Error contract: partial, not total -- see `run_diagnostics`' own
docstring for the complete statement, which is authoritative.** In short:
checks 2 and 3 catch their own failure into a `fail` `CheckResult`, and
they are the only guards. Three unguarded workspace reads and the injected
`reset_point_available` callback can raise straight out, and because this
module is compute-then-render any of them destroys every accumulated
result before the adapter renders one. Both facts are faithful to the
pre-extraction body.

Do not read that as "`doctor` guards everything by construction" -- an
earlier draft of this header said exactly that, and it would license an
unwrapped call site the function contract contradicts. The interesting
comparison is still that this is a THIRD shape beside `status`
(propagates) and `lint` (a typed `LintInputUnavailable` for its three
input reads), not that it is safer than either.

`CheckResult` moved into this module alongside
the checks that build it (it is a check's raw OUTCOME, not a chat/embed
seam); `_render_check` stays adapter-side in `cli/main.py` -- it is a
render loop, not a read.

WALL 1 -- the backend seam. `doctor` never calls `.chat()` or `.embed()`;
it calls `client.list_models()` (check 3), the pure `model_tag_matches`
(checks 4, 5, 5b), and `client.locality` (check 11). Neither `LLMBackend`
nor `Embedder` covers any of that, so `openkos.llm.base` gained a THIRD
capability-scoped Protocol, `BackendDiagnostics`, declaring exactly those
two members -- see its own docstring for why it is separate from
`LLMBackend` rather than folded into it. This module takes a
`BackendDiagnostics` as an injected parameter and imports ONLY
`openkos.llm.base` (`tests/unit/application/test_layering.py::
test_application_modules_bind_no_concrete_llm_backend`) -- never
`openkos.llm.ollama`. The CLI adapter builds the one concrete
`OllamaClient` and passes it in; structural typing means it satisfies
`BackendDiagnostics` with no extra construction.

That boundary also had to cover EXCEPTIONS, which the brief for this slice
did not spell out: check 3 must distinguish "nothing is listening"
(`OllamaUnavailable`, whose remediation branches on `shutil.which
("ollama")`) from "the backend answered but errored" (generic
`OllamaError`, no such branch) -- and it cannot do that by naming either
concrete type. `openkos.llm.base` therefore also gained
`BackendError`/`BackendUnavailable`, which `ollama.OllamaError`/
`OllamaUnavailable` now subclass alongside their existing bases (see
`llm/base.py` and `llm/ollama.py`); every existing `except OllamaError`/
`except OllamaUnavailable` call site elsewhere in the codebase is
unaffected; this module catches only the two new generic types. The same
boundary problem applies to `model_tag_matches`: it is a PURE,
backend-agnostic string comparison that checks 4/5/5b need, so it moved
from `ollama.py` into `base.py` too, with `ollama.py` re-exporting it
(and `InstalledModel`/`BackendHostLocality`) so every existing
`from openkos.llm.ollama import ...` call site is unaffected.

`resolve_diagnostic_model` exists ONLY because the CLI adapter must build
the concrete `BackendDiagnostics` client BEFORE it can call
`run_diagnostics` (WALL 1's injection contract), yet check 2's own
`config.read_config` read -- which is what actually DECIDES `model` on the
ordinary path -- lives inside `run_diagnostics`, alongside the
`CheckResult` it produces. Splitting `config.read_config`'s call site in
two is the one place this module's shape is NOT the pre-extraction body's
shape verbatim: `resolve_diagnostic_model` reads `openkos.yaml` once, purely
to hand the adapter a model tag; `run_diagnostics`'s own check 2 reads it
again to build its `CheckResult` and to hand later checks their `cfg`. Both
reads are the same file, read moments apart, in the same process, with no
write in between -- so they always agree in practice -- and this is
plumbing forced by the injection boundary, not a second RENDER-boundary
service call in the `status`-style sense (`run_diagnostics` alone is still
the one call that produces every `CheckResult`).

WALL 2 -- `openkos.vcs`. `test_layering.py` bans `openkos.vcs` from
`application/*` unconditionally (`application/lifecycle.py`'s own module
docstring states the same rule for `merge`/`unmerge`/`forget`/`purge`:
every `vcs_git.*` call stays adapter-side). `doctor` calls four vcs
functions: `git_available()`/`filter_repo_available()` (checks 9, 10,
parameterless `shutil.which` predicates) and, inside check 13's violation
branch only, `repo_root(root)`/`has_reset_point(root)` (used purely to pick
which remediation STRING check 13 prints; they never affect pass/fail).
All four stay adapter-side, matching `lifecycle.py`'s precedent: the CLI
adapter computes three booleans (`git_available`, `filter_repo_available`,
`reset_point_available`, a thunk so its two `git` subprocess calls are
still paid only inside check 13's violation branch) and injects them; this
module never imports `openkos.vcs` and performs no git I/O of its own.
Unlike the two booleans, it is called by this module, inside check 13's
violation branch and nowhere else; `run_diagnostics`' own docstring
carries why.

`okf.survey_bundle` (check 6), `state.vectorstore.probe_vec_loadable`
(check 8), and `bundle.ledger.scan_torn_writes`/`scan_nesting_violations`
(checks 12, 13) are all legal `application/` imports -- none of
`openkos.cli`, `typer`, `rich`, `openkos.vcs` -- so those reads cross into
this module for free, exactly as they do for `status`/`lifecycle`.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.bundle import ledger as bundle_ledger
from openkos.llm.base import (
    BackendDiagnostics,
    BackendError,
    BackendUnavailable,
    InstalledModel,
    model_tag_matches,
)
from openkos.model import okf
from openkos.state.vectorstore import probe_vec_loadable


@dataclass(frozen=True)
class CheckResult:
    """One `doctor` check's outcome (D5): accumulated, never raised, so a
    failure never short-circuits the checks that follow it. Moved verbatim
    from `cli.main.CheckResult` (issue #995, PR 6) -- it is a check's raw
    outcome, gathered alongside the checks that build it, not a rendered
    string; `cli.main._render_check` stays adapter-side and is the only
    thing that turns this into output."""

    label: str
    status: Literal["pass", "fail", "skip"]
    critical: bool
    remediation: str | None = None
    detail: str | None = None


def resolve_diagnostic_model(root: Path) -> str:
    """The `model` tag the CLI adapter probes Ollama with (checks 3/4,
    reachability and model-installed) -- `cfg.model` inside a workspace
    with valid config, `config.DEFAULT_MODEL` otherwise. Mirrors check 2's
    own fallback exactly (see module docstring for why this reads
    `openkos.yaml` a second time), so the client the adapter builds and
    this module's own config-valid check always agree on what was probed.

    Never raises: a missing or malformed `openkos.yaml` degrades to the
    default, exactly as check 2 does."""
    if config.require_workspace(root) is not None:
        return config.DEFAULT_MODEL
    try:
        return config.read_config(root).model
    except (OSError, ValueError):
        return config.DEFAULT_MODEL


def run_diagnostics(
    root: Path,
    *,
    client: BackendDiagnostics,
    git_available: bool,
    filter_repo_available: bool,
    reset_point_available: Callable[[], bool],
) -> tuple[CheckResult, ...]:
    """Run every one of `doctor`'s fifteen checks, in the SAME order the
    pre-extraction command body ran them, WITHOUT rendering a single line
    (see module docstring for why this is one call, not two). `client`,
    `git_available`, `filter_repo_available`, and `reset_point_available`
    are the CLI adapter's injected answers to WALL 1 and WALL 2 above --
    this module performs no network I/O and no `openkos.vcs` I/O of its
    own.

    `reset_point_available` is a CALLABLE, not a `bool`, and the difference
    is load-bearing. The two predicates beside it are `shutil.which` probes
    that cost nothing; this one shells out to `git` twice
    (`repo_root` + `has_reset_point`), and the pre-extraction body paid
    that ONLY inside check 13's `if violations:` branch. Passing a `bool`
    would move both subprocess calls onto every in-workspace `doctor`
    invocation -- a cost added to the command people reach for precisely
    when their workspace is already misbehaving. A thunk keeps the
    adapter owning the `openkos.vcs` call and this module owning WHEN it
    is worth making, so the original cost profile survives the extraction.
    Callback injection across this boundary is the established shape:
    `application.pending.current_finding_digest` returns one for
    `state.findings.open_findings`'s `current_digest` hook.

    THE RAISE CONTRACT, stated completely, because an adapter author
    builds on it and two earlier drafts of this paragraph overstated it.

    Two checks catch their own failure into a `fail` `CheckResult`:
    check 2 (`except OSError, ValueError` around `read_config`) and
    check 3 (`except BackendUnavailable, BackendError` around
    `list_models`). Those mirror the pre-extraction body's three `except`
    clauses, and they are the ONLY guards here.

    FOUR things can raise straight out of this function:

    - `okf.survey_bundle` (check 6), `bundle_ledger.scan_torn_writes`
      (check 12) and `bundle_ledger.scan_nesting_violations` (check 13)
      are unguarded workspace reads, exactly as they were unguarded in
      the pre-extraction body.
    - the injected `reset_point_available` callback is the CALLER's code
      run by this function, and the CLI adapter's implementation lets
      `vcs_git.GitError` propagate on purpose (see its own docstring:
      the pre-extraction body left those two `git` calls unguarded in
      this same branch, and a refactor is the wrong place to change which
      errors become messages).

    Because this module is compute-then-render, ANY of those four
    destroys all fifteen accumulated results before the adapter renders
    one -- an operator on a workspace broken enough to trip them sees a
    traceback, not a partial diagnosis. That is faithful, not new: the
    pre-extraction body accumulated into the same list and echoed only at
    the end, so the same failures lost the same output. It is a real gap
    and it deserves its own issue; closing it means deciding what a
    partial `doctor` run should report, which is a product decision.

    The `client` parameter carries no such caveat: check 3 catches
    `BackendError`/`BackendUnavailable` itself.

    Remediation TEXT lives only here; `llm/` stays config-free (D1)."""
    results: list[CheckResult] = []

    # 1. workspace-initialized (informational)
    workspace_reason = config.require_workspace(root)
    in_workspace = workspace_reason is None
    results.append(
        CheckResult(
            "Workspace initialized",
            "pass" if in_workspace else "fail",
            critical=False,
            remediation=None if in_workspace else "openkos init",
            detail=None if in_workspace else workspace_reason,
        )
    )

    # 2. config-valid (critical, workspace-only; SKIP outside)
    cfg: config.Config | None = None
    if in_workspace:
        try:
            cfg = config.read_config(root)
            results.append(
                CheckResult(
                    "Config valid", "pass", critical=True, detail=f"model {cfg.model}"
                )
            )
        except (OSError, ValueError) as exc:
            results.append(
                CheckResult(
                    "Config valid",
                    "fail",
                    critical=True,
                    remediation="fix openkos.yaml",
                    detail=str(exc),
                )
            )
    else:
        results.append(CheckResult("Config valid", "skip", critical=True))

    model = cfg.model if cfg is not None else config.DEFAULT_MODEL
    embedding_model = (
        cfg.embedding_model if cfg is not None else config.DEFAULT_EMBEDDING_MODEL
    )

    # 3. Ollama-reachable (critical, always)
    reachable = False
    installed: list[InstalledModel] = []
    installed_tags: list[str] = []
    try:
        installed = client.list_models()
        installed_tags = [m.tag for m in installed]
        reachable = True
        results.append(
            CheckResult(
                "Ollama reachable",
                "pass",
                critical=True,
                detail=f"{len(installed)} models",
            )
        )
    except BackendUnavailable as exc:
        if shutil.which("ollama") is None:
            remediation = (
                "no `ollama` binary found on PATH -- install from "
                "https://ollama.com, or if Ollama is already installed "
                "(e.g. the macOS app) start it with `ollama serve`"
            )
        else:
            remediation = "ollama serve"
        results.append(
            CheckResult(
                "Ollama reachable",
                "fail",
                critical=True,
                remediation=remediation,
                detail=str(exc),
            )
        )
    except BackendError as exc:  # non-transport server error
        results.append(
            CheckResult("Ollama reachable", "fail", critical=True, detail=str(exc))
        )

    # 4. model-installed (critical, always; SKIP-blocked if unreachable, D6)
    label = f"Model '{model}' installed"
    if not reachable:
        results.append(
            CheckResult(
                label, "skip", critical=True, detail="blocked: Ollama unreachable"
            )
        )
    elif model_tag_matches(model, installed_tags):
        results.append(CheckResult(label, "pass", critical=True))
    else:
        results.append(
            CheckResult(
                label, "fail", critical=True, remediation=f"ollama pull {model}"
            )
        )

    # 5. embedding-model-installed (informational, always; SKIP-blocked if
    # unreachable, same D6 rationale as model-installed -- one root cause,
    # never double-reported). Reuses the already-fetched `installed` list,
    # constructs no additional client.
    embedding_label = f"Embedding model '{embedding_model}' installed"
    if not reachable:
        results.append(
            CheckResult(
                embedding_label,
                "skip",
                critical=False,
                detail="blocked: Ollama unreachable",
            )
        )
    elif model_tag_matches(embedding_model, installed_tags):
        results.append(CheckResult(embedding_label, "pass", critical=False))
    else:
        results.append(
            CheckResult(
                embedding_label,
                "fail",
                critical=False,
                remediation=f"ollama pull {embedding_model}",
            )
        )

    # 5b. task-models-installed (informational, always; SKIP-blocked if
    # unreachable, same D6 one-root-cause rationale as checks 4 and 5).
    #
    # ONE check covering every per-task model rather than one check per task
    # (issue #513): the check COUNT stays fixed regardless of how many tasks
    # a workspace keys, which is what lets the doctor spec keep pinning a
    # total. Only models DIFFERING from the global tag are examined -- a task
    # resolving `cfg.model` is already covered by check 4, and reporting it
    # twice would double-count one root cause.
    #
    # Informational, never critical: a missing per-task model fails only the
    # stage that named it (#515 decision 2), so `ingest`, `query`, and
    # `adjudicate` all still work. Exiting 1 on a workspace that is fine for
    # every other verb would be a false alarm rather than a diagnosis.
    task_models = {
        task: config.resolve_task_model(cfg, task)
        for task in sorted(config.TASK_MODEL_KEYS)
        if cfg is not None
    }
    if cfg is None:
        task_models = {
            task: tag
            for task, tag in config.DEFAULT_TASK_MODELS.items()
            if isinstance(tag, str)
        }
    extra_models = {task: tag for task, tag in task_models.items() if tag != model}
    task_label = "Task models installed"
    if not extra_models:
        # #650: nothing is packaged anymore, so a stock workspace lands
        # here -- the pass detail is where the recommendation stays
        # discoverable, naming the un-adopted measured upgrade(s) for any
        # task the workspace never keyed in `models:`.
        recommendations = {
            task: tag
            for task, tag in sorted(config.RECOMMENDED_TASK_MODELS.items())
            if cfg is not None
            and task not in cfg.models
            and config.resolve_task_model(cfg, task) != tag
        }
        detail = "none configured beyond the global model"
        if recommendations:
            named = ", ".join(
                f"{task} -> {tag}" for task, tag in recommendations.items()
            )
            detail += f"; optional measured upgrade: {named} (see docs/cli.md)"
        results.append(
            CheckResult(
                task_label,
                "pass",
                critical=False,
                detail=detail,
            )
        )
    elif not reachable:
        results.append(
            CheckResult(
                task_label,
                "skip",
                critical=False,
                detail="blocked: Ollama unreachable",
            )
        )
    else:
        missing = {
            task: tag
            for task, tag in extra_models.items()
            if not model_tag_matches(tag, installed_tags)
        }
        if missing:
            named = ", ".join(f"{task} -> {tag}" for task, tag in missing.items())
            results.append(
                CheckResult(
                    task_label,
                    "fail",
                    critical=False,
                    detail=f"missing: {named}",
                    remediation=" && ".join(
                        f"ollama pull {tag}" for tag in dict.fromkeys(missing.values())
                    ),
                )
            )
        else:
            named = ", ".join(f"{task} -> {tag}" for task, tag in extra_models.items())
            results.append(
                CheckResult(task_label, "pass", critical=False, detail=named)
            )

    # 6. bundle-readable (informational, workspace-only; SKIP outside)
    bundle_empty = False
    if in_workspace:
        survey = okf.survey_bundle(config.WorkspaceLayout(root).bundle_dir)
        # #568: a clean survey counting nothing means a freshly-`init`ed
        # bundle -- checks 7/7b below use this to skip instead of failing
        # with a `reindex` that would build nothing.
        bundle_empty = (
            not survey.findings and survey.sources == 0 and survey.concepts == 0
        )
        if not survey.findings:
            results.append(
                CheckResult(
                    "Bundle readable",
                    "pass",
                    critical=False,
                    detail=f"{survey.sources} sources, {survey.concepts} concepts",
                )
            )
        else:
            results.append(
                CheckResult(
                    "Bundle readable",
                    "fail",
                    critical=False,
                    detail=f"{len(survey.findings)} issue(s)",
                )
            )
    else:
        results.append(CheckResult("Bundle readable", "skip", critical=False))

    _EMPTY_BUNDLE_DETAIL = "empty bundle -- ingest a source first (openkos ingest)"

    # 7. workspace-vectors-present (informational, workspace-only; SKIP
    # outside -- mirrors check 6's workspace-only shape). Distinct from
    # vector-extension-loadable's throwaway `:memory:` probe
    # (`probe_vec_loadable()`, which says nothing about a specific
    # workspace's own index file): this checks whether THIS workspace's
    # `.openkos/vectors.db` exists on disk (purge-transactional-cleanup
    # #142). Staleness (mtime) is deliberately out of scope -- absent-only.
    if in_workspace:
        if config.WorkspaceLayout(root).vectors_db_path.exists():
            results.append(
                CheckResult("Workspace vector index present", "pass", critical=False)
            )
        elif bundle_empty:
            # #568: right after `init` there is nothing to index -- a
            # `[FAIL] -> openkos reindex` at step 5 of the README quickstart
            # reads as a broken install, and `reindex` would build nothing.
            results.append(
                CheckResult(
                    "Workspace vector index present",
                    "skip",
                    critical=False,
                    detail=_EMPTY_BUNDLE_DETAIL,
                )
            )
        else:
            results.append(
                CheckResult(
                    "Workspace vector index present",
                    "fail",
                    critical=False,
                    remediation="openkos reindex",
                )
            )
    else:
        results.append(
            CheckResult("Workspace vector index present", "skip", critical=False)
        )

    # 7b. workspace-fts-present (informational, workspace-only; SKIP
    # outside -- mirrors check 7's shape exactly, for the OTHER derived
    # retrieval store). Issue #553's evidence: `doctor` passed every check
    # while the workspace's first query was about to run dense-only,
    # because nothing here ever looked at `.openkos/fts.db`. Absent-only,
    # like check 7: staleness is `reindex`'s manifest gate's job, and
    # `next` reports it separately (#381).
    if in_workspace:
        if config.WorkspaceLayout(root).fts_db_path.exists():
            results.append(
                CheckResult("Workspace FTS index present", "pass", critical=False)
            )
        elif bundle_empty:
            results.append(
                CheckResult(
                    "Workspace FTS index present",
                    "skip",
                    critical=False,
                    detail=_EMPTY_BUNDLE_DETAIL,
                )
            )
        else:
            results.append(
                CheckResult(
                    "Workspace FTS index present",
                    "fail",
                    critical=False,
                    remediation="openkos reindex",
                )
            )
    else:
        results.append(
            CheckResult("Workspace FTS index present", "skip", critical=False)
        )

    # 8. vector-extension-loadable (informational, always; NO SKIP branch --
    # unlike embedding-model-installed, this shares no root cause with any
    # other check: it depends only on the local Python/SQLite build, never
    # on workspace state or Ollama reachability). Probes a throwaway
    # `:memory:` connection -- creates no files (Doctor Is Read-Only).
    if probe_vec_loadable():
        results.append(CheckResult("Vector extension loadable", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "Vector extension loadable",
                "fail",
                critical=False,
                remediation=(
                    "run openkos with an extension-capable Python interpreter "
                    "(e.g. a uv-managed interpreter) that supports SQLite "
                    "extension loading"
                ),
            )
        )

    # 9. git-available (informational, always; NO SKIP branch -- shares no
    # root cause with any other check; exists for the not-yet-wired `purge`
    # verb, privacy-purge Slice 1 PR2). `git_available` is the ADAPTER's
    # already-computed `vcs_git.git_available()` result (WALL 2) -- this
    # module performs no `openkos.vcs` I/O of its own.
    if git_available:
        results.append(CheckResult("git available", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "git available",
                "fail",
                critical=False,
                remediation=(
                    "install git (e.g. https://git-scm.com/downloads, or "
                    "`brew install git`)"
                ),
            )
        )

    # 10. git-filter-repo-available (informational, always; NO SKIP branch)
    if filter_repo_available:
        results.append(CheckResult("git-filter-repo available", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "git-filter-repo available",
                "fail",
                critical=False,
                remediation=(
                    "install git-filter-repo (e.g. `pip install git-filter-repo`, "
                    "or `brew install git-filter-repo`)"
                ),
            )
        )

    # 11. backend-host-locality (informational, always; ALWAYS EMITTED).
    # Reuses the SAME `client` check 3 called, so what is reported is the
    # host `doctor` itself would have sent to, never a re-derivation. It
    # skips when Ollama is unreachable despite being ABLE to answer without
    # the server: locality is a literal-form check over the host the client
    # already resolved, so the skip is about how the line READS beside a
    # failure, not about an inability to answer.
    #
    # NEVER `fail` (issue #240): `[FAIL]` on a non-local backend would call a
    # legitimate configuration broken, and a failing status invites a future
    # reader to make this check critical, which would let an informational
    # report flip an exit code that scripts gate on.
    locality = client.locality
    exemption_enabled = (
        cfg.confidential_local_exemption
        if cfg is not None
        else config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
    )
    where = "this machine" if locality.is_local else "not this machine"
    exemption_state = (
        "active" if (locality.is_local and exemption_enabled) else "inactive"
    )
    # The detail differs per branch so the configured host survives the skip:
    # only the claim that anything was VERIFIED goes away (#389).
    results.append(
        CheckResult(
            "Backend host locality",
            "pass" if reachable else "skip",
            critical=False,
            detail=(
                f"{where} ({locality.display_host}); confidential local "
                f"exemption {exemption_state}"
                if reachable
                else (
                    f"configured for {where} ({locality.display_host}); not "
                    "verified while Ollama is unreachable; confidential local "
                    f"exemption {exemption_state}"
                )
            ),
        )
    )

    # 12. merge-ledger-torn-writes (informational, workspace-only; SKIP
    # outside -- Check A, design Decision 5: mechanically exact, zero false
    # positives/negatives). A `.pending` marker means a two-phase write was
    # interrupted mid-flight; `doctor` PREVIEWS what `recover` would decide
    # (`bundle_ledger.scan_torn_writes`, read-only) but never repairs. The
    # remediation names `merge`/`unmerge` -- whose `bundle_ledger.recover`
    # pass is what actually resolves a pending marker -- NOT `openkos
    # repair`, whose Gate 1 refuses outright while any marker is pending
    # (#603: the old text sent the operator in a circle).
    if in_workspace:
        torn = bundle_ledger.scan_torn_writes(config.WorkspaceLayout(root).bundle_dir)
        if torn:
            results.append(
                CheckResult(
                    "Merge ledger torn writes",
                    "fail",
                    critical=False,
                    detail=f"{len(torn)} pending marker(s)",
                    remediation=(
                        "run `openkos merge` or `openkos unmerge` on the "
                        "affected survivor -- its recovery pass resolves "
                        "the pending marker; the repair verb refuses while "
                        "one is pending"
                    ),
                )
            )
        else:
            results.append(
                CheckResult("Merge ledger torn writes", "pass", critical=False)
            )
    else:
        results.append(CheckResult("Merge ledger torn writes", "skip", critical=False))

    # 13. merge-ledger-entries-free-of-post-merge-mutation (informational,
    # workspace-only; SKIP outside -- Check B, design Decision 5: doctor-
    # command spec "Merge-Ledger Integrity Check"). Nested-prefix equality
    # over every committed sidecar (`bundle_ledger.scan_nesting_violations`,
    # read-only); a `[FAIL]` names BOTH remedies -- the repair verb (a
    # ledger merely unmigrated, not corrupted) and `git reset --hard
    # <first-merge>~1` + `openkos reindex` (a ledger the check judges
    # corrupted) -- and states pre-fix reversibility is not guaranteed.
    # The git-reset half is gated on `reset_point_available`, the ADAPTER's
    # thunk over `vcs_git.repo_root(root) is not None and
    # vcs_git.has_reset_point(root)` (WALL 2). It is called HERE, inside
    # the violation branch, and nowhere else -- that is the whole reason it
    # is a callable rather than a `bool`, so those two `git` subprocess
    # calls stay unpaid on the common path, exactly as the pre-extraction
    # body left them:
    # `_autocommit` is best-effort and silently no-ops with no repo, no
    # configured git identity, or any `GitError`/`OSError`, so a workspace
    # that never actually committed has no reset point at all -- printing
    # that remedy unconditionally would name a command that cannot work.
    if in_workspace:
        bundle_dir = config.WorkspaceLayout(root).bundle_dir
        violations = bundle_ledger.scan_nesting_violations(bundle_dir)
        if violations:
            if reset_point_available():
                reset_remedy = (
                    "run `git reset --hard <first-merge>~1` then `openkos reindex`"
                )
            else:
                reset_remedy = (
                    "no git reset point is available in this workspace (no "
                    "repository, no configured git identity, or no commit "
                    "history) -- there is no remedy that restores "
                    "reversibility for the affected merge(s)"
                )
            results.append(
                CheckResult(
                    "Merge ledger entries free of post-merge mutation",
                    "fail",
                    critical=False,
                    detail=f"{len(violations)} entr{'y' if len(violations) == 1 else 'ies'}",
                    remediation=(
                        "if a ledger is merely unmigrated (still embedded in "
                        "the survivor's own frontmatter, not corrupted), run "
                        f"`openkos repair`; if corrupted, {reset_remedy} -- "
                        "reversibility of merges made before this fix is not "
                        "guaranteed"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    "Merge ledger entries free of post-merge mutation",
                    "pass",
                    critical=False,
                )
            )
    else:
        results.append(
            CheckResult(
                "Merge ledger entries free of post-merge mutation",
                "skip",
                critical=False,
            )
        )

    return tuple(results)
