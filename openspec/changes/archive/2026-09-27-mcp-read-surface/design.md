# Design: mcp-read-surface — a read-only MCP server that never discloses what it must not

## Technical Approach

This follows the proposal (Closes #1009, #1010), its binding "Decisions"
table, the binding "Decisions (2026-09-26)" in `exploration.md`, and the
owner's P1 answer: a note that is not confidential is returned **as
written**, and only structured channels are gated.

The adapter is a new package, `src/openkos/mcp/`, that is the only async
code in the repository (ADR-0021 D2). It calls synchronous application
services on worker threads and never reaches below them for knowledge-model
facts. The design keeps each concern at exactly one seam:

- **Disclosure authority** is one fail-closed predicate pair in
  `sensitivity.py`, next to `blocks_llm_send` (`sensitivity.py:93-108`) and
  reusing its rank. It has no LLM-send hatches (ADR-0028).
- **Disclosure application** is `mcp/gate.py`, the only module in the
  repository that calls the new predicate. Every tool result passes through
  a gate function before it is serialized, and the gate never mutates a
  service result.
- **Framing and stdout ownership** live in `mcp/transport.py`. Lifecycle,
  dispatch, error mapping, cancellation and progress live in
  `mcp/server.py`. The registry, hand-written schemas and the argument
  validator live in `mcp/tools.py` (ADR-0027).
- **Knowledge reads** stay in `application/`. The existing `run_query`,
  `list_provenance_sources` and `next_action` are reused. Two small new
  services are added because the adapter must not parse frontmatter or read
  the ledger itself: `application/concept_read.py` (`get`, `navigate`) and
  `application/consistency.py` (the `warnings` checks).
- **Service-side preparations** are additive and byte-identical when unused:
  structured `pending` subjects, an optional `progress` callback on
  `answer()`/`run_query`, id lists parallel to `AnswerResult`'s title lists,
  and chat-client construction moved into `application/backends.py`.

The delta specs (`specs/mcp`, `sensitivity-aware-llm`, `next-action-pointer`,
`query-answer`, `query-application-service`) are written in parallel. Where
this design sharpens or adjusts the proposal, it says so under **Open
Questions / spec alignment**.

## Architecture Decisions

### Decision 1: The disclosure predicate is a per-value check plus an allowed-set sibling

**Choice.** `sensitivity.py` gains two functions and imports nothing new
(it stays a leaf: stdlib plus `openkos.model.okf`).

```python
def blocks_disclosure(value: object, *, expose_confidential: bool = False) -> bool:
    if expose_confidential:
        return False
    return blocks_llm_send(value)

def disclosable_concept_ids(
    bundle_dir: Path, *, expose_confidential: bool = False
) -> frozenset[str]: ...
```

- `blocks_disclosure` delegates to `blocks_llm_send`, so absent, blank,
  whitespace, unrecognized and non-string values all block
  (`sensitivity.py:106-108`, `okf._rank`'s own fail-closed ranking).
- `disclosable_concept_ids` walks `okf._iter_docs` **once** and returns the
  ids that **may** be disclosed. A doc with `read_error` or `parse_error`
  is excluded (its value cannot be verified, so it ranks confidential). Any
  other doc is included when `blocks_disclosure(meta.get("sensitivity"))`
  is `False`. Under `expose_confidential=True` every walked id is included,
  unreadable ones too, because the policy then discloses every rank.
- Neither function has an `include_confidential` or `local_exemption`
  parameter. A signature test pins that.

**Why an allowed set, when `sensitive_concept_ids` (`sensitivity.py:236-277`)
returns a blocked set.** A blocked set fails open for any id the walk never
reached: a dangling provenance target, a file created after the walk, a
subtree the walk could not list. An allowed set withholds all of them by
construction. The consequence, recorded in ADR-0028, is that a dangling id
is withheld even under `--expose-confidential`; there is no document behind
it to disclose.

**Alternatives considered.** A `should_disclose_to_mcp_caller(metadata,
*, caller_may_see_confidential)` taking frontmatter (exploration's
candidate): rejected because the gate also needs a bare-value form for
`BundleObject.sensitivity`-shaped inputs, and a value predicate composes
into both. A zero-cost short-circuit under the flag, as
`sensitive_concept_ids` has: rejected, because the allowed set needs the
walk to know which ids exist.

**Rationale.** One fail-closed rank for both boundaries, and a set whose
failure direction is "withhold".

### Decision 2: The gate is the only caller, and every tool result is built by a gate function

**Choice.** `mcp/gate.py` is the only module under `src/` (besides
`sensitivity.py` itself) that references `blocks_disclosure` or
`disclosable_concept_ids`, and the only `mcp` module that imports
`openkos.sensitivity`. An AST test pins both facts.

A registered `Tool` has two halves:

- `run(arguments, ctx, progress) -> object` calls services and returns their
  raw result. It is written in `tools.py`.
- `disclose(raw, snapshot) -> dict` is written in `gate.py` and is the only
  thing that produces the serialized payload.

`tools.execute` (sync, on the worker thread) is the only composition:

```python
raw = tool.run(arguments, ctx, progress)
snapshot = gate.take_snapshot(ctx.layout.bundle_dir, expose_confidential=ctx.expose_confidential)
consistency = application_consistency.read_consistency(ctx.layout, stale_reads=tool.stale_reads)
return gate.finish(tool.disclose(raw, snapshot), consistency)
```

The snapshot is taken **after** `run`, so an object raised to confidential
while the service was reading is still withheld. `gate.Snapshot` is a frozen
dataclass holding the allowed set and the policy flag, with one method,
`discloses(concept_id) -> bool`. For the `get` target, whose sensitivity
the service read together with its body, the gate requires **both**
`snapshot.discloses(id)` and `not blocks_disclosure(record.sensitivity,
expose_confidential=...)`: the body and its own label come from one read,
and the snapshot catches a later raise.

**Rationale.** The proposal's "one call site to audit" becomes a structural
property: the server has no code path that serializes a `run` result.

### Decision 3: Every result has the same three disclosure-safe keys

Every `structuredContent` object, success or error, carries:

- `withheld: int`, the number of **entries** the gate removed from this
  result's structured channels (a row, an edge, a citation, a title, a
  provenance id, an ancestor, a pending item). An object that appears in two
  channels counts once per channel. This is what the gate can count exactly,
  including for title lists that carry no ids of their own.
- `warnings: list[{code, count | stores, message}]` (Decision 11).
- `not_run: list[{label, reason}]`, where every `reason` is a fixed string
  and every `label` comes from a fixed vocabulary:
  `in_flight_write`, `stale_index`, `concept_read`, `relations`,
  `provenance_walk`, `graph_build`. A `NotRun` produced by a service whose
  `label` is a document path (`list_service.py:303`) or whose `reason` is
  `str(exc)` never crosses the boundary as such: document-labelled entries
  are aggregated into one entry whose reason is `"<n> document(s) could not
  be read"`. The gate forwards only allowlisted labels, so an unknown label
  is aggregated too.

Skip notices name unreadable documents (`lint.py:315-356`,
`AnswerResult.skip_notices`, `answer.py:400`) and are reported as
`skipped_documents: int`, separately from `withheld`.

### Decision 4: `get` reads through a new service and returns a curated field set

**Choice.** `application/concept_read.py`:

```python
class ConceptNotFound(Exception): ...

@dataclass(frozen=True)
class ConceptRecord:
    concept_id: str
    sensitivity: object          # raw frontmatter value, for the gate only
    type: str | None
    title: str                   # whitespace-collapsed, like listing.BundleObject
    description: str
    status: Literal["active", "deprecated"]
    body: str
    relations: tuple[okf.Relation, ...]
    provenance: tuple[str, ...]  # string entries only, order kept
    not_run: tuple[read_outcome.NotRun, ...] = ()

@dataclass(frozen=True)
class UnreadableConcept:
    concept_id: str

def read_concept(layout: config.WorkspaceLayout, concept_id: str) -> ConceptRecord | UnreadableConcept: ...
```

- Resolution goes through `application.lifecycle.resolve_concept_path`
  (`lifecycle.py:137-180`). It canonicalizes the id, refuses absolute paths,
  `..` segments and reserved names, refuses a symlink escape, and refuses a
  missing file. Its `ValueError` becomes `ConceptNotFound`. This is the
  path-traversal defense for a caller-supplied id.
- One `read_text` + `okf.load_frontmatter`. An `OSError`,
  `UnicodeDecodeError` or parse failure returns `UnreadableConcept`, not an
  exception (ADR-0022: incompleteness is data).
- `status` is `deprecated` when the id is in
  `lifecycle.deprecated_concept_ids(bundle_dir)` (one walk; the same R2 rule
  `listing.list_objects` replicates at `listing.py:155-169`).
- A malformed `relations:` (`okf.decode_relations` raises `ValueError`,
  `okf.py:1512-1523`) yields `relations=()` and a `NotRun("relations", ...)`.
- `merged_from`, `absorbed_snapshot` and every other frontmatter key are
  never copied. A curated field set fails closed against keys added later.

The `get` tool also calls `list_service.list_provenance_sources(layout,
canonical_id)` (`list_service.py:259-312`) for source ancestors.

**Disclosure (`gate.disclose_get`).**

| Raw input | Payload |
| --- | --- |
| target not disclosable (Decision 2's conjunction) | `concept: null`, `withheld: 1`, nothing else about it |
| `UnreadableConcept`, flag off | not in the allowed set, so `concept: null`, `withheld: 1` |
| `UnreadableConcept`, flag on | `concept: null`, `not_run: [{label: "concept_read", reason: "the concept could not be read"}]` |
| `ConceptRecord`, disclosable | `concept` with `relations` and `provenance` filtered through the snapshot, and `source_ancestors` = `ancestors ∩ allowed`, each with `title` and `sensitivity` from the `rows` lookup |

`sensitivity` is echoed only when it is a member of
`okf.SENSITIVITY_ORDER`, else `"unknown"` (the same normalization as
`listing.py:123-126`). The body is returned as written (P1).

### Decision 5: `navigate` returns both directions from `build_graph`, filtered per edge

**Choice.** In `application/concept_read.py`:

```python
@dataclass(frozen=True)
class Neighbor:
    concept_id: str
    direction: Literal["out", "in"]
    relation_type: str | None    # None = untyped body link; "derived_from" for a provenance link

@dataclass(frozen=True)
class Neighborhood:
    concept_id: str
    neighbors: tuple[Neighbor, ...]   # sorted (direction, concept_id, relation_type or "")
    skipped_count: int                 # len(store.skipped)

def concept_neighbors(layout: config.WorkspaceLayout, concept_id: str) -> Neighborhood: ...
```

It resolves the id as `read_concept` does, then runs
`with build_graph(layout.bundle_dir) as store:` (`sqlite_graph.py:629-656`,
no candidates, so no vector store is opened) and filters `store.edges()` to
those with `source_id == id` (out) or `target_id == id` (in).
`SqliteGraphStore.neighbors` is out-only (`sqlite_graph.py:361-368`), which
is why `edges()` is used. The graph rebuilds per call (proposal: measured
before any caching).

**Disclosure (`gate.disclose_navigate`).** The target must be disclosable,
else `concept_id: null, withheld: 1`. A neighbor survives only when its id is
in the allowed set: the target's side is already checked, so this is the
proposal's "both ends disclosable" rule. `skipped_count > 0` becomes a
`graph_build` `not_run` entry with a count-only reason.

**Rationale.** The graph is sensitivity-blind by construction
(`graph/base.py:1-14`), so the filter has to be per edge at the boundary.
Returning inbound edges is what makes "neighbors" mean neighbors; the
`direction` field keeps the typed relation's orientation honest.

### Decision 6: `pending` is gated on structured subjects, and `openkos next` stays byte-identical

**Service changes (`application/next_action.py`).**

- `NextAction` gains `subjects: tuple[str, ...] | None = None`
  (`next_action.py:124-139`). `None` means **undeclared**, and any disclosure
  gate must withhold it. `()` means declared subject-free.
- `NextResult` gains `declination_subjects: tuple[tuple[str, ...] | None,
  ...] = ()`, index-aligned with `declinations` (`next_action.py:142-166`).
  `declinations` itself is unchanged, so `render_lines`
  (`next_action.py:941-957`) is untouched.
- `BundleSignals.record_declination(notice, *, subjects)` takes a
  **required** keyword (`next_action.py:185-191`), so mypy rejects a call
  site that forgets it. The signals object keeps a parallel list.
- `lint.LintFinding` gains `related_ids: tuple[str, ...] =
  field(default=(), compare=False)` (`lint.py:145-163`). It is populated for
  `below-source-sensitivity` with `(source_id,)` (`lint.py:1346-1356`) and
  for `multi-source-uncovered` with the cited ids (`lint.py:1422-1435`), the
  ids those details interpolate. `compare=False` keeps every existing
  `LintFinding` equality assertion valid. No lint output renders it.

**Subjects per tier.** Each `NextAction(...)` and `record_declination(...)`
call passes `subjects=` explicitly:

| Tier (line) | `subjects` | Why |
| --- | --- | --- |
| bootstrap, both branches (`:448`, `:455`) | `()` | fixed text |
| missing vector index (`:474`), missing FTS (`:500`) | `()` | fixed text |
| stale indexes (`:530`) | `()` | store names only |
| unextracted: action (`:592-595`) and both declinations (`:578`, `:587`) | `(finding.concept_id,)` | reason and command name the Source and its own `resource` |
| unjudged: action (`:641-644`) and declinations (`:625`, `:635`) | `(finding.concept_id,)` | same |
| below-source-sensitivity (`:677-680`) | `(finding.concept_id, *finding.related_ids)` | the detail names the Source |
| multi-source-uncovered: action (`:734-737`), declination (`:728`) | `(finding.concept_id, *finding.related_ids)` | the detail names every cited id |
| duplicate groups (`:772`), non-NFC (`:810`) | `()` | count only |
| open contradictions (`:855-865`) | `finding.pair_ids` | the reason names both ids |

**Gate (`gate.disclose_pending`).**

- `action` is kept only when `subjects is not None` and every subject is in
  the allowed set. Otherwise `action: null` and `withheld += 1`.
- `declinations`: when `len(declination_subjects) != len(declinations)`,
  every declination is withheld (fail closed). Otherwise each is kept when
  its subjects are declared and all disclosable.
- `skip_notices` become `skipped_documents: len(...)`.

**Byte-identity.** A characterization golden over `openkos next`'s stdout is
committed first and must stay green through the change (Testing Strategy,
slice 7).

**Alternatives considered.** Matching concept ids in the reason text:
rejected, because lint's detail is free prose and a document-controlled value
can contain anything. A `Declination` dataclass replacing the string tuple:
rejected, because `declinations` is compared as strings across the existing
`next` tests; a parallel tuple is additive, and its misalignment fails
closed. Deriving related ids inside the tiers: rejected, because it would
duplicate lint's closure logic.

### Decision 7: Chat-client construction moves into `application/backends.py` with an injected factory, and the CLI keeps one-line delegators

**Choice.**

```python
# application/backends.py -- imports config and typing only
class _Locality(Protocol):
    @property
    def is_local(self) -> bool: ...

class HasLocality(Protocol):
    @property
    def locality(self) -> _Locality: ...

ClientT = TypeVar("ClientT")

def chat_client(
    cfg: config.Config, *, factory: Callable[..., ClientT], task: str | None = None
) -> ClientT:
    return factory(
        model=config.resolve_task_model(cfg, task),
        timeout=cfg.chat_timeout,
        max_generation_tokens=cfg.max_generation_tokens,
        context_window=cfg.context_window,
        temperature=cfg.temperature,
        seed=cfg.seed,
    )

def resolve_local_exemption(client: HasLocality, cfg: config.Config) -> bool:
    return client.locality.is_local and cfg.confidential_local_exemption
```

The two docstrings move with the bodies (`cli/main.py:150-210`,
`cli/main.py:3895-3920`). `cli/main.py` keeps:

```python
def _chat_client(cfg: config.Config, *, task: str | None = None) -> OllamaClient:
    return application_backends.chat_client(cfg, factory=OllamaClient, task=task)

def _resolve_local_exemption(client: OllamaClient, cfg: config.Config) -> bool:
    return application_backends.resolve_local_exemption(client, cfg)
```

**Why a required factory, not an import of `OllamaClient`.**
`tests/unit/application/test_layering.py:107-130` forbids any
`application/*.py` from importing an `openkos.llm.*` module other than
`openkos.llm.base`, so services never bind a concrete backend (ADR-0018 D1).
An injected factory keeps that guard unchanged.

**Why delegators, not repointed call sites.** `OllamaClient` is resolved from
`cli/main`'s module globals at call time, and the unit suite's autouse
network guard patches exactly that name (`tests/unit/conftest.py:476`).
About 200 test references use `openkos.cli.main.OllamaClient`,
`_chat_client` or `_resolve_local_exemption`. Moving the construction without
the delegator would make those patches silently inert (a patched name that
is no longer read), and the network guard would stop covering chat clients.
The delegators keep behavior, the seam and the tests unchanged. The single
definition lives in `application/`. `cli/curate.py` keeps its own client
construction, which `tests/unit/cli/test_chat_timeout_wiring.py` pins.

### Decision 8: `answer()` and `run_query` take an optional progress callback; `AnswerResult` gains id lists

**Progress.** In `retrieval/answer.py`:

```python
AnswerPhase = Literal["retrieving", "assembling", "checking", "synthesizing"]
ProgressCallback = Callable[[AnswerPhase, int, int], None]  # (phase, completed, total)
```

`answer(..., progress: ProgressCallback | None = None)` (after
`revision_history`, `answer.py:1221-1235`). With `total = 4 if
sufficiency_check else 3`, it calls, only when `progress is not None`:

| Call site | Emission |
| --- | --- |
| before `_fts_search` (`:1296`) | `("retrieving", 0, total)` |
| before `_assemble_context` (`:1342`) | `("assembling", 1, total)` |
| before `_context_holds_the_answer` (`:1404`), only when `sufficiency_check` | `("checking", 2, total)` |
| before `llm.chat` (`:1425`) | `("synthesizing", total - 1, total)` |

The empty-question short-circuit (`:1282-1290`) emits nothing. A
short-circuit after assembly simply stops emitting; the response ends the
request. `completed` strictly increases within a call. `run_query` gains the
same keyword (default `None`) and passes it to `answer` next to
`revision_history` (`application/query.py:148-168`). The module stays
config-free.

**Id lists.** `AnswerResult` gains three index-aligned lists, all
`field(default_factory=list)` after `history_truncated_titles`
(`answer.py:493`): `excerpted_ids`, `omitted_ids`, `history_truncated_ids`.
`_assemble_context` gains keyword-only `omitted_ids_out` and
`history_truncated_ids_out` (default `None`, so the harness calls at
`evals/query_*/…` are unaffected). They are appended in the same statements
as their titles (`answer.py:956-957`, `:974-976`, `:998-999`), and
`excerpted_ids` is derived next to `excerpted_titles` (`answer.py:1360-1364`)
and threaded onto the three returns that carry the titles (`:1375`,
`:1408`, `:1450`). The CLI never reads them.

**Why ids.** `#882`'s lists carry titles only, and titles are not unique.
Scrubbing a title list by id, as the proposal binds, needs the id beside
each title.

**Amendment (Slice 9 correction).** `AnswerResult` gains a FOURTH additive
field, `context_ids`, alongside the three above: see Decision 9's
disclosure section for what it carries and why a citation-only check
cannot substitute for it.

### Decision 9: `query` composes the two boundaries as a conjunction

`tools.run_query_tool`:

```python
cfg = config.read_config(ctx.layout.root)
llm = ctx.make_llm(cfg)            # backends.chat_client(cfg, factory=OllamaClient)
embedder = ctx.make_embedder(cfg)  # OllamaClient(model=cfg.embedding_model)
local_exemption = ctx.local_exemption_for(llm, cfg) if ctx.expose_confidential else False
outcome = application_query.run_query(
    question, layout=ctx.layout, cfg=cfg, llm=llm, embedder=embedder,
    limit=limit, include_deprecated=False,
    include_confidential=False, local_exemption=local_exemption,
    progress=progress,
)
```

`include_confidential` is always `False`. With the flag off, confidential
content never enters the prompt. With the flag on, it enters only when the
chat backend is verifiably local.

**Disclosure (`gate.disclose_query`).**

- `citations` are kept when disclosable, else counted.
- **The answer text is withheld** (`answer: ""`, `answer_withheld: true`)
  **when EITHER (a) any citation is withheld, OR (b) any object whose
  content actually entered the prompt is not disclosable, whether or not
  the model went on to cite it.** (a) alone is not sufficient: `answer()`'s
  `citations` field is narrowed, AFTER `llm.chat` returns, to only the
  blocks the model's own footer reports drawing on (#753) — an object the
  model does not cite is simply absent from that narrowed list, even
  though its content was placed in the prompt and the wording may have
  drawn on it regardless. (b) is checked against `AnswerResult.
  context_ids`, a field captured in `answer()` BEFORE that narrowing —
  every concept id whose content was placed in a context block,
  index-aligned with `context_block_count`. A `context_ids` length
  inconsistent with `context_block_count` (a defect condition) is ALSO
  treated as "some object may be withheld," fail-closed, mirroring the
  title-list misalignment rule below. Neither half of this rule adds to
  `withheld`'s own count: an uncited context object was never a citation,
  title, or any other counted channel entry to begin with.

  With the launch opt-in off, both halves are reachable through the SAME
  race: an object read as public by `_assemble_context`, then raised to
  confidential before `llm.chat` returns. (a) alone missed the sub-case
  where the model's footer does not name that object's block — the
  now-confidential content stays out of `citations` (so nothing there
  looks withheld) while remaining fully present in the synthesized answer
  text; (b) closes it, since `context_ids` names the object regardless of
  citation. This is the fail-closed choice for that race, now covering
  every object that reached the prompt, not only a cited one.
- Each title list is zipped with its id list. A misaligned pair of lists
  drops every title in it and counts them (fail closed). A title whose id is
  not disclosable is dropped and counted. History titles keep their
  ` (earlier version)` suffix.
- `skip_notices` become `skipped_documents`. Counts, flags, `attribution`
  and `no_match_cause` pass through.

### Decision 10: The transport owns stdin and stdout, and framing is byte-exact

`mcp/transport.py`:

```python
class StdioStreams(NamedTuple):
    reader: BinaryIO
    writer: BinaryIO

@contextmanager
def claim_stdio() -> Iterator[StdioStreams]: ...

def start_reader(reader: BinaryIO, loop: asyncio.AbstractEventLoop,
                 queue: asyncio.Queue[bytes | None]) -> threading.Thread: ...

class ParseError(Exception): ...
def decode_line(raw: bytes) -> object | None: ...        # None = blank line, ignored
def encode_message(message: Mapping[str, object]) -> bytes: ...

class MessageWriter:
    def __init__(self, writer: BinaryIO) -> None: ...
    def send(self, message: Mapping[str, object]) -> None: ...  # loop thread only
```

- **`claim_stdio`** flushes `sys.stdout`, `os.dup(1)` into a private
  descriptor, `os.dup2(2, 1)`, rebinds `sys.stdout = sys.stderr`, and yields
  `StdioStreams(reader=sys.stdin.buffer, writer=os.fdopen(private, "wb"))`.
  On exit it restores descriptor 1 and `sys.stdout` and closes the private
  handle. A `print`, a `typer.echo`, a C-level write to descriptor 1, or a
  child process inheriting it all land on stderr. This works the same way on
  Windows (`os.dup`/`os.dup2` are portable), and a `BufferedWriter` over a raw
  descriptor does no newline translation.
- **`start_reader`** runs a daemon thread that loops
  `stream.readline()` until `b""`, posting each line with
  `loop.call_soon_threadsafe(queue.put_nowait, line)`, and posts `None` on
  end of input or on a read error (logged). A `RuntimeError` from a closed
  loop is swallowed.
- **`decode_line`** strips a trailing `\r\n` or `\n` (a Windows client may
  send `\r\n`), returns `None` for a blank line, decodes UTF-8 and calls
  `json.loads` with `parse_constant` raising, so `NaN`/`Infinity` are parse
  errors. Any failure raises `ParseError`.
- **`encode_message`** is `json.dumps(message, ensure_ascii=False,
  allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"`. JSON
  escapes every newline inside strings, so one message is one line.
- **`MessageWriter.send`** writes and flushes. It is called only from the
  event-loop thread, which makes it the single writer, so lines never
  interleave.

Logging: `serve()` attaches one `StreamHandler(sys.stderr)` to the
`openkos.mcp` logger (level INFO, `propagate=False`) and removes it on exit.
Nothing configures logging at import time.

### Decision 11: Consistency warnings come from a new service and never raise

`application/consistency.py`:

```python
@dataclass(frozen=True)
class Consistency:
    in_flight_writes: int | None       # None when the check could not run
    stale_stores: tuple[str, ...]
    not_run: tuple[read_outcome.NotRun, ...]

def read_consistency(
    layout: config.WorkspaceLayout, *, stale_reads: tuple[str, ...] = ()
) -> Consistency: ...
```

- `in_flight_writes = len(bundle_ledger.scan_torn_writes(layout.bundle_dir))`
  (`ledger.py:301-332`). It can raise (`:316` reads and parses each marker),
  so an exception becomes `NotRun("in_flight_write", str(exc))`. The gate
  replaces the reason with a fixed string (Decision 3), because a marker path
  names its survivor.
- `stale_stores = application_status.stale_index_names(layout,
  reads=stale_reads)` (`status.py:205`) when `stale_reads` is non-empty. It
  is the helper the CLI's `query` already uses (`cli/main.py:14325`), so both
  surfaces agree on what "stale" means. It never raises (it degrades to
  `()`), so `stale_index` never produces a `not_run` entry.
- `gate.finish` renders `{"code": "in_flight_write", "count": n, "message":
  <fixed>}` when `n > 0`, and `{"code": "stale_index", "stores": [...],
  "message": <fixed>}` when stores are listed. The `query` tool declares
  `stale_reads=("fts",)`; the others declare `()`.

### Decision 12: Lifecycle, dispatch and protocol errors

`mcp/server.py`, `PROTOCOL_VERSION: Final = "2025-11-25"`.

| Situation | Response |
| --- | --- |
| line is not valid JSON or UTF-8 | `-32700`, `id: null` |
| a JSON array (batch) | one `-32600`, `id: null` |
| not an object, `jsonrpc != "2.0"`, `method` not a string, or `id` null, boolean, float or another type | `-32600`, with the id when it was a valid id, else `null` |
| a response object from the client (no `method`) | ignored; the server sends no requests |
| request before `initialize` was answered, other than `initialize`/`ping` | `-32600` "server not initialized" |
| second `initialize` | `-32600` |
| unknown request method | `-32601` |
| unknown notification | ignored |
| `tools/call` with `params` not an object, `name` not a string, `arguments` present and not an object, or a non-string/int `progressToken` | `-32602` |
| unknown tool name | `-32602` |
| arguments that fail the tool's `inputSchema` | tool result, `isError: true`, `error.code = "invalid_arguments"` (see Open Questions) |
| a request id already in flight | `-32600` "duplicate request id" |
| any exception not in the tool-error table, or an encoding failure | `-32603` with the fixed message "internal error"; the traceback goes to stderr only |

`initialize` answers `{"protocolVersion": "2025-11-25", "capabilities":
{"tools": {"listChanged": false}}, "serverInfo": {"name": "openkos",
"version": <package version or "0+unknown">}, "instructions": <one fixed
sentence>}` whatever version the client named, once `params.protocolVersion`
is a string (else `-32602`). `notifications/initialized` is recorded but is
not required before `tools/*`: the revision only asks the client not to send
requests before the `initialize` response, and several deployed clients send
`tools/list` immediately. `ping` answers `{}` in any state. `initialize` and
`ping` are handled inline on the loop thread, so neither is cancellable.

`tools/list` returns every registry entry with `name`, `title`,
`description`, `inputSchema`, `outputSchema` and `annotations:
{readOnlyHint: true, destructiveHint: false, idempotentHint: true,
openWorldHint: false}`. A `cursor` is ignored and no `nextCursor` is sent.

**Tool errors** are a result with `isError: true`, `structuredContent =
{"error": {code, retryable, message}, "withheld": 0, "warnings": [],
"not_run": []}` and the same JSON in the `text` block. The table is ordered,
subclass first, and a test enforces the ordering:

| Exception | `code` | `retryable` |
| --- | --- | --- |
| `OllamaUnavailable` | `ollama_unavailable` | true |
| `OllamaModelNotFound` | `model_not_found` | false |
| `OllamaEmbeddingDimensionMismatch` | `embedding_dimension_mismatch` | false |
| `FtsUnavailable` | `fts_unavailable` | false |
| `OllamaError` | `ollama_error` | true |
| `ConceptNotFound` | `concept_not_found` | false |
| `WorkspaceReadError` (an `OSError`, or `config.read_config`'s `ValueError`, wrapped by `tools.execute`) and any other `OSError` | `read_failed` | true |

Every `message` is a fixed string from the table, never `str(exc)`. This
preserves the CLI's cause-specific distinctions (ADR-0018 D2,
`application/query.py:135-141`) without echoing exception text, which can
carry a confidential path, a document title or content.

### Decision 13: Concurrency, cancellation and progress

- **One task per request, one daemon worker thread per tool call.**
  `run_in_worker(fn)` creates a loop future, starts
  `threading.Thread(target=..., daemon=True)`, and resolves the future with
  `loop.call_soon_threadsafe(...)`, guarded by `if not fut.done()`. Read
  tools take no lock (ADR-0020 D3). Daemon threads let the process exit on
  end of input without joining abandoned readers; `asyncio.to_thread`'s
  executor threads are joined at interpreter exit, which would hold the
  process for up to the 600-second chat timeout. ADR-0027 records that the
  first write tool must revisit this.
- **In-flight map.** `self._inflight: dict[RequestKey, InFlight]`, with
  `RequestKey = tuple[Literal["s", "i"], str | int]` so that `"1"` and `1`
  are distinct ids. `InFlight` holds the task, a `finished` flag and
  `last_progress = -1`. The entry is removed when the task completes.
- **Cancellation.** `notifications/cancelled` with a known `requestId` calls
  `task.cancel()` and logs "request … cancelled; its worker was abandoned" to
  stderr. An unknown id, an `initialize` id, or an already-finished request is
  ignored. The task catches `CancelledError`, logs, and re-raises **without
  writing a response**. The response is written synchronously right after
  the worker's future resolves, with no `await` in between, so a
  cancellation either arrives before the write (no response) or after it
  (ignored). No task ever reports that work stopped.
- **Progress.** Only `query` has `emits_progress=True`, and a sink is built
  only when `params._meta.progressToken` is a string or an int. The sink
  posts `(phase, completed, total)` with `loop.call_soon_threadsafe`, and a
  closed loop is swallowed. The loop-side `_send_progress` drops the
  notification when the request is finished or cancelled, or when
  `completed <= last_progress` (the monotonic guard). It sends
  `{"method": "notifications/progress", "params": {"progressToken": token,
  "progress": completed, "total": total, "message": <fixed phase text>}}`.
  All progress posts are made before the worker returns, and the future's
  result is posted through the same FIFO, so every progress notification is
  written before the response.
- **End of input.** The dispatch loop stops, cancels every in-flight task
  (abandonment), awaits their cancellation, logs the count, and `serve()`
  returns `0`. `KeyboardInterrupt` returns `130`. No timeout is added
  (ADR-0021 D8).

### Decision 14: The `openkos mcp` verb

```python
@app.command(
    "mcp",
    help="Serve this workspace to an MCP client over stdio (read-only).",
    rich_help_panel="Explore",
)
def mcp_cmd(
    workspace: Path = typer.Option(Path("."), "--workspace", help=...),
    expose_confidential: bool = typer.Option(False, "--expose-confidential", help=...),
) -> None:
    root = workspace.resolve()
    reason = config.require_workspace(root)            # stderr + exit 1 on refusal
    config.read_config(root)                           # stderr + exit 1 on OSError/ValueError
    from openkos.mcp import server as mcp_server       # lazy: asyncio stays off other verbs
    raise typer.Exit(code=mcp_server.serve(root, expose_confidential=expose_confidential))
```

- `mcp` joins `_READ_ONLY_COMMANDS` (`cli/main.py:274-282`). It never writes,
  and ADR-0020 D1 forbids holding the lock for a server's lifetime.
  `test_every_command_is_classified` otherwise fails.
- `"Explore"` is already in `PANEL_ORDER` (`cli/main.py:15728-15734`).
- Refusal lines follow the CLI's shape: `openkos mcp: refusing to serve --
  <reason>.` Nothing is written to stdout before serving starts.
- `serve()` logs one stderr advisory at start-up when the embedding host is
  not local, mirroring the CLI's `_warn_if_nonlocal_embed_host` (#199). The
  embedded text is the client's question, not bundle content.
- `config.read_config` runs again per tool call, so an edited
  `openkos.yaml` takes effect without a restart, as it would for a CLI run.

### Decision 15: Tool schemas and the argument validator

| Tool | `inputSchema` (all `"type": "object"`, `"additionalProperties": false`) |
| --- | --- |
| `query` | `question`: string, `minLength` 1 (required); `limit`: integer, `minimum` 1 (optional; the handler defaults it to 5, the CLI's default at `cli/main.py:14114-14116`) |
| `get` | `concept_id`: string, `minLength` 1 (required) |
| `navigate` | `concept_id`: string, `minLength` 1 (required) |
| `pending` | no properties |

`tools.validate_arguments(schema, arguments) -> str | None` supports exactly
`SUPPORTED_SCHEMA_KEYWORDS = {"type", "properties", "required",
"additionalProperties", "items", "minLength", "minimum", "description",
"default"}`. `type` may be a string or a list over `object`, `array`,
`string`, `integer`, `boolean` and `null`. `integer` rejects `bool`.
`additionalProperties` supports only `false`. A test walks every registered
`inputSchema` and `outputSchema` and fails on any keyword outside the set.
Without that test, a keyword the validator ignores would be silently
unchecked, which fails open. Error messages name the property and never
echo its value.

Each `outputSchema` requires `withheld`, `warnings` and `not_run`, and lists
every other key (including `error`) as optional, so success and error
payloads both conform. Tests validate every produced `structuredContent`
against its tool's `outputSchema` with the same validator.

### Decision 16: The enumeration guard and its canary

The guard lives in `tests/unit/mcp/test_enumeration_guard.py`, with fixture
helpers in `tests/unit/mcp/canary.py`. It drives the **full server path**
(JSON-RPC in, bytes out) in memory, so error mapping and serialization are
covered, not only the handlers.

**Fixture workspace** (built on the `init` layout the other CLI tests use):

- `concepts/zq-canary-7f3a.md`: `sensitivity: confidential`, title `Zq Canary
  Title 7f3a`, body containing `ZQ-CANARY-BODY-7F3A`, a `relations:` entry to
  `concepts/pub` (an inbound edge on `navigate(pub)`).
- `sources/zq-canary-src-7f3a.md`: a confidential Source with `extraction_status:
  failed`, so `pending`'s unextracted tier fires on it (with
  `next_action.vector_store_is_empty` and `fts_index_present` patched to their
  "healthy" values, the public seams the `next` tests already patch).
- `concepts/pub.md`: public, with `relations:` to the canary and
  `provenance:` citing the canary and the confidential Source. Its **prose
  never mentions a canary**; P1 returns prose as written.
- `concepts/zq-broken-7f3a.md`: invalid UTF-8, so unreadable, which ranks
  confidential.
- A pending merge-ledger marker whose survivor is the canary (for
  `in_flight_write`).
- A findings row pairing `pub` with the canary.
- An FTS index built with the same model-free `state.fts` writer `reindex`
  uses, so `query` can retrieve the canary. A fake `LLMBackend` echoes the
  whole user message back as its answer, so any canary content in the prompt
  would reach the answer text.

**Needles**: every canary id, the slugs without their directory, the title
and the body marker, each also case-folded. The guard searches every byte
the server wrote (responses and notifications), both raw and after
`json.loads` of each `text` block.

**Matrix.** `GUARD_MATRIX: dict[str, list[Call]]` has one entry per tool.
Each tool is called with: a disclosable id (`pub`), each canary id, a
missing id, invalid arguments, and injected failures. Failures are injected
by monkeypatching the service the tool calls to raise `OSError(f"cannot read
{CANARY_ID}: {BODY_MARKER}")` and `RuntimeError(CANARY_TITLE)`, and, for
`query`, by making the fake LLM raise each `Ollama*` error with a canary in
its message. All of that runs with `expose_confidential=False`.

**Three assertions, all permanent:**

1. `set(tools.REGISTRY) == set(GUARD_MATRIX)`. A new tool without a matrix
   entry fails the guard.
2. `find_canary_leaks(run_matrix(REGISTRY)) == []`.
3. **The guard can fail.** A test-only tool `leaky_probe`, whose `disclose`
   returns the canary title, is added to a copy of the registry (with a
   matrix row); `find_canary_leaks` must return a non-empty list. The same
   copy without the matrix row must fail assertion 1.

**Positive controls** keep the needles live, because a guard with zero
exposure proves nothing. With `expose_confidential=True`, `get` on the
canary must contain its title, `navigate(pub)` must list it, and `query`
with a local fake backend must retrieve it. If any control finds no needle,
the fixture is broken and the test fails.

## Data Flow

```
MCP client ──stdin (NDJSON)──► reader thread ──call_soon_threadsafe──► asyncio.Queue
                                                                          │
                                         server.handle(line) ◄────────────┘
                     ┌────────────── inline: initialize / ping / tools/list / notifications
                     │
                     └─ tools/call ─► validate ─► task ─► run_in_worker(tools.execute)
                                                              │ (daemon thread)
                               tool.run ─► application service(s) ─► raw result
                               gate.take_snapshot ─► sensitivity.disclosable_concept_ids
                               consistency.read_consistency ─► scan_torn_writes / stale_index_names
                               tool.disclose(raw, snapshot) + gate.finish ─► payload dict
                                                              │
                     result ◄── future (call_soon_threadsafe) ┘
                     │
                     └─► MessageWriter.send ─► private fd (the real stdout) ─► MCP client
      (fd 1 and sys.stdout point at stderr while serving; logs go to stderr)
```

### Sequence: `query` with a progress token, then cancellation of a second call

```
client            server (loop)             worker A (daemon)           run_query/answer
  │ tools/call #7 query, _meta.progressToken=p │                               │
  ├──────────────►│ validate, inflight[#7]      │                               │
  │               │ run_in_worker ─────────────►│ tools.execute ───────────────►│
  │               │                             │                progress("retrieving",0,3)
  │               │◄── call_soon_threadsafe ────┤◄──────────────────────────────┤
  │◄── notifications/progress p 0/3 ────────────┤                progress("assembling",1,3)
  │◄── notifications/progress p 1/3 ────────────┤                progress("synthesizing",2,3)
  │◄── notifications/progress p 2/3 ────────────┤         llm.chat ... returns  │
  │               │                             │ snapshot, consistency, disclose│
  │               │◄── future result (FIFO after the progress posts) ────────────┤
  │◄── result #7 (structuredContent + text) ───┤ inflight[#7] removed          │
  │ tools/call #8 query                          │                               │
  ├──────────────►│ inflight[#8], worker B starts │                              │
  │ notifications/cancelled requestId=#8          │                              │
  ├──────────────►│ task#8.cancel(); log "abandoned" to stderr                   │
  │               │ no response for #8; worker B keeps running, writes nothing   │
  │               │ its late progress and result are dropped (finished/cancelled)│
```

### Sequence: `get` with a confidential relation

```
tools.execute
  ├─ concept_read.read_concept(pub) ─► ConceptRecord(relations=[canary, x], provenance=[src, canary])
  ├─ list_service.list_provenance_sources(pub) ─► ancestors=[sources/zq-canary-src-7f3a, sources/s1]
  ├─ gate.take_snapshot ─► allowed = {pub, x, sources/s1, …}   (canary, src, broken excluded)
  ├─ consistency ─► in_flight_writes=1
  └─ gate.disclose_get ─► relations=[x], provenance=[…s1-side only], source_ancestors=[s1]
                          withheld = 1 (relation) + 1 (provenance) + 1 (ancestor) = 3
                          warnings=[{in_flight_write, count 1}]
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/sensitivity.py` | Modify | `blocks_disclosure`, `disclosable_concept_ids`; module docstring gains the disclosure boundary |
| `src/openkos/mcp/__init__.py` | Create | Package docstring only; imports nothing |
| `src/openkos/mcp/transport.py` | Create | `claim_stdio`, `start_reader`, `decode_line`, `encode_message`, `MessageWriter`, `ParseError` |
| `src/openkos/mcp/server.py` | Create | `PROTOCOL_VERSION`, `Server`, dispatch tables, protocol and tool error mapping, in-flight map, cancellation, progress, `run_in_worker`, `serve()` |
| `src/openkos/mcp/tools.py` | Create | `Tool`, `ToolContext`, `REGISTRY`, the four `run_*` functions, schemas, `validate_arguments`, `SUPPORTED_SCHEMA_KEYWORDS`, `execute` |
| `src/openkos/mcp/gate.py` | Create | `Snapshot`, `take_snapshot`, `disclose_get/navigate/pending/query`, `finish`, the `NotRun` allowlist |
| `src/openkos/application/concept_read.py` | Create | `read_concept`, `concept_neighbors`, `ConceptRecord`, `UnreadableConcept`, `Neighbor`, `Neighborhood`, `ConceptNotFound` |
| `src/openkos/application/consistency.py` | Create | `read_consistency`, `Consistency` |
| `src/openkos/application/backends.py` | Create | `chat_client` (injected factory), `resolve_local_exemption`, the two moved docstrings |
| `src/openkos/application/next_action.py` | Modify | `NextAction.subjects`, `NextResult.declination_subjects`, `record_declination(subjects=)`, subjects at every tier |
| `src/openkos/lint.py` | Modify | `LintFinding.related_ids` (`compare=False`), populated for the two sensitivity kinds |
| `src/openkos/retrieval/answer.py` | Modify | `AnswerPhase`, `ProgressCallback`, `answer(progress=)`, the three id lists, `_assemble_context` id outs |
| `src/openkos/application/query.py` | Modify | `run_query(progress=)` threaded to `answer` |
| `src/openkos/cli/main.py` | Modify | `_chat_client`/`_resolve_local_exemption` become delegators; the `mcp` verb; `mcp` in `_READ_ONLY_COMMANDS` |
| `docs/adr/0027-hand-rolled-stdio-mcp-server.md`, `docs/adr/0028-mcp-disclosure-is-its-own-boundary.md`, `docs/adr/README.md` | Create/Modify | Written in this phase, status Proposed; committed with slices 2 and 1 |
| `docs/cli.md`, `docs/architecture.md`, `docs/roadmap.md` | Modify | The `mcp` entry with a client configuration example and the flag-off completeness note; the `mcp/` package; MVP 3 status |
| `tests/unit/test_sensitivity.py` (or its existing home) | Modify | Predicate and set sibling |
| `tests/unit/mcp/{test_transport,test_server,test_tools,test_gate,test_enumeration_guard,test_layering,test_stdio_subprocess}.py`, `tests/unit/mcp/canary.py` | Create | Per slice |
| `tests/unit/application/{test_concept_read,test_consistency,test_backends}.py` | Create | Services |
| `tests/unit/application/test_layering.py` | Modify | Add `openkos.mcp` to the offender list |
| `tests/unit/cli/test_next_golden.py`, `tests/unit/cli/test_next*.py`, `tests/unit/test_lint*.py` | Create/Modify | Golden, subjects, `related_ids` |
| `tests/unit/retrieval/test_answer.py`, `tests/unit/application/test_query_service.py` | Modify | Progress byte-identity, id lists, threading |
| `tests/unit/cli/test_mcp_cmd.py` | Create | Verb flags, refusals, panel, lazy import |

No file under `pyproject.toml`, `examples/` or `templates/` changes.

## Interfaces / Contracts

```python
# sensitivity.py
def blocks_disclosure(value: object, *, expose_confidential: bool = False) -> bool: ...
def disclosable_concept_ids(bundle_dir: Path, *, expose_confidential: bool = False) -> frozenset[str]: ...

# mcp/gate.py
@dataclass(frozen=True)
class Snapshot:
    allowed: frozenset[str]
    expose_confidential: bool
    def discloses(self, concept_id: str) -> bool: ...
def take_snapshot(bundle_dir: Path, *, expose_confidential: bool) -> Snapshot: ...
def finish(payload: dict[str, object], consistency: Consistency) -> dict[str, object]: ...

# mcp/tools.py
@dataclass(frozen=True)
class ToolContext:
    layout: config.WorkspaceLayout
    expose_confidential: bool
    make_llm: Callable[[config.Config], LLMBackend]
    make_embedder: Callable[[config.Config], Embedder]
    local_exemption_for: Callable[[LLMBackend, config.Config], bool]

ProgressSink = Callable[[str, int, int], None]

@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    run: Callable[[Mapping[str, object], ToolContext, ProgressSink | None], object]
    disclose: Callable[[object, Snapshot], dict[str, object]]
    stale_reads: tuple[str, ...] = ()
    emits_progress: bool = False

REGISTRY: Mapping[str, Tool]
def validate_arguments(schema: Mapping[str, object], arguments: object) -> str | None: ...
def execute(tool: Tool, arguments: Mapping[str, object], ctx: ToolContext,
            progress: ProgressSink | None) -> dict[str, object]: ...

# mcp/server.py
PROTOCOL_VERSION: Final = "2025-11-25"
def serve(root: Path, *, expose_confidential: bool) -> int: ...

# retrieval/answer.py (additive)
AnswerPhase = Literal["retrieving", "assembling", "checking", "synthesizing"]
ProgressCallback = Callable[[AnswerPhase, int, int], None]
def answer(question: str, *, …, revision_history: bool = False,
           progress: ProgressCallback | None = None) -> AnswerResult: ...
# AnswerResult: excerpted_ids, omitted_ids, history_truncated_ids: list[str] (default_factory=list)

# application/next_action.py (additive)
class NextAction: command: str; reason: str; subjects: tuple[str, ...] | None = None
class NextResult: …; declination_subjects: tuple[tuple[str, ...] | None, ...] = ()
```

**Wire shapes** (`structuredContent`; `text` carries the same JSON):

```jsonc
// get
{"concept": {"id", "type", "title", "description", "sensitivity", "status", "body",
             "relations": [{"target", "type"}], "provenance": ["…"],
             "source_ancestors": [{"id", "title", "sensitivity"}]} | null,
 "withheld": 0, "warnings": [], "not_run": []}
// navigate
{"concept_id": "…" | null, "neighbors": [{"id", "direction", "relation"}], "withheld": 0, "warnings": [], "not_run": []}
// pending
{"action": {"command", "reason"} | null, "declinations": ["…"], "skipped_documents": 0, "withheld": 0, "warnings": [], "not_run": []}
// query
{"answer": "…", "answer_withheld": false,
 "citations": [{"id", "title", "excerpted", "confidential", "history"}],
 "llm_invoked", "no_match_cause", "attribution",
 "counts": {"fts_hits", "dense_hits", "fused", "context_blocks"},
 "degraded": {"dense", "sufficiency", "vector_store_unavailable", "fts_unavailable"},
 "excerpted_titles": [], "omitted_titles": [], "history_truncated_titles": [],
 "skipped_documents": 0, "withheld": 0, "warnings": [], "not_run": []}
```

## Testing Strategy

Strict TDD with the runner `uv run pytest`. Every test is written RED first,
except the characterization golden in slice 7, which is written first and
green on `main` as a regression net. **Mutation targets** name the line a
test must catch when it is mutated; purge `__pycache__` between a mutation
and its revert. Coverage stays at or above 90%. No `pytest-asyncio`: async
code is driven with `asyncio.run(...)` inside synchronous tests. Tests that
need a worker to block use a `threading.Event`, never a sleep.

| Slice | Test | Mutation target it must catch |
| --- | --- | --- |
| 1 | `blocks_disclosure(v) == blocks_llm_send(v)` for a corpus (`None`, `""`, `"  "`, `"public"`, `"private"`, `"confidential"`, `"Confidential"`, `"secret"`, `1`, `[]`, `{}`, `True`); with the flag on, always `False` | Delegating to `okf._rank` (blank would rank private); dropping the flag short-circuit |
| 1 | The signature has exactly `value` and `expose_confidential` (no LLM hatch), and the flag is keyword-only with default `False` | Adding `local_exemption`; defaulting `True` |
| 1 | `disclosable_concept_ids` over public, private, confidential, blank, absent, unreadable and unparseable docs returns only the public and private ids; with the flag, every walked id | Using a block list; including unreadable ids with the flag off |
| 1 | An id with no document is not in the set, even with the flag | — (pins the allowed-set semantics) |
| 1 | `sensitivity.py` imports only stdlib and `openkos.model.okf` (AST) | A new import |
| 2 | `decode_line`: `\n`, `\r\n`, blank, invalid UTF-8, invalid JSON and `NaN` all behave as Decision 10 states | Stripping only `\n`; accepting `NaN` |
| 2 | `encode_message` output has exactly one trailing `\n`, no `\r`, no other raw newline (a string containing `\n` round-trips) | `ensure_ascii`/separator drift that breaks framing; `allow_nan=True` |
| 2 | `start_reader` over an `os.pipe` posts each line and then `None`; a closed loop does not raise in the thread | Missing sentinel on end of input |
| 2 | `MessageWriter.send` writes bytes to the given binary stream | — |
| 2 | `claim_stdio` in a subprocess: `print`, `sys.stdout.write` and `os.write(1, …)` land on stderr; only transport writes reach stdout (`cross_platform_smoke`) | Rebinding `sys.stdout` without `dup2` (fd-level writes leak) |
| 3 | Lifecycle table (Decision 12): pre-init rejection, `ping` before init, second `initialize`, version negotiation answers `2025-11-25` for `"2024-11-05"` and `"2099-01-01"`, `initialized` optional | Accepting `tools/list` before `initialize`; echoing the client's version |
| 3 | Every protocol-error row: `-32700`, batch `-32600`, bad `jsonrpc`, bool/float/null id, `-32601`, `-32602` envelope cases, unknown tool | Treating a boolean id as an integer; answering batches element-wise |
| 3 | `-32603` carries only "internal error" when a test tool raises `RuntimeError("secret text")`, and stderr carries the traceback | Using `str(exc)` |
| 3 | `validate_arguments`: required, `additionalProperties: false`, `minLength`, `minimum`, `integer` rejects `True`, type unions, `items` | `isinstance(x, int)` without excluding `bool` |
| 3 | Every registered schema uses only `SUPPORTED_SCHEMA_KEYWORDS` (walked recursively) | A keyword removed from the check set |
| 3 | Invalid arguments give `isError` with `invalid_arguments` | Mapping to `-32602` (see Open Questions) |
| 3 | Duplicate in-flight id gives `-32600`, and the first request still completes | Overwriting the in-flight entry |
| 4 | `openkos mcp` in a non-workspace exits 1 with the refusal on stderr and nothing on stdout; bad config exits 1 | Validating after serving starts |
| 4 | Flags reach `serve()` (patched recorder); `mcp` is in `_READ_ONLY_COMMANDS` and the `Explore` panel; help text passes `test_no_command_help_publishes_internal_references` | `--expose-confidential` defaulting on |
| 4 | Importing `openkos.cli.main` in a fresh subprocess leaves `openkos.mcp` out of `sys.modules` | A module-level `import openkos.mcp` |
| 4 | Layering (AST): `asyncio` only under `mcp/`; `mcp/` never imports `openkos.cli`; `application/` never imports `openkos.mcp`; `mcp/` imports only stdlib and `openkos.*`; only `gate.py` references the predicate pair | An `asyncio` import in `application/`; a predicate call in `tools.py` |
| 4 | Enumeration guard assertions 1-3 over the empty registry plus `leaky_probe`; `find_canary_leaks` catches the probe | A needle check that searches only `structuredContent` |
| 4 | Subprocess tier (`cross_platform_smoke`, `OLLAMA_HOST=http://127.0.0.1:9`): `initialize` sent with `\r\n` → `initialized` → `tools/list` → `ping` → stdin closed; every stdout line is a JSON-RPC 2.0 message, stdout has no `\r`, exit 0 | Text-mode stdout; not exiting on end of input |
| 5 | `read_concept`: curated fields only (a frontmatter key `secret_ids` is absent from the record), `ConceptNotFound` for missing, `..`, absolute and reserved ids, `UnreadableConcept` for invalid UTF-8, malformed `relations:` gives `relations=()` plus `NotRun` | Copying frontmatter through; raising on unreadable |
| 5 | `read_consistency`: a pending marker counts 1; a marker that raises gives `in_flight_writes=None` and a `NotRun`; `stale_reads=("fts",)` lists a stale `fts` | Letting `scan_torn_writes` propagate |
| 5 | `disclose_get` table (Decision 4), including the conjunction: a target the snapshot allows whose own read says `confidential` is withheld, and the reverse | Checking only the snapshot, or only the record |
| 5 | `withheld` equals the number of removed relations + provenance ids + ancestors | Counting distinct objects instead of entries |
| 5 | Document-labelled `NotRun`s are aggregated with a count-only reason; the allowlisted labels pass with fixed reasons | Forwarding `NotRun.reason` |
| 5 | Guard matrix row for `get`, plus its positive control | — |
| 6 | `concept_neighbors`: out and in edges, typed and untyped, `derived_from`; missing id raises | Using `store.neighbors` (out only) |
| 6 | `disclose_navigate`: a confidential neighbor in either direction is removed and counted; a withheld target returns `concept_id: null, withheld: 1`; `skipped_count` becomes `graph_build` | Filtering only outbound edges |
| 6 | Guard matrix row for `navigate`, plus its positive control | — |
| 7 | **Golden (first, green on `main`)**: `openkos next` stdout for bootstrap, missing index, unextracted with a declination and a skip notice, multi-source-uncovered with an unspellable-id declination, and an open contradiction | Changing any reason string or rendering order |
| 7 | AST: every `NextAction(` call in `next_action.py` passes `subjects=`, and every `record_declination(` call passes `subjects=` | A new tier without subjects |
| 7 | Per-tier subjects equal Decision 6's table (one test per tier) | Omitting `related_ids` from the below-source tier |
| 7 | `LintFinding.related_ids` values for both sensitivity kinds; equality ignores it | `compare=True` (breaks existing equality tests) |
| 7 | `disclose_pending`: undeclared subjects withheld; a confidential subject withholds the action; misaligned declination subjects withhold all; skip notices become a count | Treating `None` like `()`; zipping misaligned tuples |
| 7 | Guard matrix row for `pending`, plus a positive control with the flag on | — |
| 8 | `chat_client` passes exactly the six kwargs to a recording factory, honoring `resolve_task_model`; `resolve_local_exemption` truth table (4 cases) | Dropping `timeout=`; `or` for `and` |
| 8 | `cli/main.py`'s `_chat_client` and `_resolve_local_exemption` are single-`return` delegators (AST), and each target has exactly one definition under `src/`; `test_chat_timeout_wiring.py` and the application layering guard stay green unchanged | A second copy of the body |
| 8 | Progress byte-identity: over the same fixture, `answer(progress=None)` and `answer(progress=recorder)` send equal `messages` lists (recording fake LLM) and return equal `AnswerResult`s; the recorder saw phases in order, `completed` strictly increasing, `total` 3 (4 with `sufficiency_check`) | Emitting when `progress is None`; wrong total with the sufficiency check |
| 8 | `run_query` passes `progress` to `answer` (spy) | Dropping the kwarg |
| 8 | `excerpted_ids`, `omitted_ids` and `history_truncated_ids` align index-for-index with their title lists on the existing #882 and history fixtures | Appending the id outside the title's statement |
| 9 | Conjunction: with a recording fake LLM, the canary body is in the prompt only for flag on **and** a local backend; never for flag off, and never for flag on with a remote backend | `include_confidential=ctx.expose_confidential` |
| 9 | `disclose_query`: citations filtered; one withheld citation withholds the answer; misaligned title lists are dropped; skip notices become a count | Keeping the answer when a citation is withheld |
| 9 | Tool-error table: each row's exception gives its code and retryable flag with a fixed message; the table is ordered subclass-first (checked pairwise) | Moving `OllamaError` above its subclasses |
| 9 | Cancellation: a query blocked on an `Event` is cancelled; no response is written for it; a later `ping` and `tools/call` are answered; releasing the worker afterwards writes nothing | Writing a response after `CancelledError` |
| 9 | Progress: with a `progressToken`, notifications arrive strictly increasing and all before the response; without a token, none; late progress after the response is dropped | Sending progress without a token; a missing monotonic guard |
| 9 | `stale_index` warning on `query` only; `in_flight_write` on every tool | Declaring `stale_reads` on `get` |
| 9 | Guard matrix row for `query`, with every `Ollama*` injection and the positive control | — |
| 9 | Subprocess tier: `query` with the poisoned host returns `isError` `ollama_unavailable`, `retryable: true` (`cross_platform_smoke`) | — |
| 10 | Docs only; existing doc tests (`test_adr_index.py`, help-text checks) stay green | — |

## Threat Matrix

This change adds a process-integration boundary: another program launches
`openkos mcp` and speaks to it over stdio. The skill's standard rows are
mostly not applicable, and the rows that matter for this boundary are
listed after them.

| Boundary | Applicability | Design response | Planned RED tests |
| --- | --- | --- | --- |
| Documentation-like paths | N/A: nothing is classified or executed by file type | — | — |
| Git repository selection | N/A: no git invocation; read tools never commit. The analogous workspace-root authority is below | — | — |
| Commit state | N/A: nothing is committed | — | — |
| Push state | N/A | — | — |
| PR commands | N/A | — | — |
| **Workspace root selection** | Applicable: `--workspace DIR` chooses what is served | Resolved once, validated with `config.require_workspace` and `read_config` before serving; one process serves one root; no request can change it | Slice 4: refusal for a non-workspace; nothing on stdout before validation |
| **Caller-supplied concept ids** | Applicable: `get`/`navigate` take a path-shaped id | `resolve_concept_path`: canonicalization, `..`, absolute, reserved and symlink-escape refusals, before any read | Slice 5: `../../etc/passwd`, `/abs`, `index`, a symlinked segment all give `concept_not_found` and read nothing |
| **stdout integrity** | Applicable | `claim_stdio` (fd-level redirect plus `sys.stdout` rebinding); single writer on the loop thread | Slice 2 subprocess test; slice 4 subprocess tier |
| **Framing across platforms** | Applicable | Binary streams; `\r\n` accepted on input; `\n` only on output | Slices 2 and 4 |
| **Disclosure through error text** | Applicable | Fixed messages for every protocol and tool error; tracebacks to stderr only | Slices 3 and 9; the enumeration guard's injected exceptions |
| **Environment inheritance** | Applicable: the server inherits `OLLAMA_HOST` | The same locality checks as the CLI; stderr advisory for a remote embedding host; the subprocess tier runs with a poisoned host so "model-free" stays checkable | Slice 4 and 9 subprocess tests |
| **Resource exhaustion by the peer** | Accepted risk: the peer is a local process the user launched; no line-length cap or worker bound (ADR-0021 accepted risk) | Documented | — |

## Migration / Rollout

No migration is needed. Nothing is persisted, and no workspace file, config
key or derived store changes. Reverting the slices removes the verb; a client
configured to launch it then fails to start the server, with no effect on the
workspace.

**Slices** (auto-chain, stacked-to-main). This design adjusts the proposal's
seven slices to **ten**, because the two new services, the id lists and the
transport's fd handling do not fit the proposal's per-slice budgets. The
estimates count authored lines at this codebase's docstring density.

| Slice | Content | Estimate |
| --- | --- | --- |
| 1 | Disclosure predicate and set sibling, their tests, ADR-0028 and its index row | ~270 |
| 2 | `mcp/__init__.py`, `mcp/transport.py`, transport tests including the fd-hygiene subprocess test, ADR-0027 and its index row | ~360 |
| 3 | `mcp/server.py` core (lifecycle, dispatch, protocol errors, generic `-32603`, `run_in_worker`), `mcp/tools.py` skeleton (`Tool`, empty `REGISTRY`, `validate_arguments`, `execute`), tests with test-only tools | ~400 |
| 4 | `openkos mcp` verb, `_READ_ONLY_COMMANDS`, `serve()`, `mcp/gate.py` skeleton (`Snapshot`, `take_snapshot`, `finish` without warnings), the canary fixture and guard with `leaky_probe`, layering tests, the subprocess tier | ~390 |
| 5 | `application/concept_read.read_concept`, `application/consistency.py`, `gate.disclose_get` and warnings in `finish`, the `get` tool, `concept_not_found`/`read_failed` mapping, guard row | ~400 |
| 6 | `concept_read.concept_neighbors`, `gate.disclose_navigate`, the `navigate` tool, guard row | ~280 |
| 7 | `next` golden first; `NextAction.subjects`, `declination_subjects`, `record_declination(subjects=)`, `LintFinding.related_ids`; `gate.disclose_pending`, the `pending` tool, guard row | ~380 |
| 8 | `application/backends.py` and the CLI delegators; `answer`/`run_query` `progress`; `AnswerResult` id lists | ~360 |
| 9 | The `query` tool, `gate.disclose_query`, the Ollama/FTS error rows, cancellation, progress notifications, `stale_index`, guard row, subprocess `ollama_unavailable` | ~400 |
| 10 | `docs/cli.md` (entry, flags, client configuration example, the flag-off completeness note, the per-object prose rule), `docs/architecture.md`, `docs/roadmap.md` | ~150 |

The total is about 3,390 lines against the proposal's 2,400–2,800. The
difference is the two new application services, the id lists, the
fd-level stdout guard, and the positive controls and matrix of the
enumeration guard. Slices 3 and 9 sit at the budget; if either grows, split
3 into "lifecycle" and "tools/call plus validator", and 9 into "query tool
and disclosure" and "cancellation and progress" (the latter tested with a
test-only blocking tool).

## Open Questions / spec alignment

None of these blocks the design. The first is a verification item; the rest
are wording items for the spec author and the orchestrator.

- [ ] **Argument validation errors.** This design returns an `isError` tool
  result with `invalid_arguments` for schema violations, and keeps `-32602`
  for a malformed `params` envelope and an unknown tool. That follows my
  reading that revision 2025-11-25 asks for input-validation failures as
  tool execution errors (SEP-1303), so the model can correct itself. The
  proposal's table says `-32602` for "invalid params". **Verify against the
  2025-11-25 tools page before slice 3 merges**; if it disagrees, the fix is
  one row of the mapping.
- [ ] **Module layout and layering.** Two new application services
  (`concept_read.py`, `consistency.py`) are added so that `mcp/` never parses
  frontmatter or reads the ledger. The `mcp` import allowlist therefore
  becomes: `openkos.application.*`, `openkos.sensitivity` (from `gate.py`
  only), `openkos.config`, `openkos.read_outcome`, `openkos.llm.base`,
  `openkos.llm.ollama` (the client class and the exception types the error
  table maps), and `openkos.state.fts` (`FtsUnavailable` only). `openkos.graph`
  is no longer imported by `mcp/`, because `navigate` goes through
  `concept_read`.
- [ ] **Backend relocation wording.** The definitions of `chat_client` and
  `resolve_local_exemption` live in `application/backends.py`, with the
  client class injected. `cli/main.py` keeps `_chat_client` and
  `_resolve_local_exemption` as one-line delegators, not repointed call
  sites (Decision 7). The spec should say "the definition moves; the CLI
  delegates; behavior and the CLI's test seam are unchanged".
- [ ] **`withheld` semantics.** It counts removed **entries** per channel,
  not distinct objects (Decision 3). Skip notices are reported as
  `skipped_documents`, not added to `withheld`.
- [ ] **Every result carries `not_run`.** It is required next to `withheld`
  and `warnings`, with fixed reasons and an allowlisted label vocabulary.
  `stale_index` cannot produce a `not_run` entry, because
  `stale_index_names` never raises.
- [ ] **An unreadable `get` target under `--expose-confidential`** is a
  success result with `concept: null` and a `concept_read` `not_run` entry,
  not `read_failed`. With the flag off, it is `withheld: 1`.
- [ ] **Allowed-set semantics.** An id with no walked document (a dangling
  ancestor or relation target) is withheld even under
  `--expose-confidential`.
- [ ] **`query`'s answer text** is withheld (`answer_withheld: true`) when
  any of its citations is withheld. `AnswerResult` gains `excerpted_ids`,
  `omitted_ids` and `history_truncated_ids`; the `query-answer` delta should
  name them next to the progress callback.
- [ ] **`next-action-pointer` delta.** `NextAction.subjects` distinguishes
  `None` (undeclared, always withheld) from `()` (declared subject-free).
  `NextResult.declination_subjects` is a parallel tuple, and a misaligned
  one withholds every declination. `lint.LintFinding.related_ids` is new,
  additive and excluded from equality; lint's output is unchanged.
- [ ] **Progress vocabulary.** Phases are `retrieving`, `assembling`,
  `checking` (only with the sufficiency check) and `synthesizing`;
  `progress` starts at 0 and `total` is 3 or 4.
- [ ] **`navigate`** returns inbound and outbound neighbors with a
  `direction` field.
- [ ] **Workers are daemon threads**, not `asyncio.to_thread`, so the process
  exits on end of input without joining abandoned reads (ADR-0021 D2 "or
  equivalent"; recorded in ADR-0027 with the write-tool caveat). On end of
  input, in-flight requests are abandoned and the process exits 0.
- [ ] **`mcp` joins `_READ_ONLY_COMMANDS`.**
- [ ] **Slices: ten, not seven** (Migration / Rollout).

## Orchestrator verification (2026-09-26)

The open protocol point was verified against https://modelcontextprotocol.io/specification/2025-11-25/server/tools, section "Error Handling":
- **Input validation errors** are *Tool Execution Errors*, reported in a result with `isError: true`.
- **Unknown tools** and requests that fail the `CallToolRequest` schema are *Protocol Errors*, reported as `-32602`.

The design's reading is therefore confirmed: invalid arguments return `isError` with `invalid_arguments`, and an unknown tool or a malformed envelope returns `-32602`.
