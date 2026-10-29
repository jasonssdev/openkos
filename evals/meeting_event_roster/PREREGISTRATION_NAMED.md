# Pre-registration: a named-speaker roster rule (#1689, task T5a)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
`named` command is run.** A change after that point is a new registration, not
an edit to this one. Production code and defaults are unchanged by this file.
No model is called by anything here. Every number marked **(proposed)** is the
author's proposal until the owner confirms it. This follows the retry
registration (`PREREGISTRATION_RETRY.md`, PR #1718) and its results
(`RESULTS_RETRY.md`, PR #1721, LICENSED on both arms), whose closing rule says
the OpenSpec change "must first generalize the roster rule".

## Question

`roster_of` reads 2 to 4 capitals or digits opening a line (`PM:`), the AMI
form. A source with named speakers (`Ana:`, `Dr. Smith:`, `María José:`) yields
no roster, the prompt then says "none labelled" and asks for no participants.
**Is there a roster rule that reads named speakers with no false speaker and
leaves the AMI rosters exactly as they are?** It adopts nothing and changes
neither the compose prompt nor the participant check.

## What production receives (read, not assumed)

Production extraction hands the Event compose step the **raw source text**; a
meeting is recognised by `_is_meeting_shaped` (`src/openkos/extraction/concept.py`):
a gathering word in the title, or content that reads as speaker turns
(`_transcript_shaped_text`). The content test uses `_TRANSCRIPT_TURN_RE`: an
optional timestamp prefix (`[00:01:10] `, `10:31 `), a label of at most 39
characters that does not open with `# - * + > | `` ` ``, a colon and a space
(which excludes URLs), and bold turns (`**Ana:** `, `**Ana**: `) after paired
emphasis is stripped (`_turn_match`). A label is then rejected structurally:
more than 3 words, any digit, or 3 or more characters in capitals. A label counts
as a speaker when it recurs at least 3 times, at least 2 do, and recurring turns
are at least 10 and half the non-blank lines. The roster rule proposed
here **is** that detector's definition of a speaker, mirrored in the harness (the
rule code does not import the private function; only the self-test does, to pin them). In
production it would run only on sources already judged meeting-shaped. A
title-only match (a summarised note) has no turns and so gets no roster,
correctly.

## The proposed rule (`roster_of_named`, frozen here)

`roster_of` is untouched (it stays the registered rule for the AMI sources). The
new function returns, in order of first appearance, the union of:

1. **AMI-form labels**: exactly what `roster_of` returns (2 to 4 capitals or
   digits, at least 2 lines). The union is what keeps a label such as `PM`
   readable even where production's grammar would reject a longer capitals label.
2. **Production speakers**: the labels production's detector counts as recurring
   speakers, with its criteria and constants unchanged: the line shape of
   `_TRANSCRIPT_TURN_RE` and `_turn_match` (optional timestamp, label not opening
   with a markdown structure character, colon then whitespace then text, bold
   `**Ana:**` / `**Ana**:` retried once with paired emphasis stripped); a label of
   **at most 3 words, no digit, and not 3 or more capitals**; **recurrence of at
   least 3 lines** (`_TRANSCRIPT_MIN_TURNS_PER_SPEAKER`); and **at least 2** such
   speakers (`_TRANSCRIPT_MIN_SPEAKERS`), otherwise this part is empty. Production's
   further shape gate (10 recurring turns, half the non-blank lines) decides whether
   a source is a transcript at all and is not repeated here.

**One definition, no lexicon.** An earlier draft of this proposal used its own
label grammar plus a closed list of about 50 heading words (`Nota`, `Acción`, ...)
and recurrence 2. It was considered and dropped: a second detector would let the
Event roster and the meeting gate disagree on who the speakers are. The roster and
the gate now share one definition of a speaker; a heading that recurs 3 times
beside another counts as a speaker in both, by design.

Known limits, inherited from production's grammar and not fixed here: a name of
four or more words (`Ana de la Cruz`) and a recurring heading label are not
distinguished from, respectively, a prose prefix and a speaker; an all-capitals
label of 3 or more characters is never a production speaker (AMI `PM`, `ID`, `UI`,
`ME` are 2 letters and are read by both rules); an AMI-form label of 3 or 4
capitals that recurs twice is still read via `roster_of`, as it is today.

## Data (stated plainly)

**No real named-speaker transcript exists locally beyond one.** Listed
read-only: `~/Dev/Projects/openkos-eval-runs` and `~/openkos-eval-runs` hold the
AMI Spanish translations (speaker labels `PM ID UI ME`), model outputs, sheets
and the `ask-screen` built workspace (three project meetings with named
speakers, not committed and not used here because their names are the owner's
colleagues). The only committed named-speaker transcript is
`examples/extraction-corpus/sources/medium-10-reunion-plataforma.md` (fictional,
four speakers, 92 turn lines). So the check is built from:

| Set | Content | Role |
|---|---|---|
| **Synthetic fixtures** | 14 positive and 8 negative sources written in the harness (`NAMED_FIXTURES`, `NAMED_NEGATIVES`), fictional names: plain, titles, compound names, timestamps, bold, particles, initials, apostrophes and hyphens, accents, a header block and `Nota:`/`Acción:` lines inside a transcript (twice each, below recurrence), a stray single label, URLs and time lines, AMI form, mixed AMI and named; negatives: header block only, repeated headings (twice), Q and A (twice), a monologue, two turns each, prose with colons, bullets and quotes, no labels | precision, recall, false labels |
| **medium-10** | the committed fictional meeting; gold `Elena Vidal, Marcos Iturra, Paula Cifuentes, Tomás Reyes` | one real-form named check |
| **AMI sources** | the 5 Spanish sources of the roster run (`mmb.SOURCES`), read from the translations directory | no-regression control |

Everything else about a real production corpus (Slack exports, Zoom/Teams
captions, dictation) is **not measured**; see Limits.

## Bars (all proposed; model-free; decided before `named` is run)

- **N1 precision**: speaker-level precision over the positive fixtures
  **= 1.0** (no label that is not a speaker).
- **N2 recall**: speaker-level recall over the positive fixtures **at least
  0.95**.
- **N3 exact rosters**: the share of positive fixtures whose roster equals the
  gold set **at least 0.90**.
- **N4 negatives**: labels returned on the negative fixtures **= 0**.
- **N5 real**: on medium-10, precision **= 1.0** and recall **at least 0.95**
  (that is, all four).
- **N6 AMI unchanged**: for every one of the 5 AMI sources,
  `roster_of_named(text) == roster_of(text)` (same labels, same order). Any
  difference rejects.

N1 to N6 are the same bars as the first draft; only the rule under test changed.

Verdict (`named_verdict`): **LICENSED** only when N1 to N6 all hold, otherwise
**REJECTED**. No INCONCLUSIVE: the check is deterministic and complete over its
set. The self-test also pins the guards, the union rule, the floor, the scoring
and that the AMI-only rule fails these bars on the named fixtures.

## Is a model arm needed?

**No, and none is registered.** The compose prompt is byte-identical (hash
`4f93593ea5bea022`) and already tells the model to name participants only by the
labels in the list, a name the source gives following in parentheses. What
changes is the content of one line, `SPEAKERS IN THE SOURCE (complete list)`,
from `PM, ID` to `Ana, Luis`. The retry measured that the model obeys the list
(0 of 30 bodies named an absent participant). A name is, if anything, easier to
copy than `PM`. That is an expectation, not a measurement. If the owner wants
the claim measured, the minimal arm is: medium-10 and a few synthetic named transcripts as the sources, `r1` only (the shipped gate), 10 calls at about 21 s
plus a 2 min load, about 0.1 GPU-hours typical, **hard cap 1 GPU hour**, one
Ollama job at a time, GPU reserved by the owner so ask first, and **its own
registration** (it is not part of this one).

## Cost forecast

Model-free: zero GPU, a few seconds. The self-test adds 8 tests.

## Disclosed before this registration (forking paths)

- The fixtures were written **together with the rule, by the same author, in the
  same sitting**; the self-test passes on all of them. The fixtures are therefore
  not blind: they were shaped by the classes the author thought of (headings,
  URLs, times, bullets). The rule was revised once on review, from an own grammar
  with a lexicon to production's definition (above); fixtures were then adjusted
  to it (three turns per speaker, a three-word name instead of a four-word one),
  which is a fixture change made after seeing the rule fail on them. Pass on the
  fixtures is thus weak evidence; the harder, less-tuned evidence is medium-10
  and the 5 AMI sources, and even those were looked at.
- The author read the first 25 lines of medium-10 and the speaker-label counts of
  all 12 AMI translations (`PM ID UI ME` only, hundreds of turns each) and the
  head of one `ask-screen` meeting, to design the guards. `named` was **not**
  run, so no N5 or N6 number has been seen. N6 may fail for a reason the author
  has not seen (a capitalised word followed by a colon inside AMI utterances).
- No threshold is the author's: recurrence 3, two speakers and the label grammar
  are production's, mirrored; the self-test pins the two constants to
  `concept.py` and the speaker sets to `_turn_match` on every fixture, medium-10
  and the registered AMI-form fixture.

## Exact commands

```sh
git rev-parse HEAD
H=evals/meeting_event_roster/measure_meeting_event_roster.py
OLLAMA_HOST=127.0.0.1:1 uv run python $H --self-test
OLLAMA_HOST=127.0.0.1:1 uv run python $H named > "$HOME/Dev/Projects/openkos-eval-runs/meeting-event-roster-named.json"
```

`named` prints `fixtures` (precision, recall, exact, wrong, false labels),
`real`, `ami` (rosters and the sources whose roster changed) and `verdict`. It
runs once, after this file is REGISTERED; nothing is re-run, subset or extended
after a number is seen.

## The closing rule

- **LICENSED**: the roster rule is generalized as `roster_of_named`; the
  OpenSpec change for the roster-grounded Event body may proceed with it (and
  with the retry arm's re-ask, as that arm's closing rule says). This file
  adopts nothing.
- **REJECTED**: reported with the bar and the offending sources. A new rule is a
  new registration; the OpenSpec change does not proceed with a rule that failed
  N1, N4 or N6. A failure of N2, N3 or N5 alone narrows what the roster covers
  and is reported as such, for the owner to decide.

## Limits (assumptions, labelled)

- Synthetic fictional fixtures plus one fictional transcript: no real
  named-speaker corpus was available. The rule is untested on exports with
  `Name (role):`, `Name - text`, `<Name>` (IRC), all-caps names (`ANA:`),
  one-word lowercase handles, non-Latin scripts, or speakers called `Nota`.
- A speaker who recurs as a heading word, a name of four or more words, and the
  AMI-vs-production grammar differences (above) are inherited, not measured away.
- Roster extraction only; whether the model then uses the list well on named
  speakers is the unmeasured expectation above.

## The owner confirms

Nothing below is confirmed yet.

1. **The rule**: the name-form roster is exactly production's recurring
   speakers (same line shape, same label grammar, recurrence 3, speaker floor 2),
   mirrored not imported and pinned by a self-test, unioned with `roster_of`
   unchanged.
2. **No lexicon**: the earlier own-grammar-and-heading-list variant was
   considered and dropped so the Event roster and the meeting gate share one
   definition of a speaker.
3. **The data**: synthetic fixtures plus medium-10 plus the 5 AMI sources, no
   `ask-screen` meetings, and the plain statement that no real named-speaker
   corpus exists locally.
4. **Bars N1 to N6** and the verdict (LICENSED only if all hold; REJECTED
   otherwise, with the N2, N3, N5 nuance).
5. **No model arm** in this registration, with the optional minimal arm
   (about 0.1 GPU-hours, cap 1 GPU hour) left to its own registration if wanted.
6. **The inherited limits** (four-word names, recurring headings, 3-or-more-capital
   labels outside the AMI union) are accepted.
7. **Disclosure**: fixtures and rule were written together and adjusted after the
   revision; medium-10 and the AMI label counts were looked at; `named` was not run.
8. **The closing rule** as written.
