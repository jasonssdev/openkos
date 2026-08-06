# Exploration: bound-generation-length

**Issue**: #422 — extraction is bimodal. Every successful run finished under 60 s
(slowest 56.2 s); 4 of 60 runs hit a 180 s deadline and returned nothing; an
earlier sweep hit 3 of 5 at a 600 s deadline. Raising the deadline converts a
3-minute loss into a 10-minute one; it does not convert a hung run into a slow
one.
**Targets** #379's bounded-cost criterion — criterion 2 after the 2026-08-06
reformulation (the issue body predates that renumbering and calls it criterion
3; both name the same gate item). It is the P0 gate's only remaining open
criterion.
**Phase**: `sdd-explore`. No production code written; no behavior changed.

Two of the first pass's recommendations are replaced below, both with evidence.
Everything else is carried forward.

## Current state

### The client seam

`OllamaClient.chat` (`src/openkos/llm/ollama.py:396-450`) builds the `/api/chat`
POST body at `:399-406` with `model`, `messages`, `stream`, `think` — and **no
`options` key**. Ollama's `options` object accepts `num_predict`, a hard
server-side ceiling on generated tokens. The extension point exists; the project
does not use it.

`_chat_client` (`src/openkos/cli/main.py:105-128`) names the seam's scope in its
own docstring: *"Every chat verb goes through here… a workspace that raises its
deadline raises it for `ingest`, `curate`, `query`, `adjudicate`,
`suggest-relations`, and `contradictions` alike."* `curate.py:985-987` builds its
own `OllamaClient` because importing `_chat_client` would be circular
(`main.py:123-126`), and `tests/unit/cli/test_chat_timeout_wiring.py` pins both
sites so they cannot drift.

So there are exactly **two construction sites**, already under a drift-pinning
test, and adding an optional payload key changes no downstream signature.

### `extract_json_items` on truncated input — the crux

`src/openkos/llm/parsing.py:64-95`. On a mid-JSON truncation, all four
candidates fail in sequence: `json.loads(raw)` raises and is caught (`:89-90`);
`_strip_code_fence` requires a **closing** fence (`:23-26`);
`_first_bracket_block` requires a closing `]` (`:36-40`); `_first_brace_block`
requires a closing `}` (`:29-33`). Every candidate is `None` or unparseable, so
the loop falls through to `return []` (`:95`).

**It degrades cleanly and never raises.** But that `[]` is structurally
identical to "the model legitimately found nothing." `extract_concept`
(`extraction/concept.py:498-564`) then builds
`ExtractionReport(produced=0, retained=0)`, and its own docstring says such a
report *"renders no notice."*

This is the load-bearing finding for the design: **a token cap is safe for
function but must not ship silently.** Today a hung call costs 600 s and ends in
`OllamaUnavailable` → `extraction_status: failed` — loud. A cap with no signal
would return in seconds with `[]` and no notice — fast but silent. That trades a
visible failure for an invisible one, which is the exact defect #381, #404 and
#409 were each filed about. The bound and its signal must ship together.

### Empirical grounding for the ceiling (measured 2026-08-06)

The first pass flagged, correctly, that no token-count data existed in the repo
and that shipping an unmeasured ceiling would repeat `_MAX_OBJECTS_PER_SOURCE`'s
history (calibrated 5 → 6 only after it was found discarding real material in
12/14 and 13/13 runs, `concept.py:426-438`).

That gap is now closed. Five extraction calls were run against local `qwen3:8b`
through the project's own `_build_messages` and `_SYSTEM_PROMPT`, on 17 KB real
prose sources, reading `eval_count` off the response:

| source | `eval_count` | `done_reason` | objects |
| --- | --- | --- | --- |
| `docs/architecture.md` | **4154** | `stop` | — |
| `docs/knowledge-object-model.md` | 1624 | `stop` | 11 |
| `docs/user-journey.md` | 962 | `stop` | — |
| `docs/architecture.md` (repeat) | 269 | `stop` | 1 |
| `docs/testing.md` | 107 | `stop` | 1 |

