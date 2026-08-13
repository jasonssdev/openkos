# `language_leak` — the chunked-path title-language probe (#563)

Issue #563: 39 short course documents showed zero title-language leakage,
but two long, chunked Spanish meeting transcripts leaked ~25% — later
chunks emitted English titles (`knowledge-recovery-system`,
`knowledge-engine-setup-and-usage`), and the slug is the permanent Concept
ID. This probe reproduces that failure shape deterministically and gives a
prompt change something to move against.

```bash
python evals/language_leak/run_language_leak_probe.py --arm baseline --runs 3
python evals/language_leak/run_language_leak_probe.py --arm treatment --runs 3
```

One ~24 KB synthetic Spanish meeting transcript (7 chunk windows) whose
prose names English technical terms heavily — the code-switched register of
the transcripts that leaked. The probe runs the PER-WINDOW extraction calls
(`_extract_once` per `_chunk_lines` window, the fan-out both chunked paths
share) and scores every candidate title as `es` / `en` / `mixed` /
`neutral` by marker-word membership. **Leak rate** = `en`+`mixed` share.

## What it measured (qwen3:8b, 3 runs/arm, 2026-08-13)

| arm | leak rate | titles/run | run latency | window errors |
| --- | --- | --- | --- | --- |
| baseline (shipped prompt) | **0.69** | 46.3 | 436s | 2 |
| named-language anchor | **0.63** | 53.7 | **798s** | 5 |

**The mechanism is confirmed and it is enormous at window level.** 96 of
139 baseline titles carried English (`Onboarding Procedure for New Team
Members`, from a 100%-Spanish source). The field's ~25% is what survives
the judge; the windows underneath emit ~2/3 leaked candidates.

