# Doctor Command Specification

## Purpose

`openkos doctor` is a read-only environment health scan: a fixed set of
checks against the local workspace and the configured model backend (Ollama
by default, or an OpenAI-compatible server), printed as
`[PASS]`/`[FAIL]`/`[SKIP]`/`[NOT RUN]` lines with actionable remediation,
usable even before `openkos init`.

## Requirements

### Requirement: Doctor Runs And Prints All Applicable Checks

`doctor` MUST execute all checks applicable to the current context, in
this order, labelled as printed: `Workspace initialized`, `Config valid`,
the backend-reachable check (`Ollama reachable` or `OpenAI-compatible
server reachable`, by configured backend), `Model '<tag>' installed`,
`Embedding model '<tag>' installed`, `Task models installed`,
`Bundle readable`, `Workspace vector index present`,
`Workspace FTS index present`, `Vector extension loadable`, `git available`,
`git-filter-repo available`, `Backend host locality`,
`Merge ledger torn writes`, and
`Merge ledger entries free of post-merge mutation` — and print exactly one
`[PASS]`/`[FAIL]`/`[SKIP]`/`[NOT RUN]` line per applicable check. It MUST
NOT stop or skip remaining checks after any single check fails.

A check whose own read raises an unexpected exception — specifically
`okf.survey_bundle` (the bundle-readable check), `bundle_ledger.scan_torn_writes`
and `bundle_ledger.scan_nesting_violations` (the two merge-ledger checks),
and the injected `reset_point_available()` thunk failing inside the
merge-ledger-integrity check's violation branch — MUST be reported as
`not-run`, a fourth structured outcome carrying the raised reason, and MUST
NOT prevent any other applicable check from running or the report from
rendering. `doctor` MUST print completed and not-run counts
alongside the check lines.
This requirement binds the OBSERVABLE outcome, never a specific exception
class at the service boundary. The `reset_point_available()` case is
deliberately worded as "the thunk fails": `application/` may not import
`openkos.vcs` — the ban is enforced by an AST scan in
`tests/unit/application/test_layering.py` — so the git-specific class stays
adapter-side and the service sees only an adapter-level failure carrying
its reason. A conformance test MUST assert the rendered `[NOT RUN]` line
and the surviving reason, not the identity of the class the service caught.

A check whose own outcome depends on a value the bundle-readable check
produces MUST also report `not-run` when the bundle-readable check reports
`not-run`, rather than guessing at that value: the workspace-vector-index-present
and workspace-fts-present checks each decide between `skip` (an empty
bundle) and `fail` (a non-empty bundle with no index yet) using the
bundle-readable check's own reading of the bundle, and when that reading
did not happen, MUST report `not-run` instead of either — carrying a
reason that names the bundle-readable check as the unmet dependency. Both
downstream checks MUST NOT report `fail` when this applies. This rule does
not extend to a check that can answer without that value: if the
workspace's own index file already exists on disk, the corresponding check
still reports `pass`, since that observation does not depend on the
bundle-readable check at all.

#### Scenario: Healthy workspace prints all applicable checks

- GIVEN an initialized workspace, valid config, reachable Ollama, both
  configured models installed, a readable bundle, a present workspace vector
  index, a loadable vector extension, and both git binaries available
- WHEN `openkos doctor` runs
- THEN it prints one `[PASS]` line per check, covering all applicable checks

#### Scenario: A failing check does not stop later checks from running

- GIVEN Ollama is unreachable AND `openkos.yaml` is malformed
- WHEN `openkos doctor` runs
- THEN both the config-valid and Ollama-reachable checks print `[FAIL]`,
  and every other applicable check still prints its own result

#### Scenario: A raising bundle-readable check is reported not-run without discarding the rest

- GIVEN an initialized workspace where `okf.survey_bundle` raises an
  unexpected exception
- WHEN `openkos doctor` runs
- THEN the bundle-readable check prints `[NOT RUN]` carrying the raised
  reason, and every other applicable check still prints its own result

#### Scenario: A raising bundle-readable check also stops its two dependent checks from guessing

- GIVEN an initialized workspace where `okf.survey_bundle` raises an
  unexpected exception, and neither the workspace's vector index nor its
  FTS index file exists on disk
- WHEN `openkos doctor` runs
- THEN the bundle-readable, workspace-vector-index-present, and
  workspace-fts-present checks all print `[NOT RUN]` — none of the three
  prints `[FAIL]` — each of the two dependent checks carries a reason
  naming the bundle-readable check, and the report's not-run count
  includes all three

#### Scenario: A merge-ledger scan raise degrades only that check

- GIVEN an initialized workspace where `bundle_ledger.scan_torn_writes` or
  `bundle_ledger.scan_nesting_violations` raises an unexpected exception
- WHEN `openkos doctor` runs
- THEN that merge-ledger check prints `[NOT RUN]` carrying the raised
  reason, and the report still renders every other check's own result

#### Scenario: reset_point_available() raising degrades only the integrity check