Two things follow.

**The ceiling can be grounded.** The largest legitimate, fully-completed reply
was 4154 tokens. A rail at 8192 leaves roughly 2× headroom over the largest
observed real extraction.

**The bimodality is visible in token counts.** `docs/architecture.md` produced
4154 tokens on one call and 269 on another — same document, same model, same
prompt, a 15× spread. That is #422's variance measured at the generation layer
rather than the wall clock, and it corroborates the issue independently.

`done_reason` was `stop` on all five, confirming the field is present in the
non-streaming `/api/chat` response and is simply discarded today at
`ollama.py:442`, where `chat()` reads only `data["message"]["content"]`.

## Correction 1 — how truncation is reported (prior recommendation replaced)

**Prior recommendation**: extend `ExtractionReport` with a truncation field, set
at `extract_concept`'s call site from *"a comparison of raw reply length against
a cap-derived heuristic."*

**Why it is wrong.** A heuristic on reply length is a third source of silent
wrongness sitting on top of the two this change exists to remove, and it is
unnecessary: `done_reason` is authoritative, present in every response (verified
above), and free. Inferring what the server already told us is strictly worse
than reading it.

The prior pass framed the alternative as a binary — either a heuristic, or widen
`LLMBackend.chat` from `-> str` to a richer type. The second option was rejected
on blast radius, correctly: `LLMBackend` is a `Protocol` (`llm/base.py:21-26`)
that every test double implements structurally, so changing the return type
breaks every stub in the suite.

**There is a third option, and it is cleaner than both.** Raise.

`OllamaClient.chat` detects `done_reason == "length"` and raises a new
`OllamaGenerationCapped(OllamaError)`, alongside the existing
`OllamaUnavailable` / `OllamaModelNotFound` / `OllamaEmbeddingDimensionMismatch`
(`ollama.py:43-58`). This works because the error family is **already** the
contract:

- `extract_concept`'s docstring (`concept.py:542`): *"Any `OllamaError`-family
  exception raised by `llm.chat` propagates."*
- `concept.py:8-14`: the CLI catches `OllamaError` to keep its Source-only
  fallback.

So a capped generation lands in exactly the handling a hung one gets today —
`extraction_status: failed`, loud, per-source — while the wall clock drops from
600 s to bounded. No `Protocol` change, no stub churn, no client state, no
heuristic, and **no `ExtractionReport` change at all**.

It is also the honest representation. A truncated reply is not a partial
success: `extract_json_items` returns `[]`, so there is nothing to salvage.
Raising says what happened.

## Correction 2 — the ceiling is a rail, not a tuning knob

The prior pass treated the default as needing a calibration sweep on the scale
of `_MAX_OBJECTS_PER_SOURCE`'s (15+ runs per cell) before anything could ship.

That framing over-reaches, because the two ceilings do different jobs.
`_MAX_OBJECTS_PER_SOURCE` is a **quality** ceiling: it decides how much real
material to keep, so mis-calibration silently discards good work. `num_predict`
is a **safety rail**: its only job is to stop a generation that is not going to
terminate. The project already has this distinction — `_MAX_CANDIDATE_GROUPS =
50` (`resolution/candidates.py:88`) is a rail, and #427's measurement confirmed
it was never the binding constraint at real corpus scale.

A rail should be set generously enough that legitimate work never reaches it,
and that is a much weaker evidentiary requirement than tuning. The five samples
above are sufficient for that: 8192 against a 4154-token observed maximum. It
must be documented as a rail, with the measurement that justifies it, exactly as
`DEFAULT_CHAT_TIMEOUT` documents its own number and its own limits
(`config.py:75-89`).

## Options evaluated

