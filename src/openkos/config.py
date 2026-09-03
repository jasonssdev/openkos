"""Workspace root: `openkos.yaml`, `AGENTS.md`, and the four refusal conditions.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory (docs/architecture.md:141-154). `bundle/` is not a workspace
on its own -- `raw/` sits outside it by design, so the workspace root is
where the engine's own files live, not the OKF bundle root.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Final, NamedTuple

import yaml

from openkos import fsio
from openkos.model import okf, types

DEFAULT_MODEL = "qwen3:8b"
"""The packaged default Ollama model tag, offered when no `--model` is given."""

TASK_MODEL_KEYS = frozenset(
    {
        "extraction",
        "adjudication",
        "edge_typing",
        "volatility_typing",
        "contradiction",
    }
)
"""Every task `models:` may name (issue #515), keyed by TASK rather than by
verb -- see `Config.models` for why that distinction is load-bearing.

Only `edge_typing` has a harness behind it today (`evals/edge_typing/`, the
sweep in #516). The other four are accepted because restricting the schema
to the one measured task would be arbitrary, NOT because a value for them
is advisable: #508's rule is that a per-task default must be justified on a
fixture, and three of these have no fixture at all. The docs say which is
which; the schema does not pretend to."""

DEFAULT_TASK_MODELS: dict[str, str | None] = {"edge_typing": None}
"""Packaged per-task model defaults (issue #513), overriding `DEFAULT_MODEL`
for the tasks listed here and no others. Since #650 no task ships a value:
`edge_typing`'s key stays listed so the opt-in surface remains visible, and
its `None` means `resolve_task_model` follows the global `model:`.

#513 packaged `gemma2:27b` here on #516's sweep (0.81 relation-TYPE
accuracy on `evals/edge_typing/`'s 17-edge fixture against `qwen3:8b`'s
0.44). #650 inverted the default for three reasons, none disputing that
measurement: (1) the 15.6 GB pull made the out-of-the-box curation path
the broken one -- for a local-first tool the download IS the barrier to
entry; (2) DIRECTION was never measured, and direction is where the
observed errors live (the 2026-08-13 e2e saw `gemma2:27b` reverse at
least two of five asymmetric edges); (3) since #624 every asymmetric
suggestion sits behind per-item consent marked `direction
model-suggested, unverified`, so the accuracy gap buys fewer operator
rejections, not graph quality. Works on install, better if you opt in --
see `RECOMMENDED_TASK_MODELS`."""

RECOMMENDED_TASK_MODELS: dict[str, str] = {"edge_typing": "gemma2:27b"}
"""The documented per-task recommendations (#650): tags that measured best
on a task's harness but are NOT packaged as defaults, with the opt-in being
an explicit `models: {<task>: <tag>}` in `openkos.yaml`.

`edge_typing -> gemma2:27b` carries #516's 0.81-vs-0.44 relation-type
accuracy, costs a 15.6 GB `ollama pull`, and is the worst extractor
measured (0.24/0.00 subject recall against the default's 0.81/0.76) -- so
it must only ever move edge typing. `doctor` and `curate`'s Structure gate
surface this map so the recommendation is discoverable exactly where the
old packaged default used to be diagnosed and consented to."""

DEFAULT_EMBEDDING_MODEL = "bge-m3"
"""The packaged default Ollama embedding model tag (ADR-0006: reliability-
first -- `bge-m3` proved measurably more resilient to transient embed
failures than the previous `qwen3-embedding:0.6b` default). Written into
`openkos.yaml.template` via its own placeholder (`write_config`), and used
as `read_config`'s `is not None` fallback for an omitted or explicit-null
`embedding_model:` key, distinct from the chat `DEFAULT_MODEL`."""

EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...] = (DEFAULT_EMBEDDING_MODEL,)
"""Vetted embedding model tags known to produce 1024-float vectors,
satisfying the `EMBED_DIM` contract (ADR-0006). Default first (=
recommended). This is policy data, not transport/classification, so it
lives here rather than `llm/ollama.py` (D1). Honesty rule: an entry is
added only after a measured 1024-dim embed -- never a guess. Gates ONLY
the interactive embedding-model picker's candidate list; it never gates
`--embedding-model` or a hand-written `openkos.yaml` value (see
`validate_embedding_model`, which checks YAML-safety alone)."""

DEFAULT_REVIEW = True
"""Packaged default for `review`: show a preview and confirm before saving."""

DEFAULT_SENSITIVITY = "private"
"""Packaged default for `default_sensitivity`, matching `openkos.yaml.template`."""

DEFAULT_TYPE_SENSITIVITY_DEFAULTS: Final[dict[str, int]] = {}
"""Packaged default for `type_sensitivity_defaults` (issue #669, ADR-0015;
EMPTIED by #756): no type is born above the workspace `default_sensitivity`
floor unless the operator says so.

**The empty mapping is the policy, not an oversight.** This shipped as
`{"Person": 1}`, and the mechanism is worth having -- but deciding on the
operator's behalf was wrong on the primary use case, a local bundle against
a local backend. There it protects nothing: with
`confidential_local_exemption` on and a verified-local Ollama, confidential
objects participate normally, so the setting produced notices and no
exclusion. And it diluted the signal it is made of -- when 100% of a type
is `confidential`, the marker stops meaning "this one is especially
sensitive" and starts meaning "this is a Person", the same failure that got
the `type_alternative` notice aggregated. Type correlates with risk; it
does not measure it (a Person extracted from published council minutes is
not sensitive). So the offset is documented as a RECOMMENDED opt-in for
anyone working with material about third parties, and the packaged policy
is "none". Please do not restore `{"Person": 1}` on the assumption that an
empty default was an accident.

`read_config` always returns a COPY of this dict, never the shared module
object -- a caller mutating the returned mapping must not corrupt the
packaged default for the next `read_config` call."""

DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION = True
"""Packaged default for `confidential_local_exemption` (issue #240): a
`confidential` concept MAY be sent to an `llm.chat` backend that is
verifiably this machine, without `--include-confidential`.

`True` because local-first is the design: on a stock install Ollama runs on
loopback, nothing leaves the machine, and `sensitivity` -- which governs
EGRESS -- has nothing to protect against. Shipping this `False` would leave
every stock workspace in the state #240 opens by describing: confidential
objects silently absent from every retrieval and resolution pass, and users
trained to pass `--include-confidential` habitually, which disables the one
gate that would matter the day the backend is NOT local.

Setting it `false` restores the pre-#240 blanket behavior (`confidential`
means "no LLM ever, local or not"). It is deliberately a WORKSPACE-level key
rather than a per-command flag: a security policy that depends on
remembering to type a flag on every invocation is not a policy. It never
weakens the fail-closed posture on its own -- the exemption also requires
`OllamaClient.locality.is_local`, so an unknown or unparseable host is still
treated as remote regardless of this value."""

DEFAULT_FRESHNESS_WINDOW = "7d"
"""Packaged default for `freshness_window`, matching `openkos.yaml.template`.

Raw passthrough only -- the `"7d"`/`"2w"` duration grammar is parsed by
`lint.parse_window`, not here (policy stays out of `config`)."""

DEFAULT_CHAT_TIMEOUT = 600.0
"""Packaged default for `chat_timeout`, in seconds, matching
`openkos.yaml.template` and `llm.ollama.DEFAULT_TIMEOUT` (issue #405).

Raised from the 120s that shipped before. 120s was measured too low for the
documents this product targets: across a sweep of 9 sources x 4 sampling
arms x 5 runs, 8 calls timed out, every one of them on a 6-17 KB real
document, while none of the 700-800 B demo fixtures ever did.

This deadline is NOT a lever against runaway generation. The same
measurement found a fixture that timed out 5 of 5 at 120s and 5 of 5 again
at 300s under greedy decoding -- a model that never terminates is not
rescued by a longer wait, and raising this value further would only make the
failure slower to observe. That failure mode belongs to the prompt's
anti-enumeration instruction (#404), not here."""

DEFAULT_MAX_GENERATION_TOKENS = 8192
"""Packaged default for `max_generation_tokens`, in tokens, matching
`openkos.yaml.template` and forwarded to Ollama as `options.num_predict`
(issue #422).

This is a SAFETY RAIL against a non-terminating generation, NOT a
quality-tuning knob -- it is expected never to bind on legitimate work.
Grounded in a real measurement (2026-08-06): five extraction calls through
the project's own `_build_messages`/`_SYSTEM_PROMPT`, run against local
`qwen3:8b` on 17 KB real prose sources, produced `eval_count` of 4154,
1624, 962, 269, 107 -- all with `done_reason: "stop"` (a normal completion,
never truncated). 8192 leaves roughly 2x headroom over the largest
legitimate completed reply observed (4154).

Before this change, nothing bounded how much a chat call could GENERATE:
`chat_timeout` (above) only bounds how long the client WAITS, so a
generation that never terminates burned the full deadline and returned
nothing (issue #422). Reaching this ceiling raises `OllamaGenerationCapped`
(`llm.ollama`) rather than returning a silently truncated reply -- a
mis-set rail is loud, not silent."""

PROMPT_CONTEXT_ALLOWANCE = 4096
"""Tokens reserved for the PROMPT half of the context window (issue #691).

Measured, not guessed. Both prompt shapes the engine builds were sent to
local `qwen3:8b` at their largest, and Ollama's own `prompt_eval_count` read
back (2026-08-14, Spanish prose -- which tokenizes worse than English, so
this is the unfavourable case):

- extraction at a FULL `extraction.concept._CHUNK_TARGET` window (4000
  characters of source plus the system prompt, 12043 characters in all):
  **2707 tokens**;
- `query` with `limit`=5 retrieved bodies packed in full (13516
  characters): **3263 tokens**.

Deliberately NOT tuned down to the extraction case alone: `num_ctx` is one
setting shared by every chat seam, so it must be sized for the largest
prompt any of them builds.

**This value is UNDERSTATED for the unchunked band, and the reading above
that `query` is the real bound is WRONG (issue #829).** Both measurements
above are of a CHUNKED window, but `extraction.concept._CHUNK_THRESHOLD` is
18 000, so a source up to that length is sent WHOLE in one call. Re-measured
2026-08-23 against the unchunked extraction prompt, Spanish (the
unfavourable case), reading Ollama's own `prompt_eval_count` per call:

| source chars | prompt tokens | tokens/char |
| --- | --- | --- |
| 4 000 (one chunk) | 2 782 | 0.696 |
| 9 688 | 4 140 | 0.427 |
| 12 000 | 4 720 | 0.393 |
| 16 000 | 5 666 | 0.354 |
| 17 999 (at the threshold) | **6 142** | 0.341 |

The 4 000-char row reproduces the 2 707 above, so the original calibration
stands for the chunked band; what it does not cover is the other one.
Extraction at the threshold takes **6 142 tokens, 2 046 over this reserve**,
which makes extraction the largest prompt in the product rather than
`query`'s 3 263.

Note the shape, because #829 extrapolated it the other way and got ~7 700:
tokens/char FALLS with length as the fixed system prompt amortizes, so a
rate read off a short source overstates a long one.

The consequence is live on shipped defaults: 6 142 + 8 192 = 14 334 exceeds
`DEFAULT_CONTEXT_WINDOW`, so the window -- not the ceiling -- is what bounds
a reply at the threshold. `llm.ollama` now reads both counters back and
names whichever bound actually bound, so the misattribution is fixed; the
RESERVE is deliberately left at 4096, because raising it raises
`minimum_context_window` and therefore `DEFAULT_CONTEXT_WINDOW`, which is a
memory-footprint decision (see that constant's own 7.2 GB note) rather than
a calibration one."""

DEFAULT_CONTEXT_WINDOW = 12288
"""Packaged default for `context_window`, in tokens, forwarded to Ollama as
`options.num_ctx` (issue #691).

Exactly `minimum_context_window(DEFAULT_MAX_GENERATION_TOKENS)`, so the
shipped value and the enforced floor cannot drift apart.

What that identity does NOT guarantee is that the window never binds before
the ceiling: the reserve half of the sum is calibrated for the CHUNKED band
(see `PROMPT_CONTEXT_ALLOWANCE`), and an unchunked extraction prompt at the
`extraction.concept._CHUNK_THRESHOLD` boundary measures 6 142 tokens, which
leaves 12288 - 6142 = 6 146 tokens of generation room against the 8 192
ceiling. Issue #829 weighed the three ways out -- raise this window to
~14 336, lower the prose chunk threshold, or leave both -- and chose to
LEAVE BOTH, deliberately:

- Every legitimate reply ever measured fits with 2.6x room to spare: the
  largest across the #828/#830 sweeps is 2 315 tokens
  (`evals/generation_runaway/`), against the 6 146 of worst-case room. Only
  a runaway -- a reply that generates up to whatever bound exists -- meets
  the window first, and a runaway cut at 6 146 costs strictly less than one
  cut at 8 192 while being exactly as unusable.
- Raising the window charges every user more KV cache (the 7.2 GB note
  below) to protect headroom no legitimate reply uses; lowering
  `_CHUNK_THRESHOLD` costs more chat calls per source, and #454 measured
  why the threshold sits where it does.
- The one real harm the gap caused -- the capped exception blaming the
  wrong setting -- was fixed at the source in #848: `llm.ollama` reads both
  counters back and names whichever bound actually bound.

Left unpinned, `ollama ps` showed `qwen3:8b` reserving a 32768-token window
and 10 GB (2026-08-14) -- weights are ~5 GB, the rest is KV cache for a
window the engine never fills. KV cache scales linearly with the window, and
pinning this value was measured back on the same machine and model:
**7.2 GB at CONTEXT 12288**, a 2.8 GB saving. On a 48 GB machine that is
invisible; on the 16 GB machine this product targets it is the difference
between one slot that eats the whole machine and room to work."""


def minimum_context_window(max_generation_tokens: int) -> int:
    """The smallest `context_window` that cannot truncate a reply the
    ceiling permits, for a prompt within `PROMPT_CONTEXT_ALLOWANCE`
    (issue #691).

    Ollama's `num_ctx` bounds the prompt and the completion TOGETHER, so the
    floor is the prompt allowance plus the generation ceiling -- a window
    sized for the prompt alone would cut off exactly the replies
    `max_generation_tokens` is set to permit.

    The guarantee is exactly as strong as the reserve, and the reserve is
    calibrated for the CHUNKED band: an unchunked extraction prompt at the
    `_CHUNK_THRESHOLD` boundary exceeds it by 2 046 tokens, so at that
    extreme the window binds before the ceiling. Weighed and accepted, not
    an oversight -- `DEFAULT_CONTEXT_WINDOW`'s docstring records the #829
    decision and the measurements behind it.

    Kept as a function rather than a constant because the two settings are
    not independent: a workspace that raises its generation ceiling raises
    this floor with it, and the pair that silently truncates is precisely a
    raised ceiling next to an unchanged window.
    """
    return PROMPT_CONTEXT_ALLOWANCE + max_generation_tokens


DEFAULT_CONCURRENT_EXTRACTION = False
"""Packaged default for `concurrent_extraction` (issue #744): whether the
chunked extraction fan-out sends its windows concurrently.

`False`, and unlike `DEFAULT_UNION_JUDGE` this default is NOT a hedge about
quality -- #739 measured no quality movement on either axis at any
concurrency level. It is off because the entire gain is conditional on
`OLLAMA_NUM_PARALLEL` being at least two on the `ollama serve` process,
which openkos can document and cannot set. Against a default server the
requests queue perfectly and nothing is gained, so defaulting this ON would
advertise a speedup most installations cannot deliver.

Turning it on where the server is NOT configured returns the same results in
the same order -- but not for free. Each request's `chat_timeout` keeps
running while that request queues behind its sibling, so per-call wall time
inflates and a workspace whose timeout sits near its single-call latency can
begin timing out on sources that used to succeed. Results are never wrong,
only late; this is the reason the key is opt-in rather than a default that
"cannot hurt".

The measured speedup, memory and quality numbers live in
`evals/ingest_concurrency/report.md` and are summarised for operators under
`concurrent_extraction` in `docs/cli.md`. They are deliberately not restated
here so that one correction reaches every reader."""


DEFAULT_UNION_JUDGE = True
"""Packaged default for `union_judge` (design D9, #456): the union-of-runs +
selector-judge extraction pipeline (`extraction.concept.extract_concept_union`)
replaces the blind, position-based `_MAX_OBJECTS_PER_SOURCE` truncation.

`True` because the union+judge path is the measured improvement: two runs
merged and judge-selected recovers subjects a single run's cap silently
discarded (design proposal). The rollback is one line -- flipping this
constant to `False` restores the single-run, single-cap `extract_concept`
path byte-for-byte -- so shipping the improved path as the default carries no
un-recoverable risk, unlike `confidential_local_exemption` (a security
posture) or `default_sensitivity` (data classification)."""

DEFAULT_SUFFICIENCY_CHECK = True
"""Packaged default for `sufficiency_check` (#760): `query` asks one cheap
model call whether the assembled context can answer the question at all, and
refuses instead of synthesising when it cannot.

`True` because what it prevents is a CORRECTNESS defect (#753: the model
answers a conceptual question from its own knowledge and the reply wears the
bundle's authority), and because the cost side was measured rather than
assumed. `evals/query_sufficiency/`, qwen3:8b, 10 runs: zero false refusals
across 100 grounded checks, while refusing all ten adjacent questions --
including the three the shipped `USED:` attribution does not catch and the
one #753 itself reports.

The price is one extra chat call per answered query, a measured median of
1.12s against a default Ollama, which serialises. That is why this is a key
and not a constant: a workspace whose bundle is broad enough that adjacent
questions are rare pays latency for a refusal it will seldom need, and
turning it off restores the pre-#760 path byte-for-byte. The `USED:`
attribution still strips citations off an ungrounded answer either way, so
`False` is degraded, never unguarded."""

DEFAULT_RATIONALE_LANGUAGE: Final[str | None] = None
"""Packaged default for `rationale_language` (issue #812): the language
`curate`'s Metadata and Structure stages write their per-item RATIONALES in.

`None` -- pin nothing -- and that is the whole point of the key rather than
an undecided default. Unset does NOT mean English: it means the language is
inherited per item from whichever documents dominate that concept type or
that edge's pair, so one table the operator reads top to bottom arrives four
rows in English and two in Spanish. That is the defect #812 reports, and it
still ships as the default because fixing it costs a prompt change on a
common path.

Both rationale prompts (`resolution.edge_typing._SYSTEM_PROMPT`,
`resolution.volatility_typing._SYSTEM_PROMPT`) are English system text whose
user turn carries the concept bodies; pinning appends one sentence to the
SYSTEM half (`llm.prompting.RATIONALE_LANGUAGE_TEMPLATE`). This repo does
not adopt a longer prompt on a common path unmeasured --
`extraction.concept._LANGUAGE_ANCHOR`'s docstring records exactly that rule
for exactly this instruction, and a longer extraction prompt has already
been measured here to lose its A/B. So the unset path assembles the pre-#812
prompt BYTE FOR BYTE, the pinned path is the one an operator opted into, and
the cost of opting in is what `evals/edge_typing/`'s `--rationale-language`
arm exists to measure.

Deliberately a WORKSPACE key rather than a majority vote over the bundle.
The only language machinery the engine has is
`extraction.concept._dominant_language`: module-private, es/en only,
`None` when neither wins, and computed per SOURCE TEXT at extraction time --
never persisted, never bundle-wide. Promoting it would have shipped a
"bundle language" that cannot name most languages and abstains exactly when
a mixed corpus needs it most."""

DEFAULT_VOLATILITY_WINDOWS: dict[str, str] = {"slow": "90d", "volatile": "7d"}
"""Packaged per-tier default windows (freshness-lint-v1, design: "Per-tier
windows (CONCRETE, FINAL)"): `slow` = 90d, `volatile` = 7d -- continuity
with today's global default for fast-moving types. `static` has no window
value (never flagged), so it is never a key of this map. Raw passthrough
only, like `DEFAULT_FRESHNESS_WINDOW` -- the duration grammar and the
tier-resolution precedence are `lint.resolve_windows`'s job, not
`config`'s."""

_MAX_RATIONALE_LANGUAGE_CHARS: Final = 40
"""Length cap on `rationale_language` (issue #812), measured on the
STRIPPED value.

A language name is short. The longest spellings an operator plausibly
writes -- `Traditional Chinese (Taiwan)` at 28, `Latin American Spanish` at
22, `Brazilian Portuguese` at 20 -- clear this with room to spare, so the
cap costs no real value and refuses the shape a language name never has: a
sentence. Chosen generously for that reason; it is a sanity bound, not a
filter."""

_RATIONALE_LANGUAGE_SENTENCE_MARKS: Final = ".!?"
"""Characters `rationale_language` refuses (issue #812).

Sentence-ending punctuation only, and a DENY list rather than an allow list
on purpose: the key is free-form across every script (`config.Config`), so
an allow list would have to enumerate Han, Cyrillic, Devanagari and the
punctuation real names carry (`Chinese (Simplified)`, `Serbo-Croatian`) and
would silently refuse whatever it forgot. A name needs no full stop; a
value carrying one is prose that was typed into a name field."""

_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9._:/-]+")

_YAML_RESERVED_WORDS = frozenset({"yes", "no", "true", "false", "on", "off", "null"})
"""PyYAML's default `SafeLoader` resolver treats these exact tokens (any
casing) as `bool`/`None`, not a plain string -- an unquoted `model: yes` in
`openkos.yaml` therefore reads back as `model=True`, not `model="yes"`
(issue #128, defect #2). Rejected as a whole-token match only: a reserved
word appearing as a substring of a longer tag (`yesmodel`, `on-prem`) is
unaffected by this resolver behavior and stays valid."""


def _validate_model_token(tag: str, field: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into `openkos.yaml`.

    Shared body for `validate_model` and `validate_embedding_model` (D5):
    one source of truth for YAML-scalar safety, parameterized only by
    `field` for the raised messages. The assembled line
    `<field>: <VALUE>  # comment` must remain a valid single-line YAML plain
    scalar, so this validates via an ALLOWLIST rather than blocking
    individually known-bad characters: every character of the trimmed value
    must be a letter, digit, `.`, `_`, `:`, `/`, or `-`. Within that
    allowlist, a trailing colon (`qwen3:`) would still corrupt the line into
    `<field>: qwen3:  # ...`, whose `: ` is invalid YAML (a colon read as a
    mapping separator), and a leading colon or leading `-` would likewise be
    misread (an empty key, or a YAML block-sequence entry), so those three
    positions are rejected on top of the character allowlist. A colon in the
    middle stays allowed: Ollama's `name:tag` convention (`qwen3:8b`,
    `mistral:7b`) and the defaults `qwen3:8b`/`bge-m3` both rely on it.

    Checks safety only -- never allowlist membership. `validate_model` has
    no allowlist; `validate_embedding_model` deliberately does not gate on
    `EMBEDDING_MODEL_ALLOWLIST` here (D6) -- that gate applies to the
    interactive picker's candidates only.
    """
    trimmed = tag.strip()
    if not trimmed:
        raise ValueError(f"{field} must not be blank")
    if trimmed.lower() in _YAML_RESERVED_WORDS:
        raise ValueError(
            f"{field} must not be a YAML reserved word "
            "(yes/no/true/false/on/off/null) -- it would not round-trip as "
            "the literal string"
        )
    if not _MODEL_TOKEN_RE.fullmatch(trimmed):
        raise ValueError(
            f"{field} must not contain characters other than letters, digits, "
            "'.', '_', ':', '/', or '-'"
        )
    if trimmed.startswith(":") or trimmed.endswith(":"):
        raise ValueError(f"{field} must not start or end with ':'")
    if trimmed.startswith("-"):
        raise ValueError(f"{field} must not start with '-'")
    return trimmed


def validate_model(tag: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into `openkos.yaml`.

    See `_validate_model_token` for the full safety-check rationale.
    """
    return _validate_model_token(tag, "model")


def validate_embedding_model(tag: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into
    `openkos.yaml`'s `embedding_model:` line.

    Applies the SAME YAML-safety and reserved-word rejection as
    `validate_model` (see `_validate_model_token`), independent of
    `EMBEDDING_MODEL_ALLOWLIST` membership (D6): an off-allowlist value
    passed via `--embedding-model` must still pass this check and be
    written, never silently coerced to the default.
    """
    return _validate_model_token(tag, "embedding_model")


@dataclass(frozen=True)
class WorkspaceLayout:
    """The four paths init reads and writes at a workspace root, plus the
    engine's own cache paths (`openkos_dir`/`vectors_db_path`).

    The cache paths are PURE path derivation, like every property here --
    resolving them creates nothing on disk. Unlike the four init paths
    above, `openkos_dir`/`vectors_db_path` are never written by `init`
    (embedding-vector-store, Slice 2a): they are engine-cache paths a
    consumer (e.g. `state.vectorstore.open_vector_store`) creates lazily on
    first open, not part of a freshly initialized workspace's file set.
    """

    root: Path

    @property
    def config_path(self) -> Path:
        """`openkos.yaml`: the workspace marker (Q7.6) and layout declaration."""
        return self.root / "openkos.yaml"

    @property
    def agents_path(self) -> Path:
        """`AGENTS.md`: the engine's operating manual for this workspace."""
        return self.root / "AGENTS.md"

    @property
    def raw_dir(self) -> Path:
        """`raw/`: immutable sources, outside the OKF bundle."""
        return self.root / "raw"

    @property
    def bundle_dir(self) -> Path:
        """`bundle/`: the OKF bundle root."""
        return self.root / "bundle"

    @property
    def openkos_dir(self) -> Path:
        """`.openkos/`: the engine's own on-disk cache directory (e.g. the
        vector store). NOT created by `init` -- a consumer creates it lazily
        on first open."""
        return self.root / ".openkos"

    @property
    def vectors_db_path(self) -> Path:
        """`.openkos/vectors.db`: the sqlite-vec vector store database."""
        return self.openkos_dir / "vectors.db"

    @property
    def fts_db_path(self) -> Path:
        """`.openkos/fts.db`: the persisted FTS5 derived index (Slice 5).

        Mirrors `vectors_db_path`'s pure-derivation contract: written ONLY
        by `state.reindex.reindex`, lazily -- this property never creates
        anything on disk by itself."""
        return self.openkos_dir / "fts.db"

    @property
    def graph_db_path(self) -> Path:
        """`.openkos/graph.db`: the persisted graph projection (Slice 5, PR2).

        Mirrors `fts_db_path`'s pure-derivation contract: written ONLY by
        `openkos.graph.sqlite_graph.reindex_graph`, lazily -- this property
        never creates anything on disk by itself."""
        return self.openkos_dir / "graph.db"

    @property
    def findings_db_path(self) -> Path:
        """`.openkos/findings.db`: the persisted machine-verdict store
        (durable-pending-work, design Decision 1).

        THREE tenants share this one file, each its own table family and
        module: contradiction findings (`state.findings`, #653),
        adjudication verdicts (`state.adjudications`, #779), and
        edge-typing suggestions (`state.edge_suggestions`, #799). They
        share the file deliberately -- `purge` deletes it wholesale and
        `forget` sweeps it for purge-id membership, so a new tenant
        inherits both erasure paths instead of opening a new privacy
        surface. A new tenant MUST be added to `_sweep_findings_for_ids`.

        Mirrors `vectors_db_path`'s pure-derivation contract: written ONLY
        by `state.findings.record_findings`, lazily -- this property never
        creates anything on disk by itself. Shares `vectors_db_path`'s
        rebuild posture, not `fts_db_path`'s: `purge` deletes it and never
        rebuilds it in-line (a findings row is recomputable at LLM cost,
        never free)."""
        return self.openkos_dir / "findings.db"

    @property
    def insight_questions_db_path(self) -> Path:
        """`.openkos/insight_questions.db`: cached embeddings of the SOURCE
        QUESTION every filed insight was saved from.

        Mirrors `findings_db_path`'s pure-derivation contract: written ONLY
        by `query --save`'s near-duplicate scan, lazily -- this property
        never creates anything on disk by itself, and `purge` deletes it
        without rebuilding it in-line.

        Rebuilding is FREE in correctness terms and merely slow: a missing
        row is a cache miss the next save re-embeds. That is why the scan can
        treat this store as advisory and degrade to "could not check" rather
        than refusing to save."""
        return self.openkos_dir / "insight_questions.db"


class RefusalCondition(NamedTuple):
    """One reason `init` might refuse to write, with its workspace classification."""

    marks_workspace: bool
    reason: str


def _refusal_conditions(root: Path) -> Iterator[RefusalCondition]:
    """The ONE place that defines every reason `init` might refuse to write at `root`.

    Yields `RefusalCondition(marks_workspace, reason)` in priority order --
    the first item is the reason `refusal_reason` reports. `marks_workspace`
    is `True` for the four conditions the spec names as "already a
    workspace" (existing `openkos.yaml`, existing `AGENTS.md`, non-empty
    `raw/`, non-empty `bundle/`) and `False` for the two that answer a
    different question: `raw` or `bundle` already exists as a plain file, or
    as a symlink. Neither is a workspace, yet init still cannot write there.
    For the plain-file case, `Path.mkdir` would raise `FileExistsError` --
    an `OSError` Phase B (`cli/main.py`) DOES catch, so nothing goes
    uncaught, but without this pre-flight condition the failure would only
    surface as the generic "failed while creating the workspace" message,
    and only after any earlier Phase-B writes had already landed. For the
    symlink case, `Path.mkdir`/`open("x")` would follow the link, letting
    init write through it into whatever directory or file the symlink
    targets -- potentially outside the workspace root entirely -- instead
    of refusing outright. `is_workspace` and `refusal_reason` both read this
    generator, so extending what counts as either question changes both at
    once instead of the two silently drifting apart.
    """
    layout = WorkspaceLayout(root)
    if layout.config_path.exists():
        yield RefusalCondition(
            True, f"'{layout.config_path.name}' already exists in this directory"
        )
    if layout.agents_path.exists():
        yield RefusalCondition(
            True, f"'{layout.agents_path.name}' already exists in this directory"
        )
    for path in (layout.raw_dir, layout.bundle_dir):
        if path.is_symlink():
            yield RefusalCondition(False, f"'{path.name}' is a symlink")
        elif path.exists() and not path.is_dir():
            yield RefusalCondition(
                False, f"'{path.name}' exists and is not a directory"
            )
        elif path == layout.bundle_dir and _non_empty_dir(path):
            yield RefusalCondition(
                True,
                f"'{path.name}/' already exists and is not empty; a previous init "
                "may have crashed mid-write -- inspect and remove it before retrying",
            )
        elif _non_empty_dir(path):
            yield RefusalCondition(
                True, f"'{path.name}/' already exists and is not empty"
            )


def is_workspace(root: Path) -> bool:
    """True if `root` already looks like an initialized (or partially seeded) workspace.

    Checks the four refusal conditions the spec names: an existing
    `openkos.yaml`, an existing `AGENTS.md`, or a non-empty `raw/` or
    `bundle/`. A directory holding unrelated files but none of these is NOT
    a workspace -- init may adopt it.
    """
    return any(marks_workspace for marks_workspace, _ in _refusal_conditions(root))


def refusal_reason(root: Path) -> str | None:
    """Return why `init` must refuse to write at `root`, or `None` if it may proceed."""
    return next((reason for _, reason in _refusal_conditions(root)), None)


_NO_WORKSPACE_REASON = (
    "no OpenKOS workspace found in this directory (run 'openkos init' first)"
)


_UNREADABLE_WORKSPACE_REASON_PREFIX = "OpenKOS workspace files at"
"""Prefix for the distinct permission-denied reason `require_workspace`
returns -- kept as a separate constant from `_NO_WORKSPACE_REASON` because
the two cases are NOT the same: this one means the workspace exists but
could not be inspected, not that it is missing."""


_SYMLINKED_SEGMENT_REASON_PREFIX = "OpenKOS workspace path"
"""Prefix for the symlink-boundary refusal (#926). A THIRD distinct reason,
kept apart from `_NO_WORKSPACE_REASON` and the unreadable-workspace prefix
because it answers yet another question: the workspace is present and
readable, but part of it leaves the workspace tree."""


def symlinked_segment(path: Path, boundary: Path) -> Path | None:
    """Return the outermost segment of `path` strictly below `boundary` that is
    a symlink, `path` itself if it is not under `boundary` at all, or `None`
    when `path` is contained (#926).

    The shared containment primitive behind every symlink refusal. Two
    genuinely different escapes fold into this one check, and BOTH matter:

    * A linked **inner directory** carries writes and deletes outside the
      workspace. `bundle/area/thing.md` with `area` linked resolves into the
      external tree, so `fsio.remove_file`'s `unlink` deletes the external
      file and `fsio.write_atomic`'s temp-then-`replace` writes there.
    * A linked **leaf** does NOT carry writes or deletes -- `unlink` removes
      the link, and `replace` overwrites the link -- but it is a READ vector:
      every reader resolves through it, so external bytes reach prompts,
      answers, and the git lifecycle. Refusing only the directory case would
      leave that open.

    Segments AT OR ABOVE `boundary` are deliberately NOT inspected. Reaching a
    workspace through a linked ancestor (`~/ws` -> `/Volumes/x/ws`) is ordinary
    use and escapes nothing: everything below still resolves within one tree.
    `boundary` itself is excluded for the same reason plus a second one --
    `require_workspace` already owns whether `bundle/` is a link, and reporting
    it here too would make every concept path blame the wrong segment.

    `is_symlink()` is `False` for a path that does not exist, so this admits an
    absent path as contained; callers that need existence decide that
    separately (`_resolve_concept_path` still owns its own `is_file` refusal).
    """
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return path
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def symlink_boundary_reason(path: Path, boundary: Path) -> str | None:
    """`symlinked_segment` rendered as the shared D1-shaped refusal string, or
    `None` when `path` is contained.

    One phrasing for every caller (`require_workspace`, `_resolve_concept_path`,
    `forget`'s purge cascade) so the refusal reads the same wherever the
    boundary is crossed, and names the segment the operator must actually
    remove rather than the path they typed.
    """
    escaping = symlinked_segment(path, boundary)
    if escaping is None:
        return None
    return (
        f"{_SYMLINKED_SEGMENT_REASON_PREFIX} '{escaping}' is a symlink; "
        "OpenKOS refuses to read or write through it because it can leave the "
        "workspace tree -- replace the link with the real directory or file, "
        "or move the target inside the workspace"
    )


def require_workspace(root: Path) -> str | None:
    """Return `None` if `root` already holds an initialized workspace, else
    the exact refusal reason string every read-only command shares (D1).

    `None` means both `bundle/index.md` and `bundle/log.md` are `is_file()`
    at `root` -- the same check `ingest` performed inline before this
    extraction. `is_file()` only swallows `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP`
    into `False`; it RE-RAISES any other `OSError`, notably
    `PermissionError` on a bundle directory or file this process cannot
    stat. That case is caught here and reported as a DISTINCT reason (never
    `_NO_WORKSPACE_REASON`, since the workspace demonstrably exists -- it
    just could not be read) so callers never see a raw traceback. Callers
    (`ingest`, `status`) format their own command-specific prefix around
    this reason; `config` stays free of `typer` (layering).
    """
    layout = WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    try:
        both_files_present = index_path.is_file() and log_path.is_file()
    except OSError as exc:
        return (
            f"{_UNREADABLE_WORKSPACE_REASON_PREFIX} '{layout.bundle_dir}' "
            f"could not be read ({exc})"
        )
    if not both_files_present:
        return _NO_WORKSPACE_REASON
    # Symlink boundary (#926), AFTER presence: a missing workspace and an
    # escaping one are different answers, and the missing-workspace reason
    # ("run 'openkos init' first") is the right one when there is nothing
    # there at all. `raw/` is checked only when it exists -- this gate's
    # contract is the two bundle spine files, and demanding `raw/` would
    # refuse workspaces that legitimately have none.
    #
    # Each path is asked for its REASON and only a non-`None` one returns.
    # The obvious shape -- `if path.is_symlink(): return
    # symlink_boundary_reason(path, root)` -- is fail-OPEN: `symlinked_segment`
    # excludes the boundary itself, so any path equal to `root` yields `None`
    # and that `None` returns from the whole function as "workspace fine",
    # skipping every remaining path. Never gate on one predicate and return
    # another's answer.
    for path in (layout.raw_dir, layout.bundle_dir, index_path, log_path):
        reason = symlink_boundary_reason(path, root)
        if reason is not None:
            return reason
    return None


def _non_empty_dir(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _read_template(filename: str) -> str:
    """Read a packaged template from `src/openkos/templates/` (D4).

    Uses `importlib.resources`, never `__file__` or a relative path, so this
    works identically from an editable install and from an installed wheel.
    """
    return (resources.files("openkos") / "templates" / filename).read_text(
        encoding="utf-8"
    )


def write_agents(root: Path) -> None:
    """Write a byte-identical copy of the packaged `AGENTS.md` template at `root`.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2), matching `bundle.create`'s guarantee.
    """
    content = _read_template("agents.md.template")
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.agents_path, content)


_MODEL_PLACEHOLDER = "__OPENKOS_MODEL__"
_EMBEDDING_MODEL_PLACEHOLDER = "__OPENKOS_EMBEDDING_MODEL__"

_PLACEHOLDERS = (_MODEL_PLACEHOLDER, _EMBEDDING_MODEL_PLACEHOLDER)
"""The declared placeholder set -- the ONE place a new one is added (#210).

Both the substitution regex below and `write_config`'s per-placeholder count
guards are derived from this tuple, because the set used to be written out by
hand in three places and omitting any one of them failed SILENTLY: the count
guard only inspects the raw template, so it still passed; `re.sub` simply
never matched; and `openkos.yaml` was written with a literal
`__OPENKOS_SOMETHING__` in it. That file is still valid YAML, so `read_config`
parsed it and handed the caller a placeholder string as a real value.

One hand-written site remains by necessity -- `write_config`'s `substitutions`
mapping, which binds each token to a runtime argument this module-level tuple
cannot know. Omitting a token there is LOUD: the derived regex matches it and
the substitution callback raises `KeyError`."""

_PLACEHOLDER_RE = re.compile("|".join(re.escape(p) for p in _PLACEHOLDERS))
"""Matches any declared placeholder, so `write_config` substitutes in ONE pass.

Sequential `str.replace` calls would not be order-independent: the character
allowlist in `_MODEL_TOKEN_RE` admits `_`, so a validated `model` value may
itself equal the embedding placeholder token, and a later pass would overwrite
the value the earlier pass just wrote. A single pass never re-examines
substituted text."""

_TEMPLATE_PLACEHOLDER_RE = re.compile(r"__OPENKOS_[A-Z0-9_]*?__")
"""Any placeholder-SHAPED token, declared or not.

The body is LAZY. Greedy would run straight through a second token's
`__OPENKOS_` prefix -- every character of it is inside this same class --
and backtrack only to the final `__`, so two placeholders written back to
back would scan as ONE merged match belonging to no declaration, and the
check below would reject a template in which both are correctly declared and
each appears exactly once. Lazy stops at the first closing delimiter, which
is what a delimiter-bounded token means.

Closes the one hole deriving `_PLACEHOLDER_RE` cannot: a token added to the
packaged template that no constant declares. Every declaration-driven check
still passes in that case -- there is nothing to be inconsistent WITH -- and
the token is written through verbatim.

Applied to the TEMPLATE before substitution, never to the finished content
after it. A survivor scan of the output would be simpler and would be wrong:
`__OPENKOS_EMBEDDING_MODEL__` is a legal `model` value (the allowlist admits
`_`), and two tests pin that it round-trips intact, so an output scan would
refuse to write a config that is entirely correct."""


def write_config(
    root: Path,
    model: str = DEFAULT_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> None:
    """Write the packaged `openkos.yaml` template at `root`, with `model`
    and `embedding_model` substituted for the template's two independent
    placeholders (`__OPENKOS_MODEL__`, `__OPENKOS_EMBEDDING_MODEL__`).

    `model` and `embedding_model` are the ONLY user-selectable fields: every
    other line is byte-identical to the packaged template regardless of the
    chosen model(s). The directory itself remains the single source of
    truth for the workspace's identity, so nothing in `openkos.yaml` is
    derived from `root`. Substitution is ONE constrained pass over the two
    known placeholder tokens (`_PLACEHOLDER_RE`) -- never a YAML dumper or
    serializer -- so it cannot reformat, reorder, or fold any other line the
    way a round-trip through a YAML library could, and it never re-examines
    text it just substituted (see `_PLACEHOLDER_RE`). `validate_model` and
    `validate_embedding_model` each run first and raise `ValueError` before
    any file is written if their value is blank or contains whitespace, a
    quote, or `#`; `embedding_model` is validated for YAML-safety only,
    independent of `EMBEDDING_MODEL_ALLOWLIST` membership (D6).

    Two template checks run before any substitution, both derived from
    `_PLACEHOLDERS` rather than written per token (#210). First, the template
    may contain no placeholder-shaped token that nothing declares. Second,
    each declared placeholder must be matched exactly once -- counted by
    running `_PLACEHOLDER_RE` over the template, the SAME alternation that
    then performs the substitution, so the guard measures what the operation
    will actually do rather than what a substring search reports.

    Together they mean adding a placeholder is a single edit to
    `_PLACEHOLDERS` plus its template line and its `substitutions` entry,
    with every way of half-applying it raising rather than writing a literal
    token into `openkos.yaml`.

    Exclusive-create mode ("x") never overwrites an existing file (D2).
    """
    validated_model = validate_model(model)
    validated_embedding_model = validate_embedding_model(embedding_model)
    template = _read_template("openkos.yaml.template")
    undeclared = sorted(
        set(_TEMPLATE_PLACEHOLDER_RE.findall(template)) - set(_PLACEHOLDERS)
    )
    if undeclared:
        raise ValueError(
            "packaged template contains undeclared placeholder(s): "
            + ", ".join(repr(token) for token in undeclared)
        )
    # Counted with the SAME alternation that performs the substitution, never
    # with `str.count`. The two disagree: `str.count` counts OVERLAPPING
    # substrings, while `re.sub` consumes NON-OVERLAPPING. Two declared tokens
    # sharing one underscore pair (`__OPENKOS_MODEL__OPENKOS_EMBEDDING_MODEL__`)
    # are therefore each "present once" to `str.count` while substitution can
    # only ever consume the first -- writing the second through as literal
    # text, valid YAML, no error. A guard that does not measure what the
    # operation will actually do is not a guard.
    matched = _PLACEHOLDER_RE.findall(template)
    mismatched = [
        (placeholder, matched.count(placeholder))
        for placeholder in _PLACEHOLDERS
        if matched.count(placeholder) != 1
    ]
    if mismatched:
        raise ValueError(
            "packaged template placeholder mismatch: "
            + "; ".join(
                f"{placeholder!r} is matched {count} time(s) by the "
                "substitution scan, expected exactly one"
                for placeholder, count in mismatched
            )
        )
    substitutions = {
        _MODEL_PLACEHOLDER: validated_model,
        _EMBEDDING_MODEL_PLACEHOLDER: validated_embedding_model,
    }
    content = _PLACEHOLDER_RE.sub(lambda m: substitutions[m.group(0)], template)
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.config_path, content)


@dataclass(frozen=True)
class Config:
    """The subset of `openkos.yaml` the engine reads back at runtime.

    Fields absent from the file fall back to the same packaged defaults
    `openkos.yaml.template` ships (D3): `DEFAULT_MODEL`, `DEFAULT_REVIEW`,
    `DEFAULT_SENSITIVITY`, `DEFAULT_FRESHNESS_WINDOW`,
    `DEFAULT_EMBEDDING_MODEL`, and `DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION`. `embedding_model` IS part of
    `openkos.yaml.template`, written via its own placeholder by
    `write_config`; this fallback still applies when reading a
    pre-existing or hand-edited `openkos.yaml` that omits the key or sets
    it to an explicit YAML null.
    """

    model: str
    review: bool
    default_sensitivity: str
    freshness_window: str
    embedding_model: str
    chat_timeout: float
    """Seconds an `llm.chat` request may take before the transport gives up
    (issue #405), defaulting to `DEFAULT_CHAT_TIMEOUT` when the key is absent
    or explicitly null. Validated as a positive number and coerced to
    `float` at read time, so every consumer sees one type. Governs the CHAT
    seams only -- embedding calls keep `OllamaClient`'s own default, and the
    liveness probes keep `_PREFLIGHT_TIMEOUT`, which answers a different
    question (is anything listening) and must stay short."""
    max_generation_tokens: int
    """Hard ceiling, in tokens, on how much a single `llm.chat` request may
    GENERATE before Ollama cuts it off (issue #422), defaulting to
    `DEFAULT_MAX_GENERATION_TOKENS` when the key is absent or explicitly
    null. Validated as a positive integer and forwarded as
    `options.num_predict` at the CHAT seams only, mirroring
    `chat_timeout`'s scope exactly. A SAFETY RAIL, not a quality knob:
    reaching it raises `OllamaGenerationCapped` rather than returning a
    silently truncated reply."""
    context_window: int | None
    """Context window, in tokens, pinned on every `llm.chat` request as
    `options.num_ctx` (issue #691), or `None` to leave the model's own
    default in place.

    Absent from the file, this resolves to
    `max(DEFAULT_CONTEXT_WINDOW, minimum_context_window(max_generation_tokens))`
    -- derived rather than fixed, so a workspace that raised its generation
    ceiling and never heard of this key still gets a window big enough for
    it.

    This is the one field whose EXPLICIT null does not mean the packaged
    default, and deliberately so: "no window pinned" is a real state that no
    positive integer can express, whereas for every other field the absent
    behaviour IS the default. `context_window:` written out with no value
    therefore opts out, and sends a request byte-identical to the pre-#691
    one.

    A present value is floor-checked at read time, not merely type-checked.
    Too LOW is worse than unset: Ollama drops the head of the prompt and
    returns a confident answer built on a truncated document, with no error
    anywhere. Validation is the only place that failure can be made
    impossible rather than documented."""
    confidential_local_exemption: bool
    """Whether a `confidential` concept may be included in an `llm.chat`
    payload when the backend host is verifiably this machine (issue #240),
    defaulting to `DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION` when the key is
    absent or explicitly null. This is only ONE of the two terms: the CLI
    ANDs it with the client's own `locality.is_local`, so `true` never
    grants an exemption for a host that could not be proven local."""
    volatility_windows: dict[str, str]
    """Raw `volatility_windows` passthrough (freshness-lint-v1): `{}` when
    absent or explicitly null, matching every other field's `is not None`
    fallback. A present map passes through verbatim -- duration-grammar
    parsing and the `static`/`slow`/`volatile` precedence stay in
    `lint.resolve_windows`, not here."""
    type_tiers: dict[str, str]
    """Raw `type_tiers` passthrough (freshness-suggest-windows,
    `concept-volatility` spec): `{}` when absent or explicitly null,
    mirroring `volatility_windows`'s `is not None` fallback. A present map
    passes through verbatim -- unknown-type/invalid-tier validation and the
    override step in the `volatility`/`type_tiers`/registry-default/fallback
    precedence stay in `lint.window_for_doc`, not here."""
    models: dict[str, str]
    """Per-task model overrides (issue #515): `{}` when absent or explicitly
    null, mirroring `volatility_windows`/`type_tiers`'s `is not None`
    fallback so this adds no new parsing convention.

    Keyed by TASK, never by verb: `suggest_edge_types` is used by BOTH
    `curate`'s Structure stage and standalone `suggest-relations`, and a
    per-verb key would let those two drift onto different models -- the
    drift #385's design already prevents by routing both through one write
    core. The eval harnesses also score the task, not the verb, so a
    per-task key is the only shape a measurement can justify.

    Unlike its two passthrough precedents, entries ARE validated at read
    time (see `read_config`): an unknown key or a malformed value is
    refused rather than degraded. Silently falling back to the global
    default is the dangerous option here -- the operator would keep writing
    relation types believing they are getting the model they named.

    A resolved value is never a promise the model is INSTALLED. That
    failure is per-stage and surfaces at the `llm.chat` seam with the
    actionable `ollama pull <model>` wording, failing only the stage that
    named it."""
    union_judge: bool
    """Whether `ingest` uses the union-of-runs + selector-judge extraction
    pipeline (design D9, #456), defaulting to `DEFAULT_UNION_JUDGE` when the
    key is absent or explicitly null. `False` restores the single-run,
    single-cap `extract_concept` path byte-for-byte -- the CLI passes this
    value explicitly to `_stage_derived_objects`'s `union_judge` kwarg
    rather than defaulting it there, so the product-ON default lives in
    exactly ONE place."""
    sufficiency_check: bool
    """Whether `query` runs the pre-synthesis sufficiency check (#760),
    defaulting to `DEFAULT_SUFFICIENCY_CHECK` when the key is absent or
    explicitly null. `False` restores the pre-#760 path byte-for-byte and
    removes the extra chat call. The CLI passes this value explicitly to
    `answer`'s `sufficiency_check` kwarg rather than defaulting it there, so
    the product-ON default lives in exactly ONE place -- `answer` itself
    defaults `False`, which keeps every library and eval caller unchanged."""
    concurrent_extraction: bool
    """Whether the chunked extraction fan-out sends its windows concurrently
    (issue #744), defaulting to `DEFAULT_CONCURRENT_EXTRACTION` when the key
    is absent or explicitly null.

    A BOOLEAN, not a worker count, and deliberately so: #739 measured the
    speedup saturating at two in-flight windows -- 3 and 4 are statistically
    indistinguishable from 2 (t~0.05) while resident memory keeps climbing --
    so the count is a private constant in `extraction.concept`
    (`_FAN_OUT_CONCURRENCY`) that no config value can raise past its
    evidence. This key answers only "may the fan-out use it at all".

    Affects the CHUNKED path only: a source under `_chunk_threshold_for` runs
    a single whole-document call (or two, under `union_judge`) and has no
    windows to overlap. Both extraction entry points honour it, so the answer
    never depends on `union_judge`."""
    type_sensitivity_defaults: dict[str, int]
    """Per-OKF-type sensitivity offset above `default_sensitivity` (issue
    #669, ADR-0015): `DEFAULT_TYPE_SENSITIVITY_DEFAULTS` (a copy, and EMPTY
    since #756) when the key is absent from `openkos.yaml` or explicitly
    null, so a stock workspace applies no per-type offset at all and every
    object is born at the floor. An explicit `{}` means the same thing said
    out loud. `Person: 1` is the recommended opt-in for workspaces holding
    material about third parties. Unlike `volatility_windows`/`type_tiers`'s lazy passthrough,
    entries ARE validated eagerly at `read_config` time (see below),
    mirroring `models:`'s precedent: a silently-wrong SECURITY default
    produces a run that looks completely ordinary.

    Consumed by `type_birth_sensitivity`, never by `read_config` itself."""
    rationale_language: str | None
    """The language `curate`'s Metadata and Structure stages write their
    per-item RATIONALES in (issue #812), or `None` -- the default -- to pin
    nothing and send the pre-#812 prompt byte for byte. See
    `DEFAULT_RATIONALE_LANGUAGE` for why unset is not English.

    **Free-form, and validated only for SHAPE.** A present value must be a
    non-blank, single-line string, no longer than
    `_MAX_RATIONALE_LANGUAGE_CHARS` once stripped, carrying no
    sentence-ending punctuation; it is stripped and then interpolated
    verbatim into both rationale prompts. There is no accepted vocabulary
    because there is nothing to check one against: the engine holds no
    language registry, the model is the only component that resolves the
    name, and an enum would have inherited `_dominant_language`'s es/en
    horizon (`DEFAULT_RATIONALE_LANGUAGE`).

    **What the shape checks are for.** They bound the value to the shape a
    language name has -- one short line without a full stop -- so that a
    YAML block scalar, a pasted multi-line answer, or a sentence typed into
    a name field fails at `read_config` rather than reaching the model. They
    are NOT a prompt-injection defence and do not make this field untrusted
    input. `openkos.yaml` is the operator's own file: this key is trusted
    exactly as `model:` and every other prompt-affecting key in it is
    trusted, and an operator who wants these prompts to say something else
    can already say so by editing the repository. A short, unpunctuated
    instruction would pass every check here, and that is the trust boundary
    working as designed, not a gap in it.

    **The failure mode that buys.** A name the model does not resolve --
    a typo, a language it was not trained on, a dialect it collapses -- is
    NOT an error anywhere. `read_config` accepts it, the prompt carries it,
    and the model writes the rationales in whatever it decided the name
    meant. The operator sees that in the very next `curate` table, which is
    the same place they would see a language they did not want, so the loop
    is short; but nothing in the engine will tell them the value was wrong.
    None of the shape checks above sees this: a typo is short, single-line
    and unpunctuated, which is exactly what a real name looks like.

    Scope: the two rationale prompts only. Extraction, adjudication,
    contradiction detection and `query` are untouched -- their outputs are
    the corpus's own content, where following the source language is
    correct, not a defect. It also does not reach ALREADY-persisted edge
    suggestions: a run that serves a suggestion an earlier run paid for
    (#799) shows that earlier run's rationale, in that run's language, and
    curate's own served-count notice is where that shows up."""


def read_config(root: Path) -> Config:
    """Parse `openkos.yaml` at `root` and return its `model`/`review`/
    `default_sensitivity` fields, falling back to packaged defaults for any
    field the file omits OR sets to an explicit YAML null (D3).

    Uses `yaml.safe_load` -- never a loader that can construct arbitrary
    Python objects from untrusted YAML. A `yaml.YAMLError` (malformed YAML),
    a `TypeError` (some PyYAML constructor code paths raise this directly
    rather than a `YAMLError`, e.g. for a mapping with an unhashable complex
    key), or a root that parses but is not a mapping all raise `ValueError`,
    so callers can catch alongside `OSError` (a missing or unreadable file)
    with a single `except (OSError, ValueError)`, matching `init`'s
    convention. `raw.get(key, DEFAULT)` alone only falls back when `key` is
    ABSENT -- a key present with an explicit `key: null` (or bare `key:`)
    parses to `None`, which would otherwise violate `Config`'s typed fields.
    Each field is therefore checked `is not None` before falling back, not
    truthiness: `review: false` is a real value (`False is not None`), so it
    must survive untouched and never get coerced to `DEFAULT_REVIEW`.
    """
    layout = WorkspaceLayout(root)
    text = layout.config_path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except (yaml.YAMLError, TypeError) as exc:
        raise ValueError(f"{layout.config_path.name}: invalid YAML -- {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(
            f"{layout.config_path.name}: expected a mapping at the document root"
        )
    model = raw.get("model")
    review = raw.get("review")
    default_sensitivity = raw.get("default_sensitivity")
    freshness_window = raw.get("freshness_window")
    embedding_model = raw.get("embedding_model")
    chat_timeout = raw.get("chat_timeout")
    max_generation_tokens = raw.get("max_generation_tokens")
    # Membership, not `.get()`: `raw.get` returns `None` for BOTH an absent
    # key and an explicit YAML null, and #691 gives those two opposite
    # meanings -- absent derives a window, explicit null opts out of pinning
    # one at all. This is the only key that needs to tell them apart.
    context_window_present = "context_window" in raw
    context_window = raw.get("context_window")
    confidential_local_exemption = raw.get("confidential_local_exemption")
    volatility_windows = raw.get("volatility_windows")
    type_tiers = raw.get("type_tiers")
    models = raw.get("models")
    union_judge = raw.get("union_judge")
    sufficiency_check = raw.get("sufficiency_check")
    concurrent_extraction = raw.get("concurrent_extraction")
    type_sensitivity_defaults = raw.get("type_sensitivity_defaults")
    rationale_language = raw.get("rationale_language")
    if model is not None and not isinstance(model, str):
        raise ValueError(
            f"{layout.config_path.name}: 'model' must be a string, got "
            f"{type(model).__name__}"
        )
    if embedding_model is not None and not isinstance(embedding_model, str):
        raise ValueError(
            f"{layout.config_path.name}: 'embedding_model' must be a string, got "
            f"{type(embedding_model).__name__}"
        )
    if chat_timeout is not None and (
        isinstance(chat_timeout, bool)
        or not isinstance(chat_timeout, int | float)
        or chat_timeout <= 0
    ):
        # `bool` is excluded FIRST and explicitly, because it subclasses
        # `int`: without that term `chat_timeout: true` would resolve to a
        # one-second deadline, so every chat call would fail instantly and
        # look like a dead backend rather than a bad config value. This is
        # the same int-as-bool hazard `confidential_local_exemption` guards
        # below, pointed the other way.
        #
        # Non-positive values are refused for a related reason: `urllib`
        # treats them as an immediate expiry, so `chat_timeout: 0` would
        # disable the LLM entirely while reading like a deliberate setting.
        raise ValueError(
            f"{layout.config_path.name}: 'chat_timeout' must be a positive "
            f"number of seconds, got {chat_timeout!r}"
        )
    if max_generation_tokens is not None and (
        isinstance(max_generation_tokens, bool)
        or not isinstance(max_generation_tokens, int)
        or max_generation_tokens <= 0
    ):
        # `bool` is excluded FIRST and explicitly, mirroring `chat_timeout`'s
        # own int-as-bool guard above: without that term
        # `max_generation_tokens: true` would resolve to a one-token
        # ceiling and truncate every reply immediately.
        #
        # Ollama's `num_predict` sentinels are refused outright, not merely
        # non-positive values: `-1` means "unlimited" to Ollama and would
        # silently disable the very bound this change installs; `0` means
        # "return no completion"; `-2` means "fill the context window".
        # Accepting any of them would be a footgun disguised as a valid
        # setting, so this rail requires a plain positive integer -- never
        # a fraction (Ollama's `num_predict` is a token count) and never
        # one of Ollama's own reserved meanings.
        raise ValueError(
            f"{layout.config_path.name}: 'max_generation_tokens' must be a "
            f"positive integer number of tokens, got {max_generation_tokens!r}"
        )
    # Resolved here rather than at construction below, because the
    # `context_window` floor is derived from it: checking a window against
    # the PACKAGED ceiling while the workspace runs a raised one would let
    # through exactly the pair that truncates.
    resolved_max_generation_tokens = (
        max_generation_tokens
        if max_generation_tokens is not None
        else DEFAULT_MAX_GENERATION_TOKENS
    )
    context_floor = minimum_context_window(resolved_max_generation_tokens)
    if context_window is not None:
        if (
            isinstance(context_window, bool)
            or not isinstance(context_window, int)
            or context_window <= 0
        ):
            # `bool` first and explicitly, the same int-as-bool hazard
            # `chat_timeout` and `max_generation_tokens` each guard above.
            raise ValueError(
                f"{layout.config_path.name}: 'context_window' must be a "
                f"positive integer number of tokens, got {context_window!r}"
            )
        if context_window < context_floor:
            # The one validation in this file that exists to prevent a SILENT
            # failure rather than a loud one. Every other bad value here
            # produces a visible error at the seam it governs; a `num_ctx`
            # below the floor produces a normal-looking answer computed from
            # a prompt Ollama truncated without telling anyone (#691). The
            # remedy is named in the message, because the floor moves with
            # `max_generation_tokens` and a bare number would look arbitrary.
            raise ValueError(
                f"{layout.config_path.name}: 'context_window' must be at least "
                f"{context_floor} tokens (a {PROMPT_CONTEXT_ALLOWANCE}-token "
                f"prompt allowance plus max_generation_tokens="
                f"{resolved_max_generation_tokens}), got {context_window}. A "
                f"smaller window is worse than none: Ollama truncates the "
                f"prompt silently. Raise it, lower max_generation_tokens, or "
                f"write `context_window:` with no value to leave the window "
                f"unpinned."
            )
    if confidential_local_exemption is not None and not isinstance(
        confidential_local_exemption, bool
    ):
        # Validated, never coerced (issue #240), mirroring how `model` and
        # `embedding_model` validate their own type. This key decides whether
        # `confidential` content may reach an LLM at all, so a value the user
        # meant as something else -- `maybe`, `1`, a nested mapping -- must
        # fail loudly instead of being evaluated for truthiness and silently
        # enabling the exemption. `isinstance(x, bool)` is deliberately
        # narrower than a truthiness test: YAML resolves `1` to `int`, and
        # accepting int-as-bool would make `: 0` and `: false` agree only by
        # coincidence of Python's numeric tower.
        raise ValueError(
            f"{layout.config_path.name}: 'confidential_local_exemption' must be "
            f"a boolean, got {type(confidential_local_exemption).__name__}"
        )
    if models is not None:
        # Validated entry by entry, NOT passed through like
        # `volatility_windows`/`type_tiers` (issue #515). Those two degrade a
        # malformed value silently because a wrong freshness window shows up
        # as a stale-stamp the operator can see and re-lint. A wrong model
        # key does not: the run completes, the suggestions look ordinary, and
        # relation types get written by a model the operator did not choose.
        # #515 decision 2 refuses a silent fallback to the global default for
        # a model that is not installed, and that reasoning does not
        # distinguish a name that is missing from a name that is malformed.
        if not isinstance(models, dict):
            raise ValueError(
                f"{layout.config_path.name}: 'models' must be a mapping of "
                f"task -> model tag, got {type(models).__name__}"
            )
        for task, tag in models.items():
            if task not in TASK_MODEL_KEYS:
                known = ", ".join(sorted(TASK_MODEL_KEYS))
                raise ValueError(
                    f"{layout.config_path.name}: 'models' names unknown task "
                    f"{task!r}; valid tasks are: {known}"
                )
            if tag is None:
                # An explicit YAML null is the opt-out from a PACKAGED
                # per-task default (#513): "use the global `model:` for this
                # task". It is kept in the map rather than dropped -- an
                # absent key is exactly the state that resolves to the
                # packaged default, so dropping it would make the opt-out
                # silently do nothing.
                continue
            if not isinstance(tag, str):
                raise ValueError(
                    f"{layout.config_path.name}: 'models.{task}' must be a "
                    f"string model tag or null, got {type(tag).__name__}"
                )
            if not tag.strip():
                raise ValueError(
                    f"{layout.config_path.name}: 'models.{task}' must not be blank"
                )
    if union_judge is not None and not isinstance(union_judge, bool):
        # Validated, never coerced (design D9), mirroring
        # `confidential_local_exemption`'s own guard: `isinstance(x, bool)`
        # is deliberately narrower than a truthiness test, since YAML
        # resolves `1` to `int` and accepting int-as-bool would make
        # `union_judge: 0` and `: false` agree only by coincidence of
        # Python's numeric tower.
        raise ValueError(
            f"{layout.config_path.name}: 'union_judge' must be a boolean, got "
            f"{type(union_judge).__name__}"
        )
    if sufficiency_check is not None and not isinstance(sufficiency_check, bool):
        # Same narrow `isinstance(x, bool)` guard as `union_judge` above: YAML
        # resolves `1` to `int`, and a key that gates a model call must not
        # accept a count that looks like one.
        raise ValueError(
            f"{layout.config_path.name}: 'sufficiency_check' must be a boolean, "
            f"got {type(sufficiency_check).__name__}"
        )
    if concurrent_extraction is not None and not isinstance(
        concurrent_extraction, bool
    ):
        # Same narrow `isinstance(x, bool)` guard as `union_judge` above, and
        # here the int case is not merely a YAML technicality: this key reads
        # like a worker count, so `concurrent_extraction: 2` is exactly what
        # an operator writes when they expect to choose the concurrency. It
        # is refused rather than truthy-coerced, because coercion would give
        # them the behaviour they asked for by accident and teach them a
        # setting that does not exist.
        # The message says "not a worker count" WITHOUT naming the count: the
        # number lives in `extraction.concept._FAN_OUT_CONCURRENCY` and
        # `config` does not import `extraction`. Restating it here would be a
        # second copy free to drift from the constant it describes.
        raise ValueError(
            f"{layout.config_path.name}: 'concurrent_extraction' must be a "
            f"boolean, got {type(concurrent_extraction).__name__} -- it is an "
            f"on/off switch, not a worker count"
        )
    if type_sensitivity_defaults is not None:
        # Validated entry by entry, eagerly, mirroring `models:`'s own
        # precedent (issue #515) rather than `volatility_windows`/
        # `type_tiers`'s lazy passthrough (design D1): a wrong freshness
        # window shows up as a stale stamp the operator can re-lint, a wrong
        # SECURITY default does not -- the run completes, the documents look
        # ordinary, and concepts are born at a level nobody chose.
        if not isinstance(type_sensitivity_defaults, dict):
            raise ValueError(
                f"{layout.config_path.name}: 'type_sensitivity_defaults' must be "
                f"a mapping of type -> offset, got "
                f"{type(type_sensitivity_defaults).__name__}"
            )
        for doc_type, offset in type_sensitivity_defaults.items():
            if doc_type not in types.BUILDABLE_TYPES:
                # `BUILDABLE_TYPES`, not `CLASSIFIABLE_TYPES`: the wider set
                # `_stage_filed_answer` accepts (`Insight`), and it refuses
                # `Source` for free -- `Source` is not buildable, so "Sources
                # are never type-defaulted" is enforced by the type domain
                # itself, not by a comment (design D1).
                raise ValueError(
                    f"{layout.config_path.name}: 'type_sensitivity_defaults' names "
                    f"unrecognized type {doc_type!r}"
                )
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not (0 <= offset <= len(okf.SENSITIVITY_ORDER) - 1)
            ):
                # `bool` excluded FIRST and explicitly, mirroring
                # `chat_timeout`/`confidential_local_exemption`'s own guards:
                # without it, `Person: true` would resolve to offset `1` by
                # Python's numeric tower. `0 <= offset <= len(...) - 1`:
                # `offset == 0` is the legal, inert "no raise for this type"
                # spelling; `offset >= len(SENSITIVITY_ORDER)` is unreachable
                # at every possible floor -- indistinguishable from the
                # ceiling offset, so it is a typo, not a policy, and must
                # fail loudly (design D1).
                raise ValueError(
                    f"{layout.config_path.name}: "
                    f"'type_sensitivity_defaults.{doc_type}' must be an integer "
                    f"offset between 0 and {len(okf.SENSITIVITY_ORDER) - 1}, "
                    f"got {offset!r}"
                )
    if rationale_language is not None and (
        not isinstance(rationale_language, str)
        or not rationale_language.strip()
        or "\n" in rationale_language
        or "\r" in rationale_language
        or len(rationale_language.strip()) > _MAX_RATIONALE_LANGUAGE_CHARS
        or any(
            mark in rationale_language for mark in _RATIONALE_LANGUAGE_SENTENCE_MARKS
        )
    ):
        # One guard and one message for six rejections, because they share
        # one remedy: write a language name. That is the opposite of
        # `models:`'s entry-by-entry messages, and deliberately -- there is
        # no sub-key here to name, only a value that is or is not usable.
        #
        # `isinstance(x, str)` catches the YAML-reserved-word case FIRST,
        # the same int-as-bool hazard `chat_timeout` guards pointed at a
        # different type: `rationale_language: no` resolves to `False`, and
        # `no` is a genuinely plausible thing to write here meaning "do not
        # pin one" -- which is spelled by leaving the key out, and must not
        # be silently accepted as a language named `False`.
        #
        # WHAT THE SHAPE CHECKS ARE AND ARE NOT. This value is interpolated
        # verbatim into the SYSTEM turn of both rationale prompts (#812), so
        # every rejection here is about the value not being SHAPED like a
        # language name: a line break (a YAML block scalar or a pasted
        # multi-line answer that arrives here by accident), a value longer
        # than any language is spelled, and sentence-ending punctuation --
        # the three things a name never has and a sentence always does.
        #
        # They are NOT a prompt-injection defence, and must not be read as
        # one. `openkos.yaml` is the operator's own file; `rationale_language`
        # is trusted exactly as `model:`, `models:` and every other
        # prompt-affecting key in it is trusted, and an operator who wants to
        # change what these two prompts say can edit this repository. What
        # the checks buy is the ACCIDENT: a YAML shape nobody meant to write,
        # and a value that is obviously not a language, caught at
        # `read_config` instead of surfacing as rationales that read wrong in
        # the next `curate` table. A short, single-line, unpunctuated
        # instruction still passes, and that is not a hole -- it is the
        # trust boundary this file has always had.
        #
        # NOT validated against any vocabulary either: see
        # `Config.rationale_language` for why there is nothing to check a
        # language name against, and what it costs when the name is wrong.
        raise ValueError(
            f"{layout.config_path.name}: 'rationale_language' must be a "
            f"non-blank, single-line language name of at most "
            f"{_MAX_RATIONALE_LANGUAGE_CHARS} characters and no sentence "
            f"punctuation, got {rationale_language!r}"
        )
    return Config(
        model=model if model is not None else DEFAULT_MODEL,
        review=review if review is not None else DEFAULT_REVIEW,
        default_sensitivity=(
            default_sensitivity
            if default_sensitivity is not None
            else DEFAULT_SENSITIVITY
        ),
        freshness_window=(
            freshness_window
            if freshness_window is not None
            else DEFAULT_FRESHNESS_WINDOW
        ),
        embedding_model=(
            embedding_model if embedding_model is not None else DEFAULT_EMBEDDING_MODEL
        ),
        # Coerced, not merely passed through: `chat_timeout: 900` is a YAML
        # int and the field is typed `float`, so the boundary normalizes it
        # once instead of leaving every consumer to handle both.
        chat_timeout=(
            float(chat_timeout) if chat_timeout is not None else DEFAULT_CHAT_TIMEOUT
        ),
        max_generation_tokens=resolved_max_generation_tokens,
        # Three-way, unlike every other field's two-way `is not None`: a
        # present value passes through (already floor-checked above), an
        # explicit null opts out, and an ABSENT key derives a window that
        # covers this workspace's own ceiling (#691).
        context_window=(
            context_window
            if context_window is not None
            else (
                None
                if context_window_present
                else max(DEFAULT_CONTEXT_WINDOW, context_floor)
            )
        ),
        confidential_local_exemption=(
            confidential_local_exemption
            if confidential_local_exemption is not None
            else DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
        ),
        volatility_windows=(
            volatility_windows if volatility_windows is not None else {}
        ),
        type_tiers=(type_tiers if type_tiers is not None else {}),
        models=(models if models is not None else {}),
        union_judge=(union_judge if union_judge is not None else DEFAULT_UNION_JUDGE),
        sufficiency_check=(
            sufficiency_check
            if sufficiency_check is not None
            else DEFAULT_SUFFICIENCY_CHECK
        ),
        concurrent_extraction=(
            concurrent_extraction
            if concurrent_extraction is not None
            else DEFAULT_CONCURRENT_EXTRACTION
        ),
        type_sensitivity_defaults=(
            type_sensitivity_defaults
            if type_sensitivity_defaults is not None
            else dict(DEFAULT_TYPE_SENSITIVITY_DEFAULTS)
        ),
        # Stripped, not merely passed through, on `chat_timeout`'s
        # normalise-at-the-boundary reasoning: the value is interpolated
        # into a sentence in two prompts, so padding a quoted value carries
        # would otherwise ship to the model inside it, twice.
        rationale_language=(
            rationale_language.strip()
            if rationale_language is not None
            else DEFAULT_RATIONALE_LANGUAGE
        ),
    )


def resolve_task_model(cfg: Config, task: str | None) -> str:
    """Return the model tag `task` should run on (issues #515, #513).

    Precedence, highest first:

    1. `cfg.models[task]` -- what the workspace explicitly asked for. An
       explicit YAML null here DECLINES a packaged default and falls to
       `cfg.model`, which is the only way to opt out of one.
    2. `DEFAULT_TASK_MODELS[task]` -- the packaged per-task default. Empty
       since #650 (`edge_typing`'s value is `None`), kept as a mechanism:
       the precedence is measured and pinned, only the shipped map changed.
    3. `cfg.model` -- the global default.

    The operator's stated choice always beats a shipped one, and the shipped
    one beats the global default only for the tasks it names.

    `task=None` means "this caller is not one of the measured tasks" and
    resolves to `cfg.model`. It is a first-class answer, not a missing
    argument: `query` synthesizes an answer with no harness behind it, and
    `curate`'s locality probe asks about the HOST rather than any task. Both
    pass `None` deliberately, which lets every chat seam call this one
    function instead of branching around it.

    This is the whole per-task mechanism. `models:` is ADDITIVE -- a
    workspace that keys `edge_typing` moves edge typing and nothing else, so
    a config with no `models:` at all resolves every task to `cfg.model`
    exactly as it did before this existed. That property is what let #515
    collect edge typing's measured +0.37 without moving the extraction
    pipeline `evals/extraction_cap/` tuned on `qwen3:8b`, which no harness
    would have caught.

    Never raises. `read_config` already refuses a malformed `models`, but
    `Config` is a plain dataclass a fixture or a future caller can build
    directly, and this function is read at every chat seam: an
    `AttributeError` here would take down a verb that has a perfectly good
    global default sitting right next to it. A non-mapping `models`, a
    missing key, and a non-`str` value all fall back to `cfg.model` -- the
    same defensive widening `lint.resolve_windows` applies to its own two
    passthrough maps, for the same reason.

    Returning a tag is NOT a claim the model is installed. `curate` and the
    standalone verbs discover that at the `llm.chat` seam and fail only the
    stage that named it (#515 decision 2)."""
    raw: object = cfg.models
    if task is None:
        return cfg.model
    if isinstance(raw, dict) and task in raw:
        tag = raw[task]
        # An explicit null declines a packaged per-task default and follows
        # the global `model:` (#513) -- checked BEFORE the packaged map, or
        # the opt-out could never win. A malformed value cannot reach here
        # through `read_config`, which refuses it; a hand-built `Config` is
        # degraded rather than raising, since this runs at every chat seam.
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
        return cfg.model
    packaged = DEFAULT_TASK_MODELS.get(task)
    if isinstance(packaged, str) and packaged.strip():
        return packaged.strip()
    return cfg.model


def type_birth_sensitivity(cfg: Config, doc_type: str, base: object) -> str:
    """Return the birth-time sensitivity for a concept of `doc_type` given
    `base`, the sensitivity it would otherwise be born at (issue #669,
    ADR-0015, design D3).

    `base` is the Source's resolved `stamp_sensitivity` on the ingest path,
    or the cited-concept high-water-mark on the `query --save` path -- this
    function does not care which, it only needs the value. When `doc_type`
    is present in `cfg.type_sensitivity_defaults`, the result is
    `combine_sensitivity(base, raise_by(cfg.default_sensitivity, offset))`:
    the offset is applied to the CONFIG FLOOR, never to `base` directly, so
    a `base` already above the floor-plus-offset still wins via the
    high-water-mark -- the type default can only raise, never lower or
    override an already-higher inherited value. When `doc_type` has no
    entry, `base` is returned canonicalized unchanged (folded through
    `combine_sensitivity(base, base)`, which is `_rank`'s own fail-closed
    canonicalization, not a verbatim passthrough of a possibly-dirty value).

    Both `build_concept` birth seams (`_stage_derived_objects`,
    `_stage_filed_answer`) call this with the base appropriate to their own
    seam, and MUST produce identical output for identical inputs -- the
    formula lives here exactly once so that identity holds by construction.
    """
    offset = cfg.type_sensitivity_defaults.get(doc_type)
    if offset is None:
        return okf.combine_sensitivity(base, base)
    return okf.combine_sensitivity(base, okf.raise_by(cfg.default_sensitivity, offset))


_TYPE_TIERS_HEADER_PREFIX = "type_tiers:"
"""Column-0-only header prefix `set_type_tier` looks for. A leading `#`
never matches this (string `startswith` on the RAW line, no lstrip), so the
shipped-template fully-commented `# type_tiers:` line is correctly treated
as case (c) -- absent -- not as an editable header."""

_TYPE_TIERS_ENTRY_RE = re.compile(
    r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<sep>[ \t]*)"
    r"(?P<val>\S+)(?P<rest>.*)$"
)
"""One `type_tiers:` block entry: `{indent}{Key}:{sep}{val}{rest}`, where
`rest` is everything after the value to end-of-line (a trailing comment or
trailing whitespace), captured so case (a)'s rewrite can preserve it
byte-for-byte."""


def _split_line_ending(line: str) -> tuple[str, str]:
    """Split `line` (from `str.splitlines(keepends=True)`) into its content
    and its original line terminator (`"\\n"`, `"\\r\\n"`, or `""` for a
    final line with none), so a rewritten line can keep the exact same
    terminator it had before."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


@dataclass(frozen=True)
class _TypeTierEntry:
    """One parsed `type_tiers:` block entry, plus its position in the file."""

    line_index: int
    indent: str
    key: str
    sep: str
    val: str
    rest: str


def _validate_type_tier_vocab(concept_type: str, tier: str) -> None:
    valid_types = {ot.name for ot in types.REGISTRY}
    if concept_type not in valid_types:
        raise ValueError(
            f"{concept_type!r} is not a known concept type "
            f"(expected one of {sorted(valid_types)})"
        )
    if tier not in types.VOLATILITY_TIERS:
        raise ValueError(
            f"{tier!r} is not a valid volatility tier "
            f"(expected one of {sorted(types.VOLATILITY_TIERS)})"
        )


def _append_fresh_type_tiers_block(yaml_text: str, concept_type: str, tier: str) -> str:
    """Case (c): no uncommented `type_tiers:` header exists (absent, or the
    shipped-template fully-commented state) -- append a brand-new block at
    EOF after ensuring exactly one trailing newline; the rest of the file is
    untouched."""
    if not yaml_text:
        base = ""
    elif yaml_text.endswith("\n"):
        base = yaml_text.rstrip("\n") + "\n"
    else:
        base = yaml_text + "\n"
    return f"{base}type_tiers:\n  {concept_type}: {tier}\n"


def _parse_type_tiers_block(lines: list[str], header_idx: int) -> list[_TypeTierEntry]:
    """Parse the `type_tiers:` block body starting right after `header_idx`.

    The body is every following line that is blank, a comment (at any
    indent, including column 0), or a real `{indent}Key: val` entry; it ends
    at the first column-0 line that is neither blank nor a comment (the next
    top-level key). Raises `ValueError` on a tab-indented entry or a line
    inside the block that is indented but does not parse as an entry
    (fail-closed on any un-editable shape)."""
    entries: list[_TypeTierEntry] = []
    idx = header_idx + 1
    while idx < len(lines):
        content, _ = _split_line_ending(lines[idx])
        stripped = content.strip()
        if stripped == "" or stripped.startswith("#"):
            idx += 1
            continue
        if not content[:1].isspace():
            break
        match = _TYPE_TIERS_ENTRY_RE.match(content)
        if match is None:
            raise ValueError(
                "openkos.yaml: unrecognized line inside the 'type_tiers:' "
                f"block (line {idx + 1})"
            )
        indent = match.group("indent")
        if "\t" in indent:
            raise ValueError(
                "openkos.yaml: tab-indented 'type_tiers:' entry is not supported"
            )
        rest = match.group("rest")
        rest_stripped = rest.strip()
        if rest_stripped and not rest_stripped.startswith("#"):
            # The value token is `\S+`, so anything non-comment after it (a
            # second token, a YAML anchor tail like `&a slow`, a spilled quoted
            # value) would be blindly re-appended by the case (a) rewrite and
            # silently corrupt the value. Refuse rather than guess -- only a
            # bare tier and an optional trailing comment are editable.
            raise ValueError(
                "openkos.yaml: unsupported value in the 'type_tiers:' entry "
                f"(line {idx + 1}); only a bare tier and an optional trailing "
                "comment are supported"
            )
        entries.append(
            _TypeTierEntry(
                line_index=idx,
                indent=indent,
                key=match.group("key"),
                sep=match.group("sep"),
                val=match.group("val"),
                rest=rest,
            )
        )
        idx += 1

    if entries:
        first_indent = entries[0].indent
        if any(entry.indent != first_indent for entry in entries):
            raise ValueError(
                "openkos.yaml: inconsistent indentation inside the 'type_tiers:' block"
            )
        key_counts: dict[str, int] = {}
        for entry in entries:
            key_counts[entry.key] = key_counts.get(entry.key, 0) + 1
        if any(count > 1 for count in key_counts.values()):
            raise ValueError(
                "openkos.yaml: duplicate entry inside the 'type_tiers:' block"
            )
    return entries


def set_type_tier(yaml_text: str, concept_type: str, tier: str) -> str:
    """Return `yaml_text` with `type_tiers[concept_type] = tier` set via
    comment-safe text surgery -- never a YAML round-trip, so every other
    line, including comments, stays byte-identical (freshness-suggest-
    windows / `set-volatility` write verb, #140).

    Three edit cases, all load-bearing (design: "Text-Surgery Algorithm"):
    (a) the block already has an entry for `concept_type` -- only that
    line's value is rewritten, indent/separator/trailing comment preserved;
    (b) the block exists but has no entry for `concept_type` -- a new
    `{indent}{concept_type}: {tier}` line is inserted right after the last
    real entry, using the block's own canonical indent (or a fixed 2-space
    indent if the block is empty, i.e. header-only); (c) no uncommented
    `type_tiers:` header exists at all (absent, or the shipped-template
    fully-commented state) -- a fresh block is appended at EOF.

    Raises `ValueError` -- and returns nothing, leaving the caller to not
    write -- on `concept_type`/`tier` outside the known vocabulary (defense-
    in-depth; the CLI validates first) or on any `type_tiers:` shape this
    cannot confidently edit: an inline flow-mapping (`type_tiers: {...}`),
    more than one `type_tiers:` header key, a non-mapping scalar value, a
    tab-indented block, inconsistent entry indentation, or a duplicate entry
    key. Idempotent: if the entry already equals `tier`, the rewritten line
    is identical to the original, so the returned text is byte-identical to
    `yaml_text` (defense-in-depth -- the CLI already short-circuits before
    calling this).
    """
    _validate_type_tier_vocab(concept_type, tier)

    lines = yaml_text.splitlines(keepends=True)
    header_indices = [
        i
        for i, line in enumerate(lines)
        if _split_line_ending(line)[0].startswith(_TYPE_TIERS_HEADER_PREFIX)
    ]

    if len(header_indices) > 1:
        raise ValueError("openkos.yaml: multiple 'type_tiers:' keys found")

    if not header_indices:
        return _append_fresh_type_tiers_block(yaml_text, concept_type, tier)

    header_idx = header_indices[0]
    header_content, _ = _split_line_ending(lines[header_idx])
    trailing = header_content[len(_TYPE_TIERS_HEADER_PREFIX) :]
    trailing_stripped = trailing.strip()
    if trailing_stripped and not trailing_stripped.startswith("#"):
        if trailing_stripped.startswith("{"):
            raise ValueError(
                "openkos.yaml: inline flow-mapping 'type_tiers: {...}' is not supported"
            )
        raise ValueError(
            "openkos.yaml: 'type_tiers:' has a non-mapping value "
            f"({trailing_stripped!r})"
        )

    entries = _parse_type_tiers_block(lines, header_idx)
    existing = next((entry for entry in entries if entry.key == concept_type), None)
    new_lines = list(lines)

    if existing is not None:
        _, terminator = _split_line_ending(lines[existing.line_index])
        new_content = (
            f"{existing.indent}{existing.key}:{existing.sep}{tier}{existing.rest}"
        )
        new_lines[existing.line_index] = new_content + terminator
        return "".join(new_lines)

    if entries:
        insert_indent = entries[0].indent
        insert_at = entries[-1].line_index + 1
    else:
        insert_indent = "  "
        insert_at = header_idx + 1

    new_lines.insert(insert_at, f"{insert_indent}{concept_type}: {tier}\n")
    return "".join(new_lines)