- GIVEN a flagged ledger-integrity violation where the underlying git probe
  errors, so the injected `reset_point_available()` thunk fails
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[NOT RUN]` carrying the
  raised reason, and every other applicable check still prints its own
  result

#### Scenario: The report states completed and not-run counts

- GIVEN a run where one check reports not-run and the rest complete
- WHEN `openkos doctor` runs
- THEN the printed report states how many checks completed and how many did
  not run

### Requirement: Failed Checks Print Actionable Remediation

Each `[FAIL]` line MUST be immediately followed by an indented
`-> <fix command>` line naming the user's own next command: for the
`ollama` backend, an unreachable server points to starting it or installing
it, depending on whether the `ollama` binary is resolvable on the current
process's PATH, and a missing model points to pulling that model tag; for
the `openai-compatible` backend, an unreachable server points to verifying
that the configured server is running at its configured endpoint (no
`ollama serve`/`shutil.which("ollama")` signal applies, since there is no
single universal binary or start command across llama.cpp, LM Studio, vLLM,
and LocalAI), and a missing model lists the model ids the configured server's own
`/v1/models` response reports and advises setting `model:` to one of them
(no `ollama pull` reference, since it does not apply), with a note that
llama.cpp's `llama-server` reports its loaded GGUF file's path (or the value
passed to `--alias`, when set) rather than an arbitrary model name in
`/v1/models` — so on that server a "model not found" critical MAY be a false
alarm caused by naming convention rather than a genuinely missing model, and
the remediation MUST say so rather than asserting the model is absent. An
uninitialized workspace points to initializing it, for either backend.
`doctor` MUST NOT run these commands itself.

For the `ollama` backend's unreachable case specifically, the system MUST
use `shutil.which("ollama")` as a non-authoritative signal to select the
remediation wording: WHEN `shutil.which("ollama")` returns `None`, the
remediation MUST state that no `ollama` binary was found on PATH and point
to https://ollama.com for installation, and MUST NOT claim "Ollama is not
installed"; WHEN a binary is found but the endpoint still refuses the
connection, the remediation MUST point to `ollama serve`; WHEN the signal cannot be read confidently, the remediation
MUST cover both remedies rather than asserting either state as certain. For
the `openai-compatible` backend, `doctor` MUST NOT probe for or reference
`ollama`, `shutil.which("ollama")`, `ollama serve`, or `ollama pull` in any
remediation line: none of that wording applies to a non-Ollama server.

#### Scenario: Binary found, endpoint refuses — start-server remediation

- GIVEN `shutil.which("ollama")` resolves to a path, but the endpoint
  refuses the connection
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a
  `-> ollama serve` remediation line

#### Scenario: No binary on PATH — install remediation, no over-claim

- GIVEN `shutil.which("ollama")` returns `None`
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a remediation
  line stating no `ollama` binary was found on PATH and pointing to
  https://ollama.com
- AND the remediation text never states "Ollama is not installed"

#### Scenario: Uncertain signal covers both remedies

- GIVEN the `shutil.which("ollama")` signal cannot be read confidently
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check's remediation covers both installing and
  starting Ollama, rather than asserting either state as certain

#### Scenario: Missing model shows a pull remediation naming the tag

- GIVEN Ollama is reachable but the configured model tag is not installed
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` followed by an indented
  fix line naming a pull command for that exact configured tag

#### Scenario: Outside a workspace shows an init remediation

- GIVEN the current directory is not an initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-initialized check prints `[FAIL]` followed by an
  indented fix line naming the init command

#### Scenario: openai-compatible server unreachable — endpoint-check remediation, no Ollama wording

- GIVEN `cfg.backend == "openai-compatible"` and the configured endpoint
  refuses the connection
- WHEN `openkos doctor` runs
- THEN the backend-reachable check prints `[FAIL]` followed by a remediation
  line naming the configured endpoint and advising the user to verify that
  server is running
- AND the remediation contains no reference to `ollama`, `ollama serve`, or
  `shutil.which("ollama")`

#### Scenario: openai-compatible model missing — no ollama pull reference

