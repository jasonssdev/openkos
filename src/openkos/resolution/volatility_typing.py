"""Read-only LLM volatility-tier suggestion over concept TYPES present in a
bundle (freshness-suggest-windows, S2 -- `suggest-volatility`).

Mirrors `resolution/edge_typing.py`'s leaf structure one layer over: this
module never imports `openkos.config`; the caller supplies an `LLMBackend`,
never an `OllamaClient` constructed here. Importing the `BackendError`
TYPE from `openkos.llm.base` (issue #1057 Phase 2a, Decision 3) keeps that
discipline intact: `base.py` is itself a config-free stdlib leaf, and the
error family is the failure contract every `LLMBackend` caller already
speaks -- backend-agnostic, so this module never needs a concrete
backend's own module to catch its own `llm.chat`'s failure.

A `BackendError`-family exception raised by `llm.chat` mid-loop STOPS the
loop but never discards paid-for work (issue #441): each completed type
cost one real LLM call, so `suggest_volatility` returns a
`TierSuggestionBatch` carrying every completed `TierSuggestion`
(sorted-name order, one per suggested type) plus the failure that stopped
the loop and the 1-based index of the type whose chat raised. A complete
run returns `failure=None`. Only PARSING and VALIDATION failures degrade a
single type's suggestion -- those never stop the loop, and no suggested
type is ever skipped or dropped; the one type-level skip is the documented
sampled-docs-all-excluded filter, which never calls `llm.chat` for that
type at all.

Unlike `edge_typing` (per-EDGE suggestion), this module operates per concept
TYPE: it reuses `lint.collect_docs` to group every readable, parseable doc
by its `type` frontmatter field, then samples a small, DETERMINISTIC subset
of that type's concept bodies (design's "Deterministic Sampling Rule") to
show the LLM -- one `llm.chat` call per type, never per concept.
"""

import hashlib
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path

from openkos import lint, sensitivity
from openkos.llm import parsing, prompting
from openkos.llm.base import BackendError, LLMBackend, Message
from openkos.llm.prompts import load_prompt
from openkos.model import okf, types

N_SAMPLE_CONCEPTS = 5
"""Per type, the number of concepts (ordered by sorted `identity`) whose
bodies are shown to the LLM (design's Deterministic Sampling Rule)."""