**The named-anchor treatment was REJECTED.** Replacing the meeting-path
anchor with one that NAMES the document's dominant language ("Write every
title … in Spanish — the dominant language of the document…") moved leakage
0.69 → 0.63 — one flipped title of noise — while nearly doubling run
latency and pushing more thinking runaways into the generation cap. Prompt
instructions do not carry this rule at this model tier, exactly as
`evals/edge_typing`'s direction-guard measured for edge direction (#561)
and the anti-twin clause measured before that (#380). The candidate is kept
reproducible as the probe's `treatment` monkeypatch; the production prompt
is untouched.

**A deeper reading of the leaked titles**: many are the model naming a
subject in the language the subject's own name appears in — `Model Context
Protocol` *is* the proper name; `Evaluation Harness` is quoted verbatim
from the Spanish prose. The genuinely harmful class is the translatable
title rendered in English (`Recovery of Knowledge Project`,
`Decision on Bundle and Storage Centralized`). A fix has to separate those
classes, which prose instructions demonstrably cannot; the deterministic
follow-up direction (post-extraction language gate on translatable titles)
is filed from these numbers.

## The #618 gate, measured (qwen3:8b, 3 runs, 2026-08-13)

The probe now measures the class split and the shipped production gate
(`concept._drop_wrong_language_titles`, chunked paths only) on the same
emissions:

- **Harmful class** = a PURE-`en` title with no verbatim support in the
  prose, after stripping balanced `(...)` spans (#592's precedent —
  `Model Context Protocol (MCP)` is the proper name plus its acronym, not
  a leak). `mixed` is NOT harmful: on this fixture a mixed title is a
  Spanish title quoting an English term, which is the model doing the
  right thing. The first analysis counted `mixed` and unstripped
  parentheticals as harmful and overstated the class ~2x.
- The gate's classifier is deliberately DIFFERENT from the probe's: the
  gate votes generic function words; the probe's marker lists are this
  fixture's constructed ground truth. The probe measures the gate, not
  itself.

| metric | value |
| --- | --- |
| harmful-class rate, pre-gate | **0.15** (18 of 117 titles) |
| harmful-class rate, post-gate | **0.08** (8 of 106 titles) |
| gate drops across runs | 11 — every one harmful-shaped, zero false positives |
| objects per run | 39.0 → 35.3 (no collapse) |

Every dropped title carried an English function word (`Decision on …`,
`Onboarding Procedure for …`, `… with Judge Ensemble`). **The residual 8
are bare English noun phrases with NO function words at all** (`Knowledge
Recovery Project Phase Two`, `Evaluation Pipeline Project`, `New Team
Onboarding Procedure`) — invisible to function-word voting by
construction. A bigram-adjacency check (drop a neutral multi-word title
whose word bigrams are not all adjacent in the prose) separates them on
these emissions but has an unresolved false-positive risk on legitimate
dominant-language titles that also vote neutral; that residual class was
filed as #622 and measured below.

## The #622 bigram-adjacency extension, measured (2026-08-13) — REJECTED as-is

The candidate: for a title the gate's voter sees NO function words in
(gate-neutral — deliberately excluding MIXED titles, which may compose
legitimately), strip balanced `(...)` spans, then require every
consecutive word pair to appear adjacent in the prose; non-adjacent means
recombination, not quotation, and drops. Implemented in this probe
(`bigram_adjacent`, `score_extension`, `--analyze` for stored emissions),
NOT in production.

Measured over every stored emission set (two stored baseline files, one
fresh 3-run baseline `20260813T071333Z`, and the rejected-treatment file —
12 runs, ~500 kept titles):

| emission set | residual harmful (post-#618) | caught | false positives |
| --- | --- | --- | --- |
| baseline stored ×2 (6 runs) | 9 | 9 | **0** |
| baseline fresh (3 runs) | 10 | 10 | **0** |
| treatment stored (3 runs) | 2 | 2 | **1** — `Snapshot Derivado` |

On production-shaped emissions the extension is perfect: 19/19 caught,
zero false positives, harmful-after-gate 0.11 → **0.00** on the fresh
run. But the treatment file holds exactly the false-positive specimen
#622 predicted: `Snapshot Derivado` — a legitimate dominant-language
title (Spanish morphology, English loanword), gate-neutral because
neither word is a function word, and non-adjacent because the prose says
`snapshots derivados` (plural) while the title composes the singular.
**Morphological variation breaks bigram adjacency structurally in
Spanish**, so the false-positive class is demonstrated non-empty on real
model output — under a different prompt, but the composing behavior is
the model's, not the prompt's. Per the shipping bar (zero false
positives; a false positive is silent data loss, the same asymmetry that
keeps the twin-rule floor), the pure bigram check does NOT ship.

**The evidence-backed next candidate is the combo** (#622's own option
(a)): exempt any title containing a word with Spanish orthographic
markers (accents, or suffixes `-ción/-sión/-miento/-ería/-encia/-ancia/
-dad/-ado/-ada`) before the adjacency test. Re-scored offline over all
12 stored runs: **21/21 residuals caught, 0 missed, 0 false positives**
— `Snapshot Derivado` is exempted by `-ado` while every caught residual
is pure-English orthography. Filed as the follow-up from these numbers.

## The #630 combo, measured (2026-08-13) — SHIPPED

The candidate #622's rejection pointed at: exempt any title containing a
word with Spanish orthographic markers (accents `áéíóúüñ`, or suffixes
`-ción/-sión/-miento/-ería/-encia/-ancia/-dad/-ado/-ada`, matched only
when the word is STRICTLY longer than the suffix, so English `dad` never
matches) BEFORE the adjacency test. Implemented in this probe
(`spanish_orthography`, wired into `score_extension`) and measured over
every stored emission set PLUS one fresh 3-run baseline sweep
(`20260813T085052Z` — 15 runs, 597 kept titles):

| emission set | residual harmful (post-#618) | caught | false positives |
| --- | --- | --- | --- |
| baseline stored ×2 (6 runs) | 9 | 9 | **0** |
| baseline fresh #1 (3 runs, `071333Z`) | 10 | 10 | **0** |
| baseline fresh #2 (3 runs, `085052Z`) | 11 | 11 | **0** |
| treatment stored (3 runs) | 2 | 2 | **0** — `Snapshot Derivado` exempted by `-ado` |
| **total** | **32** | **32** | **0** |

Residual harmful after gate + extension: **0.00 on every run**. One
adjudication was needed on the fresh sweep: the model emitted `Language
Leakage Measurement` — a translation of the transcript's own subject
(`la fuga de idioma`) none of whose words appear anywhere in the prose —
which the hand-built marker lists could not label (`language`, `leakage`,
`measurement` were in neither list), so the scorer initially counted two
CORRECT drops as false positives. The three unambiguously-English words
were added to `_EN_MARKERS` (adjudication note inline); the generated
`085052Z` report file predates that adjudication and shows the stale
`FP!` marks. The English-collision check the #630 design point asked for:
zero English titles in any stored emission trigger the exemption, and a
`/usr/share/dict/words` sweep finds only rare loanwords (`tornado`,
`armada`, `avocado`) — each a potential missed catch (fail-open), never a
false positive, which is the side the zero-FP bar protects.

Production shipped the same mechanism in
`concept._drop_wrong_language_titles` (#630): on a SPANISH-dominant
document only (the measured direction), a gate-NEUTRAL title (no function
words on either side; MIXED never reaches the check) with no Spanish
orthographic marker, no verbatim support (balanced `(...)` stripped
first), and non-adjacent bigrams drops — chunked paths only, the all-drop
floor and both fail-open guards unchanged.

## Measurement lessons (paid for four times)

1. **Never run an eval client uncapped.** A bare `OllamaClient` has no
   `num_predict` bound; qwen3's thinking ran away unboundedly and one call
   exceeded THIRTY minutes before the 1800s transport deadline killed the
   whole arm. Production always runs with `max_generation_tokens` (8192);
   a probe measuring production-shaped behavior must too.
2. **The judge call is the pathological one.** Whole-source + full
   candidate list in one prompt is where the runaway lived. This probe
   measures per-window calls only — the judge merely SELECTS among titles
   the windows already emitted, so window-level language decides the
   outcome, and the probe stays runnable.
3. **A window failure must not kill the arm.** `OllamaError` per window is
   counted (`window errors`) and skipped; a language probe needs many
   titles, not all-or-nothing runs.
4. **One probe process at a time, and no branch switches while it runs.**
   A zombie look-alike launch starved a sibling into spurious timeouts, and
   a `git checkout` removed the script from under a queued arm. Check
   `pgrep` before relaunching; leave the working tree alone until the run
   ends.

Never compare arms measured on different fixture text.