- GIVEN `cfg.backend == "openai-compatible"`, the configured server is
  reachable, but the configured model is not available on it
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` followed by a remediation
  line naming the configured model and advising the user to make it
  available on that server
- AND the remediation contains no `ollama pull` reference

#### Scenario: openai-compatible model missing — lists the server's reported ids

- GIVEN `cfg.backend == "openai-compatible"`, the configured server is
  reachable, and its `/v1/models` response lists ids `a`, `b`, `c`, none of
  which match the configured model
- WHEN `openkos doctor` runs
- THEN the model-installed check's remediation lists `a`, `b`, `c` and
  advises setting `model:` to one of them

#### Scenario: llama.cpp's GGUF-path listing is called out as a possible false alarm

- GIVEN `cfg.backend == "openai-compatible"` and the configured server is a
  llama.cpp `llama-server` instance whose `/v1/models` response lists a GGUF
  file path (or the value passed to `--alias`) instead of the configured
  model name
- WHEN `openkos doctor` runs and the model-installed check fails
- THEN the remediation mentions that `llama-server` reports the GGUF path
  (or `--alias`) rather than an arbitrary model name, so the mismatch MAY
  not mean the model is genuinely missing
### Requirement: Exit Code Reflects Critical Failures Only

`doctor` MUST exit `0` when every applicable check completes (no
`not-run`) and no CRITICAL check (config valid, backend reachable, chat
model installed) reports `fail`; `1` when at least one CRITICAL check
reports `fail`, regardless of whether any check also reports `not-run` — a
known critical failure remains the dominant, already-actionable signal; and
`2` when at least one check reports `not-run` and no CRITICAL check reports
`fail` — the report could not be completed. Every check other than those
three CRITICAL ones stays informational: a `fail` on any of them, alone, MUST NOT cause a non-zero
exit, and a `not-run` on any of them, alone (with no critical failure),
MUST NOT push the exit code past `2`.

#### Scenario: Informational-only failure still exits zero

- GIVEN the vector-extension check fails while every critical check passes
  and no check reports not-run
- WHEN `openkos doctor` runs
- THEN the process exits with code `0`

#### Scenario: Any critical failure causes exit one

- GIVEN one critical check fails while all other checks pass and none
  reports not-run
- WHEN `openkos doctor` runs
- THEN the process exits with code `1`

#### Scenario: Not-run present and every critical check passes exits two, not zero

- GIVEN one check reports not-run (its read raised) and every CRITICAL
  check passes
- WHEN `openkos doctor` runs
- THEN the process exits with code `2`, never `0`

#### Scenario: Not-run present alongside a critical failure still exits one

- GIVEN one check reports not-run AND a CRITICAL check reports `fail`
- WHEN `openkos doctor` runs
- THEN the process exits with code `1`, not `2` — the critical failure
  dominates the incomplete-report signal

#### Scenario: A fully completed healthy run exits zero

- GIVEN every applicable check completes with no not-run outcome and no
  critical failure
- WHEN `openkos doctor` runs
- THEN the process exits with code `0`

### Requirement: Doctor Works Outside An Initialized Workspace

Outside an initialized workspace, `doctor` MUST still run: the
workspace-initialized check reports an informational `[FAIL]` with init
remediation, the config-valid and bundle-readable checks are skipped as not
applicable, and the Ollama-reachable and chat-model-installed checks MUST
still run — checked against the default chat model — and MUST still
determine the exit code. The embedding-model-installed and
vector-extension-loadable checks MUST also still run pre-init, both as
informational checks — the latter depends only on the local SQLite/Python
environment, not on workspace state.

#### Scenario: Unhealthy pre-init environment exits one

- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN it prints results for workspace, Ollama-reachable, chat
  model-installed, embedding model-installed, and vector-extension-loadable,
  and exits with code 1

#### Scenario: Healthy pre-init environment exits zero

- GIVEN no initialized workspace, Ollama reachable, both default models
  installed, and a loadable vector extension
- WHEN `openkos doctor` runs
- THEN every applicable check passes and the process exits with code 0

### Requirement: Model-Installed Check Uses Tag-Normalized Matching

The configured (or default, outside a workspace) model MUST count as
installed if it matches an installed tag exactly, or matches that tag's
`<name>:latest` form.

#### Scenario: Bare configured tag matches a :latest installed entry

- GIVEN the configured model tag has no explicit version suffix and an
  installed entry reports it as `<name>:latest`
- WHEN `openkos doctor` runs
- THEN the model-installed check passes

#### Scenario: Non-matching tag fails with pull remediation

- GIVEN no installed entry matches the configured tag under either
  normalization
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` with a pull remediation

### Requirement: Embedding-Model-Installed Check

`doctor` MUST report whether the configured (or, outside a workspace,
default) `embedding_model` is installed, using the same tag-normalized
`model_tag_matches()` comparison and `[PASS]`/`[FAIL]`/`[SKIP]` +
remediation pattern as the chat model-installed check. This check MUST be
informational (its failure alone MUST NOT affect the exit code). WHEN
Ollama is unreachable, this check MUST print `[SKIP]` with a
blocked-by-unreachable detail rather than `[FAIL]`, to avoid
double-reporting the same root cause already surfaced by the
Ollama-reachable check.

#### Scenario: Embedding model installed passes

- GIVEN Ollama is reachable and the configured `embedding_model` tag is
  installed (exact or `:latest`-normalized match)
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[PASS]`

#### Scenario: Embedding model missing shows a pull remediation

- GIVEN Ollama is reachable but the configured `embedding_model` tag is
  not installed
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[FAIL]` followed by an indented
  fix line naming a pull command for that exact tag, and the process still
  exits 0 if every critical check otherwise passes

#### Scenario: Ollama unreachable skips the embedding-model check

- GIVEN Ollama is unreachable
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[SKIP]` with a
  blocked-by-unreachable detail, not `[FAIL]`, and the Ollama-reachable
  check alone reports the root cause

### Requirement: Task-Models-Installed Check

`doctor` MUST report whether every per-task model the workspace resolves
(`models:` entries and packaged `DEFAULT_TASK_MODELS` defaults)
is installed, using the same tag-normalized `model_tag_matches()`
comparison and `[PASS]`/`[FAIL]`/`[SKIP]` + remediation pattern as the chat
model-installed check.

This MUST be exactly ONE check regardless of how many tasks resolve a
model, so the total check count stays fixed. It MUST examine only models
DIFFERING from the global `model:` — a task resolving the global tag is
already covered by the model-installed check, and reporting it twice would
double-count one root cause. WHEN no task resolves a differing model, this
check MUST print `[PASS]`.

This check MUST be informational: its failure alone MUST NOT affect the
exit code, because a missing per-task model fails only the stage that named
it while every other verb still works. WHEN Ollama is unreachable, it MUST
print `[SKIP]` with a blocked-by-unreachable detail rather than `[FAIL]`,
for the same one-root-cause reason as the embedding-model check.

#### Scenario: A missing per-task model is reported without failing the run

- GIVEN Ollama is reachable and the packaged `edge_typing` model is not
  installed
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[FAIL]`, names the task and its
  model, and offers a pull command for that exact tag, and the process
  still exits 0 if every critical check otherwise passes