| Option | Description | Effort | Verdict |
| --- | --- | --- | --- |
| (a) `num_predict` at the client seam + raise on `done_reason == "length"` | Config-driven ceiling mirroring `chat_timeout`; a new `OllamaError` subclass carries the signal | ~120–180 | **Recommended** |
| (b) Cap + truncation field on `ExtractionReport`, set by heuristic | As the first pass proposed | ~190–310 | Rejected — infers what `done_reason` states, and leaves non-extraction verbs capped but silent |
| (c) Cap + widen `LLMBackend.chat` return type | Authoritative but breaks every structural stub | High | Rejected on blast radius |
| (d) `stop` conditions instead of a ceiling | A `stop` list ends generation on a matched string | Low | Rejected as a substitute — a generation that never terminates never reaches a stop string. Possible complement, not the fix |
| (e) Raise `chat_timeout` further | Symptom only | ~5 | Rejected by the issue itself |

**Recommendation: (a).** Client seam, so `curate`, `adjudicate`,
`suggest-relations` and `contradictions` get the same bound extraction does —
per-verb placement would repeat the exact drift defect #405's `chat_timeout`
work was written to close.

## Affected areas

- `src/openkos/llm/ollama.py` — `OllamaClient.__init__` gains an optional
  ceiling; `chat()` emits `options.num_predict` and raises
  `OllamaGenerationCapped` on `done_reason == "length"`; new error class beside
  the existing three.
- `src/openkos/cli/main.py:105-128` (`_chat_client`) and
  `src/openkos/cli/curate.py:985-987` — both construction sites, mirroring
  `chat_timeout`'s two-site pattern.
- `src/openkos/config.py` — new constant with its measurement documented, new
  `Config` field, `read_config` parse and validation.
- `src/openkos/templates/openkos.yaml.template`, `docs/cli.md` — documented
  beside `chat_timeout`, same section shape.
- **Not** `src/openkos/extraction/concept.py`, and **not**
  `src/openkos/llm/parsing.py`. Correction 1 removes both from scope.

## Open decision for `sdd-propose`

Ollama's `num_predict` sentinels (`0` = return no completion, `-1` = unlimited,
`-2` = fill context) carry server-defined meanings, so `chat_timeout`'s "reject
`<= 0`" validation cannot be copied verbatim. Either accept the sentinels and
document them, or refuse them and require a positive integer. Refusing is the
better default for a safety rail — `-1` would silently disable the very bound
this change installs — but it must be a stated decision, not an accident.

## Tests

- Behavior-first, injected `urlopen` / stub `LLMBackend`; never a real Ollama
  call in a unit test.
- `chat()` sends `options.num_predict` when configured, and omits `options`
  entirely when not — so an opted-out workspace is byte-identical to today.
- `done_reason == "length"` raises `OllamaGenerationCapped`; `"stop"` does not.
- A `test_chat_timeout_wiring.py`-shaped pinning test across both construction
  sites, to prevent the drift that test already guards for `chat_timeout`.
- `extract_json_items` returns `[]` on a mid-object truncation — true today,
  currently unpinned by any test found in this pass.
- Config validation, including whichever sentinel decision is taken.

## Changed-lines forecast

| Slice | Estimate |
| --- | --- |
| Config knob + template + docs | ~60–90 |
| Client seam: `options` payload, error class, `done_reason` check, two wiring sites | ~40–60 |
| Tests | ~50–80 |
| Combined | ~150–230 |

One slice, one PR. Correction 1 removes the `ExtractionReport` work the first
pass costed at ~30–50.

## Risks

- A rail set too low silently truncates legitimate work. Mitigated by measuring
  first (8192 against a 4154 observed maximum) and by the fact that reaching it
  now raises rather than returning `[]` — a mis-set rail is loud, not silent.
- Non-extraction verbs gain the bound and the error, but none of them has
  extraction's per-source `extraction_status` record, so the operator experience
  of a capped `curate` call is worth checking during design rather than assumed.
- The five samples are one model (`qwen3:8b`) on one machine. Sufficient to site
  a rail, not to claim a universal number — which is why the knob is
  configurable and documented as a rail.

## Ready for proposal

Yes, with the sentinel-validation decision resolved explicitly rather than
deferred.