M_TRUNCATE_CHARS = 1000
"""Each sampled concept body is truncated to this many characters before
being included in the prompt (design's Deterministic Sampling Rule)."""

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid suggestion JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`edge_typing._MALFORMED_REPLY_RATIONALE`)."""

_DEGRADED_RATIONALE_FALLBACK = "no rationale provided for a fail-closed degrade"
"""Stable rationale fallback for a well-formed-JSON reply whose `tier` is
missing/non-string/invalid (`suggested_tier=None`) AND whose parsed
`rationale` is empty or whitespace-only. Distinct from
`_MALFORMED_REPLY_RATIONALE`, which is for a reply that could not be parsed
as a JSON object at all -- this constant upholds `TierSuggestion.rationale`'s
"never blank on the fail-closed degrade paths" invariant when the model DID
reply with parseable JSON but left `rationale` blank."""

_SYSTEM_PROMPT = load_prompt("volatility_typing/system")
"""Stable system half of the 2-message prompt (mirrors
`edge_typing._SYSTEM_PROMPT`): the JSON-only instruction baked into system
text; the `user` message carries the type name, current default tier, and
the sampled concept bodies."""


@dataclass(frozen=True)
class TierSuggestion:
    """One concept TYPE's LLM-suggested volatility tier + rationale.

    Ephemeral -- never a persisted OKF type or `bundle`/`state` file."""

    type_name: str
    """The concept type this suggestion is for (e.g. `"Person"`)."""
    current_default: str
    """`types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, "")` -- the
    registry's current default tier for this type, `""` if `type_name` is
    not a registered type."""
    suggested_tier: str | None
    """A value in `types.VOLATILITY_TIERS`, or `None` on a fail-closed
    degrade (malformed reply, missing/non-string `tier`, or a `tier` value
    that is not a member of `types.VOLATILITY_TIERS`) -- never surfaced as
    if it were a valid tier."""
    rationale: str
    """Free-text explanation; may be blank on a well-formed reply that
    omitted one, but is never blank on the fail-closed degrade paths."""


@dataclass(frozen=True)
class ComputedTier:
    """What a freshly asked type's answer is keyed on, so the caller can persist
    it and a later run can recognise the same question (#1332)."""

    prompt_digest: str
    """sha256 over the exact messages sent: the type, its default, the sampled
    bodies, the rationale language and the rubric. Any change re-asks."""
    input_refs: tuple[str, ...]
    """The concept ids whose bodies the prompt carried."""


def prompt_digest(messages: list[Message]) -> str:
    """The identity of one question: a hash of the exact chat messages."""
    payload = json.dumps(
        [[m["role"], m["content"]] for m in messages], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TierSuggestionBatch:
    """Outcome of one `suggest_volatility` run: every completed suggestion
    plus, when the loop was cut short, the failure that stopped it (issue
    #441). Ephemeral, like `TierSuggestion` -- never a persisted OKF type
    or `bundle`/`state` file.

    Partials ride the RETURN, not an exception payload, on purpose (mirrors
    `adjudication.AdjudicationBatch`/`edge_typing.EdgeSuggestionBatch`): an
    exception-carried partial forces every caller into a try/except that
    must remember to salvage the results off the exception, and the one
    caller that forgets reintroduces exactly the work-discarding bug this
    type exists to fix. A return value cannot be silently dropped by an
    unhandled raise."""

    results: list[TierSuggestion]
    """Every completed suggestion, in sorted-type order -- each one was
    fully paid for (its `llm.chat` call succeeded) before the loop
    stopped."""
    failure: BackendError | None = None
    """The `BackendError`-family exception that stopped the loop, or `None`
    for a complete run."""
    failed_index: int | None = None
    """1-based index of the TYPE whose `llm.chat` raised `failure`, counted
    over the sorted types ENTERING the loop -- a type the post-sampling
    re-check filtered out (no chat call, no suggestion) still consumes a
    position, exactly as `adjudicate_candidates`'s no-readable-members
    short-circuit consumes an index (#441) -- so this is NOT a count of
    chat calls attempted. `None` when the run completed. The failed type
    produced no suggestion and no `on_progress` call, and no later type
    was ever prompted."""
    deferred: int = 0
    """How many types a `max_calls` bound left unasked: each would have been a
    chat call (a type the post-sampling re-check filters out never would, so it
    is not counted). `0` for an unbounded run and for one the bound did not
    cut. A deferred type is simply asked on a later run; nothing is persisted
    for it, so there is nothing to resume from."""
    computed: dict[str, ComputedTier] = field(default_factory=dict)
    """One entry per type the model was ASKED this run (a served type is not
    here), keyed by type name. Includes a degraded answer; the caller persists
    only the ones with a valid tier."""


def _reread_sensitivity_blocked(
    doc: lint.LintDoc,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> bool:
    """Re-read `doc.path`'s frontmatter fresh and check its OWN
    `sensitivity` value via `sensitivity.should_block` -- `LintDoc` itself
    carries no `sensitivity` field (only `freshness`/`type`/`volatility`),
    so this is a genuine re-read, mirroring query's fresh re-read
    (`retrieval/answer.py:211-214`) rather than a cheap re-check of
    already-parsed metadata (directory-walk-observability follow-up,
    defense-in-depth).

    `include_confidential` is taken directly by this guard (correction
    batch, post-4R-review FIX 2), matching the sibling `_load_doc`/
    `_load_members` contract in `contradiction.py`/`edge_typing.py`/
    `adjudication.py` exactly -- the earlier bare `(doc) -> bool` signature
    relied on the caller wrapping every invocation in its own `if not
    include_confidential:` check, which a maintainer calling this guard
    directly (by analogy with its 3 siblings, which all take the flag) could
    easily omit, silently reintroducing a fail-open bypass. `True` short-
    circuits to `False` (never blocked) BEFORE reading anything, preserving
    the original zero-I/O cost of the bypass path.

    An unreadable/unparseable re-read degrades to `True` (exclude): if this
    doc's current sensitivity cannot be verified, the fail-closed default is
    to keep it out of the prompt, matching `sensitivity.sensitive_concept_ids`'s
    own unreadable/unparseable -> blocked branch. This applies regardless of
    `include_confidential` -- a read/parse failure is a stronger "cannot
    verify at all" signal, orthogonal to the deliberate escape hatch, which
    only ever bypasses a SUCCESSFULLY re-read sensitivity value.

    Note (design): this verb's leak surface is narrower than the other
    three -- ids come from a live `lint.collect_docs` walk (not
    `graph.db`), so a subtree that lost its `r` bit is already absent from
    `docs`. This guard ships for uniform defense-in-depth per locked scope
    and future-proofs a possible index-sourced refactor. Correction batch
    FIX 3: callers now apply this guard only to the SAMPLED subset (at most
    `N_SAMPLE_CONCEPTS` docs per type, via `_sample_docs_by_type`), not the
    full bundle -- see `suggest_volatility`'s docstring.

    `local_exemption` (issue #240) is the second short-circuit, and it
    short-circuits on exactly the same terms as `include_confidential`: a
    verified-local backend means nothing leaves the machine, so there is
    nothing to re-check and no reason to pay for the re-read. Defaults to
    `False`, fail-closed."""
    if include_confidential or local_exemption:
        return False
    try:
        text = doc.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    try:
        metadata, _body = okf.load_frontmatter(text)
    except (
        okf.FrontmatterError
    ):  # any parse failure fails closed (return, not continue)
        return True
    return sensitivity.should_block(
        metadata,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )


def _sample_docs_by_type(docs: list[lint.LintDoc]) -> dict[str, list[lint.LintDoc]]:
    """Group `docs` by `type` (blank-type docs excluded -- a doc with no
    `type` frontmatter key is not a real concept type), then deterministically
    sample each type's docs: the first `N_SAMPLE_CONCEPTS` docs of that type
    ordered by sorted `identity` (design's Deterministic Sampling Rule).

    Sorting by `identity` -- not bundle-walk order -- is what makes the
    INPUT selection reproducible regardless of filesystem walk order; the
    LLM's OUTPUT need not be deterministic, only what it is shown.

    Correction batch (post-4R-review resilience FIX 3): returns `LintDoc`
    objects, not truncated body strings (renamed from
    `_sample_bodies_by_type`) -- so the caller can apply
    `_reread_sensitivity_blocked` to ONLY this small per-type sampled set
    instead of re-reading every doc's frontmatter across the FULL bundle
    before sampling. The previous full-bundle re-read was needless I/O for
    this verb's weakest-leverage guard (design note: ids come from a live
    `lint.collect_docs` walk, so the walk-miss leak this guard defends
    against cannot fire here in the first place) -- a bundle with thousands
    of docs paid for a frontmatter re-read on every one of them just to
    sample `N_SAMPLE_CONCEPTS=5` per type."""
    by_type: dict[str, list[lint.LintDoc]] = {}
    for doc in docs:
        if not doc.type:
            continue
        by_type.setdefault(doc.type, []).append(doc)
    sampled: dict[str, list[lint.LintDoc]] = {}
    for type_name, type_docs in by_type.items():
        ordered = sorted(type_docs, key=lambda d: d.identity)
        sampled[type_name] = ordered[:N_SAMPLE_CONCEPTS]
    return sampled


def _build_messages(
    type_name: str,
    current_default: str,
    bodies: list[str],
    rationale_language: str | None = None,
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors `edge_typing._build_messages`):
    system rubric + a user turn listing the type name, its current default
    tier, and the sampled concept bodies.

    `rationale_language` (issue #812) appends
    `prompting.RATIONALE_LANGUAGE_TEMPLATE` to the SYSTEM turn, and only
    when a workspace pinned one. `None` -- the default -- sends
    `_SYSTEM_PROMPT` verbatim, byte-identical to the pre-#812 prompt; see
    `edge_typing._build_messages` for the full reasoning, which is the same
    here for the same reason. It bites HARDER at this seam: one call covers
    a whole concept TYPE, so a single type whose sampled bodies lean
    Spanish reports its whole row in Spanish beside English neighbours."""
    body_block = "\n\n".join(bodies)
    user_content = (
        f"TYPE: {type_name}\nCURRENT DEFAULT TIER: {current_default}\n\n{body_block}"
    )
    return [
        {
            "role": "system",
            "content": prompting.with_rationale_language(
                _SYSTEM_PROMPT, rationale_language
            ),
        },
        {"role": "user", "content": user_content},
    ]


def _parse_reply(raw: object) -> tuple[str | None, str]:
    """Fail-closed parse + validate of one type's LLM reply: never raises.
    An unparseable or non-object reply degrades to `(None,
    _MALFORMED_REPLY_RATIONALE)`. Otherwise `tier` is coerced to a string
    (non-string -> `None`) and checked against `types.VOLATILITY_TIERS`: a
    value that is not a member degrades to `suggested_tier=None`. On EITHER
    of those two degrade branches, the parsed `rationale` is used as-is if
    it is a non-blank string, but falls back to
    `_DEGRADED_RATIONALE_FALLBACK` when it is missing, non-string, or
    blank/whitespace-only -- `TierSuggestion.rationale` is never blank on a
    fail-closed degrade path (its own docstring's invariant). On the
    successful (non-degrade) path, `rationale` is kept as-is (including
    blank) since a well-formed reply is allowed to omit one."""
    data = parsing.extract_json_object(raw)
    if data is None:
        return None, _MALFORMED_REPLY_RATIONALE

    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""

    tier_raw = data.get("tier")
    if not isinstance(tier_raw, str):
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK

    tier = tier_raw.strip()
    if tier not in types.VOLATILITY_TIERS:
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK
    return tier, rationale


_Planned = tuple[
    int, str, list[lint.LintDoc], list[Message], str, TierSuggestion | None
]


def _plan_questions(
    bundle_dir: Path,
    *,
    include_confidential: bool,
    local_exemption: bool,
    rationale_language: str | None,
    skip_types: Collection[str],
    served: Callable[[str, str], TierSuggestion | None] | None,
) -> tuple[list[_Planned], int]:
    """Every question this run would put to the model, in sorted-type order,
    each with the answer `served` already holds for it (or `None`), plus the
    number of types entering the loop. Reads, never asks."""
    blocked = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    docs, _skip_notices = lint.collect_docs(bundle_dir)
    docs = [doc for doc in docs if doc.identity not in blocked]
    sampled_docs = {
        type_name: type_docs
        for type_name, type_docs in _sample_docs_by_type(docs).items()
        if type_name not in skip_types
    }
    planned: list[_Planned] = []
    for type_index, type_name in enumerate(sorted(sampled_docs), start=1):
        type_docs = [
            doc
            for doc in sampled_docs[type_name]
            if not _reread_sensitivity_blocked(
                doc,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
            )
        ]
        if not type_docs:
            continue
        bodies = [doc.body[:M_TRUNCATE_CHARS] for doc in type_docs]
        current_default = types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, "")
        messages = _build_messages(
            type_name,
            current_default,
            bodies,
            rationale_language=rationale_language,
        )
        digest = prompt_digest(messages)
        hit = served(type_name, digest) if served is not None else None
        planned.append((type_index, type_name, type_docs, messages, digest, hit))
    return planned, len(sampled_docs)


def unanswered_type_count(
    bundle_dir: Path,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
    rationale_language: str | None = None,
    skip_types: Collection[str] = (),
    served: Callable[[str, str], TierSuggestion | None] | None = None,
) -> int:
    """How many chat calls a run with these arguments would issue: the types
    whose exact prompt neither `skip_types` nor `served` already answers. The
    exact price `curate` quotes before it spends anything."""
    planned, _count = _plan_questions(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
        rationale_language=rationale_language,
        skip_types=skip_types,
        served=served,
    )
    return sum(1 for *_rest, hit in planned if hit is None)


def suggest_volatility(
    bundle_dir: Path,
    *,
    llm: LLMBackend,
    include_confidential: bool = False,
    local_exemption: bool = False,
    rationale_language: str | None = None,
    on_progress: Callable[[int, int, TierSuggestion], None] | None = None,
    max_calls: int | None = None,
    skip_types: Collection[str] = (),
    served: Callable[[str, str], TierSuggestion | None] | None = None,
) -> TierSuggestionBatch:
    """Suggest a volatility tier + rationale for every distinct concept TYPE
    present under `bundle_dir`, read-only.

    Reuses `lint.collect_docs` to walk and group the bundle. Returns a
    `TierSuggestionBatch` whose `results` hold exactly one `TierSuggestion`
    per SUGGESTED type, in sorted-name order -- one `llm.chat` call per
    type, never per concept (module docstring).

    A `BackendError`-family exception raised by `llm.chat` stops the loop
    and comes back IN the batch (`failure` set, `failed_index` naming the
    1-based type entering the loop whose chat raised -- see that field's
    docstring for why a filtered-out type still counts) rather than
    propagating (issue #441): propagation made the caller pay for every
    completed call and then discard all of the completed suggestions with
    the raise -- #422's `OllamaGenerationCapped` made that edge fast and
    frequent. Only the `llm.chat` call sits inside the guard;
    reply-parsing/validation failures still degrade that one type's
    suggestion, and a raise from the caller's own `on_progress` still
    propagates untouched. A complete run returns `failure=None,
    failed_index=None`.

    sensitivity-fail-closed-filter (S3b): unless `include_confidential` is
    `True`, the shared `sensitivity.sensitive_concept_ids(bundle_dir)`
    predicate is computed ONCE and any doc whose `identity` is blocked is
    dropped BEFORE `_sample_docs_by_type` ever samples it -- a confidential
    concept's body never reaches the prompt. `include_confidential=True`
    skips the predicate walk entirely, at zero added cost. This walk-based
    `blocked` filter always applies to the FULL bundle (cheap: an id
    membership check, no I/O) and is unaffected by the FIX-3 change below.

    directory-walk-observability follow-up (correction batch, post-4R-review
    resilience FIX 3): the walk-INDEPENDENT per-doc re-check
    (`_reread_sensitivity_blocked`) now runs AFTER `_sample_docs_by_type`,
    against only the sampled subset (at most `N_SAMPLE_CONCEPTS` docs per
    type) -- not against the full bundle beforehand. A type whose sampled
    docs are ALL excluded by either filter yields no suggestion for that
    type at all (it never reaches an `llm.chat` call with an empty body
    list).

    `on_progress` (issue #190, mirroring `suggest_edge_types`'s #134
    contract), if given, is called once per EMITTED `TierSuggestion`, in
    sorted-type order, AFTER that suggestion is built, with `(index, total,
    suggestion)`: `index` is the 1-based count of suggestions emitted so
    far, and `total == len(sampled_docs)` -- the number of distinct types
    entering the loop. A type whose sampled docs are ALL excluded by the
    post-sampling re-check emits no suggestion and fires NO callback, so
    `total` is an UPPER BOUND: the final `index` ends below `total`
    whenever a type was filtered out. The type whose chat RAISED does not
    count either -- it produced no suggestion, so there is nothing to
    report progress on (#441). It never affects the returned batch; an
    exception it raises propagates to the caller (it is the caller's own
    callback).

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.

    `rationale_language` (issue #812) pins the language the model writes its
    `rationale` in, threaded straight through to `_build_messages` and never
    re-derived here -- the same contract, wording and default as
    `edge_typing.suggest_edge_types`'s own parameter, because curate renders
    both suggesters' rationales into one table the operator reads top to
    bottom. `None` -- the default -- assembles the pre-#812 prompt byte for
    byte. The CLI resolves it once from `config.Config.rationale_language`;
    this module stays config-free.

    `max_calls` (the unattended budget's per-stage bound) caps the `llm.chat`
    calls this run MAY issue, in the same sorted-type order: once that many
    calls were issued (a raised one included, it was still spent) every type
    left to ask is counted in `TierSuggestionBatch.deferred` instead. `None` --
    the default, and the only value a CLI run ever passes -- is unbounded and
    leaves the loop byte-identical.

    `skip_types` names types the caller already holds a suggestion for (a fresh
    pending-work row): they are left out of the sample, cost no call and do not
    count toward `total`. Empty -- the default -- changes nothing.

    `served(type_name, prompt_digest)` (#1332) answers a type whose exact
    prompt was already answered: a hit lands in `results` like a fresh answer
    but costs no call, does not spend `max_calls`, does not fire `on_progress`
    and does not count toward its `total`. `None` -- the default -- asks every
    type, byte-identical to before."""
    planned, sampled_count = _plan_questions(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
        rationale_language=rationale_language,
        skip_types=skip_types,
        served=served,
    )
    results: list[TierSuggestion] = []
    computed: dict[str, ComputedTier] = {}
    # Progress counts the questions to ASK; with no `served` this is the
    # historical upper bound, `len(sampled_docs)`.
    total = sampled_count - sum(1 for *_rest, hit in planned if hit is not None)
    calls_issued = 0
    deferred = 0
    served_so_far = 0
    for type_index, type_name, type_docs, messages, digest, hit in planned:
        if hit is not None:
            results.append(hit)
            served_so_far += 1
            continue
        if max_calls is not None and calls_issued >= max_calls:
            deferred += 1
            continue
        # Guard ONLY the chat call (#441): a transport/model failure must
        # not discard the completed suggestions, while parse/validate/
        # progress failures keep their own existing contracts untouched.
        calls_issued += 1
        try:
            reply = llm.chat(messages)
        except BackendError as exc:
            return TierSuggestionBatch(
                results=results,
                failure=exc,
                failed_index=type_index,
                computed=computed,
            )
        suggested_tier, rationale = _parse_reply(reply)
        suggestion = TierSuggestion(
            type_name=type_name,
            current_default=types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, ""),
            suggested_tier=suggested_tier,
            rationale=rationale,
        )
        results.append(suggestion)
        computed[type_name] = ComputedTier(
            prompt_digest=digest,
            input_refs=tuple(doc.identity for doc in type_docs),
        )
        if on_progress is not None:
            on_progress(len(results) - served_so_far, total, suggestion)
    return TierSuggestionBatch(results=results, deferred=deferred, computed=computed)