#### Scenario: Declining a packaged default leaves nothing to check

- GIVEN `models.edge_typing` is an explicit null and no other task
  resolves a differing model
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[PASS]`

#### Scenario: Ollama unreachable skips the task-models check

- GIVEN Ollama is unreachable and a task resolves a differing model
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[SKIP]` with a
  blocked-by-unreachable detail, not `[FAIL]`

### Requirement: Vector-Extension-Loadable Check

`doctor` MUST report whether the `sqlite-vec` extension is loadable on the
current Python/SQLite environment, reusing the same accumulate-never-raise
`CheckResult` pattern as the other checks. This check MUST be informational
(its failure alone MUST NOT affect the exit code), MUST NOT depend on
workspace state or Ollama reachability, and MUST print exactly one
`[PASS]`/`[FAIL]` line without duplicating a root cause already reported by
another check.

#### Scenario: Extension loadable passes

- GIVEN the current environment can `enable_load_extension` and load
  `sqlite-vec`
- WHEN `openkos doctor` runs
- THEN the vector-extension check prints `[PASS]`

#### Scenario: Extension not loadable shows an extension-capable remediation

- GIVEN the current environment cannot load extensions (e.g. system or
  Homebrew Python without `enable_load_extension`)
- WHEN `openkos doctor` runs
- THEN the vector-extension check prints `[FAIL]` followed by an indented
  fix line naming an extension-capable Python (e.g. a uv-managed
  interpreter), and the process still exits 0 if every critical check
  otherwise passes

#### Scenario: Check runs independently of Ollama's state

- GIVEN Ollama is unreachable
- WHEN `openkos doctor` runs
- THEN the vector-extension check still runs and reports its own
  `[PASS]`/`[FAIL]` result, rather than being skipped

### Requirement: Workspace Vector Index Presence Check

`doctor` MUST report whether the WORKSPACE `.openkos/vectors.db`
(`layout.vectors_db_path`) exists on disk, as a check distinct from the
existing Vector-Extension-Loadable Check (which probes a throwaway
`:memory:` connection and says nothing about the workspace's own index
file). A `fail` on this check MUST stay informational (an absent index
alone MUST NOT affect the exit code), it MUST run only when a workspace is
initialized (skipped outside a workspace, mirroring the
config-valid/bundle-readable checks), and a `[FAIL]` line MUST be followed
by an indented fix line naming `openkos reindex`.

This check decides between `skip` and `fail` using the bundle-readable
check's own reading of the bundle. When that reading did not happen, this
check MUST report `not-run` rather than either, and MUST NOT print a
`openkos reindex` remediation about a bundle nobody could read. Unlike a
`fail` on this check, a `not-run` DOES contribute to the incomplete exit
code `2`, because the report could not be completed.

#### Scenario: Present workspace vectors.db passes

- GIVEN an initialized workspace whose `.openkos/vectors.db` file exists
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[PASS]`

#### Scenario: Absent workspace vectors.db fails with a reindex remediation

- GIVEN an initialized workspace whose `.openkos/vectors.db` file is
  absent (e.g. after `openkos purge`)
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[FAIL]` followed by an indented
  fix line naming `openkos reindex`, and the process still exits 0 if
  every critical check otherwise passes

#### Scenario: An unread bundle makes this check not-run, not fail

- GIVEN an initialized workspace whose `.openkos/vectors.db` file is
  absent AND whose bundle-readable check reported `not-run`
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[NOT RUN]` naming the
  bundle-readable check as the unmet dependency, prints no `openkos
  reindex` remediation, and the process exits `2`

#### Scenario: A present index still passes when the bundle was unread

- GIVEN an initialized workspace whose `.openkos/vectors.db` file EXISTS
  AND whose bundle-readable check reported `not-run`
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[PASS]`, because the file's
  presence on disk does not depend on the bundle-readable check

#### Scenario: Check is skipped outside a workspace

- GIVEN no initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[SKIP]` (not applicable), and
  does not affect the exit code

### Requirement: Workspace FTS Index Presence Check

`doctor` MUST report whether the WORKSPACE `.openkos/fts.db`
(`layout.fts_db_path`) exists on disk, mirroring the Workspace Vector Index
Presence Check's exact shape (`doctor` passed every check while
the workspace's first query was about to answer without lexical retrieval,
because nothing ever looked at `fts.db`). A `fail` on this check MUST stay
informational (an absent index alone MUST NOT affect the exit code), it
MUST run only when a workspace is initialized (skipped outside a
workspace), MUST be absent-only (staleness stays `reindex`'s manifest
gate's job and `next`'s stale tier's report), and a `[FAIL]` line MUST be
followed by an indented fix line naming `openkos reindex`.

Mirroring the Workspace Vector Index Presence Check exactly, this check
MUST report `not-run` when the bundle-readable check reported `not-run`,
MUST NOT print a `openkos reindex` remediation in that case, and its
`not-run` DOES contribute to the incomplete exit code `2`.

#### Scenario: Present workspace fts.db passes

- GIVEN an initialized workspace whose `.openkos/fts.db` file exists
- WHEN `openkos doctor` runs
- THEN the workspace-FTS check prints `[PASS]`

#### Scenario: Absent workspace fts.db fails with a reindex remediation

- GIVEN an initialized workspace whose `.openkos/fts.db` file is absent
- WHEN `openkos doctor` runs
- THEN the workspace-FTS check prints `[FAIL]` followed by an indented fix
  line naming `openkos reindex`, and the process still exits 0 if every
  critical check otherwise passes

#### Scenario: An unread bundle makes the FTS check not-run, not fail

- GIVEN an initialized workspace whose `.openkos/fts.db` file is absent
  AND whose bundle-readable check reported `not-run`
- WHEN `openkos doctor` runs
- THEN the workspace-FTS check prints `[NOT RUN]` naming the
  bundle-readable check as the unmet dependency, prints no `openkos
  reindex` remediation, and the process exits `2`

#### Scenario: FTS presence check is skipped outside a workspace

- GIVEN no initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-FTS check prints `[SKIP]` (not applicable), and does
  not affect the exit code

### Requirement: Doctor Prints A Leading Version Banner

`openkos doctor` MUST print a version banner line — the same
`openkos {version}` string produced by `openkos --version` — before any
check output, using the same resolution and `PackageNotFoundError` fallback
(`openkos unknown`) as `--version`. This banner is informational only: it is
NOT a `CheckResult` at all (doctor emits ten of those), MUST NOT be counted
among the applicable checks, and MUST NOT affect the exit code.

#### Scenario: Banner precedes all check lines

- GIVEN any workspace state (initialized or not)
- WHEN `openkos doctor` runs
- THEN the first printed line is `openkos {version}`, followed by the
  existing per-check `[PASS]`/`[FAIL]`/`[SKIP]` lines

#### Scenario: Check count and exit code are unaffected by the banner

- GIVEN an initialized workspace where every applicable check passes
- WHEN `openkos doctor` runs
- THEN ten check lines print — the banner adds none — and the process still
  exits 0

### Requirement: Doctor Never Raises On A Malformed Model Config

`openkos doctor` MUST NOT raise an uncaught exception when the configured
`model` value is malformed at the type level — specifically when
`openkos.yaml` contains a `model:` value that PyYAML resolves to a non-`str`
type (for example the YAML 1.1 boolean literal `yes`, which resolves to
Python `True`). This contract is on the user-observable outcome, not on
which internal mechanism catches the malformed value: whether the guard
lives in the config-valid check (via `read_config` raising `ValueError`,
already wrapped in `try/except (OSError, ValueError)`) or in an independent
guard around the model-installed checks, the end state MUST be identical —
doctor reports a `[FAIL]` line with actionable remediation pointing at
fixing `openkos.yaml`, reuses the existing accumulated-never-raised
`CheckResult` convention and the standard `[PASS]/[FAIL]/[SKIP] <label>` +
optional indented `-> <remediation>` output shape for every check line, and
every other applicable check still runs and prints its own result. No new
check-line shape is introduced by this requirement; the one exception is the
single leading version banner line (see "Doctor Prints A Leading Version
Banner"), which precedes the checks and is not itself a check line.

#### Scenario: Non-str model value fails cleanly instead of crashing

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
  (parsed by PyYAML as the Python `bool` `True`)
- WHEN `openkos doctor` runs
- THEN it does not raise an uncaught exception and prints no traceback

#### Scenario: Malformed model reports FAIL with actionable remediation

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
- WHEN `openkos doctor` runs
- THEN at least one check prints `[FAIL]` followed by an indented
  `-> <remediation>` line that points the user at fixing `openkos.yaml`'s
  `model:` value

#### Scenario: Other applicable checks still run despite the malformed model

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
  and Ollama is reachable
- WHEN `openkos doctor` runs
- THEN every other applicable check (Ollama-reachable, embedding-model
  installed, bundle readable, vector-extension loadable) still prints its
  own `[PASS]`/`[FAIL]`/`[SKIP]` result

#### Scenario: Check-line shape is unchanged; only the leading banner is new

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
- WHEN `openkos doctor` runs
- THEN every check line still matches the existing
  `[PASS]`/`[FAIL]`/`[SKIP] <label>` format, with remediation (when present)
  as an indented `-> <fix command>` line, and the only new line in the
  entire output is the single leading `openkos {version}` banner preceding
  all checks

### Requirement: Doctor Model-Installed Checks Depend Only On Tag Matching

Doctor's chat-model-installed and embedding-model-installed checks MUST
report outcomes that depend solely on tag-normalized matching
(`model_tag_matches`) against the tags of the entries `list_models()`
returns, unaffected by the per-model family field those entries also carry.

#### Scenario: Configured model present in installed tags still passes

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and the configured chat model tag matches one of them (exact or
  `:latest`-normalized)
- WHEN `openkos doctor` runs
- THEN the chat model-installed check prints `[PASS]`, identical to its
  outcome for a plain tag list

#### Scenario: Configured model absent still fails with pull remediation

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and no entry matches the configured chat model tag
- WHEN `openkos doctor` runs
- THEN the chat model-installed check prints `[FAIL]` with a pull
  remediation naming the configured tag, identical to its outcome for
  a plain tag list

#### Scenario: Embedding-model check outcome also unchanged

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and the configured `embedding_model` tag matches an installed
  entry
- WHEN `openkos doctor` runs
- THEN the embedding-model-installed check prints `[PASS]`, identical to
  its outcome before the contract change

### Requirement: Git and Git-Filter-Repo Availability Check

`doctor` MUST report whether `git` is resolvable on PATH and whether
`git-filter-repo` is installed and invocable, reusing the same
accumulate-never-raise `CheckResult` pattern as the other checks. These are
two independent checks and MUST print one `[PASS]`/`[FAIL]` line each. Both
MUST be informational (their failure alone MUST NOT affect the exit code) and
MUST run independently of workspace state and Ollama reachability, since
`purge` needs this signal even outside an initialized workspace.

#### Scenario: Both available passes

- GIVEN `git` is on PATH and `git-filter-repo` is installed
- WHEN `openkos doctor` runs
- THEN the git-filter-repo check prints `[PASS]`

#### Scenario: git-filter-repo missing shows an install remediation

- GIVEN `git` is on PATH but `git-filter-repo` is not installed
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by an indented fix line naming
  how to install `git-filter-repo`, and the process still exits 0 if every
  critical check otherwise passes

#### Scenario: git itself missing shows an install remediation

- GIVEN no `git` binary is resolvable on PATH
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by an indented fix line naming
  how to install `git`

#### Scenario: Check runs pre-init and independently of Ollama

- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN the git-filter-repo check still runs and reports its own
  `[PASS]`/`[FAIL]` result, unaffected by workspace or Ollama state

### Requirement: Merge-Ledger Torn-Write Check

`doctor` MUST add one new check, `merge ledger torn writes`, that scans
`bundle/.state/ledger/` for any `*.ledger.okf.pending` two-phase-write
marker — the trace a `merge`/`unmerge` leaves behind when it crashes
mid-commit. This check is mechanically exact (a marker either exists or it
does not, and its recorded hash either matches the survivor's current
content or it does not), so it has zero false positives and zero false
negatives, unlike the post-merge-mutation check below. It MUST be
informational (its failure alone MUST NOT affect the exit code) and MUST
follow the existing `[PASS]`/`[FAIL]`/`[SKIP]` +
`-> <remediation>` shape. A `[FAIL]` line's remediation MUST name
`openkos merge`/`openkos unmerge` on the affected survivor — the
operations whose recovery pass (`bundle_ledger.recover`) actually
resolves a pending marker — and MUST NOT name the repair verb, whose own
torn-write gate refuses outright while any marker is pending (naming
it would send the operator in a circle). This check MUST NOT write, modify, or delete any file —
`doctor` stays read-only; it detects and advises, it never repairs.

#### Scenario: A workspace with no pending markers passes

- GIVEN a workspace where no `merge`/`unmerge` has ever crashed mid-commit
- WHEN `openkos doctor` runs
- THEN the merge-ledger torn-writes check prints `[PASS]`

#### Scenario: A torn write fails with the merge/unmerge remediation

- GIVEN a `*.ledger.okf.pending` marker under `bundle/.state/ledger/`
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by remediation naming
  `openkos merge`/`openkos unmerge` on the affected survivor, and the
  remediation does not name the repair verb

#### Scenario: The check never writes

- GIVEN any combination of pending and non-pending ledger sidecars
- WHEN `openkos doctor` runs
- THEN no file under `bundle/.state/ledger/` (or anywhere else) is
  created, modified, or deleted by this check

### Requirement: Merge-Ledger Integrity Check

`doctor` MUST add one new check, `merge ledger entries free of post-merge
mutation`, that inspects every sidecar under `bundle/.state/ledger/` and
flags an entry whose recorded snapshot(s) no longer match what a
byte-exact `unmerge` would require — i.e. the ledger was mutated by
something other than the merge/unmerge machinery after being written. This
check MUST be informational (its failure alone MUST NOT affect the exit
code) and MUST follow the existing `[PASS]`/`[FAIL]`/`[SKIP]` +
`-> <remediation>` shape used by every other check. This check MUST NOT
write, modify, or delete any file — `doctor` stays read-only; it detects
and advises, it never repairs.

A `[FAIL]` line's remediation MUST ALWAYS name the repair verb (for a
ledger that is merely unmigrated, not corrupted) and MUST ALWAYS state
that reversibility of merges made while the ledger was embedded is not
guaranteed. The
second remedy is conditional on the workspace actually having one, because
the auto-commit that would create it is best-effort and silently no-ops
with no repository, no configured git identity, or any git error:

- WHEN the workspace is a git repository with a reachable reset point, the
  remediation MUST name `git reset --hard <first-merge>~1` followed by
  `openkos reindex` (for a ledger the check judges corrupted).
- WHEN it is not — no repository, no configured git identity, or no commit
  history — the remediation MUST say so explicitly and MUST NOT claim
  reset-and-replay is available, stating instead that no remedy restores
  reversibility for the affected merge(s).

The check MUST skip, never flag, any entry whose recorded `survivor_before`
snapshot embeds no ledger entries of its own. This skip is required for
correctness: after the ledger relocation a `survivor_before` snapshot is a
survivor document whose frontmatter carries no `merged_from` key, so every
post-relocation entry embeds nothing, and a check without the skip would
flag every legitimate multi-entry ledger created after the relocation.

The check therefore has THREE documented false negatives, and a `[PASS]`
MUST NOT be read as evidence that any ledger content was compared:

1. a single-entry ledger — nothing is nested;
2. cross-survivor pollution — `merge_core`'s `other_files` can rewrite a
   link inside a third survivor's embedded snapshot, which the ledger
   alone cannot distinguish from correct bytes;
3. every post-relocation entry — via the skip rule above. This is the
   widest of the three: on a workspace created after the relocation the
   check has nothing in scope at all.

#### Scenario: Clean ledgers pass

- GIVEN a workspace whose every `bundle/.state/ledger/` sidecar matches
  its expected byte-exact-restore state
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[PASS]`

#### Scenario: A corrupted ledger with a reset point fails with both remediation paths

- GIVEN a `bundle/.state/ledger/` sidecar whose recorded snapshots no
  longer round-trip byte-exact, in a git repository with a reachable reset
  point
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by remediation naming both the
  repair verb and the `git reset --hard`+`openkos reindex` path, and
  stating pre-fix reversibility is not guaranteed

#### Scenario: A corrupted ledger with no reset point names no reset-and-replay path

- GIVEN the same flagged sidecar in a workspace with no reachable git
  reset point (no repository, no configured git identity, or no commit
  history)
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by remediation that still names
  the repair verb and still states pre-fix reversibility is not
  guaranteed, but reports that no git reset point is available and that no
  remedy restores reversibility for the affected merge(s), rather than
  naming the `git reset --hard`+`openkos reindex` path

#### Scenario: The check never writes

- GIVEN any combination of clean and corrupted ledgers
- WHEN `openkos doctor` runs
- THEN no file under `bundle/.state/ledger/` (or anywhere else) is
  created, modified, or deleted by this check

#### Scenario: A workspace with no ledger sidecars passes trivially

- GIVEN a workspace where no merge has ever occurred
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[PASS]`, having found no
  sidecar to flag

#### Scenario: A post-relocation multi-entry ledger is skipped, not flagged

- GIVEN a `bundle/.state/ledger/` sidecar with two or more entries, every
  one of them created after the ledger relocation and therefore embedding
  a `survivor_before` snapshot with no `merged_from` key
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[PASS]`, having skipped
  every entry rather than comparing and flagging it

### Requirement: Backend Host Locality Check

`doctor` MUST always emit one informational `Backend host locality` check,
reusing the SAME backend client the backend-reachable check built, so it
reports the host `doctor` itself would send to and never a re-derivation.
The check MUST be `pass` when the backend is reachable, with a detail that
states whether the host is `this machine` or `not this machine`, the
configured host, and whether the confidential local exemption is `active`
(the host is local AND `confidential_local_exemption` is enabled) or
`inactive`. WHEN the backend is unreachable it MUST be `skip`, its detail
MUST say the locality is configured but not verified while the backend is
unreachable, and it MUST still name the host and the exemption state. It
MUST NEVER report `fail`, so a non-local backend is not called broken and
the check can never change the exit code.

#### Scenario: A reachable local backend reports this machine

- GIVEN a reachable backend on `localhost` and
  `confidential_local_exemption` enabled
- WHEN `openkos doctor` runs
- THEN it prints a `[PASS] Backend host locality` line naming
  `this machine`, the host, and `confidential local exemption active`

#### Scenario: A non-local backend is reported, never failed

- GIVEN a reachable backend on a non-loopback host
- WHEN `openkos doctor` runs
- THEN the `Backend host locality` line is `[PASS]`, names
  `not this machine`, reports the exemption as `inactive`, and the exit
  code is unaffected

#### Scenario: An unreachable backend skips the locality check

- GIVEN the backend is unreachable
- WHEN `openkos doctor` runs
- THEN the `Backend host locality` line is `[SKIP]`, says the locality is
  not verified while the backend is unreachable, and still names the host

### Requirement: Doctor Is Read-Only

`doctor` MUST NOT create, modify, or delete any file, and MUST NOT execute
any remediation command on the user's behalf; it only diagnoses and
advises.

#### Scenario: Doctor run leaves the workspace unchanged

- GIVEN any combination of passing and failing checks
- WHEN `openkos doctor` runs
- THEN no file in the workspace is created, modified, or deleted, and no
  fix command is executed by `doctor` itself
### Requirement: Doctor Reports Group- Or World-Accessible Engine State

In a workspace, `doctor` MUST report an informational, non-critical `[FAIL]`
naming every existing `.openkos/` directory, file directly inside it, and
`bundle/.state/` directory that group or other can access, with its mode and a
one-line `chmod go-rwx <paths>` remediation. When nothing is exposed (and on
platforms without POSIX modes) it MUST add no line, so the fixed check count is
unchanged. `bundle/` and `raw/` MUST NOT be inspected for this check.

#### Scenario: A world-readable store is named with the fix

- GIVEN `.openkos/` has mode `0755` and `.openkos/fts.db` has mode `0644`
- WHEN `openkos doctor` runs
- THEN one non-critical `[FAIL]` names both paths with their modes and the
  remediation is `chmod go-rwx .openkos .openkos/fts.db`

#### Scenario: A private workspace adds no line

- GIVEN `.openkos/` is `0700` and its files are `0600`
- WHEN `openkos doctor` runs
- THEN no engine-state line is printed

### Requirement: Not-Run Is Structured Data On The Returned CheckResult

`run_diagnostics`'s returned `CheckResult` MUST carry `not-run` as a
`status` outcome that is a peer of `pass`/`fail`/`skip` on the returned
dataclass — not a string invented only when the CLI renders. The raised
reason MUST be part of that same returned value (e.g. its `detail` field),
so any caller of the service, including a non-CLI adapter, can distinguish
`not-run` from `pass`/`fail`/`skip` and read why, without parsing rendered
text.

#### Scenario: The returned value carries not-run before any rendering

- GIVEN a check whose read raises an unexpected exception
- WHEN `run_diagnostics` returns
- THEN the corresponding `CheckResult` in the returned tuple has the
  not-run `status` and carries the raised reason, independent of whether
  the CLI has rendered anything yet

#### Scenario: A non-CLI caller reads not-run without parsing printed text

- GIVEN the same not-run outcome
- WHEN a caller other than the CLI inspects the returned `CheckResult`
  directly
- THEN it distinguishes not-run from `pass`/`fail`/`skip` from the
  structured value alone
### Requirement: Doctor Shows The Effective Endpoint And Its Resolution Source

`doctor` MUST print, alongside its backend-reachable check, the effective
endpoint it is checking against and which resolution source produced it
(explicit constructor argument, `OLLAMA_HOST`, the configured `base_url`, or
the packaged default), per the `backend-selection` endpoint-resolution
precedence. This line is informational and MUST NOT affect the exit code.
WHEN the resolution source is the packaged default, `doctor` MUST NOT print
this endpoint-and-source line at all: the reachable check's detail MUST
remain byte-identical to its pre-existing default-path wording (e.g.
`12 models`), so a user who never sets `base_url` or `OLLAMA_HOST` sees only
that default-path output.

#### Scenario: The effective endpoint and its source are shown

- GIVEN a workspace with `base_url: http://127.0.0.1:8000` configured and no
  `OLLAMA_HOST` set
- WHEN `openkos doctor` runs
- THEN it prints the effective endpoint `http://127.0.0.1:8000` and states
  that it came from the configured `base_url`

#### Scenario: OLLAMA_HOST is named as the source when it wins precedence

- GIVEN `OLLAMA_HOST` is set in the environment alongside a configured
  `base_url`
- WHEN `openkos doctor` runs
- THEN it prints the endpoint resolved from `OLLAMA_HOST` and names
  `OLLAMA_HOST`, not `base_url`, as its source

#### Scenario: The packaged default prints no endpoint-and-source line

- GIVEN a workspace with no `base_url` configured, no `OLLAMA_HOST` set, and
  `backend: ollama` resolving to the packaged default endpoint
- WHEN `openkos doctor` runs
- THEN the reachable check's detail is byte-identical to its pre-existing
  default-path wording, and no endpoint-and-source line is printed

### Requirement: Doctor States Where The API Key Will Be Sent

`doctor` MUST print an informational `API key destination` line that is
always `[PASS]`. For `backend: openai-compatible` with `OPENKOS_OPENAI_API_KEY`
set it MUST name each origin the key will be sent to (`scheme://host[:port]`,
no path, query or userinfo) and whether that origin is this machine; with no
key set it MUST say no key is sent; for `backend: ollama` it MUST say no key
is sent. It MUST NOT print the key's value.

#### Scenario: A remote destination is named

- GIVEN `backend: openai-compatible`, `base_url: https://api.example.com/v1`
  and `OPENKOS_OPENAI_API_KEY` set
- WHEN `openkos doctor` runs
- THEN the `API key destination` line names `https://api.example.com` as not
  this machine and the key value appears nowhere

#### Scenario: No key or an ollama backend

- GIVEN no key is set, or `backend: ollama`
- WHEN `openkos doctor` runs
- THEN the `API key destination` line is `[PASS]` and states that no key is
  sent

### Requirement: The API Key Is Never Printed By Doctor

WHEN `OPENKOS_OPENAI_API_KEY` is set, `doctor` MUST NOT print its value on
any output stream, under any check outcome, including a failing
backend-reachable check whose remediation names the endpoint. `doctor` MUST
NOT gate any check's pass/fail outcome on the key's presence or absence.

#### Scenario: A configured key never appears in doctor's output

- GIVEN `OPENKOS_OPENAI_API_KEY` is set to a value
- WHEN `openkos doctor` runs, including when the backend-reachable check
  fails
- THEN that value appears nowhere in `doctor`'s stdout or stderr

#### Scenario: An absent key does not affect any check's outcome

- GIVEN `OPENKOS_OPENAI_API_KEY` is unset and the configured
  `openai-compatible` server requires no key
- WHEN `openkos doctor` runs
- THEN no check fails, is skipped, or is reported not-run because of the
  key's absence

